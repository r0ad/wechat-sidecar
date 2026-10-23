"""微信窗口定位与截屏。

采集方式（优先级从高到低）：
1. **Windows Graphics Capture（WGC）**：按窗口句柄抓取，窗口被遮挡也能读，
   且只在画面变化时出帧；
2. GDI 屏幕截取（PIL.ImageGrab）：WGC 不可用时的兜底，此时要求微信在前台。

两种方式都只读取屏幕像素，不注入、不读取微信内存/数据库。
"""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass

import numpy as np
from PIL import ImageGrab

try:  # pragma: no cover - 仅 Windows
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    win32api = None  # type: ignore[assignment]
    win32gui = None  # type: ignore[assignment]
    win32process = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]

WECHAT_TITLES = {"微信", "WeChat", "Weixin"}
WECHAT_EXES = {"weixin.exe", "wechat.exe"}


class CaptureError(RuntimeError):
    """窗口不可用 / 截屏失败。"""


@dataclass
class WeChatWindow:
    hwnd: int
    title: str
    exe: str
    minimized: bool

    def client_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.client_bbox()
        return right - left, bottom - top

    def client_bbox(self) -> tuple[int, int, int, int]:
        """客户区在屏幕坐标系中的 (left, top, right, bottom)。"""
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        sx, sy = win32gui.ClientToScreen(self.hwnd, (left, top))
        return sx, sy, sx + (right - left), sy + (bottom - top)

    def dpi_scale(self) -> float:
        """窗口所在显示器的缩放比例（96 DPI = 1.0）。"""
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(self.hwnd)
            if dpi:
                return dpi / 96.0
        except Exception:
            pass
        return 0.0


def enable_dpi_awareness() -> None:
    """必须在任何窗口/截屏 API 之前调用，否则坐标会被系统缩放。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _exe_name(pid: int) -> str:
    if win32api is None:
        return ""
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
            int(handle), 0, buf, ctypes.byref(size)
        )
        return buf.value.rsplit("\\", 1)[-1] if ok else ""
    finally:
        win32api.CloseHandle(handle)


def list_candidates() -> list[WeChatWindow]:
    if win32gui is None:
        raise CaptureError("缺少 pywin32，无法枚举窗口（pip install pywin32）")
    found: list[WeChatWindow] = []

    def _callback(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if title not in WECHAT_TITLES:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = _exe_name(pid)
        if exe and exe.lower() not in WECHAT_EXES:
            return True
        found.append(
            WeChatWindow(
                hwnd=hwnd,
                title=title,
                exe=exe,
                minimized=bool(win32gui.IsIconic(hwnd)),
            )
        )
        return True

    win32gui.EnumWindows(_callback, None)
    return found


def find_window() -> WeChatWindow:
    """找到微信主窗口；优先非最小化、面积最大的那个。"""
    candidates = list_candidates()
    if not candidates:
        raise CaptureError(
            "没有找到微信窗口：请确认微信已登录、主窗口没有关闭到托盘"
            "（点一下任务栏/托盘里的微信图标）"
        )
    normal = [w for w in candidates if not w.minimized]
    if not normal:
        raise CaptureError("微信窗口已最小化，请先还原窗口（最小化时无法截屏）")
    normal.sort(key=_area, reverse=True)
    return normal[0]


def _area(win: WeChatWindow) -> int:
    try:
        left, top, right, bottom = win.client_bbox()
        return max(0, right - left) * max(0, bottom - top)
    except Exception:
        return 0


def is_foreground(hwnd: int) -> bool:
    if win32gui is None:
        return False
    fg = win32gui.GetForegroundWindow()
    if not fg:
        return False
    try:
        root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    except Exception:
        root = hwnd
    return fg in (hwnd, root)


def is_covered(win: WeChatWindow) -> bool:
    """窗口是否被其他窗口遮挡（GDI 截屏前必须检查，否则会读到别人的画面）。"""
    if win32gui is None:
        return False
    try:
        left, top, right, bottom = win.client_bbox()
    except Exception:
        return True
    width, height = right - left, bottom - top
    if width < 80 or height < 80:
        return True
    points = [
        (left + width // 2, top + height // 2),
        (left + width // 4, top + height // 4),
        (left + 3 * width // 4, top + height // 4),
        (left + width // 4, top + 3 * height // 4),
        (left + 3 * width // 4, top + 3 * height // 4),
    ]
    for x, y in points:
        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return True
        root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        if root != win.hwnd:
            return True
    return False


def is_minimized(win: WeChatWindow) -> bool:
    try:
        return bool(win32gui.IsIconic(win.hwnd))
    except Exception:
        return False


def bring_to_front(hwnd: int) -> None:
    if win32gui is None:
        return
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def grab_client(win: WeChatWindow) -> np.ndarray:
    """GDI 兜底：截取客户区可见像素，返回 RGB ndarray（仅内存）。"""
    bbox = win.client_bbox()
    if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
        raise CaptureError("微信窗口尺寸异常（可能处于最小化/过渡动画）")
    image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
    return np.asarray(image)


class WgcCapturer:
    """按窗口句柄的 WGC 采集器。

    回调线程只保存最新一帧；调用方用 seq 判断是否有新画面，
    避免重复 OCR 同一帧。
    """

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._seq = 0
        self._closed = False
        self._control = None
        self._start()

    def _start(self) -> None:
        try:
            from windows_capture import WindowsCapture
        except Exception as exc:  # pragma: no cover - 依赖缺失
            raise CaptureError(f"未安装 windows-capture：{exc}") from exc

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self.hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, _control) -> None:  # pragma: no cover - 回调
            buffer = frame.frame_buffer
            rgb = np.ascontiguousarray(buffer[:, :, 2::-1])  # BGRA → RGB
            with self._lock:
                self._latest = rgb
                self._seq += 1

        @capture.event
        def on_closed() -> None:  # pragma: no cover - 回调
            with self._lock:
                self._closed = True

        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:  # pragma: no cover - 启动失败
            raise CaptureError(f"WGC 启动失败：{exc}") from exc

    def grab(self) -> tuple[np.ndarray, int] | None:
        with self._lock:
            if self._latest is None:
                return None
            return self._latest, self._seq

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def stop(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()
            except Exception:
                pass


def create_capturer(win: WeChatWindow) -> tuple[object, str]:
    """返回 (capturer, mode)。mode ∈ {"wgc", "gdi"}。"""
    try:
        return WgcCapturer(win.hwnd), "wgc"
    except CaptureError:
        return None, "gdi"


def grab_window(win: WeChatWindow, timeout: float = 2.5) -> tuple[np.ndarray, str]:
    """一次性取一帧（自检/调试用）。返回 (RGB ndarray, mode)。"""
    try:
        capturer = WgcCapturer(win.hwnd)
    except CaptureError:
        capturer = None
    if capturer is not None:
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                got = capturer.grab()
                if got is not None:
                    return got[0], "wgc"
                time.sleep(0.05)
            raise CaptureError("WGC 等待画面超时（窗口可能被最小化或处于动画中）")
        finally:
            capturer.stop()
    if not is_foreground(win.hwnd):
        raise CaptureError(
            "微信窗口被遮挡：请把微信切到前台，或安装 windows-capture 以支持后台采集"
        )
    return grab_client(win), "gdi"

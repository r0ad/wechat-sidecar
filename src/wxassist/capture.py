"""微信窗口定位与截屏。

只截取自己屏幕上微信窗口的**客户区**，不做任何注入或内存读取。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import numpy as np
from PIL import ImageGrab

try:  # pragma: no cover - 仅在 Windows 上可用
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    win32gui = None  # type: ignore[assignment]
    win32process = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]

WECHAT_TITLES = {"微信", "WeChat", "Weixin"}
WECHAT_EXES = {"weixin.exe", "wechat.exe"}

DWMWA_EXTENDED_FRAME_BOUNDS = 9


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
    if win32process is None:
        return ""
    try:
        handle = win32process.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
    except Exception:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
    finally:
        win32process.CloseHandle(handle)
    return ""


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
        raise CaptureError("没有找到微信窗口，请先登录并打开微信主界面")
    normal = [w for w in candidates if not w.minimized]
    if not normal:
        raise CaptureError("微信窗口已最小化，Windows 不渲染最小化窗口，请先还原")
    # 只保留标题恰为“微信”的主窗口，避免把独立聊天窗口当成主窗口
    normal.sort(key=lambda w: _area(w), reverse=True)
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
    """截取客户区，返回 RGB ndarray（仅内存）。"""
    bbox = win.client_bbox()
    if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
        raise CaptureError("微信窗口尺寸异常（可能处于最小化/过渡动画）")
    image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
    return np.asarray(image)


def grab_screen_region(bbox: tuple[int, int, int, int]) -> np.ndarray:
    image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
    return np.asarray(image)

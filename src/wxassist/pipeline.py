"""采集主循环：截屏 → OCR → 解析 → 去重 → 触发建议。

跑在独立线程里；通过回调把结果交给 UI，线程内不碰 Qt。
采集优先使用 WGC（窗口被遮挡也能读，且只在画面变化时出帧）。
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .capture import (
    CaptureError,
    WeChatWindow,
    create_capturer,
    find_window,
    grab_client,
    is_foreground,
    is_minimized,
)
from .config import Config
from .engine import SuggestEngine
from .layout import BubbleParser, ChatRegion, compute_region
from .models import Analysis, Message
from .ocr import OcrEngine, OcrError
from .store import Store
from .tracker import SessionTracker

SIGNATURE_THRESHOLD = 2.0


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        engine: SuggestEngine,
        on_analysis,
        on_status,
        on_debug=None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.on_analysis = on_analysis
        self.on_status = on_status
        self.on_debug = on_debug or (lambda *_args, **_kwargs: None)

        self._parser = BubbleParser(cfg.capture)
        self._ocr = OcrEngine()
        self._tracker: SessionTracker | None = None
        self._last_signature: np.ndarray | None = None
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_window: WeChatWindow | None = None
        self._last_error = ""
        self._last_status: tuple[str, str] = ("", "")
        self._session = ""

        self._capturer = None
        self._capture_mode = ""
        self._capture_hwnd: int | None = None
        self._last_seq = -1

    # ---------- 控制 ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wxassist-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._close_capturer()

    def pause(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self._status("paused", "已暂停采集")
        else:
            self._paused.clear()
            self._status("ok", "监听中")
            self._last_signature = None
            self._last_seq = -1

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def analyze_now(self, session: str | None = None) -> None:
        """手动重新生成：用最近的上下文再分析一次。"""
        session = session or self._session
        if not session:
            self._status("wait", "还没有会话，先打开一个微信聊天")
            return
        history = self.store.recent_messages(session, self.cfg.privacy.max_context_messages)
        incoming = [m for m in history if not m.is_me][-1:]
        if not incoming:
            self._status("wait", "没有可分析的对方消息")
            return
        self._emit_analysis(session, history, incoming)

    # ---------- 主循环 ----------

    def _run(self) -> None:
        interval = max(0.25, self.cfg.capture.interval_ms / 1000.0)
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                if self._paused.is_set():
                    time.sleep(0.2)
                    continue
                self._tick()
            except CaptureError as exc:
                self._status("wait", str(exc))
                self._close_capturer()
                time.sleep(0.5)
            except OcrError as exc:
                self._status("error", str(exc))
            except Exception as exc:  # pragma: no cover - 保护线程
                self._status("error", f"采集异常：{exc}")
                time.sleep(0.3)
            elapsed = time.perf_counter() - started
            time.sleep(max(0.05, interval - elapsed))

    def _tick(self) -> None:
        win = find_window()
        self._last_window = win
        if is_minimized(win):
            raise CaptureError("微信窗口已最小化，请先还原窗口")

        frame = self._frame(win)
        if frame is None:
            self._status("ok", f"监听中 · {self._session or '等待画面'}")
            return
        image, mode = frame
        height, width = image.shape[:2]
        region = compute_region(width, height, win.dpi_scale(), self.cfg.capture)

        lines = self._ocr.recognize(
            image, bbox=(region.chat_left, 0, width, region.input_top)
        )
        result = self._parser.parse(lines, image, region)
        session = result.session or "当前会话"
        self._session = session
        self._capture_mode = mode

        if self._tracker is None:
            self._tracker = SessionTracker(session)
            new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
            self._persist(new_messages)  # 首帧只建立基线
            self._status("ok", f"监听中 · {session}（{_mode_text(mode)}）")
            self.on_debug(session, result, lines, "")
            return

        if session != self._tracker.session:
            self._tracker.switch_session(session, result.bubbles)
            self._status("ok", f"已切换会话 · {session}")
            self.on_debug(session, result, lines, "")
            return

        new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
        self._persist(new_messages)
        self.on_debug(session, result, lines, "")
        if not new_messages:
            self._status("ok", f"监听中 · {session}")
            return

        incoming = [m for m in new_messages if not m.is_me]
        if not incoming:
            self._status("ok", f"监听中 · {session}（刚发出一条）")
            return
        history = self.store.recent_messages(session, self.cfg.privacy.max_context_messages)
        self._status("thinking", f"正在分析 · {session}")
        self._emit_analysis(session, history, incoming)

    # ---------- 采集 ----------

    def _frame(self, win: WeChatWindow) -> tuple[np.ndarray, str] | None:
        if self._capturer is None or self._capture_hwnd != win.hwnd:
            self._close_capturer()
            capturer, mode = create_capturer(win)
            self._capturer = capturer
            self._capture_mode = mode
            self._capture_hwnd = win.hwnd
            self._last_seq = -1
            self._last_signature = None

        if self._capture_mode == "wgc" and self._capturer is not None:
            got = self._capturer.grab()
            if got is None:
                return None
            image, seq = got
            if seq == self._last_seq:
                return None
            self._last_seq = seq
            signature = _signature(image)
            if self._last_signature is not None and _similar(
                self._last_signature, signature, SIGNATURE_THRESHOLD
            ):
                return None
            self._last_signature = signature
            return image, "wgc"

        # GDI 兜底：只能读屏幕可见像素，因此要求微信在前台
        if not is_foreground(win.hwnd):
            raise CaptureError(
                "微信窗口被遮挡：请把微信切到前台，或安装 windows-capture 以支持后台采集"
            )
        image = grab_client(win)
        signature = _signature(image)
        if self._last_signature is not None and _similar(
            self._last_signature, signature, SIGNATURE_THRESHOLD
        ):
            return None
        self._last_signature = signature
        return image, "gdi"

    def _close_capturer(self) -> None:
        capturer, self._capturer = self._capturer, None
        if capturer is not None:
            try:
                capturer.stop()
            except Exception:
                pass
        self._capture_hwnd = None
        self._last_seq = -1

    # ---------- 工具 ----------

    def _emit_analysis(self, session: str, history: list[Message], incoming: list[Message]) -> None:
        analysis: Analysis = self.engine.analyze(session, history, incoming)
        if analysis.ok:
            self._status("ok", f"监听中 · {session}")
        elif analysis.error:
            self._status("error", analysis.error)
        self.on_analysis(analysis)

    def _persist(self, messages: list[Message]) -> None:
        for message in messages:
            try:
                self.store.add_message(message)
            except Exception:
                pass

    def _status(self, state: str, message: str) -> None:
        if (state, message) == self._last_status:
            return
        self._last_status = (state, message)
        self._last_error = message
        self.on_status(state, message)


def _mode_text(mode: str) -> str:
    return "WGC 后台采集" if mode == "wgc" else "屏幕采集"


def _signature(image: np.ndarray) -> np.ndarray:
    """降采样灰度签名，用于快速判断画面是否变化。"""
    small = image[::8, ::8]
    gray = small.mean(axis=2) if small.ndim == 3 else small
    return gray.astype(np.float32)


def _similar(a: np.ndarray, b: np.ndarray, threshold: float = SIGNATURE_THRESHOLD) -> bool:
    if a.shape != b.shape:
        return False
    return float(np.abs(a - b).mean()) < threshold

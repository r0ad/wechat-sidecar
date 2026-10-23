"""采集主循环：截屏 → OCR → 解析 → 去重 → 触发建议。

跑在独立线程里；通过回调把结果交给 UI，线程内不碰 Qt。
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .capture import CaptureError, WeChatWindow, find_window, grab_client, is_foreground
from .config import Config
from .engine import SuggestEngine
from .layout import BubbleParser
from .models import Analysis, Frame, Message
from .ocr import OcrEngine, OcrError
from .store import Store
from .tracker import SessionTracker


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
        self._session = ""

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

    def pause(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self._status("paused", "已暂停采集")
        else:
            self._paused.clear()
            self._status("ok", "监听中")
            self._last_signature = None

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
            except OcrError as exc:
                self._status("error", str(exc))
            except Exception as exc:  # pragma: no cover - 保护线程
                self._status("error", f"采集异常：{exc}")
            elapsed = time.perf_counter() - started
            time.sleep(max(0.05, interval - elapsed))

    def _tick(self) -> None:
        win = find_window()
        self._last_window = win
        image = grab_client(win)
        signature = _signature(image)
        if self._last_signature is not None and _similar(self._last_signature, signature):
            if self._tracker is None:
                self._status("ok", "监听中（画面无变化）")
            return
        self._last_signature = signature

        frame = Frame(image=image, rect=win.client_bbox())
        lines = self._ocr.recognize(frame.image, bbox=self._ocr_region(image))
        result = self._parser.parse(lines, image)
        session = result.session or "当前会话"
        self._session = session

        if self._tracker is None:
            self._tracker = SessionTracker(session)
            new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
            # 首帧只建立基线，不产生建议
            self._persist(new_messages)
            self._status("ok", f"监听中 · {session}")
            self.on_debug(session, result, lines, frame)
            return

        if session != self._tracker.session:
            self._tracker.switch_session(session, result.bubbles)
            self._status("ok", f"已切换会话 · {session}")
            self.on_debug(session, result, lines, frame)
            return

        new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
        self._persist(new_messages)
        self.on_debug(session, result, lines, frame)
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

    def _emit_analysis(self, session: str, history: list[Message], incoming: list[Message]) -> None:
        analysis: Analysis = self.engine.analyze(session, history, incoming)
        if analysis.ok:
            self._status("ok", f"监听中 · {session}")
        elif analysis.error:
            self._status("error", analysis.error)
        self.on_analysis(analysis)

    # ---------- 工具 ----------

    def _persist(self, messages: list[Message]) -> None:
        for message in messages:
            try:
                self.store.add_message(message)
            except Exception:
                pass

    def _ocr_region(self, image: np.ndarray) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        left = int(width * self.cfg.capture.chat_left_ratio)
        bottom = int(height * self.cfg.capture.input_top_ratio)
        return left, 0, width, max(bottom, 1)

    def _status(self, state: str, message: str) -> None:
        if message != self._last_error or state == "error":
            self._last_error = message
        self.on_status(state, message)


def _signature(image: np.ndarray) -> np.ndarray:
    """降采样灰度签名，用于快速判断画面是否变化。"""
    small = image[::8, ::8]
    gray = small.mean(axis=2) if small.ndim == 3 else small
    return gray.astype(np.float32)


def _similar(a: np.ndarray, b: np.ndarray, threshold: float = 1.5) -> bool:
    if a.shape != b.shape:
        return False
    return float(np.abs(a - b).mean()) < threshold

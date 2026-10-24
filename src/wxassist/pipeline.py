"""采集主循环：截屏 → OCR → 解析 → 去重 → 触发建议。

跑在独立线程里；通过回调把结果交给 UI，线程内不碰 Qt。
采集优先使用 WGC（窗口被遮挡也能读，且只在画面变化时出帧）。
"""

from __future__ import annotations

import difflib
import threading
import time

import numpy as np

from .capture import (
    CaptureError,
    WeChatWindow,
    bring_to_front,
    create_capturer,
    find_window,
    grab_client,
    is_covered,
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
        on_session=None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.on_analysis = on_analysis
        self.on_status = on_status
        self.on_debug = on_debug or (lambda *_args, **_kwargs: None)
        self.on_session = on_session or (lambda _session: None)

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
        self._io_lock = threading.Lock()

        self._baseline_ts = 0.0
        self._pending: list[Message] = []
        self._pending_at = 0.0
        try:
            self._known_sessions: list[str] = store.sessions()
        except Exception:
            self._known_sessions = []

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

    def analyze_now(
        self,
        session: str | None = None,
        avoid: list[str] | None = None,
        proactive: bool = True,
    ) -> None:
        """手动生成/换一批：没有新消息时也可以基于当前对话主动生成。"""
        session = session or self._session
        if not session:
            self._status("wait", "还没有会话，先打开一个微信聊天")
            return
        history = self.store.recent_messages(session, self.cfg.privacy.max_context_messages)
        if not history:
            self._status("wait", "还没有读到上下文，先打开聊天停留几秒")
            return
        incoming = [m for m in history if not m.is_me][-1:]
        self._status("thinking", f"正在生成候选 · {session}")
        self._emit_analysis(session, history, incoming, avoid=avoid, allow_proactive=proactive)

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
                self._flush_pending()
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
        if not self._io_lock.acquire(blocking=False):
            return
        try:
            self._tick_locked()
        finally:
            self._io_lock.release()

    def _tick_locked(self) -> None:
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

        if self.cfg.capture.require_send_button and not self._is_chat_view(image, region):
            self._tracker = None
            self._pending.clear()
            self._status(
                "wait",
                "当前微信不是聊天页面（可能是公众号/文章/小程序），已暂停读取；回到聊天页自动恢复",
            )
            return

        lines = self._ocr.recognize(
            image, bbox=(region.chat_left, 0, width, region.input_top)
        )
        result = self._parser.parse(lines, image, region)
        session = self._resolve_session(result.session)
        self._session = session
        self._capture_mode = mode

        if self._tracker is None:
            self._tracker = SessionTracker(session)
            new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
            self._persist(new_messages)  # 首帧只建立基线
            self._reset_confirm(session, result.bubbles)
            self._baseline_ts = time.time()
            self.on_session(session)
            self._status("ok", f"监听中 · {session}（{_mode_text(mode)}）")
            self.on_debug(session, result, lines, "")
            return

        if session != self._tracker.session:
            self._tracker.switch_session(session, result.bubbles)
            self._persist_bubbles(session, result.bubbles)  # 新会话的可见历史也要进上下文
            self._reset_confirm(session, result.bubbles)
            self._pending.clear()
            self.on_session(session)
            stored = self.store.count_messages(session)
            self._status("ok", f"已切换会话 · {session}（已读 {stored} 条上下文）")
            self.on_debug(session, result, lines, "")
            return

        self._confirm_messages(session, result.bubbles)
        new_messages = self._tracker.ingest(result.bubbles, ts=time.time())
        self.on_debug(session, result, lines, "")
        if not new_messages:
            self._status("ok", f"监听中 · {session}")
            return

        incoming = [m for m in new_messages if not m.is_me]
        if not incoming:
            self._status("ok", f"监听中 · {session}（刚发出一条）")
            return
        self._enqueue_incoming(session, incoming)

    def _is_chat_view(self, image, region) -> bool:
        """聊天页底部输入区一定有「发送」按钮；文章/小程序/朋友圈没有。"""
        height, width = image.shape[:2]
        if region.input_top >= height - 4:
            return False
        try:
            footer_lines = self._ocr.recognize(
                image, bbox=(region.chat_left, region.input_top, width, height)
            )
        except OcrError:
            return False
        return footer_has_send(footer_lines)

    def _resolve_session(self, raw: str) -> str:
        """会话名稳定化：OCR 抖动/图标误识不能把同一个会话拆成多个。"""
        if not raw and self._tracker is not None:
            return self._tracker.session
        candidates: list[str] = []
        if self._tracker is not None:
            candidates.append(self._tracker.session)
        candidates.extend(self._known_sessions)
        key = _name_key(raw)
        for name in candidates:
            known = _name_key(name)
            if not known or not key:
                continue
            if key == known or key in known or known in key:
                return name
            if difflib.SequenceMatcher(None, key, known).ratio() >= 0.8:
                return name
        if raw and raw not in self._known_sessions:
            self._known_sessions.append(raw)
            if len(self._known_sessions) > 300:
                self._known_sessions = self._known_sessions[-300:]
        return raw or "当前会话"

    def load_more_context(self, session: str, pages: int | None = None) -> int:
        """向上翻阅聊天记录，把更早的可见消息补进本地上下文。

        需要微信在前台；会临时移动鼠标滚轮，结束后滚回底部并恢复光标位置。
        """
        pages = pages or max(1, self.cfg.capture.load_more_pages)
        if not self._io_lock.acquire(timeout=5):
            raise CaptureError("采集正忙，请稍后再试")
        try:
            win = find_window()
            if is_minimized(win):
                raise CaptureError("微信窗口已最小化，请先还原")
            if not is_foreground(win.hwnd):
                bring_to_front(win.hwnd)
                time.sleep(0.25)
            if not is_foreground(win.hwnd):
                raise CaptureError("请先把微信切到前台，再点「读取更多」")

            if self._capturer is None or self._capture_hwnd != win.hwnd:
                self._close_capturer()
                capturer, mode = create_capturer(win)
                self._capturer = capturer
                self._capture_mode = mode
                self._capture_hwnd = win.hwnd
                self._last_seq = -1

            left, top, right, bottom = win.client_bbox()
            chat_left = int((right - left) * self.cfg.capture.chat_left_ratio)
            cx = left + chat_left + (right - left - chat_left) // 2
            cy = top + (bottom - top) // 2
            try:
                import win32api
                import win32con

                old_pos = win32api.GetCursorPos()
                win32api.SetCursorPos((cx, cy))
                time.sleep(0.05)
            except Exception as exc:
                raise CaptureError(f"无法控制鼠标滚轮：{exc}") from exc

            collected: list = []
            try:
                for _page in range(pages):
                    image = self._next_frame(win, timeout=1.5)
                    if image is None:
                        break
                    height, width = image.shape[:2]
                    region = compute_region(width, height, win.dpi_scale(), self.cfg.capture)
                    lines = self._ocr.recognize(
                        image, bbox=(region.chat_left, 0, width, region.input_top)
                    )
                    result = self._parser.parse(lines, image, region)
                    collected = list(result.bubbles) + collected
                    if self._resolve_session(result.session) != session:
                        break
                    self._wheel(120, 3)
                    time.sleep(0.4)
            finally:
                self._wheel(-120, 80)  # 滚回底部
                try:
                    win32api.SetCursorPos(old_pos)
                except Exception:
                    pass

            messages = []
            seen: set[str] = set()
            now = time.time()
            for bubble in collected:
                if bubble.digest() in seen:
                    continue
                seen.add(bubble.digest())
                messages.append(
                    Message(session=session, text=bubble.text, is_me=bubble.is_me, ts=now)
                )
            if not messages:
                return 0
            min_seq = self.store.min_seq(session)
            base = (min_seq if min_seq is not None else time.time()) - len(messages) * 0.002
            return self.store.add_messages_dedup(messages, seq_base=base)
        finally:
            self._io_lock.release()

    @staticmethod
    def _wheel(delta: int, times: int) -> None:
        try:
            import win32api
            import win32con

            for _ in range(times):
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
                time.sleep(0.03)
        except Exception:
            pass

    def _next_frame(self, win: WeChatWindow, timeout: float = 1.5):
        if self._capturer is None:
            return None
        start_seq = self._last_seq
        deadline = time.time() + timeout
        client_w, client_h = win.client_size()
        while time.time() < deadline:
            got = self._capturer.grab()
            if got is not None and got[1] != start_seq:
                image, seq = got
                if abs(image.shape[1] - client_w) <= 8 and abs(image.shape[0] - client_h) <= 8:
                    self._last_seq = seq
                    return image
            time.sleep(0.05)
        return None

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
            client_w, client_h = win.client_size()
            if abs(image.shape[1] - client_w) > 8 or abs(image.shape[0] - client_h) > 8:
                self._close_capturer()
                raise CaptureError("采集画面尺寸异常（窗口可能刚还原或缩放），正在重新连接…")
            signature = _signature(image)
            if self._last_signature is not None and _similar(
                self._last_signature, signature, SIGNATURE_THRESHOLD
            ):
                return None
            self._last_signature = signature
            return image, "wgc"

        # GDI 兜底：只能读屏幕可见像素，所以必须确认微信真的在最上层
        if not is_foreground(win.hwnd) or is_covered(win):
            raise CaptureError(
                "微信窗口被其他窗口遮挡，已暂停读取（切回微信前台会自动继续）"
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

    def _enqueue_incoming(self, session: str, incoming: list[Message]) -> None:
        # 启动后的前几秒画面可能还在渲染，先当基线，避免一上来就误报
        if time.time() - self._baseline_ts < self.cfg.capture.startup_warmup_sec:
            self._status("ok", f"监听中 · {session}（画面稳定中）")
            return
        self._pending = (self._pending + incoming)[-5:]
        self._pending_at = time.time()
        count = len(self._pending)
        suffix = f"{count} 条新消息" if count > 1 else "新消息"
        self._status("thinking", f"收到{suffix} · 正在生成候选…")

    def _flush_pending(self) -> None:
        """防抖：连续多条消息合并成一次分析；不在截屏线程里阻塞等待。"""
        if not self._pending:
            return
        if time.time() - self._pending_at < self.cfg.capture.analysis_debounce_sec:
            return
        pending, self._pending = self._pending, []
        session = self._session
        if not session:
            return
        history = self.store.recent_messages(session, self.cfg.privacy.max_context_messages)
        # 新消息可能还没通过「两帧确认」入库，这里直接补到上下文末尾
        existing = {message.digest() for message in history}
        for message in pending:
            if message.digest() not in existing:
                history.append(message)
        self._emit_analysis(session, history, pending)

    def _emit_analysis(
        self,
        session: str,
        history: list[Message],
        incoming: list[Message],
        avoid: list[str] | None = None,
        allow_proactive: bool = False,
    ) -> None:
        analysis: Analysis = self.engine.analyze(
            session, history, incoming, avoid=avoid, allow_proactive=allow_proactive
        )
        if analysis.ok:
            self._status("ok", f"监听中 · {session}")
        elif analysis.error:
            self._status("error", analysis.error)
        self.on_analysis(analysis)

    def _reset_confirm(self, session: str, bubbles) -> None:
        self._confirm = {bubble.digest(): (session, bubble) for bubble in bubbles}

    def _confirm_messages(self, session: str, bubbles) -> int:
        """只把「连续两帧都出现」的消息写进上下文，过滤滚动/重绘产生的碎片。"""
        current = {bubble.digest(): bubble for bubble in bubbles}
        ready_keys = {
            digest
            for digest in current
            if self._confirm.get(digest) is not None and self._confirm[digest][0] == session
        }
        to_store = [
            Message(session=session, text=current[d].text, is_me=current[d].is_me, ts=time.time())
            for d in ready_keys
        ]
        self._confirm = {
            digest: (session, bubble)
            for digest, bubble in current.items()
            if digest not in ready_keys
        }
        if not to_store:
            return 0
        try:
            return self.store.add_messages_dedup(to_store)
        except Exception:
            return 0

    def _persist(self, messages: list[Message]) -> None:
        try:
            self.store.add_messages_dedup(messages)
        except Exception:
            pass

    def _persist_bubbles(self, session: str, bubbles) -> int:
        now = time.time()
        messages = [
            Message(session=session, text=bubble.text, is_me=bubble.is_me, ts=now + idx * 0.001)
            for idx, bubble in enumerate(bubbles)
        ]
        try:
            return self.store.add_messages_dedup(messages)
        except Exception:
            return 0

    def _status(self, state: str, message: str) -> None:
        if (state, message) == self._last_status:
            return
        self._last_status = (state, message)
        self._last_error = message
        self.on_status(state, message)


SEND_TEXTS = ("发送", "发送(", "Send", "send")


def footer_has_send(lines) -> bool:
    for line in lines:
        text = (line.text or "").strip()
        if any(marker in text for marker in SEND_TEXTS):
            return True
    return False


def _name_key(name: str) -> str:
    return "".join(ch for ch in (name or "") if ch.isalnum()).lower()


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

"""应用编排：把采集线程、建议引擎和悬浮窗接起来。"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from . import capture as capture_mod
from .config import Config
from .engine import SuggestEngine
from .filler import FillError, copy_to_clipboard, fill as fill_text
from .hotkey import HotkeyManager
from .llm import LLMClient
from .models import Analysis, Message
from .overlay import Overlay, create_app
from .pipeline import Pipeline
from .store import Store


class Bridge(QObject):
    analysis_ready = Signal(object)
    status_changed = Signal(str, str)
    debug_ready = Signal(str, object, object, str)
    toggle_overlay = Signal()


class AppController:
    def __init__(self, cfg: Config) -> None:
        capture_mod.enable_dpi_awareness()
        self.cfg = cfg
        self.app: QApplication = create_app()
        self.overlay = Overlay()
        self.bridge = Bridge()
        self.store = Store(cfg.db_path())
        self.engine = SuggestEngine(LLMClient(cfg.llm), cfg, self.store)
        self.hotkeys = HotkeyManager()
        self.pipeline: Pipeline | None = None
        self._current_session = ""
        self._placed = False
        self._last_window = None

        self.bridge.analysis_ready.connect(self.overlay.show_analysis)
        self.bridge.status_changed.connect(self._on_status)
        self.bridge.debug_ready.connect(
            lambda session, bubbles, lines, extra: self.overlay.show_debug(
                session, bubbles, lines, extra
            )
        )
        self.bridge.toggle_overlay.connect(self._toggle_overlay)
        self.overlay.fill_requested.connect(self._on_fill)
        self.overlay.copy_requested.connect(self._on_copy)
        self.overlay.regenerate_requested.connect(self._on_regenerate)
        self.overlay.pause_toggled.connect(self._on_pause)

    # ---------- 生命周期 ----------

    def run(self) -> int:
        self.overlay.show()
        self.overlay.set_status("ok", "正在连接微信窗口…")
        self.pipeline = Pipeline(
            cfg=self.cfg,
            store=self.store,
            engine=self.engine,
            on_analysis=self.bridge.analysis_ready.emit,
            on_status=self.bridge.status_changed.emit,
            on_debug=lambda session, result, lines, frame: self.bridge.debug_ready.emit(
                session, result.bubbles, lines, ""
            ),
        )
        self.pipeline.start()
        if self.cfg.ui.hotkey:
            self.hotkeys.register(self.cfg.ui.hotkey, self.bridge.toggle_overlay.emit)
        self.app.aboutToQuit.connect(self.shutdown)
        return self.app.exec()

    def shutdown(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()
        self.hotkeys.unregister_all()
        try:
            self.store.close()
        except Exception:
            pass

    # ---------- 回调 ----------

    def _on_status(self, state: str, message: str) -> None:
        self.overlay.set_status(state, message)
        if state != "paused" and not self._placed:
            self._try_place_overlay()

    def _on_fill(self, text: str) -> None:
        try:
            win = self._find_window()
            action = fill_text(
                text,
                win,
                mode=self.cfg.filler.mode,
                chat_left_ratio=self.cfg.capture.chat_left_ratio,
            )
            if action == "pasted":
                self.overlay.set_status("ok", "已填入输入框（未发送，请自己按回车）")
            else:
                self.overlay.set_status("ok", "已复制到剪贴板，切到微信 Ctrl+V 即可")
        except FillError as exc:
            self.overlay.set_status("error", str(exc))
        except Exception as exc:  # pragma: no cover - 保护 UI
            self.overlay.set_status("error", f"填入失败：{exc}")

    def _on_copy(self, text: str) -> None:
        try:
            copy_to_clipboard(text)
            self.overlay.set_status("ok", "已复制到剪贴板")
        except Exception as exc:
            self.overlay.set_status("error", f"复制失败：{exc}")

    def _on_regenerate(self) -> None:
        if self.pipeline is not None:
            self.pipeline.analyze_now(self._current_session)

    def _on_pause(self, paused: bool) -> None:
        if self.pipeline is not None:
            self.pipeline.pause(paused)

    # ---------- 杂项 ----------

    def _find_window(self):
        if self._last_window is not None:
            try:
                self._last_window.client_bbox()
                return self._last_window
            except Exception:
                self._last_window = None
        self._last_window = capture_mod.find_window()
        return self._last_window

    def _try_place_overlay(self) -> None:
        try:
            win = capture_mod.find_window()
        except Exception:
            return
        left, top, right, bottom = win.client_bbox()
        screen = self.overlay.screen() or self.app.primaryScreen()
        geo = screen.availableGeometry()
        width, height = self.overlay.width(), self.overlay.height()
        x = right + 8
        if x + width > geo.right():
            x = left - width - 8
        if x < geo.left():
            x = geo.right() - width - 8
        y = max(geo.top() + 8, min(top, geo.bottom() - height - 8))
        self.overlay.move(x, y)
        self._placed = True

    def _toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()


class DemoController(AppController):
    """无微信窗口时的演示：合成会话 + Mock 模型，验证整条链路。"""

    DEMO_MESSAGES = [
        Message(session="小林", text="在吗", is_me=True, ts=1),
        Message(session="小林", text="在的，刚下班", is_me=False, ts=2),
        Message(session="小林", text="明天下午的评审你准备得怎么样了？", is_me=False, ts=3),
        Message(session="小林", text="我有点慌，怕讲不清楚", is_me=False, ts=4),
    ]

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._history: list[Message] = []
        self._step = 0

    def run(self) -> int:
        self.cfg.llm.provider = "mock"
        self.engine = SuggestEngine(LLMClient(self.cfg.llm), self.cfg, self.store)
        self.overlay.show()
        self.overlay.set_status("thinking", "演示模式：正在生成示例…")
        QTimer.singleShot(500, self._advance)
        self.app.aboutToQuit.connect(self.shutdown)
        return self.app.exec()

    def _advance(self) -> None:
        if self._step >= len(self.DEMO_MESSAGES):
            return
        message = self.DEMO_MESSAGES[self._step]
        self._step += 1
        self._history.append(message)
        self.store.add_message(message)
        self.overlay.set_session(message.session)
        if message.is_me:
            QTimer.singleShot(700, self._advance)
            return
        incoming = [message]
        history = list(self._history)
        self.overlay.set_status("thinking", "演示模式：正在分析…")

        def worker() -> None:
            analysis = self.engine.analyze(message.session, history, incoming)
            self.bridge.analysis_ready.emit(analysis)
            self.bridge.status_changed.emit(
                "ok", "演示模式：这是模拟数据，配置 API Key 后即为真实生成"
            )

        threading.Thread(target=worker, daemon=True).start()
        QTimer.singleShot(6500, self._advance)


def run_screenshot_mode(cfg: Config, path: str) -> int:
    """把悬浮窗按演示数据渲染后截图，供 README 使用。"""
    controller = DemoController(cfg)
    controller.overlay.set_status("ok", "演示模式：界面预览")
    controller.overlay.show()
    controller.overlay.set_session("小林")
    history = DemoController.DEMO_MESSAGES
    analysis: Analysis = controller.engine.analyze(
        "小林", history, [history[-1]]
    )
    controller.overlay.show_analysis(analysis)

    def save_and_quit() -> None:
        controller.overlay.grab().save(path)
        controller.app.quit()

    QTimer.singleShot(600, save_and_quit)
    return controller.app.exec()

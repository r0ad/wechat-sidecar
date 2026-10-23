"""应用编排：把采集线程、建议引擎、悬浮窗和模型设置接起来。"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import capture as capture_mod
from .config import Config
from .engine import SuggestEngine
from .filler import FillError, copy_to_clipboard, fill as fill_text
from .hotkey import HotkeyManager
from .llm import LLMClient
from .models import Analysis, Message, SessionSummary
from .overlay import Overlay, create_app
from .pipeline import Pipeline
from .secrets_store import save_api_key
from .settings_dialog import SettingsDialog
from .store import Store
from .summary_dialog import SummaryDialog

log = logging.getLogger("wxassist")


def setup_logging(cfg: Config) -> None:
    """把关键事件和未捕获异常写到本地文件（不记录聊天内容）。"""
    try:
        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(Path(cfg.data_dir) / "wxassist.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger("wxassist")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except Exception:
        pass

    sys.excepthook = lambda exc_type, exc_value, exc_tb: log.exception(
        "未捕获异常", exc_info=(exc_type, exc_value, exc_tb)
    )

    def _qt_handler(mode, context, message):  # pragma: no cover - Qt 回调
        log.warning("Qt: %s", message)

    try:
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass


class Bridge(QObject):
    analysis_ready = Signal(object)
    status_changed = Signal(str, str)
    debug_ready = Signal(str, object, object, str)
    toggle_overlay = Signal()
    summary_ready = Signal(object)
    style_ready = Signal(str, str)
    refined = Signal(str, str, str)
    session_changed = Signal(str)
    context_updated = Signal(int, str)


def _force_show(dialog) -> None:
    """显示对话框并强制它成为独立可见窗口（不随 Tool 悬浮窗被隐藏）。"""
    dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
    dialog.show()
    dialog.raise_()
    try:
        import win32con
        import win32gui

        hwnd = int(dialog.winId())
        win32gui.SetWindowLong(hwnd, win32con.GWL_HWNDPARENT, 0)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


class AppController:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.app: QApplication = create_app()
        # 悬浮窗是 Tool 窗口，意外关闭不应直接退出整个程序
        self.app.setQuitOnLastWindowClosed(False)
        self.overlay = Overlay()
        self.bridge = Bridge()
        self.store = Store(cfg.db_path())
        self.llm = LLMClient(cfg.llm, cfg.data_dir)
        self.engine = SuggestEngine(self.llm, cfg, self.store)
        self.hotkeys = HotkeyManager()
        self.pipeline: Pipeline | None = None
        self._current_session = ""
        self._placed = False
        self._last_window = None
        self._last_analysis: Analysis | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._summary_dialog: SummaryDialog | None = None
        self._auto_settings_opened = False
        self._hidden_by_auto = False
        self._manual_until = 0.0
        self._watch_timer: QTimer | None = None

        self.bridge.analysis_ready.connect(self._on_analysis)
        self.bridge.status_changed.connect(self._on_status)
        self.bridge.debug_ready.connect(
            lambda session, bubbles, lines, extra: self.overlay.show_debug(
                session, bubbles, lines, extra
            )
        )
        self.bridge.toggle_overlay.connect(self._toggle_overlay)
        self.bridge.summary_ready.connect(self._on_summary_ready)
        self.bridge.style_ready.connect(self._on_style_ready)
        self.bridge.refined.connect(self._on_refined)
        self.bridge.session_changed.connect(self.overlay.set_session)
        self.overlay.fill_requested.connect(self._on_fill)
        self.overlay.copy_requested.connect(self._on_copy)
        self.overlay.regenerate_requested.connect(self._on_regenerate)
        self.overlay.pause_toggled.connect(self._on_pause)
        self.overlay.quit_requested.connect(self._on_quit_clicked)
        self.overlay.settings_requested.connect(self._open_settings)
        self.overlay.summary_requested.connect(self._on_summary)
        self.overlay.learn_style_requested.connect(self._on_learn_style)
        self.overlay.refine_requested.connect(self._on_refine)
        self.overlay.context_requested.connect(self._on_context)
        self.overlay.load_more_requested.connect(self._on_load_more)
        self.overlay.pin_toggled.connect(self._on_pin_toggled)
        self.bridge.context_updated.connect(self._on_context_updated)

    # ---------- 生命周期 ----------

    def run(self) -> int:
        setup_logging(self.cfg)
        log.info(
            "启动 wxassist：provider=%s model=%s key=%s",
            self.cfg.llm.provider,
            self.cfg.llm.model,
            "已配置" if self.llm.has_key() else "未配置",
        )
        self.overlay.show()
        self.overlay.set_model_ready(self.llm.has_key())
        self.overlay.set_pinned(not self.cfg.ui.auto_hide)
        self.overlay.set_status("ok", "正在连接微信窗口…")
        if not self.llm.has_key():
            QTimer.singleShot(900, self._open_settings)
        self.pipeline = Pipeline(
            cfg=self.cfg,
            store=self.store,
            engine=self.engine,
            on_analysis=self.bridge.analysis_ready.emit,
            on_status=self.bridge.status_changed.emit,
            on_debug=lambda session, result, lines, frame: self.bridge.debug_ready.emit(
                session, result.bubbles, lines, ""
            ),
            on_session=lambda session: self.bridge.session_changed.emit(session),
        )
        self.pipeline.start()
        if self.cfg.ui.hotkey:
            ok = self.hotkeys.register(self.cfg.ui.hotkey, self.bridge.toggle_overlay.emit)
            log.info("全局热键 %s 注册%s", self.cfg.ui.hotkey, "成功" if ok else "失败（可忽略）")
        self.app.aboutToQuit.connect(self.shutdown)
        self._watch_timer = QTimer(self.app)
        self._watch_timer.setInterval(1000)
        self._watch_timer.timeout.connect(self._watch_foreground)
        self._watch_timer.start()
        code = self.app.exec()
        log.info("事件循环结束，退出码=%s", code)
        return code

    def shutdown(self) -> None:
        log.info("开始退出：停止采集与热键")
        if self.pipeline is not None:
            self.pipeline.stop()
        self.hotkeys.unregister_all()
        try:
            self.store.close()
        except Exception:
            pass
        log.info("退出完成")

    # ---------- 采集回调 ----------

    def _on_analysis(self, analysis: Analysis) -> None:
        self._last_analysis = analysis
        if analysis.ok:
            self.overlay.set_model_ready(True)
        elif analysis.error and ("配置模型" in analysis.error or "API Key" in analysis.error):
            self.overlay.set_model_ready(False, analysis.error)
            if not self._auto_settings_opened:
                self._auto_settings_opened = True
                QTimer.singleShot(200, self._open_settings)
        self.overlay.show_analysis(analysis)
        self.overlay.set_context_count(self.store.count_messages(analysis.session))
        if self.overlay.context_panel.isVisible():
            self._on_context()

    def _on_load_more(self) -> None:
        self._touch()
        session = self._session()
        if not session:
            self.overlay.set_status("wait", "还没有会话，先打开一个微信聊天")
            return
        if self.pipeline is None:
            return
        self.overlay.set_busy("正在向上翻阅聊天记录，读取更早的上下文…")

        def worker() -> None:
            try:
                added = self.pipeline.load_more_context(session)
            except Exception as exc:
                self.bridge.status_changed.emit("error", f"读取更多失败：{exc}")
                return
            self.bridge.context_updated.emit(added, session)

        threading.Thread(target=worker, daemon=True).start()

    def _on_context_updated(self, added: int, session: str) -> None:
        total = self.store.count_messages(session)
        if added:
            self.overlay.set_status("ok", f"已补充 {added} 条历史 · 当前上下文 {total} 条")
        else:
            self.overlay.set_status("ok", f"没有更多历史可读 · 当前上下文 {total} 条")
        self.overlay.set_context_count(total)
        if self.overlay.context_panel.isVisible():
            self._on_context()

    def _on_context(self) -> None:
        self._touch()
        session = self._session()
        if not session:
            self.overlay.show_context([], 0)
            return
        messages = self.store.recent_messages(session, 30)
        self.overlay.show_context(messages, self.store.count_messages(session))

    def _on_status(self, state: str, message: str) -> None:
        self.overlay.set_status(state, message)
        if state != "paused" and not self._placed:
            self._try_place_overlay()

    def _watch_foreground(self) -> None:
        """微信不在前台时自动收起悬浮窗，避免挡住别的窗口。"""
        if not self.cfg.ui.auto_hide:
            return
        if time.time() < self._manual_until:
            return
        # 正在和悬浮窗交互（打开菜单/点按钮）时不要收起
        if QApplication.activePopupWidget() is not None:
            return
        if self.overlay.isActiveWindow():
            return
        dialogs_open = any(
            dialog is not None and dialog.isVisible()
            for dialog in (self._settings_dialog, self._summary_dialog)
        )
        if dialogs_open:
            return
        try:
            win = capture_mod.find_window()
            focused = capture_mod.is_foreground(win.hwnd)
        except Exception:
            return
        if focused and self._hidden_by_auto:
            self.overlay.show()
            self._hidden_by_auto = False
            self._try_place_overlay()
        elif not focused and self.overlay.isVisible():
            self.overlay.hide()
            self._hidden_by_auto = True

    # ---------- 用户操作 ----------

    def _touch(self) -> None:
        """用户和悬浮窗交互后，5 分钟内不自动收起。"""
        self._manual_until = time.time() + 300

    def _on_fill(self, text: str) -> None:
        self._touch()
        try:
            win = self._find_window()
            action = fill_text(
                text,
                win,
                mode=self.cfg.filler.mode,
                chat_left_px=self.cfg.capture.chat_left_px,
                dpi_scale=win.dpi_scale(),
                chat_left_ratio=self.cfg.capture.chat_left_ratio,
                input_px=self.cfg.capture.input_px,
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
        self._touch()
        try:
            copy_to_clipboard(text)
            self.overlay.set_status("ok", "已复制到剪贴板")
        except Exception as exc:
            self.overlay.set_status("error", f"复制失败：{exc}")

    def _on_regenerate(self) -> None:
        self._touch()
        if self.pipeline is None:
            return
        avoid = [c.text for c in self._last_analysis.candidates] if self._last_analysis else []
        self.overlay.set_busy("正在换一批…")
        threading.Thread(
            target=self.pipeline.analyze_now,
            args=(self._current_session, avoid),
            daemon=True,
        ).start()

    def _on_pin_toggled(self, pinned: bool) -> None:
        self.cfg.ui.auto_hide = not pinned
        self.overlay.set_pinned(pinned)
        self.overlay.set_status(
            "ok", "已常驻显示（不会自动隐藏）" if pinned else "已恢复自动隐藏（离开微信时收起）"
        )
        try:
            self.cfg.save(Path(self.cfg.data_dir) / "config.json")
        except Exception:
            pass

    def _on_pause(self, paused: bool) -> None:
        if self.pipeline is not None:
            self.pipeline.pause(paused)

    # ---------- AI 功能 ----------

    def _on_summary(self) -> None:
        session = self._session()
        if not session:
            self.overlay.set_status("wait", "还没有会话，先打开一个微信聊天")
            return
        history = self.store.recent_messages(session, 80)
        if not history:
            self.overlay.set_status("wait", "这个会话还没有可总结的消息")
            return
        self.overlay.set_busy("正在总结会话…")

        def worker() -> None:
            summary = self.engine.summarize(session, history)
            self.bridge.summary_ready.emit(summary)

        threading.Thread(target=worker, daemon=True).start()

    def _on_summary_ready(self, summary: SessionSummary) -> None:
        if summary.error:
            self.overlay.set_status("error", summary.error)
            return
        self.overlay.set_status("ok", f"总结完成 · {summary.latency_ms}ms")
        self._summary_dialog = SummaryDialog(summary)
        _force_show(self._summary_dialog)

    def _on_learn_style(self) -> None:
        session = self._session()
        if not session:
            self.overlay.set_status("wait", "还没有会话")
            return
        self.overlay.set_busy("正在学习我的说话风格…")

        def worker() -> None:
            profile, error = self.engine.learn_style(session)
            self.bridge.style_ready.emit(profile, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_style_ready(self, profile: str, error: str) -> None:
        if error:
            self.overlay.set_status("error", error)
            return
        self.overlay.set_status("ok", "已学会你的说话风格，后续候选会模仿它")
        QMessageBox.information(None, "说话风格画像", profile)

    def _on_refine(self, original: str, style: str) -> None:
        self._touch()
        session = self._session()
        if not session:
            self.overlay.set_status("wait", "还没有会话")
            return
        history = self.store.recent_messages(session, 30)
        self.overlay.set_busy("正在改写…")
        instruction = style

        def worker() -> None:
            text, error = self.engine.refine(session, history, original, instruction)
            if error:
                self.bridge.status_changed.emit("error", error)
            else:
                self.bridge.refined.emit(original, text, instruction)

        threading.Thread(target=worker, daemon=True).start()

    def _on_refined(self, original: str, text: str, style: str) -> None:
        self.overlay.update_candidate_text(original, text)

    # ---------- 模型设置 ----------

    def _open_settings(self) -> None:
        self._touch()
        log.info("打开模型设置")
        self._auto_settings_opened = True
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            return
        dialog = SettingsDialog(self.cfg, self.llm, self.cfg.data_dir)
        dialog.saved.connect(self._apply_settings)
        self._settings_dialog = dialog
        _force_show(dialog)

    def _apply_settings(self, data: dict) -> None:
        provider = data.get("provider") or "custom"
        self.cfg.llm.provider = provider
        self.cfg.llm.base_url = data.get("base_url", "")
        self.cfg.llm.model = data.get("model", "")
        api_key = data.get("api_key", "")
        if api_key:
            try:
                save_api_key(self.cfg.data_dir, provider, api_key)
            except Exception as exc:
                self.overlay.set_status("error", f"保存 Key 失败：{exc}")
                return
        # 不把 Key 写进配置文件
        self.cfg.llm.api_key = ""
        try:
            self.cfg.save(Path(self.cfg.data_dir) / "config.json")
        except Exception as exc:
            log.warning("保存配置失败：%s", exc)
        self.llm.update_config(self.cfg.llm)
        ready = self.llm.has_key()
        self.overlay.set_model_ready(ready)
        if ready:
            self.overlay.set_status("ok", f"模型已更新：{self.cfg.llm.model}")
        else:
            self.overlay.set_status("error", "还没有填写 API Key")
        log.info("模型配置更新：provider=%s model=%s", provider, self.cfg.llm.model)

    # ---------- 杂项 ----------

    def _session(self) -> str:
        if self.pipeline is not None and getattr(self.pipeline, "_session", ""):
            return self.pipeline._session
        if self._last_analysis is not None:
            return self._last_analysis.session
        return self._current_session

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
        self._manual_until = time.time() + 300
        if self.overlay.isVisible():
            self.overlay.hide()
            self._hidden_by_auto = False
        else:
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()
            self._hidden_by_auto = False

    def _on_quit_clicked(self) -> None:
        log.warning("收到「退出」按钮点击")
        self.app.quit()

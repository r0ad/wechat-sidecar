"""置顶悬浮窗（PySide6）：候选回复卡片 + 意图摘要 + 调试面板。

只负责展示与发出用户操作信号，不直接调用截屏/模型。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .models import Analysis, Candidate

ACCENT = "#07C160"
DANGER = "#E5484D"
WARN = "#F5A524"

STYLESHEET = f"""
QFrame#card {{
    background-color: rgba(24, 28, 36, 240);
    border: 1px solid rgba(255, 255, 255, 26);
    border-radius: 16px;
}}
QLabel {{ color: #E6E8EB; }}
QLabel#title {{ font-size: 14px; font-weight: 600; color: #FFFFFF; }}
QLabel#session {{ font-size: 12px; color: #8B93A1; }}
QLabel#incoming {{ font-size: 13px; color: #D7DAE0; }}
QLabel#section {{ font-size: 11px; color: #7E8794; }}
QLabel#chip {{
    font-size: 11px; color: #B8C0CC;
    background: rgba(255, 255, 255, 14);
    border-radius: 8px; padding: 3px 8px;
}}
QLabel#warning {{ font-size: 11px; color: {WARN}; }}
QLabel#hint {{ font-size: 10px; color: #6F7885; }}
QLabel#error {{ font-size: 12px; color: {DANGER}; }}
QFrame#candidate {{
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 16);
    border-radius: 12px;
}}
QFrame#candidate[recommended="true"] {{
    background: rgba(7, 193, 96, 30);
    border: 1px solid rgba(7, 193, 96, 130);
}}
QLabel#tone {{ font-size: 11px; color: {ACCENT}; font-weight: 600; }}
QLabel#score {{ font-size: 10px; color: #7E8794; }}
QLabel#candidateText {{ font-size: 13px; color: #F2F4F7; }}
QPushButton {{
    font-size: 11px; color: #D7DAE0;
    background: rgba(255, 255, 255, 16);
    border: 1px solid rgba(255, 255, 255, 22);
    border-radius: 8px; padding: 4px 10px;
}}
QPushButton:hover {{ background: rgba(255, 255, 255, 28); }}
QPushButton#primary {{
    color: #06210F; background: {ACCENT}; border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #0AD96F; }}
QPushButton#ghost {{ background: transparent; border: 1px solid rgba(255,255,255,22); }}
QPlainTextEdit {{
    background: rgba(0, 0, 0, 90); color: #9AA4B2; font-size: 10px;
    border: 1px solid rgba(255,255,255,14); border-radius: 8px;
}}
QMenu {{
    background-color: #202632;
    color: #E6E8EB;
    border: 1px solid rgba(255,255,255,38);
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    color: #E6E8EB;
    background: transparent;
    padding: 6px 20px;
    border-radius: 5px;
    font-size: 12px;
}}
QMenu::item:selected {{
    background-color: #2C3442;
    color: #FFFFFF;
}}
QMenu::item:disabled {{ color: #6F7885; }}
QScrollArea {{ background: transparent; border: none; }}
"""

STATE_COLORS = {
    "ok": ACCENT,
    "thinking": WARN,
    "wait": "#8B93A1",
    "error": DANGER,
    "paused": "#5B6472",
}

REFINE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("更简短", "shorter"),
    ("更正式", "formal"),
    ("更温柔", "warmer"),
    ("更幽默", "humorous"),
)


class CandidateCard(QFrame):
    fill_clicked = Signal(str)
    copy_clicked = Signal(str)
    refine_clicked = Signal(str, str)

    def __init__(self, candidate: Candidate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.candidate = candidate
        self.setObjectName("candidate")
        self.setProperty("recommended", "true" if candidate.selected else "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        tone = QLabel(candidate.tone or "候选")
        tone.setObjectName("tone")
        top.addWidget(tone)
        top.addStretch(1)
        if candidate.score > 0:
            score = QLabel(f"{candidate.score:.0f}%")
            score.setObjectName("score")
            top.addWidget(score)
        layout.addLayout(top)

        self.text_label = QLabel(candidate.text)
        self.text_label.setObjectName("candidateText")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.text_label)

        actions = QHBoxLayout()
        refine_btn = QPushButton("改写")
        refine_btn.setObjectName("ghost")
        menu = QMenu(refine_btn)
        for label, key in REFINE_OPTIONS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, k=key: self.refine_clicked.emit(self.text_label.text(), k)
            )
        refine_btn.setMenu(menu)
        copy_btn = QPushButton("复制")
        copy_btn.setObjectName("ghost")
        copy_btn.clicked.connect(lambda: self.copy_clicked.emit(self.text_label.text()))
        fill_btn = QPushButton("填入")
        fill_btn.setObjectName("primary")
        fill_btn.clicked.connect(lambda: self.fill_clicked.emit(self.text_label.text()))
        actions.addWidget(refine_btn)
        actions.addStretch(1)
        actions.addWidget(copy_btn)
        actions.addWidget(fill_btn)
        layout.addLayout(actions)

    def set_text(self, text: str) -> None:
        self.candidate.text = text
        self.text_label.setText(text)


class Overlay(QWidget):
    fill_requested = Signal(str)
    copy_requested = Signal(str)
    regenerate_requested = Signal()
    pause_toggled = Signal(bool)
    hidden_by_user = Signal()
    quit_requested = Signal()
    settings_requested = Signal()
    summary_requested = Signal()
    learn_style_requested = Signal()
    refine_requested = Signal(str, str)
    context_requested = Signal()
    load_more_requested = Signal()
    pin_toggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("微信 AI 回复助手")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLESHEET)
        self.resize(440, 730)
        self._drag_offset = None
        self._paused = False
        self._cards: list[CandidateCard] = []
        self._model_ready = True
        self._build_ui()
        self._place_default()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self.card = QFrame()
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # header
        header = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {ACCENT}; font-size: 12px;")
        header.addWidget(self.dot)
        title = QLabel("微信 AI 回复助手")
        title.setObjectName("title")
        header.addWidget(title)
        self.session_label = QLabel("未连接")
        self.session_label.setObjectName("session")
        header.addWidget(self.session_label)
        header.addStretch(1)
        self.pin_btn = QPushButton("📌")
        self.pin_btn.setObjectName("ghost")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setFixedSize(26, 22)
        self.pin_btn.setToolTip("常驻显示：不再自动隐藏（再次点击恢复自动隐藏）")
        self.pin_btn.clicked.connect(self.pin_toggled.emit)
        header.addWidget(self.pin_btn)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.setFixedSize(26, 22)
        self.settings_btn.setToolTip("模型设置")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_btn)
        hide_btn = QPushButton("—")
        hide_btn.setObjectName("ghost")
        hide_btn.setFixedSize(26, 22)
        hide_btn.clicked.connect(self._hide_self)
        header.addWidget(hide_btn)
        layout.addLayout(header)

        self.status_label = QLabel("正在连接微信窗口…")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        # incoming
        self.incoming_title = QLabel("对方最新消息")
        self.incoming_title.setObjectName("section")
        layout.addWidget(self.incoming_title)

        self.incoming_label = QLabel("等待对方发来新消息…")
        self.incoming_label.setObjectName("incoming")
        self.incoming_label.setWordWrap(True)
        self.incoming_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.incoming_label)

        # intent chips
        self.chips_layout = QHBoxLayout()
        self.chips_layout.setSpacing(6)
        self.chips: list[QLabel] = []
        for _ in range(3):
            chip = QLabel("")
            chip.setObjectName("chip")
            chip.hide()
            self.chips.append(chip)
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        layout.addLayout(self.chips_layout)

        self.warning_label = QLabel("")
        self.warning_label.setObjectName("warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        # candidates
        section = QLabel("回复候选")
        section.setObjectName("section")
        layout.addWidget(section)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.candidates_host = QWidget()
        self.candidates_host.setStyleSheet("background: transparent;")
        self.candidates_layout = QVBoxLayout(self.candidates_host)
        self.candidates_layout.setContentsMargins(0, 0, 0, 0)
        self.candidates_layout.setSpacing(8)
        self.candidates_layout.addStretch(1)
        self.scroll.setWidget(self.candidates_host)
        self.scroll.hide()
        layout.addWidget(self.scroll, 1)

        self.empty_label = QLabel("还没有候选。对方发来新消息后会自动生成。")
        self.empty_label.setObjectName("hint")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label, 1)

        # footer
        row1 = QHBoxLayout()
        self.regenerate_btn = QPushButton("换一批")
        self.regenerate_btn.clicked.connect(self.regenerate_requested.emit)
        self.summary_btn = QPushButton("总结会话")
        self.summary_btn.clicked.connect(self.summary_requested.emit)
        self.style_btn = QPushButton("学我说话")
        self.style_btn.setToolTip("用我的历史回复生成说话风格画像，让候选更像本人")
        self.style_btn.clicked.connect(self.learn_style_requested.emit)
        row1.addWidget(self.regenerate_btn)
        row1.addWidget(self.summary_btn)
        row1.addWidget(self.style_btn)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.load_more_btn = QPushButton("读取更多")
        self.load_more_btn.setToolTip("向上翻阅微信聊天记录，把更早的消息读进上下文")
        self.load_more_btn.clicked.connect(self.load_more_requested.emit)
        self.context_btn = QPushButton("上下文 0")
        self.context_btn.setToolTip("查看当前会话已读取的上下文消息")
        self.context_btn.clicked.connect(self._toggle_context)
        self.pause_btn = QPushButton("暂停采集")
        self.pause_btn.clicked.connect(self._toggle_pause)
        debug_btn = QPushButton("调试")
        debug_btn.setObjectName("ghost")
        debug_btn.clicked.connect(self._toggle_debug)
        quit_btn = QPushButton("退出")
        quit_btn.setObjectName("ghost")
        quit_btn.clicked.connect(self.quit_requested.emit)
        row2.addWidget(self.load_more_btn)
        row2.addWidget(self.context_btn)
        row2.addWidget(self.pause_btn)
        row2.addStretch(1)
        row2.addWidget(debug_btn)
        row2.addWidget(quit_btn)
        layout.addLayout(row2)

        self.hint_label = QLabel("只读截屏 · 本地 OCR · 永不自动发送，发送前请自己核对")
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.context_panel = QPlainTextEdit()
        self.context_panel.setReadOnly(True)
        self.context_panel.setFixedHeight(150)
        self.context_panel.hide()
        layout.addWidget(self.context_panel)

        self.debug_panel = QPlainTextEdit()
        self.debug_panel.setReadOnly(True)
        self.debug_panel.setFixedHeight(120)
        self.debug_panel.hide()
        layout.addWidget(self.debug_panel)

    def _place_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 40, geo.top() + 80)

    # ---------- 对外更新 ----------

    def set_status(self, state: str, message: str) -> None:
        color = STATE_COLORS.get(state, "#8B93A1")
        self.dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status_label.setText(message)

    def set_session(self, session: str) -> None:
        self.session_label.setText(session or "")

    def show_analysis(self, analysis: Analysis) -> None:
        self.set_session(analysis.session)
        incoming_text = "\n".join(m.text for m in analysis.incoming) or "（新消息为空）"
        self.incoming_label.setText(incoming_text)
        self._set_chips(analysis)
        if analysis.warning:
            self.warning_label.setText("⚠ " + analysis.warning)
            self.warning_label.show()
        else:
            self.warning_label.hide()
        self._clear_candidates()

        if analysis.error:
            self.empty_label.setText(f"生成失败：{analysis.error}")
            self.empty_label.show()
            self.scroll.hide()
            return

        for candidate in analysis.candidates:
            card = CandidateCard(candidate)
            card.fill_clicked.connect(self.fill_requested.emit)
            card.copy_clicked.connect(self.copy_requested.emit)
            card.refine_clicked.connect(self.refine_requested.emit)
            self._cards.append(card)
            self.candidates_layout.insertWidget(self.candidates_layout.count() - 1, card)
        self.empty_label.hide()
        self.scroll.show()
        source = analysis.provider or "model"
        tokens = analysis.tokens_in + analysis.tokens_out
        token_text = f" · {tokens} tok" if tokens else ""
        context_text = f" · {analysis.context_count} 条上下文" if analysis.context_count else ""
        self.status_label.setText(
            f"生成完成 · {analysis.latency_ms}ms · {source}{context_text}{token_text}"
        )

    def show_debug(self, session: str, bubbles, lines, extra: str = "") -> None:
        if not self.debug_panel.isVisible():
            return
        body = [f"会话：{session}", f"OCR 行数：{len(lines)}，气泡数：{len(bubbles)}"]
        for bubble in bubbles[-12:]:
            who = "我" if bubble.is_me else "对方"
            body.append(f"[{who}] {bubble.text[:40]}")
        if extra:
            body.append(extra)
        self.debug_panel.setPlainText("\n".join(body))

    def update_candidate_text(self, original: str, new_text: str) -> None:
        for card in self._cards:
            if card.candidate.text == original:
                card.set_text(new_text)
                self.status_label.setText("已按你的要求改写")
                return

    def set_pinned(self, pinned: bool) -> None:
        self.pin_btn.blockSignals(True)
        self.pin_btn.setChecked(pinned)
        self.pin_btn.blockSignals(False)
        self.pin_btn.setToolTip(
            "已常驻显示（点击恢复自动隐藏）" if pinned else "常驻显示：不再自动隐藏"
        )

    def set_model_ready(self, ready: bool, hint: str = "") -> None:
        self._model_ready = ready
        if ready:
            if not self._cards:
                self.empty_label.setText("还没有候选。对方发来新消息后会自动生成。")
                self.empty_label.show()
                self.scroll.hide()
            return
        self.empty_label.setText(
            hint
            or "还没有配置大模型：点右上角 ⚙ 选择服务商（推荐 DeepSeek）并填入 API Key，"
            "配置后对方来新消息就会自动生成候选。"
        )
        self.empty_label.show()
        self.scroll.hide()
        self.status_label.setText("未配置模型 · 点 ⚙ 设置")

    def set_context_count(self, total: int) -> None:
        self.context_btn.setText(f"上下文 {total}")

    def show_context(self, messages, total: int) -> None:
        lines = []
        for message in messages[-30:]:
            who = "我" if message.is_me else "对方"
            lines.append(f"{who}：{message.text}")
        body = "\n".join(lines) if lines else "还没有读到上下文消息。打开微信聊天窗口停留几秒即可。"
        self.context_panel.setPlainText(body)
        self.set_context_count(total)
        if not self.context_panel.isVisible():
            self.context_panel.show()

    def _toggle_context(self) -> None:
        if self.context_panel.isVisible():
            self.context_panel.hide()
        else:
            self.context_panel.show()
            self.context_requested.emit()

    def set_busy(self, message: str) -> None:
        self.status_label.setText(message)
        self.dot.setStyleSheet(f"color: {WARN}; font-size: 12px;")

    # ---------- 内部 ----------

    def _set_chips(self, analysis: Analysis) -> None:
        values = []
        if analysis.intent:
            values.append(f"意图 · {analysis.intent}")
        if analysis.emotion:
            values.append(f"情绪 · {analysis.emotion}")
        if analysis.urgency:
            values.append(f"紧急 · {analysis.urgency}/9")
        for chip, value in zip(self.chips, values):
            chip.setText(value)
            chip.show()
        for chip in self.chips[len(values) :]:
            chip.hide()

    def _clear_candidates(self) -> None:
        self._cards.clear()
        while self.candidates_layout.count() > 1:
            item = self.candidates_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_btn.setText("继续采集" if self._paused else "暂停采集")
        self.pause_toggled.emit(self._paused)

    def _toggle_debug(self) -> None:
        self.debug_panel.setVisible(not self.debug_panel.isVisible())

    def _hide_self(self) -> None:
        self.hide()
        self.hidden_by_user.emit()

    # ---------- 交互 ----------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("wxassist")
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    return app

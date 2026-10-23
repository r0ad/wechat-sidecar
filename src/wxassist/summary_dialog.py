"""会话总结对话框：摘要 / 主题 / 待办 / 风险。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .models import SessionSummary
from .resources import app_icon

STYLE = """
QDialog { background: #181C24; }
QLabel { color: #E6E8EB; font-size: 12px; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLabel#section { color: #8B93A1; font-size: 11px; }
QLabel#risk { color: #F5A524; font-size: 12px; }
QTextEdit {
    background: rgba(255,255,255,10); color: #E6E8EB;
    border: 1px solid rgba(255,255,255,20); border-radius: 8px;
    font-size: 13px; padding: 8px;
}
QPushButton {
    background: rgba(255,255,255,16); color: #E6E8EB;
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 6px 14px; font-size: 12px;
}
QPushButton#primary { background: #07C160; color: #06210F; border: none; font-weight: 600; }
"""


class SummaryDialog(QDialog):
    def __init__(self, summary: SessionSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self.setWindowTitle(f"会话总结 · {summary.session}")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE)
        self.resize(520, 460)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"会话总结 · {self.summary.session}")
        title.setObjectName("title")
        layout.addWidget(title)

        if self.summary.error:
            error = QLabel(f"生成失败：{self.summary.error}")
            error.setObjectName("risk")
            error.setWordWrap(True)
            layout.addWidget(error)
            close = QPushButton("关闭")
            close.clicked.connect(self.accept)
            layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
            return

        section = QLabel("摘要")
        section.setObjectName("section")
        layout.addWidget(section)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(self.summary.summary)
        layout.addWidget(body, 1)

        if self.summary.topics:
            topics = QLabel("主题：" + " · ".join(self.summary.topics))
            topics.setObjectName("section")
            topics.setWordWrap(True)
            layout.addWidget(topics)

        if self.summary.todos:
            todos = QLabel("待办：\n" + "\n".join(f"· {item}" for item in self.summary.todos))
            todos.setWordWrap(True)
            layout.addWidget(todos)

        if self.summary.risk:
            risk = QLabel("⚠ " + self.summary.risk)
            risk.setObjectName("risk")
            risk.setWordWrap(True)
            layout.addWidget(risk)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy_btn = QPushButton("复制摘要")
        copy_btn.clicked.connect(self._copy)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(copy_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _copy(self) -> None:
        text = self.summary.summary
        if self.summary.todos:
            text += "\n\n待办：\n" + "\n".join(f"- {item}" for item in self.summary.todos)
        QApplication.clipboard().setText(text)

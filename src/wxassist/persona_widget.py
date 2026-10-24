"""个性化设置页：人设 / 回复偏好自由文本，注入到每条回复的提示词。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import persona as persona_mod
from .config import Config

STYLE = """
QWidget { background: #181C24; }
QLabel { color: #E6E8EB; font-size: 12px; }
QLabel#hint { color: #8B93A1; font-size: 11px; }
QPlainTextEdit {
    background: #1B202A; color: #E6E8EB;
    border: 1px solid rgba(255,255,255,22); border-radius: 8px;
    font-size: 13px; padding: 8px;
    selection-background-color: #2C3442;
}
QCheckBox { color: #E6E8EB; font-size: 12px; }
QPushButton {
    background: rgba(255,255,255,16); color: #E6E8EB;
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 5px 12px; font-size: 12px;
}
QPushButton:hover { background: rgba(255,255,255,28); }
QPushButton#primary { background: #07C160; color: #06210F; border: none; font-weight: 600; }
"""


class PersonaWidget(QWidget):
    def __init__(self, cfg: Config, data_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.data_dir = data_dir
        self.setStyleSheet(STYLE)
        self._build()
        self.reload()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        hint = QLabel(
            "写你自己的人设和回复偏好，会注入到每条回复的提示词里。"
            "点悬浮窗的「学我说话」时，生成的风格会自动写进下面的 AUTO-STYLE 区块，"
            "你手写的内容不会被覆盖。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        self.enabled_box = QCheckBox("启用个性化提示词")
        self.enabled_box.setChecked(bool(self.cfg.persona.enabled))
        toolbar.addWidget(self.enabled_box)
        toolbar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("hint")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "例如：\n"
            "我是做企业服务的销售，回复要专业但不端着。\n"
            "句子短一点，少用感叹号，不用“亲”“宝子”这类称呼。\n"
            "对方是客户时先确认需求，再给方案，不要直接报价。\n"
            "……\n\n"
            "<!-- AUTO-STYLE:BEGIN -->\n【我的说话风格（自动生成，可手动改）】\n（点「学我说话」后会自动填在这里）\n<!-- AUTO-STYLE:END -->"
        )
        self.editor.textChanged.connect(self._update_count)
        layout.addWidget(self.editor, 1)

        buttons = QHBoxLayout()
        import_btn = QPushButton("导入文本")
        export_btn = QPushButton("导出文本")
        self.save_btn = QPushButton("保存个性化")
        self.save_btn.setObjectName("primary")
        buttons.addWidget(import_btn)
        buttons.addWidget(export_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        import_btn.clicked.connect(self._import_text)
        export_btn.clicked.connect(self._export_text)
        self.save_btn.clicked.connect(lambda: self.save())

    def reload(self) -> None:
        self.editor.blockSignals(True)
        self.editor.setPlainText(persona_mod.load_persona(self.data_dir))
        self.editor.blockSignals(False)
        self._update_count()
        self.status_label.setText(f"上限 {self.cfg.persona.max_chars:,} 字")

    def save(self, silent: bool = True) -> None:
        text = self.editor.toPlainText()
        try:
            persona_mod.save_persona(self.data_dir, text)
            self.cfg.persona.enabled = self.enabled_box.isChecked()
            self.cfg.save(Path(self.data_dir) / "config.json")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status_label.setText(f"已保存 · {len(text):,} 字")
        if not silent:
            QMessageBox.information(self, "已保存", "个性化提示词已保存，下一条回复生效。")

    def _update_count(self) -> None:
        self.count_label.setText(f"{len(self.editor.toPlainText()):,} 字")

    def _import_text(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入个性化提示词", "", "文本文件 (*.txt *.md);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.editor.setPlainText(text)
        self.status_label.setText("已导入，点「保存个性化」生效")

    def _export_text(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出个性化提示词", "wxassist-persona.txt", "文本文件 (*.txt);;Markdown (*.md)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出到\n{path}")

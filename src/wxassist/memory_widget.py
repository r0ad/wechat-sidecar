"""长期记忆设置页：一个自由文本输入框，内容整段注入大模型。"""

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

from . import memory as memory_mod
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


class MemoryWidget(QWidget):
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
            "把常见的业务问答、产品资料、话术、客户背景等直接贴进来（纯文本即可，越长越自由）。"
            "每次生成回复时会把这段内容整段交给大模型，由模型自己判断怎么用；"
            "不做分词、不做检索。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        self.enabled_box = QCheckBox("启用长期记忆（注入到模型上下文）")
        self.enabled_box.setChecked(bool(self.cfg.memory.enabled))
        toolbar.addWidget(self.enabled_box)
        toolbar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("hint")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "例如：\n"
            "【退款政策】支持 7 天无理由退款，商品需保持完好，退货运费卖家承担，1-3 个工作日原路退回。\n"
            "【发货时间】工作日 17:00 前下单当天发货，偏远地区加收 12 元运费。\n"
            "【话术】客户问价格时先报活动价，再说明赠品。\n"
            "……（可以继续写很长）"
        )
        self.editor.textChanged.connect(self._update_count)
        layout.addWidget(self.editor, 1)

        buttons = QHBoxLayout()
        import_btn = QPushButton("导入文本")
        export_btn = QPushButton("导出文本")
        clear_btn = QPushButton("清空")
        self.save_btn = QPushButton("保存记忆")
        self.save_btn.setObjectName("primary")
        buttons.addWidget(import_btn)
        buttons.addWidget(export_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        import_btn.clicked.connect(self._import_text)
        export_btn.clicked.connect(self._export_text)
        clear_btn.clicked.connect(self._clear)
        self.save_btn.clicked.connect(lambda: self.save())

    # ---------- 数据 ----------

    def reload(self) -> None:
        self.editor.blockSignals(True)
        self.editor.setPlainText(memory_mod.load_memory(self.data_dir))
        self.editor.blockSignals(False)
        self._update_count()
        self.status_label.setText(f"当前上限 {self.cfg.memory.max_chars:,} 字（超出会截断）")

    def save(self, silent: bool = True) -> None:
        text = self.editor.toPlainText()
        try:
            memory_mod.save_memory(self.data_dir, text)
            self.cfg.memory.enabled = self.enabled_box.isChecked()
            self.cfg.save(Path(self.data_dir) / "config.json")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._update_count()
        self.status_label.setText(f"已保存 · {len(text):,} 字")
        if not silent:
            QMessageBox.information(self, "已保存", "长期记忆已保存，下一条回复生成时生效。")

    def _update_count(self) -> None:
        self.count_label.setText(f"{len(self.editor.toPlainText()):,} 字")

    # ---------- 操作 ----------

    def _import_text(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入长期记忆", "", "文本文件 (*.txt *.md *.markdown);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.editor.setPlainText(text)
        self.status_label.setText(f"已导入 {len(text):,} 字，点「保存记忆」生效")

    def _export_text(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出长期记忆", "wxassist-memory.txt", "文本文件 (*.txt);;Markdown (*.md)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出到\n{path}")

    def _clear(self) -> None:
        if not self.editor.toPlainText().strip():
            return
        confirm = QMessageBox.question(self, "清空", "确定清空长期记忆？清空后记得点「保存记忆」。")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.editor.setPlainText("")
        self.status_label.setText("已清空，点「保存记忆」生效")

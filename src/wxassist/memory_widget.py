"""长期记忆管理界面：问答/知识条目增删改、置顶、开关、导入导出。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import memory as memory_mod
from .models import MemoryEntry
from .store import Store

KIND_CHOICES = [
    ("问答 / FAQ", "qa"),
    ("业务知识", "fact"),
    ("偏好 / 禁忌", "preference"),
    ("说明 / 指令", "instruction"),
]

STYLE = """
QDialog, QWidget { background: #181C24; }
QLabel { color: #E6E8EB; font-size: 12px; }
QLabel#hint { color: #8B93A1; font-size: 11px; }
QLineEdit, QPlainTextEdit, QComboBox {
    background: rgba(255,255,255,12); color: #E6E8EB;
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 6px 8px; font-size: 12px;
}
QComboBox QAbstractItemView { background: #202632; color: #E6E8EB; }
QPushButton {
    background: rgba(255,255,255,16); color: #E6E8EB;
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 5px 12px; font-size: 12px;
}
QPushButton:hover { background: rgba(255,255,255,28); }
QPushButton#primary { background: #07C160; color: #06210F; border: none; font-weight: 600; }
QTableWidget {
    background-color: #1B202A; alternate-background-color: #1F2531; color: #E6E8EB;
    gridline-color: rgba(255,255,255,14);
    border: 1px solid rgba(255,255,255,20); border-radius: 8px; font-size: 12px;
}
QTableWidget::item { background: transparent; color: #E6E8EB; padding: 3px 4px; }
QTableWidget::item:selected { background-color: #2C3442; color: #FFFFFF; }
QTableWidget QTableCornerButton::section { background-color: #202632; border: none; }
QHeaderView { background: #202632; }
QHeaderView::section {
    background-color: #202632; color: #B8C0CC; border: none; padding: 6px;
}
QPushButton { color: #E6E8EB; }
QPushButton:disabled { color: #6F7885; }
"""


class MemoryEntryDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        entry: MemoryEntry | None = None,
        session: str = "",
        default_scope: str = "global",
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.session = session
        self.setWindowTitle("编辑记忆" if entry else "新增记忆")
        self.setStyleSheet(STYLE)
        self.resize(560, 520)
        self._build(default_scope)
        if entry is not None:
            self._prefill(entry)

    def _build(self, default_scope: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("全局（所有会话）", "global")
        if self.session:
            self.scope_combo.addItem(f"仅本会话：{self.session}", "session")
        self.scope_combo.setCurrentIndex(1 if default_scope == "session" and self.session else 0)
        form.addRow("生效范围", self.scope_combo)

        self.kind_combo = QComboBox()
        for label, value in KIND_CHOICES:
            self.kind_combo.addItem(label, value)
        form.addRow("类型", self.kind_combo)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("问题 / 标题，例如：退款政策是什么？")
        form.addRow("标题 / 问题", self.title_edit)

        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("可选，逗号分隔，用于召回：退款,退货,售后")
        form.addRow("关键词", self.keywords_edit)

        layout.addLayout(form)

        content_label = QLabel("内容 / 回答（可以很长，注入时会按预算截取）：")
        content_label.setObjectName("hint")
        layout.addWidget(content_label)
        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText("把常见业务问题的标准回答、产品说明、话术写在这里……")
        layout.addWidget(self.content_edit, 1)

        flags = QHBoxLayout()
        self.pinned_box = QCheckBox("置顶（每次生成都加载）")
        self.enabled_box = QCheckBox("启用")
        self.enabled_box.setChecked(True)
        flags.addWidget(self.pinned_box)
        flags.addWidget(self.enabled_box)
        flags.addStretch(1)
        layout.addLayout(flags)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _prefill(self, entry: MemoryEntry) -> None:
        index = self.scope_combo.findData(entry.scope)
        if index >= 0:
            self.scope_combo.setCurrentIndex(index)
        index = self.kind_combo.findData(entry.kind)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        self.title_edit.setText(entry.title)
        self.keywords_edit.setText(entry.keywords)
        self.content_edit.setPlainText(entry.content)
        self.pinned_box.setChecked(entry.pinned)
        self.enabled_box.setChecked(entry.enabled)

    def _on_accept(self) -> None:
        if not self.content_edit.toPlainText().strip():
            QMessageBox.warning(self, "缺少内容", "内容/回答不能为空")
            return
        self.accept()

    def data(self) -> dict:
        scope = self.scope_combo.currentData() or "global"
        return {
            "scope": scope,
            "session": self.session if scope == "session" else "",
            "kind": self.kind_combo.currentData() or "fact",
            "title": self.title_edit.text().strip(),
            "keywords": self.keywords_edit.text().strip(),
            "content": self.content_edit.toPlainText().strip(),
            "pinned": self.pinned_box.isChecked(),
            "enabled": self.enabled_box.isChecked(),
        }


class MemoryWidget(QWidget):
    def __init__(self, store: Store, session_getter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.session_getter = session_getter
        self._loading = False
        self._entries: list[MemoryEntry] = []
        self.setStyleSheet(STYLE)
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        hint = QLabel(
            "长期记忆 / 业务知识：全局条目对所有会话生效，本会话条目只对当前聊天生效。"
            "置顶条目每次都会加载，其余条目按当前对话关键词召回，再按预算注入。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("新增")
        add_btn.setObjectName("primary")
        edit_btn = QPushButton("编辑")
        delete_btn = QPushButton("删除")
        import_btn = QPushButton("导入")
        export_btn = QPushButton("导出")
        refresh_btn = QPushButton("刷新")
        for button in (add_btn, edit_btn, delete_btn, import_btn, export_btn, refresh_btn):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["启用", "置顶", "范围", "类型", "标题 / 问题", "关键词"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(1, 48)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(lambda _index: self._edit_selected())
        layout.addWidget(self.table, 1)

        add_btn.clicked.connect(self._add_entry)
        edit_btn.clicked.connect(self._edit_selected)
        delete_btn.clicked.connect(self._delete_selected)
        import_btn.clicked.connect(self._import_entries)
        export_btn.clicked.connect(self._export_entries)
        refresh_btn.clicked.connect(self.refresh)

    # ---------- 数据 ----------

    def _session(self) -> str:
        try:
            return self.session_getter() or ""
        except Exception:
            return ""

    def refresh(self) -> None:
        session = self._session()
        self._entries = self.store.list_memories(session, include_disabled=True)
        self._loading = True
        self.table.setRowCount(0)
        for entry in self._entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            enabled_item.setCheckState(
                Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
            )
            enabled_item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.table.setItem(row, 0, enabled_item)

            pinned_item = QTableWidgetItem()
            pinned_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            pinned_item.setCheckState(
                Qt.CheckState.Checked if entry.pinned else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 1, pinned_item)

            scope = "全局" if entry.scope == "global" else f"会话：{entry.session}"
            self.table.setItem(row, 2, QTableWidgetItem(scope))
            self.table.setItem(
                row, 3, QTableWidgetItem(memory_mod.KIND_LABELS.get(entry.kind, entry.kind))
            )
            self.table.setItem(row, 4, QTableWidgetItem(entry.title or entry.content[:40]))
            self.table.setItem(row, 5, QTableWidgetItem(entry.keywords))
        self._loading = False
        self.status_label.setText(f"共 {len(self._entries)} 条")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        first = self.table.item(item.row(), 0)
        if first is None:
            return
        memory_id = first.data(Qt.ItemDataRole.UserRole)
        if memory_id is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        if item.column() == 0:
            self.store.set_memory_enabled(int(memory_id), checked)
        elif item.column() == 1:
            self.store.set_memory_pinned(int(memory_id), checked)
        self.refresh()

    def _selected_entry(self) -> MemoryEntry | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    # ---------- 操作 ----------

    def _add_entry(self) -> None:
        session = self._session()
        dialog = MemoryEntryDialog(
            self,
            session=session,
            default_scope="session" if session else "global",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.add_memory(**dialog.data(), source="manual")
        self.refresh()

    def _edit_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "提示", "先在列表里选中一条记忆")
            return
        dialog = MemoryEntryDialog(self, entry=entry, session=self._session())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.update_memory(entry.id, **dialog.data())
        self.refresh()

    def _delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "提示", "先在列表里选中一条记忆")
            return
        confirm = QMessageBox.question(
            self, "删除记忆", f"确定删除「{entry.title or entry.content[:20]}」？"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_memory(entry.id)
        self.refresh()

    def _import_entries(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入记忆", "", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            added, skipped = memory_mod.import_file(self.store, path)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "导入完成", f"新增 {added} 条，跳过 {skipped} 条")

    def _export_entries(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出记忆", "wxassist-memories.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            memory_mod.export_file(path, self.store.list_all_memories(include_disabled=True))
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出到\n{path}")

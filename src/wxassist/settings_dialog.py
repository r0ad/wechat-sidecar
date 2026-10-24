"""模型设置对话框：选择服务商、填 Base URL / 模型 / API Key，并测试连接。"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import secrets_store
from .config import Config, LLMConfig
from .llm import ConnectionResult, LLMClient
from .providers import PRESETS, get_preset
from .resources import app_icon

BG = "#181C24"
FG = "#E6E8EB"
MUTED = "#8B93A1"
ACCENT = "#07C160"
DANGER = "#E5484D"

STYLE = f"""
QDialog {{ background: {BG}; }}
QLabel {{ color: {FG}; font-size: 12px; }}
QLabel#title {{ font-size: 15px; font-weight: 600; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#ok {{ color: {ACCENT}; font-size: 11px; }}
QLabel#err {{ color: {DANGER}; font-size: 11px; }}
QLineEdit, QComboBox {{
    background: rgba(255,255,255,12); color: {FG};
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 6px 8px; font-size: 12px;
}}
QComboBox QAbstractItemView {{ background: #202632; color: {FG}; selection-background-color: #2C3442; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {MUTED};
    padding: 6px 16px; margin-right: 4px; border-radius: 7px; font-size: 12px;
}}
QTabBar::tab:hover {{ color: {FG}; background: rgba(255,255,255,12); }}
QTabBar::tab:selected {{ color: #FFFFFF; background: rgba(255,255,255,16); }}
QPushButton {{
    background: rgba(255,255,255,16); color: {FG};
    border: 1px solid rgba(255,255,255,24); border-radius: 8px;
    padding: 6px 14px; font-size: 12px;
}}
QPushButton:hover {{ background: rgba(255,255,255,28); }}
QPushButton#primary {{ background: {ACCENT}; color: #06210F; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #0AD96F; }}
QPushButton:disabled {{ color: {MUTED}; }}
"""


class SettingsDialog(QDialog):
    saved = Signal(dict)
    test_finished = Signal(object)
    models_loaded = Signal(list)

    def __init__(
        self,
        cfg: Config,
        llm: LLMClient,
        data_dir: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.llm = llm
        self.data_dir = data_dir
        self.setWindowTitle("模型设置")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(640)
        self.resize(680, 600)
        self._testing = False
        self._build()
        self._prefill()
        self.test_finished.connect(self._on_test_finished)
        self.models_loaded.connect(self._on_models_loaded)

    # ---------- UI ----------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("title")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        model_tab = QWidget()
        model_layout = QVBoxLayout(model_tab)
        model_layout.setContentsMargins(0, 8, 0, 0)
        model_layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.preset_combo = QComboBox()
        for preset in PRESETS:
            self.preset_combo.addItem(preset.label, preset.key)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("服务商", self.preset_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText("deepseek-chat")
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setToolTip("从服务端拉取模型列表")
        self.refresh_btn.clicked.connect(self._on_refresh_models)
        self.refresh_btn.setFixedWidth(64)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.refresh_btn)
        form.addRow("模型", model_row)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        form.addRow("API Key", self.key_edit)

        model_layout.addLayout(form)

        self.env_hint = QLabel("")
        self.env_hint.setObjectName("hint")
        self.env_hint.setWordWrap(True)
        model_layout.addWidget(self.env_hint)

        self.note_label = QLabel("")
        self.note_label.setObjectName("hint")
        self.note_label.setWordWrap(True)
        model_layout.addWidget(self.note_label)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._on_test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        model_layout.addLayout(test_row)
        model_layout.addStretch(1)

        self.tabs.addTab(model_tab, "模型")
        from .memory_widget import MemoryWidget

        self.memory_widget = MemoryWidget(self.cfg, self.cfg.data_dir)
        self.tabs.addTab(self.memory_widget, "长期记忆")

        from .persona_widget import PersonaWidget

        self.persona_widget = PersonaWidget(self.cfg, self.cfg.data_dir)
        self.tabs.addTab(self.persona_widget, "个性化")
        layout.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _prefill(self) -> None:
        cfg = self.cfg
        preset_index = 0
        for index in range(self.preset_combo.count()):
            if self.preset_combo.itemData(index) == cfg.llm.provider:
                preset_index = index
                break
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(preset_index)
        self.preset_combo.blockSignals(False)
        self.base_url_edit.setText(cfg.llm.base_url)
        self.model_combo.setCurrentText(cfg.llm.model)
        self._update_key_placeholder()
        self._update_hint(preset_index)

    def _update_key_placeholder(self) -> None:
        provider = self.preset_combo.currentData() or "deepseek"
        existing = secrets_store.load_api_key(self.data_dir, provider)
        env_name = (get_preset(provider).key_env if get_preset(provider) else "") or ""
        env_key = self.llm.resolve_key() if env_name and not existing else ""
        if existing:
            self.key_edit.setPlaceholderText(
                f"已保存 {secrets_store.mask_key(existing)}（留空保持不变）"
            )
        elif env_key:
            self.key_edit.setPlaceholderText(f"已从环境变量 {env_name} 读取（留空保持不变）")
        else:
            self.key_edit.setPlaceholderText("sk-...（保存在本机，DPAPI 加密）")

    def _update_hint(self, index: int) -> None:
        preset = PRESETS[index] if 0 <= index < len(PRESETS) else None
        if preset is None:
            return
        parts = []
        if preset.key_env:
            parts.append(f"也可以设置环境变量 {preset.key_env}")
        if preset.note:
            parts.append(preset.note)
        self.env_hint.setText(" · ".join(parts))
        self.note_label.setText(
            "Key 只保存在本机（Windows DPAPI 加密，当前用户可解密），不会写入仓库或日志。"
        )

    def show_memory_tab(self) -> None:
        if self.memory_widget is not None:
            self.tabs.setCurrentIndex(1)

    # ---------- 事件 ----------

    def _on_preset_changed(self, index: int) -> None:
        preset = PRESETS[index] if 0 <= index < len(PRESETS) else None
        if preset and preset.base_url:
            self.base_url_edit.setText(preset.base_url)
            self.model_combo.setCurrentText(preset.model)
        elif preset:
            self.base_url_edit.setPlaceholderText("https://your-host/v1")
            self.model_combo.setPlaceholderText("模型名")
        self._update_key_placeholder()
        self._update_hint(index)
        self.test_result.setText("")

    def _collect(self) -> dict:
        return {
            "provider": self.preset_combo.currentData() or "custom",
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_combo.currentText().strip(),
            "api_key": self.key_edit.text().strip(),
        }

    def _on_test(self) -> None:
        if self._testing:
            return
        data = self._collect()
        if not data["base_url"] or not data["model"]:
            self._show_test(ConnectionResult(False, "先填写 Base URL 和模型名"))
            return
        provider = data["provider"]
        preset = get_preset(provider)
        temp_cfg = LLMConfig(
            provider=provider,
            base_url=data["base_url"],
            model=data["model"],
            api_key_env=(preset.key_env if preset else "") or self.cfg.llm.api_key_env,
            api_key=data["api_key"] or secrets_store.load_api_key(self.data_dir, provider),
            temperature=self.cfg.llm.temperature,
            max_tokens=self.cfg.llm.max_tokens,
            timeout=self.cfg.llm.timeout,
        )
        client = LLMClient(temp_cfg, self.data_dir)
        self._testing = True
        self.test_btn.setEnabled(False)
        self.test_result.setText("正在测试…")
        self.test_result.setObjectName("hint")

        def worker() -> None:
            result = client.test_connection()
            self.test_finished.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_models(self) -> None:
        data = self._collect()
        if not data["base_url"]:
            self._show_test(ConnectionResult(False, "先填写 Base URL"))
            return
        provider = data["provider"]
        preset = get_preset(provider)
        temp_cfg = LLMConfig(
            provider=provider,
            base_url=data["base_url"],
            model=data["model"] or "x",
            api_key_env=(preset.key_env if preset else "") or self.cfg.llm.api_key_env,
            api_key=data["api_key"] or secrets_store.load_api_key(self.data_dir, provider),
            timeout=self.cfg.llm.timeout,
        )
        client = LLMClient(temp_cfg, self.data_dir)
        self.refresh_btn.setEnabled(False)
        self.test_result.setText("正在拉取模型列表…")

        def worker() -> None:
            models = client.list_models()
            self.models_loaded.emit(models)

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_loaded(self, models: list) -> None:
        self.refresh_btn.setEnabled(True)
        if not models:
            self.test_result.setText("没有拉到模型列表，可手动填写模型名")
            return
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems([str(m) for m in models])
        if current:
            self.model_combo.setCurrentText(current)
        self.test_result.setText(f"已拉取 {len(models)} 个模型")

    def _on_test_finished(self, result: ConnectionResult) -> None:
        self._testing = False
        self.test_btn.setEnabled(True)
        self._show_test(result)

    def _show_test(self, result: ConnectionResult) -> None:
        self.test_result.setObjectName("ok" if result.ok else "err")
        self.test_result.setStyleSheet(f"color: {ACCENT if result.ok else DANGER};")
        suffix = f" · {result.latency_ms}ms" if result.latency_ms else ""
        self.test_result.setText(f"{result.message}{suffix}")

    def _on_save(self) -> None:
        data = self._collect()
        if not data["base_url"]:
            self._show_test(ConnectionResult(False, "Base URL 不能为空"))
            return
        if not data["model"]:
            self._show_test(ConnectionResult(False, "模型名不能为空"))
            return
        self.saved.emit(data)
        self.accept()

    def result_settings(self) -> dict:
        return self._collect()

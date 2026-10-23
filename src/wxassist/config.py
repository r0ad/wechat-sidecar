"""运行配置：内置默认值 → config.local.json → --config 覆盖。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "wxassist"


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


@dataclass
class LLMConfig:
    provider: str = "deepseek"  # deepseek | openai | ollama | mock
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    api_key: str = ""  # 可选：直接写在这里（不推荐，优先环境变量）
    temperature: float = 0.7
    timeout: int = 30


@dataclass
class CaptureConfig:
    interval_ms: int = 700
    # 微信 4.x 布局是固定像素（会随系统 DPI 缩放），优先用 px 计算
    chat_left_px: int = 300  # 图标栏 + 会话列表宽度
    header_px: int = 64  # 聊天面板顶部标题栏高度
    input_px: int = 160  # 底部输入区高度
    # DPI 未知时的兜底比例
    chat_left_ratio: float = 0.26
    header_bottom_ratio: float = 0.10
    input_top_ratio: float = 0.86
    me_x_ratio: float = 0.62
    min_line_gap_px: int = 10


@dataclass
class PrivacyConfig:
    redact_before_send: bool = True
    max_context_messages: int = 20
    store_screenshots: bool = False


@dataclass
class UIConfig:
    hotkey: str = "ctrl+alt+j"
    always_on_top: bool = True
    opacity: float = 0.97


@dataclass
class FillerConfig:
    mode: str = "copy"  # copy | paste
    paste_without_enter: bool = True


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    filler: FillerConfig = field(default_factory=FillerConfig)
    data_dir: str = ""

    # ---------- 读写 ----------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """加载配置：默认值 ← 配置文件；找不到文件就用默认值。"""
        cfg = cls()
        if path is None:
            for candidate in (
                Path("config.local.json"),
                default_data_dir() / "config.json",
            ):
                if candidate.exists():
                    path = candidate
                    break
        if path is not None:
            cfg.merge_file(Path(path))
        if not cfg.data_dir:
            cfg.data_dir = str(default_data_dir())
        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
        return cfg

    def merge_file(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.merge_dict(raw)

    def merge_dict(self, raw: dict[str, Any]) -> None:
        for section in ("llm", "capture", "privacy", "ui", "filler"):
            values = raw.get(section)
            if not isinstance(values, dict):
                continue
            target = getattr(self, section)
            valid = {f.name for f in fields(target)}
            for key, value in values.items():
                if key in valid:
                    setattr(target, key, value)
        if isinstance(raw.get("data_dir"), str) and raw["data_dir"]:
            self.data_dir = raw["data_dir"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ---------- 便捷属性 ----------

    def api_key(self) -> str:
        """优先环境变量；其次配置里的 api_key；mock 不需要。"""
        if self.llm.provider == "mock":
            return "mock"
        return os.environ.get(self.llm.api_key_env, "") or self.llm.api_key

    def db_path(self) -> Path:
        return Path(self.data_dir) / "wxassist.db"

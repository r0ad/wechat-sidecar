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
    provider: str = "deepseek"  # deepseek | openai | dashscope | zhipu | siliconflow | ollama | custom
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "DEEPSEEK_API_KEY"
    api_key: str = ""  # 一般不填，Key 存在 DPAPI 加密文件或环境变量里
    temperature: float = 0.8
    max_tokens: int = 700
    timeout: int = 45


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
    startup_warmup_sec: float = 2.0  # 启动后多久内不生成建议（等画面稳定）
    analysis_debounce_sec: float = 1.5  # 连续多条消息合并成一次分析
    load_more_pages: int = 3  # 「读取更多」向上翻几屏
    require_send_button: bool = True  # 只在聊天页（底部有「发送」）读取，避免把文章/小程序当聊天


@dataclass
class PrivacyConfig:
    redact_before_send: bool = True
    max_context_messages: int = 20
    store_screenshots: bool = False


@dataclass
class MemoryConfig:
    enabled: bool = True
    max_chars: int = 200_000  # 直接注入上限，防止误贴超大文件把请求撑爆



@dataclass
class PersonaConfig:
    enabled: bool = True
    max_chars: int = 5000


@dataclass
class UIConfig:
    hotkey: str = "ctrl+alt+j"
    always_on_top: bool = True
    opacity: float = 0.97
    auto_hide: bool = True  # 微信不在前台时自动收起悬浮窗
    follow_wechat: bool = True  # 悬浮窗跟随微信窗口移动
    auto_proactive: bool = True  # 切到新会话且没有新消息时，自动基于当前对话生成候选


@dataclass
class FillerConfig:
    mode: str = "copy"  # copy | paste
    paste_without_enter: bool = True


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
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
        for section in ("llm", "capture", "privacy", "ui", "filler", "memory", "persona"):
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

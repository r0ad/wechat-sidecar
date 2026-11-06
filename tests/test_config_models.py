"""配置模型与领域模型的单元测试。"""

from __future__ import annotations

import json

from wxassist.config import (
    Config,
    apply_capture_preset,
    capture_preset_key,
    preset_label,
)
from wxassist.models import Bubble, Message
from wxassist.providers import PRESETS, detect_provider, get_preset


def test_config_defaults_and_overrides(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"llm": {"provider": "mock", "model": "x"}, "capture": {"interval_ms": 1234}}),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.llm.provider == "mock"
    assert cfg.llm.model == "x"
    assert cfg.capture.interval_ms == 1234
    assert cfg.data_dir


def test_config_ignores_unknown_keys(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"nope": 1}}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.llm.provider == "deepseek"


def test_api_key_from_env(tmp_path, monkeypatch) -> None:
    cfg = Config.load(None)
    cfg.llm.provider = "deepseek"
    cfg.llm.api_key_env = "WXASSIST_TEST_KEY"
    monkeypatch.setenv("WXASSIST_TEST_KEY", "sk-test")
    assert cfg.api_key() == "sk-test"
    monkeypatch.delenv("WXASSIST_TEST_KEY")
    cfg.llm.api_key = ""
    assert cfg.api_key() == ""


def test_message_digest_differs_by_side() -> None:
    a = Message(session="s", text="在吗", is_me=False)
    b = Message(session="s", text="在吗", is_me=True)
    assert a.digest() != b.digest()
    assert a.role == "user"
    assert b.role == "assistant"


def test_bubble_digest() -> None:
    b = Bubble(text="hi", is_me=False, x0=0, y0=0, x1=1, y1=1)
    assert b.digest() == Bubble(text="hi", is_me=False, x0=5, y0=5, x1=6, y1=6).digest()


def test_provider_presets_use_current_model_names() -> None:
    """预设里的模型名必须跟得上官方命名（2026-10 核对）。"""
    by_key = {preset.key: preset for preset in PRESETS}
    assert by_key["deepseek"].model == "deepseek-flash"
    assert by_key["openai"].model == "gpt-6-luna"
    assert by_key["dashscope"].model == "qwen3.8-flash"
    assert by_key["zhipu"].model == "glm-5.3-flash"
    assert by_key["siliconflow"].model == "Qwen/Qwen3.6-35B-A3B"
    assert by_key["ollama"].model == "qwen3.8:8b"
    for preset in PRESETS:
        if preset.key == "custom":
            assert not preset.model
            continue
        assert preset.base_url and preset.model
        assert get_preset(preset.key) is preset


def test_detect_provider_from_base_url() -> None:
    assert detect_provider("https://api.deepseek.com/v1", "deepseek-flash") == "deepseek"
    assert detect_provider("https://api.openai.com/v1/", "gpt-6-luna") == "openai"
    assert detect_provider("https://example.com/v1", "x") == "custom"


def test_capture_preset_roundtrip() -> None:
    cfg = Config.load(None)
    assert capture_preset_key(cfg) == "balanced"  # 默认 700ms
    assert preset_label(cfg) == "均衡 0.7s"

    assert apply_capture_preset(cfg, "manual") is True
    assert cfg.capture.auto_capture is False
    assert capture_preset_key(cfg) == "manual"
    assert preset_label(cfg) == "仅手动"

    assert apply_capture_preset(cfg, "sensitive") is True
    assert cfg.capture.interval_ms == 400 and cfg.capture.auto_capture is True
    assert apply_capture_preset(cfg, "no-such-preset") is False


def test_capture_preset_custom_interval_label() -> None:
    cfg = Config.load(None)
    cfg.capture.interval_ms = 900
    cfg.capture.auto_capture = True
    assert capture_preset_key(cfg) == "custom"
    assert preset_label(cfg) == "自定义 0.9s"


def test_new_capture_and_ui_fields_merge(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "capture": {
                    "auto_capture": False,
                    "idle_backoff_sec": 3,
                    "idle_interval_ms": 2000,
                },
                "ui": {"auto_suggest": "manual", "manual_hotkey": "ctrl+alt+l"},
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.capture.auto_capture is False
    assert cfg.capture.idle_backoff_sec == 3
    assert cfg.capture.idle_interval_ms == 2000
    assert cfg.ui.auto_suggest == "manual"
    assert cfg.ui.manual_hotkey == "ctrl+alt+l"

def test_input_area_height_matches_wechat_4x() -> None:
    """125% DPI 实测：底部输入区 166 物理像素 = 133 逻辑像素。

    这个值同时决定「面板/输入区」的切分线和「填入」时点的输入框中心；
    设成 160 会把最新几条消息切进输入区，导致漏读。
    """
    import inspect

    from wxassist import filler
    from wxassist.config import CaptureConfig

    assert CaptureConfig().input_px == 133
    assert inspect.signature(filler.input_box_point).parameters["input_px"].default == 133
    assert inspect.signature(filler.fill).parameters["input_px"].default == 133


def test_non_dialogue_filters_default_on() -> None:
    """三层过滤默认全开，用户可以在配置里关掉。"""
    from wxassist.config import CaptureConfig

    cfg = CaptureConfig()
    assert cfg.filter_non_dialogue is True
    assert cfg.filter_media is True
    assert cfg.filter_quote is True

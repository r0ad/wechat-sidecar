"""配置模型与领域模型的单元测试。"""

from __future__ import annotations

import json

from wxassist.config import Config
from wxassist.models import Bubble, Message


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

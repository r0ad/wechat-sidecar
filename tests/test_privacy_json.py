"""脱敏与 JSON 解析的单元测试。"""

from __future__ import annotations

from wxassist.json_utils import extract_json
from wxassist.privacy import redact, redact_messages, sensitive_topics
from wxassist.models import Message


def test_redact_phone() -> None:
    assert redact("我电话 13812345678 你打给我") == "我电话 138****5678 你打给我"


def test_redact_idcard_bank_email() -> None:
    text = "身份证 110101199003071234 卡号 6222021234567890123 邮箱 a.b@example.com"
    out = redact(text)
    assert "110101********1234" in out
    assert "6222021234567890123" not in out
    assert "a***@example.com" in out


def test_redact_keeps_normal_numbers() -> None:
    assert redact("明天 8 点，3 个人") == "明天 8 点，3 个人"


def test_redact_messages_preserves_metadata() -> None:
    messages = [Message(session="s", text="电话13812345678", is_me=False, ts=1.0)]
    out = redact_messages(messages)
    assert out[0].text == "电话138****5678"
    assert out[0].session == "s"
    assert out[0].is_me is False


def test_sensitive_topics() -> None:
    assert "金钱" in sensitive_topics("能借我 2000 吗")
    assert "验证码/密码" in sensitive_topics("把验证码发我")
    assert sensitive_topics("今天天气不错") == []


def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    raw = '这是结果：\n```json\n{"intent": "闲聊"}\n```\n以上。'
    assert extract_json(raw) == {"intent": "闲聊"}


def test_extract_json_with_noise_around_object() -> None:
    raw = '好的，分析如下 {"a": {"b": [1, 2]}} 希望有帮助'
    assert extract_json(raw) == {"a": {"b": [1, 2]}}


def test_extract_json_array() -> None:
    assert extract_json('候选：["一", "二"]') == ["一", "二"]


def test_extract_json_invalid_returns_none() -> None:
    assert extract_json("完全没有 JSON") is None
    assert extract_json("") is None

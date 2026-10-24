"""个性化提示词与主动生成（无新消息）测试。"""

from __future__ import annotations

import json

from wxassist import persona
from wxassist.config import Config
from wxassist.engine import SuggestEngine
from wxassist.llm import LLMResult
from wxassist.models import Message


def test_persona_save_load(tmp_path) -> None:
    text = "我是销售，回复要短，不用感叹号。"
    persona.save_persona(tmp_path, text)
    assert persona.load_persona(tmp_path) == text
    assert persona.persona_path(tmp_path).name == "persona.txt"


def test_apply_style_section_preserves_manual_text() -> None:
    original = "我的人设：做企业服务销售。"
    updated = persona.apply_style_section(original, "句子短、爱用哈哈")
    assert "我的人设：做企业服务销售。" in updated
    assert "句子短、爱用哈哈" in updated
    assert persona.STYLE_BEGIN in updated and persona.STYLE_END in updated

    replaced = persona.apply_style_section(updated, "新的风格")
    assert "新的风格" in replaced
    assert "句子短、爱用哈哈" not in replaced
    assert "我的人设：做企业服务销售。" in replaced


def test_persona_block() -> None:
    block = persona.build_block("回复要专业、简短")
    assert "【我的人设 / 回复偏好】" in block
    assert "回复要专业、简短" in block
    assert persona.build_block("   ") == ""


class StubLLM:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, system, user, json_mode=True, **kwargs):
        self.calls.append((system, user))
        if "[INTENT]" in system:
            payload = {
                "intent": "闲聊",
                "emotion": "平静",
                "urgency": 2,
                "questions": [],
                "must_answer": [],
                "need": "继续聊",
                "action": "推进话题",
            }
        else:
            payload = {"candidates": [{"tone": "轻松", "text": "那我先过去找你"}]}
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            provider="stub",
            model="m",
            latency_ms=1,
        )


def make_engine(tmp_path):
    cfg = Config.load(None)
    cfg.data_dir = str(tmp_path)
    llm = StubLLM()
    return SuggestEngine(llm, cfg, None), llm, cfg


def test_engine_injects_persona(tmp_path) -> None:
    persona.save_persona(tmp_path, "我是做企业服务的，回复要短、专业。")
    engine, llm, _cfg = make_engine(tmp_path)
    history = [Message(session="客户A", text="报价多少？", is_me=False, ts=1)]
    engine.analyze("客户A", history, history)
    draft = [user for system, user in llm.calls if "[DRAFT]" in system][0]
    assert "【我的人设 / 回复偏好】" in draft
    assert "回复要短、专业" in draft


def test_proactive_generation_without_new_message(tmp_path) -> None:
    engine, llm, _cfg = make_engine(tmp_path)
    history = [
        Message(session="Alice", text="我到了", is_me=True, ts=1),
        Message(session="Alice", text="你在哪", is_me=False, ts=2),
        Message(session="Alice", text="我马上下楼", is_me=True, ts=3),
    ]
    analysis = engine.analyze("Alice", history, [], allow_proactive=True)
    assert analysis.ok
    assert analysis.candidates
    draft = [user for system, user in llm.calls if "[DRAFT]" in system][0]
    assert "主动发送" in draft
    assert "不要重复用户最后说过的话" in draft
    assert len([s for s, _u in llm.calls if "[INTENT]" in s]) == 0


def test_without_incoming_and_not_proactive_errors(tmp_path) -> None:
    engine, _llm, _cfg = make_engine(tmp_path)
    history = [Message(session="Alice", text="在吗", is_me=True, ts=1)]
    analysis = engine.analyze("Alice", history, [])
    assert not analysis.ok
    assert "没有可回复的消息" in analysis.error

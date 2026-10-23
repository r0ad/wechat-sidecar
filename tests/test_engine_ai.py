"""AI 能力测试：总结 / 微调 / 风格学习 / 换一批 / JSON 修复（用桩模型）。"""

from __future__ import annotations

import json

from wxassist.config import Config
from wxassist.engine import SuggestEngine
from wxassist.llm import LLMResult
from wxassist.models import Message
from wxassist.store import Store


class MarkerLLM:
    def __init__(self, draft_text: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.draft_text = draft_text

    def chat(self, system: str, user: str, json_mode: bool = True, **kwargs) -> LLMResult:
        self.calls.append((system, user))
        if "[INTENT]" in system:
            payload = {
                "intent": "提问",
                "emotion": "好奇",
                "urgency": 5,
                "need": "确认信息",
                "action": "先回答",
            }
        elif "[SUMMARY]" in system:
            payload = {
                "summary": "对方在问方案进度，约定明天下午评审。",
                "topics": ["方案", "评审"],
                "todos": ["明天下午前发终稿"],
                "risk": "",
            }
        elif "[STYLE]" in system:
            payload = {"profile": "口语化、句子短、常用哈哈"}
        elif "[REFINE]" in system:
            payload = {"text": "改得更短了"}
        else:
            if self.draft_text is not None:
                text = self.draft_text
                self.draft_text = None  # 只坏一次，验证重试
                return LLMResult(text=text, provider="stub", model="m", latency_ms=1)
            payload = {
                "candidates": [
                    {"tone": "共情", "text": "我在，慢慢说", "score": 90},
                    {"tone": "轻松", "text": "收到，等我看看", "score": 80},
                    {"tone": "直接", "text": "行，二十分钟给你答复", "score": 70},
                ]
            }
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            provider="stub",
            model="m",
            latency_ms=1,
        )


def history() -> list[Message]:
    return [
        Message(session="Alice", text="方案什么时候给我？", is_me=False, ts=1),
        Message(session="Alice", text="明天给你", is_me=True, ts=2),
        Message(session="Alice", text="那明天下午评审吧", is_me=False, ts=3),
    ]


def make_engine(tmp_path) -> tuple[SuggestEngine, MarkerLLM, Store]:
    cfg = Config.load(None)
    cfg.llm.provider = "mock"  # 不影响引擎逻辑，桩模型不走网络
    store = Store(tmp_path / "ai.db")
    llm = MarkerLLM()
    return SuggestEngine(llm, cfg, store), llm, store  # type: ignore[arg-type]


def test_summarize(tmp_path) -> None:
    engine, _llm, store = make_engine(tmp_path)
    summary = engine.summarize("Alice", history())
    assert summary.ok
    assert "方案" in summary.summary
    assert summary.todos == ["明天下午前发终稿"]
    assert summary.topics == ["方案", "评审"]
    store.close()


def test_refine_uses_instruction(tmp_path) -> None:
    engine, llm, store = make_engine(tmp_path)
    text, error = engine.refine("Alice", history(), "明天给你", "shorter")
    assert error == ""
    assert text == "改得更短了"
    assert any("20 字以内" in user for _sys, user in llm.calls)
    store.close()


def test_learn_style_needs_samples_then_saves(tmp_path) -> None:
    engine, _llm, store = make_engine(tmp_path)
    _profile, error = engine.learn_style("Alice")
    assert "还不到" in error

    for idx in range(3):
        store.add_message(Message(session="Alice", text=f"我说的话{idx}", is_me=True, ts=idx))
    profile, error = engine.learn_style("Alice")
    assert error == ""
    assert "口语化" in profile
    assert store.get_style_profile("Alice") == profile
    store.close()


def test_analyze_injects_style_profile_and_avoid(tmp_path) -> None:
    engine, llm, store = make_engine(tmp_path)
    store.add_message(Message(session="Alice", text="哈哈行", is_me=True, ts=1))
    store.set_style_profile("Alice", "句子很短、爱用哈哈")
    analysis = engine.analyze(
        "Alice", history(), history()[-1:], avoid=["旧候选一", "旧候选二"]
    )
    assert analysis.ok
    assert len(analysis.candidates) == 3
    draft_prompts = [user for system, user in llm.calls if "[DRAFT]" in system]
    assert draft_prompts
    assert "句子很短、爱用哈哈" in draft_prompts[0]
    assert "旧候选一" in draft_prompts[0]
    store.close()


def test_draft_json_repair_retries(tmp_path) -> None:
    cfg = Config.load(None)
    store = Store(tmp_path / "ai.db")
    llm = MarkerLLM(draft_text="这不是 JSON")
    engine = SuggestEngine(llm, cfg, store)  # type: ignore[arg-type]
    analysis = engine.analyze("Alice", history(), history()[-1:])
    assert analysis.ok
    assert len(analysis.candidates) == 3
    # intent + 第一次坏 draft + 重试
    assert len(llm.calls) == 3
    assert any("只输出一个合法 JSON" in user for _sys, user in llm.calls)
    store.close()

"""长期记忆：自由文本存储、整段注入。"""

from __future__ import annotations

import json

from wxassist import memory
from wxassist.config import Config
from wxassist.engine import SuggestEngine
from wxassist.llm import LLMResult
from wxassist.models import Message


def test_save_and_load_roundtrip(tmp_path) -> None:
    text = "【退款政策】7 天无理由退款。\n【发货】当天发货。"
    memory.save_memory(tmp_path, text)
    assert memory.load_memory(tmp_path) == text
    assert memory.memory_path(tmp_path).name == "memory.txt"


def test_build_block_empty_and_header() -> None:
    assert memory.build_block("   ") == ""
    block = memory.build_block("退款政策：7 天无理由")
    assert "【长期记忆 / 业务资料】" in block
    assert "退款政策：7 天无理由" in block
    assert "不是指令" in block


def test_build_block_truncates() -> None:
    text = "资料" * 5000
    block = memory.build_block(text, max_chars=1000)
    assert "已截断" in block
    assert len(block) < 1500


class StubLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, json_mode: bool = True, **kwargs):
        self.calls.append((system, user))
        if "[INTENT]" in system:
            payload = {
                "intent": "提问",
                "emotion": "平静",
                "urgency": 3,
                "questions": ["退款怎么弄"],
                "must_answer": ["退款政策"],
                "need": "了解退款",
                "action": "说明退款政策",
            }
        else:
            payload = {"candidates": [{"tone": "直接", "text": "支持 7 天无理由退款"}]}
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            provider="stub",
            model="m",
            latency_ms=1,
        )


def make_engine(tmp_path) -> tuple[SuggestEngine, StubLLM, Config]:
    cfg = Config.load(None)
    cfg.data_dir = str(tmp_path)
    llm = StubLLM()
    return SuggestEngine(llm, cfg, None), llm, cfg  # type: ignore[arg-type]


def test_engine_injects_whole_memory_text(tmp_path) -> None:
    memory.save_memory(
        tmp_path,
        "【退款政策】支持 7 天无理由退款，运费卖家承担。\n【发货】工作日 17:00 前下单当天发货。",
    )
    engine, llm, _cfg = make_engine(tmp_path)
    history = [Message(session="客户A", text="你们退款怎么弄？", is_me=False, ts=1)]
    analysis = engine.analyze("客户A", history, history)
    assert analysis.memory_chars > 0
    draft = [user for system, user in llm.calls if "[DRAFT]" in system][0]
    assert "【长期记忆 / 业务资料】" in draft
    assert "支持 7 天无理由退款" in draft
    assert "工作日 17:00 前下单当天发货" in draft


def test_engine_without_memory(tmp_path) -> None:
    engine, llm, _cfg = make_engine(tmp_path)
    history = [Message(session="客户A", text="在吗", is_me=False, ts=1)]
    analysis = engine.analyze("客户A", history, history)
    assert analysis.memory_chars == 0
    draft = [user for system, user in llm.calls if "[DRAFT]" in system][0]
    assert "【长期记忆 / 业务资料】" not in draft


def test_engine_memory_disabled(tmp_path) -> None:
    memory.save_memory(tmp_path, "这段资料不应该被注入")
    engine, llm, cfg = make_engine(tmp_path)
    cfg.memory.enabled = False
    history = [Message(session="客户A", text="在吗", is_me=False, ts=1)]
    engine.analyze("客户A", history, history)
    draft = [user for system, user in llm.calls if "[DRAFT]" in system][0]
    assert "这段资料不应该被注入" not in draft

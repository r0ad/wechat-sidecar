"""长期记忆：存储、召回、注入、导入导出。"""

from __future__ import annotations

import json

from wxassist.config import Config
from wxassist.memory import export_payload, format_block, import_payload, retrieve
from wxassist.models import MemoryEntry
from wxassist.store import Store


def make_entry(entry_id: int, **kwargs) -> MemoryEntry:
    base = dict(
        id=entry_id,
        scope="global",
        session="",
        kind="qa",
        title="退款政策",
        content="支持 7 天无理由退款，需保持商品完好。",
        keywords="退款,退货",
        enabled=True,
        pinned=False,
        weight=1.0,
    )
    base.update(kwargs)
    return MemoryEntry(**base)


def test_store_memory_crud_and_scope(tmp_path) -> None:
    store = Store(tmp_path / "m.db")
    global_id = store.add_memory(content="全局知识", title="全局", keywords="全局")
    session_id = store.add_memory(
        content="会话知识", scope="session", session="Alice", title="会话"
    )
    store.add_memory(content="别人的会话", scope="session", session="Bob", title="其他人")

    alice = store.list_memories("Alice")
    titles = {entry.title for entry in alice}
    assert "全局" in titles and "会话" in titles and "其他人" not in titles
    assert store.count_memories("Alice") == 2
    assert store.count_memories("Bob") == 2

    store.update_memory(session_id, content="改过的会话知识")
    assert any(entry.content == "改过的会话知识" for entry in store.list_memories("Alice"))
    store.set_memory_enabled(global_id, False)
    assert store.count_memories("Alice", include_disabled=False) == 1
    store.delete_memory(global_id)
    assert store.count_memories("Alice") == 1
    store.close()


def test_retrieve_matches_keywords_and_pinned_always() -> None:
    cfg = Config.load(None).memory
    entries = [
        make_entry(1),
        make_entry(2, title="发货时间", content="下午 5 点前下单当天发货", keywords="发货,物流"),
        make_entry(3, title="核心说明", content="这是一条置顶说明", pinned=True),
    ]
    selected = retrieve(entries, "你们退款怎么办？", cfg)
    ids = [entry.id for entry in selected]
    assert 1 in ids  # 命中退款
    assert 3 in ids  # 置顶总是带上
    assert 2 not in ids

    selected2 = retrieve(entries, "什么时候发货", cfg)
    ids2 = [entry.id for entry in selected2]
    assert 2 in ids2 and 1 not in ids2


def test_retrieve_respects_budget_and_disabled() -> None:
    cfg = Config.load(None).memory
    cfg.budget_chars = 40
    entries = [
        make_entry(1, pinned=True, content="置顶内容" * 5),
        make_entry(2, content="第二条" * 50, keywords="退款"),
        make_entry(3, enabled=False, title="停用", keywords="退款"),
    ]
    selected = retrieve(entries, "退款", cfg)
    assert 3 not in [entry.id for entry in selected]
    assert selected[0].id == 1
    assert len(selected) <= 2


def test_format_block_qa() -> None:
    block = format_block([make_entry(1)], max_entry_chars=500)
    assert "参考资料" in block
    assert "问题：退款政策" in block
    assert "回答：" in block


def test_export_import_roundtrip(tmp_path) -> None:
    source = Store(tmp_path / "a.db")
    source.add_memory(content="知识一", title="标题一", keywords="一")
    source.add_memory(content="知识二", scope="session", session="Alice", title="标题二")
    payload = export_payload(source.list_memories("Alice", include_disabled=True))
    assert payload["version"] == 1
    source.close()

    target = Store(tmp_path / "b.db")
    added, skipped = import_payload(target, payload)
    assert (added, skipped) == (2, 0)
    added, skipped = import_payload(target, payload)
    assert (added, skipped) == (0, 2)
    assert target.count_memories("Alice") == 2
    target.close()


def test_engine_injects_memory_into_prompt(tmp_path) -> None:
    from wxassist.engine import SuggestEngine
    from wxassist.llm import LLMResult
    from wxassist.models import Message

    class StubLLM:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def chat(self, system, user, json_mode=True, **kwargs):
            self.calls.append((system, user))
            if "[INTENT]" in system:
                payload = {"intent": "提问", "emotion": "平静", "urgency": 3,
                           "questions": ["退款怎么弄"], "must_answer": ["退款政策"],
                           "need": "了解退款", "action": "说明退款政策"}
            else:
                payload = {"candidates": [{"tone": "直接", "text": "支持 7 天无理由退款"}]}
            return LLMResult(text=json.dumps(payload, ensure_ascii=False), provider="stub",
                             model="m", latency_ms=1)

    cfg = Config.load(None)
    store = Store(tmp_path / "engine.db")
    store.add_memory(content="支持 7 天无理由退款，需保持商品完好。", title="退款政策", keywords="退款,退货")
    llm = StubLLM()
    engine = SuggestEngine(llm, cfg, store)  # type: ignore[arg-type]
    history = [Message(session="Alice", text="你们退款怎么弄？", is_me=False, ts=1)]
    analysis = engine.analyze("Alice", history, history)
    assert analysis.memory_count == 1
    draft_prompts = [user for system, user in llm.calls if "[DRAFT]" in system]
    assert "【参考资料" in draft_prompts[0]
    assert "支持 7 天无理由退款" in draft_prompts[0]
    store.close()

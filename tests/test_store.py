"""本地存储测试。"""

from __future__ import annotations

from wxassist.models import Analysis, Candidate, Message
from wxassist.store import Store


def test_add_and_recent(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    for idx in range(5):
        store.add_message(Message(session="Alice", text=f"m{idx}", is_me=idx % 2 == 1, ts=idx))
    recent = store.recent_messages("Alice", 3)
    assert [m.text for m in recent] == ["m2", "m3", "m4"]
    assert store.sessions() == ["Alice"]
    store.close()


def test_my_recent_messages(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    store.add_message(Message(session="Alice", text="对方1", is_me=False, ts=1))
    store.add_message(Message(session="Alice", text="我说的", is_me=True, ts=2))
    store.add_message(Message(session="Alice", text="对方2", is_me=False, ts=3))
    assert store.my_recent_messages("Alice") == ["我说的"]
    store.close()


def test_add_analysis(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    analysis = Analysis(
        session="Alice",
        incoming=[Message(session="Alice", text="在吗", is_me=False, ts=1)],
        intent="闲聊",
        candidates=[Candidate(tone="共情", text="在的", score=90)],
    )
    store.add_analysis(analysis)
    store.close()


def test_reopen_persists(tmp_path) -> None:
    path = tmp_path / "t.db"
    store = Store(path)
    store.add_message(Message(session="Bob", text="hi", is_me=False, ts=1))
    store.close()
    store = Store(path)
    assert [m.text for m in store.recent_messages("Bob")] == ["hi"]
    store.close()

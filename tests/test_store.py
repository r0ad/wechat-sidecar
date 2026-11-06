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


def test_add_messages_dedup_and_count(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    message = Message(session="A", text="在吗", is_me=False, ts=1)
    assert store.add_messages_dedup([message, message]) == 1
    assert store.count_messages("A") == 1
    same_again = Message(session="A", text="在吗", is_me=False, ts=9)
    assert store.add_messages_dedup([same_again]) == 0
    assert store.add_messages_dedup([Message(session="A", text="新的", is_me=True, ts=10)]) == 1
    assert store.count_messages("A") == 2
    store.close()


def test_seq_ordering_with_older_context(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    store.add_message(Message(session="A", text="新1", is_me=False, ts=100))
    store.add_message(Message(session="A", text="新2", is_me=False, ts=101))
    added = store.add_messages_dedup(
        [
            Message(session="A", text="旧1", is_me=False, ts=50),
            Message(session="A", text="旧2", is_me=False, ts=51),
        ],
        seq_base=90,
    )
    assert added == 2
    assert [m.text for m in store.recent_messages("A", 10)] == ["旧1", "旧2", "新1", "新2"]
    assert store.count_messages("A") == 4
    store.close()


def test_purge_garbled_messages_keeps_real_text(tmp_path) -> None:
    """老库里图片乱码的存量记录要能清掉，真实消息（含 URL）不能动。"""
    store = Store(tmp_path / "t.db")
    store.add_messages(
        [
            Message(session="A", text="合并相似的问题", is_me=False, ts=1),
            Message(session="A", text="https://r0ad.host/", is_me=True, ts=2),
            Message(session="A", text="OK", is_me=False, ts=3),
            Message(
                session="A",
                text="TE088n m877a, e38i8nM\nHEERA8\nORFRA.",
                is_me=False,
                ts=4,
            ),
            Message(session="A", text="Fe088nm8TTa, e3aiBnM\nBEAIRHATB9", is_me=False, ts=5),
        ]
    )
    assert store.purge_garbled_messages() == 2
    assert [m.text for m in store.recent_messages("A", 10)] == [
        "合并相似的问题",
        "https://r0ad.host/",
        "OK",
    ]
    assert store.purge_garbled_messages() == 0  # 再跑一次是幂等的
    store.close()


def test_purge_non_dialogue_messages(tmp_path) -> None:
    """老库里的语音时长/文件卡/文章卡要清掉，真实消息不能动。"""
    store = Store(tmp_path / "t.db")
    store.add_messages(
        [
            Message(session="A", text="好的", is_me=False, ts=1),
            Message(session="A", text='10"', is_me=False, ts=2),
            Message(session="A", text="[图片]", is_me=False, ts=3),
            Message(
                session="A",
                text="微信聊天监控与AI回复助手-\n调研报告.md\nMD\n29.2K",
                is_me=False,
                ts=4,
            ),
            Message(session="A", text="芯和半导体Xpeedic\n公众号名片", is_me=False, ts=5),
            Message(session="A", text="会议纪要.docx 已经发你了", is_me=False, ts=6),
        ]
    )
    assert store.purge_non_dialogue_messages() == 4
    assert [m.text for m in store.recent_messages("A", 10)] == [
        "好的",
        "会议纪要.docx 已经发你了",
    ]
    assert store.purge_non_dialogue_messages() == 0
    store.close()


def test_purge_quote_echoes(tmp_path) -> None:
    """引用块回声（内容与更早消息重复）清掉；正常消息和普通冒号文本保留。"""
    store = Store(tmp_path / "t.db")
    store.add_messages(
        [
            Message(session="A", text="经济师是怎么个说法呢", is_me=False, ts=1),
            Message(session="A", text="随遇而安：经济师是怎么个说法呢", is_me=False, ts=2),
            Message(session="A", text="注意：明天带伞", is_me=False, ts=3),
            Message(session="A", text="164:30", is_me=False, ts=4),
        ]
    )
    assert store.purge_quote_echoes() == 1
    texts = [m.text for m in store.recent_messages("A", 10)]
    assert "随遇而安：经济师是怎么个说法呢" not in texts
    assert "注意：明天带伞" in texts
    store.close()


def test_purge_quote_echoes_keeps_multiline_quote_with_reply(tmp_path) -> None:
    """「引用条 + 回复」是多行气泡，回复是真内容，清理时不能整条删。"""
    store = Store(tmp_path / "t.db")
    store.add_messages(
        [
            Message(session="A", text="数字游民吗？", is_me=False, ts=1),
            Message(
                session="A",
                text="冯智：数字游民吗？\n服药时最好不要吃柚子",
                is_me=False,
                ts=2,
            ),
        ]
    )
    assert store.purge_quote_echoes() == 0
    texts = [m.text for m in store.recent_messages("A", 10)]
    assert "冯智：数字游民吗？\n服药时最好不要吃柚子" in texts
    store.close()

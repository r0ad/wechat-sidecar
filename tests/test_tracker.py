"""跨帧去重：新消息识别。"""

from __future__ import annotations

from wxassist.models import Bubble
from wxassist.tracker import SessionTracker


def bubble(text: str, is_me: bool = False) -> Bubble:
    return Bubble(text=text, is_me=is_me, x0=0, y0=0, x1=10, y1=10)


def test_first_frame_returns_all() -> None:
    tracker = SessionTracker("Alice")
    new = tracker.ingest([bubble("你好"), bubble("在吗")])
    assert [m.text for m in new] == ["你好", "在吗"]
    assert all(m.session == "Alice" for m in new)


def test_append_detects_only_new() -> None:
    tracker = SessionTracker("Alice")
    tracker.ingest([bubble("你好")])
    new = tracker.ingest([bubble("你好"), bubble("在吗")])
    assert [m.text for m in new] == ["在吗"]


def test_duplicate_text_appended_once() -> None:
    tracker = SessionTracker("Alice")
    tracker.ingest([bubble("哈哈"), bubble("好的")])
    new = tracker.ingest([bubble("哈哈"), bubble("好的"), bubble("好的")])
    assert [m.text for m in new] == ["好的"]


def test_scroll_far_falls_back_to_multiset_diff() -> None:
    tracker = SessionTracker("Alice")
    tracker.ingest([bubble("A"), bubble("B")])
    # 画面完全换了一批（往上滚了很多），锚点 B 不在了
    new = tracker.ingest([bubble("C"), bubble("D")])
    assert [m.text for m in new] == ["C", "D"]


def test_own_messages_are_marked() -> None:
    tracker = SessionTracker("Alice")
    new = tracker.ingest([bubble("我发的", is_me=True)])
    assert new[0].is_me is True


def test_switch_session_does_not_emit_history() -> None:
    tracker = SessionTracker("Alice")
    tracker.ingest([bubble("历史")])
    tracker.switch_session("Bob", [bubble("Bob 的历史")])
    assert tracker.ingest([bubble("Bob 的历史")]) == []
    assert tracker.session == "Bob"

"""跨帧对齐：从每帧解析结果里挑出「新消息」。

OCR 每帧都会把整个可见聊天区重读一遍，消息会重复、滚动、上下抖动。
做法：用上一帧最后一条消息在当前帧里做锚点，锚点之后的才是新消息；
锚点找不到（滚动太远/被删除）时退回多重集差集。
"""

from __future__ import annotations

from collections import Counter

from .models import Bubble, Message


class SessionTracker:
    def __init__(self, session: str) -> None:
        self.session = session
        self.last: list[Bubble] = []

    def switch_session(self, session: str, bubbles: list[Bubble] | None = None) -> None:
        """切换会话：当前画面视为历史基线，不产生新消息。"""
        self.session = session
        self.last = list(bubbles or [])

    def ingest(self, bubbles: list[Bubble], ts: float | None = None) -> list[Message]:
        new_bubbles = self._diff(self.last, bubbles)
        self.last = list(bubbles)
        out: list[Message] = []
        for b in new_bubbles:
            out.append(
                Message(
                    session=self.session,
                    text=b.text,
                    is_me=b.is_me,
                    ts=ts or 0.0,
                    sender=b.sender,
                )
            )
        return out

    # ---------- 内部 ----------

    @staticmethod
    def _diff(prev: list[Bubble], cur: list[Bubble]) -> list[Bubble]:
        if not prev:
            return list(cur)
        prev_keys = [b.digest() for b in prev]
        cur_keys = [b.digest() for b in cur]
        anchor = prev_keys[-1]
        candidates = [i for i, key in enumerate(cur_keys) if key == anchor]
        if candidates:
            # 同文重复时可能有多个锚点：选「前序也对得上」的最早那个，
            # 这样 prev=[A,B] cur=[A,B,B] 会得到新消息 [B] 而不是 []。
            tail = prev_keys[-3:]
            for idx in candidates:
                start = idx - len(tail) + 1
                if start >= 0:
                    matched = cur_keys[start : idx + 1] == tail
                else:
                    matched = cur_keys[: idx + 1] == tail[-(idx + 1) :]
                if matched:
                    return list(cur[idx + 1 :])
            return list(cur[candidates[-1] + 1 :])
        # 锚点丢失：多重集差集
        remaining = Counter(prev_keys)
        new: list[Bubble] = []
        for bubble in cur:
            key = bubble.digest()
            if remaining[key] > 0:
                remaining[key] -= 1
            else:
                new.append(bubble)
        return new

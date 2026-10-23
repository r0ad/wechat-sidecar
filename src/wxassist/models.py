"""领域模型：OCR 行 → 气泡 → 消息 → 建议。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class OcrLine:
    """OCR 识别出的一行文字（坐标基于截图像素）。"""

    text: str
    score: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> int:
        return max(1, self.y1 - self.y0)


@dataclass
class Bubble:
    """一条消息气泡（可能由多行 OCR 合并而来）。"""

    text: str
    is_me: bool
    x0: int
    y0: int
    x1: int
    y1: int
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def digest(self) -> str:
        raw = f"{self.is_me}|{self.text}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class Message:
    """已入库的一条消息。"""

    session: str
    text: str
    is_me: bool
    ts: float = field(default_factory=time.time)
    sender: str = ""

    @property
    def role(self) -> str:
        return "assistant" if self.is_me else "user"

    def digest(self) -> str:
        raw = f"{self.session}|{self.is_me}|{self.text}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class Candidate:
    tone: str
    text: str
    score: float = 0.0
    selected: bool = False


@dataclass
class Analysis:
    """一次分析的结果，直接喂给 UI。"""

    session: str
    incoming: list[Message] = field(default_factory=list)
    intent: str = ""
    emotion: str = ""
    urgency: int = 0
    need: str = ""
    action: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    warning: str = ""
    error: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    context_count: int = 0
    memory_chars: int = 0
    provider: str = ""
    model: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.candidates)


@dataclass
class SessionSummary:
    """会话摘要（AI 生成）。"""

    session: str
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    risk: str = ""
    error: str = ""
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.summary)


@dataclass
class Frame:
    """一次截屏的产物（仅内存，不落盘）。"""

    image: object  # numpy.ndarray，RGB
    rect: tuple[int, int, int, int]
    ts: float = field(default_factory=time.time)

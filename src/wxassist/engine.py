"""建议引擎：意图判断 → 候选起草 → 过滤排序。

每次分析 = 2 次模型调用（判断 + 起草），只在「对方来新消息」时触发。
"""

from __future__ import annotations

import re
import time

from .config import Config
from .json_utils import extract_json
from .llm import LLMClient, LLMError
from .models import Analysis, Candidate, Message
from .privacy import redact_messages, sensitive_topics
from .prompts import (
    DRAFT_SYSTEM,
    INTENT_SYSTEM,
    build_draft_user,
    build_intent_user,
    format_history,
    looks_like_injection,
)
from .store import Store

MAX_CANDIDATE_CHARS = 80


class SuggestEngine:
    def __init__(self, llm: LLMClient, cfg: Config, store: Store | None = None) -> None:
        self.llm = llm
        self.cfg = cfg
        self.store = store

    # ---------- 对外接口 ----------

    def analyze(self, session: str, history: list[Message], incoming: list[Message]) -> Analysis:
        started = time.perf_counter()
        analysis = Analysis(session=session, incoming=list(incoming))
        analysis.provider = self.cfg.llm.provider
        analysis.model = self.cfg.llm.model

        if not incoming:
            analysis.error = "没有对方的新消息"
            analysis.latency_ms = _ms(started)
            return analysis

        context = history[-max(1, self.cfg.privacy.max_context_messages) :]
        if self.cfg.privacy.redact_before_send:
            context = redact_messages(context)
        history_text = format_history(context)
        relationship = session

        intent: dict = {}
        try:
            result = self.llm.chat(
                INTENT_SYSTEM, build_intent_user(history_text, relationship), json_mode=True
            )
            parsed = extract_json(result.text)
            if isinstance(parsed, dict):
                intent = parsed
        except LLMError as exc:
            analysis.error = str(exc)
            analysis.latency_ms = _ms(started)
            return analysis

        style = self.store.my_recent_messages(session, 8) if self.store else []
        try:
            draft = self.llm.chat(
                DRAFT_SYSTEM,
                build_draft_user(history_text, intent, style, relationship),
                json_mode=True,
            )
        except LLMError as exc:
            analysis.error = str(exc)
            analysis.latency_ms = _ms(started)
            return analysis

        candidates = self._parse_candidates(draft.text)
        candidates = self._sanitize(candidates, context)
        analysis.candidates = candidates
        analysis.intent = str(intent.get("intent", ""))
        analysis.emotion = str(intent.get("emotion", ""))
        try:
            analysis.urgency = int(intent.get("urgency", 0))
        except (TypeError, ValueError):
            analysis.urgency = 0
        analysis.need = str(intent.get("need", ""))
        analysis.action = str(intent.get("action", ""))
        analysis.warning = self._build_warning(context, candidates)
        analysis.latency_ms = _ms(started)

        if not candidates:
            analysis.error = "模型没有给出可用候选，请重试"

        if self.store is not None and candidates:
            try:
                self.store.add_analysis(analysis)
            except Exception:
                pass
        return analysis

    # ---------- 内部 ----------

    def _parse_candidates(self, raw: str) -> list[Candidate]:
        parsed = extract_json(raw)
        items: list = []
        if isinstance(parsed, dict):
            for key in ("candidates", "replies", "items"):
                if isinstance(parsed.get(key), list):
                    items = parsed[key]
                    break
        elif isinstance(parsed, list):
            items = parsed
        out: list[Candidate] = []
        for item in items[:6]:
            if isinstance(item, str):
                out.append(Candidate(tone="", text=item.strip(), score=0.0))
                continue
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("reply") or "").strip()
            if not text:
                continue
            try:
                score = float(item.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0.0
            out.append(Candidate(tone=str(item.get("tone", "")).strip(), text=text, score=score))
        return out

    def _sanitize(self, candidates: list[Candidate], context: list[Message]) -> list[Candidate]:
        recent = {_normalize(m.text) for m in context[-6:]}
        seen: set[str] = set()
        cleaned: list[Candidate] = []
        for candidate in candidates:
            text = _clean_text(candidate.text)
            if not text or len(text) > MAX_CANDIDATE_CHARS:
                continue
            key = _normalize(text)
            if not key or key in seen:
                continue
            # 候选如果只是复述最近说过的话，丢掉
            if key in recent:
                continue
            if looks_like_injection(text):
                continue
            seen.add(key)
            cleaned.append(Candidate(tone=candidate.tone, text=text, score=candidate.score))
        if not cleaned:
            return []
        # 分数归一化到 0-100 并降序；全为 0 时保持模型顺序
        if any(c.score > 0 for c in cleaned):
            top = max(c.score for c in cleaned)
            if top > 100:
                for c in cleaned:
                    c.score = round(c.score / top * 100, 1)
            cleaned.sort(key=lambda c: c.score, reverse=True)
        for idx, candidate in enumerate(cleaned[:3]):
            candidate.selected = idx == 0
        return cleaned[:3]

    def _build_warning(self, context: list[Message], candidates: list[Candidate]) -> str:
        incoming_text = "\n".join(m.text for m in context if not m.is_me)
        hits = sensitive_topics(incoming_text)
        if any(looks_like_injection(m.text) for m in context if not m.is_me):
            hits.append("可疑指令")
        if not hits:
            return ""
        return f"涉及敏感内容（{'、'.join(hits)}），发送前请仔细核对，不要直接透露验证码/密码/转账"


def _clean_text(text: str) -> str:
    text = text.strip().strip("\"'“”")
    text = re.sub(r"^[\-•\d.、)）\s]+", "", text)  # 去掉列表前缀
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～…\"'“”‘’]+", "", text or "").lower()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)

"""建议引擎：真实大模型调用（意图判断 → 候选起草 → 过滤排序），
外加「换一批 / 微调 / 会话总结 / 风格学习」四个 AI 能力。
"""

from __future__ import annotations

import re
import time

from .config import Config
from .json_utils import extract_json
from .llm import LLMClient, LLMError
from .models import Analysis, Candidate, Message, SessionSummary
from .privacy import redact_messages, sensitive_topics
from .prompts import (
    DRAFT_SYSTEM,
    INTENT_SYSTEM,
    REFINE_SYSTEM,
    STYLE_SYSTEM,
    SUMMARY_SYSTEM,
    build_draft_user,
    build_intent_user,
    build_refine_user,
    build_style_user,
    build_summary_user,
    format_history,
    looks_like_injection,
)
from .store import Store

MAX_CANDIDATE_CHARS = 80
MIN_STYLE_SAMPLES = 3

REFINE_INSTRUCTIONS: dict[str, str] = {
    "shorter": "压缩到 20 字以内，只保留最关键的一句，语气不变",
    "formal": "改得更正式、礼貌一些，去掉语气词和口头禅",
    "warmer": "改得更温柔、更有共情，先回应对方的情绪再给信息",
    "humorous": "改得轻松幽默一点，可以轻微自嘲，但不要油腻、不要冒犯",
}


class SuggestEngine:
    def __init__(self, llm: LLMClient, cfg: Config, store: Store | None = None) -> None:
        self.llm = llm
        self.cfg = cfg
        self.store = store

    # ---------- 生成候选 ----------

    def analyze(
        self,
        session: str,
        history: list[Message],
        incoming: list[Message],
        avoid: list[str] | None = None,
    ) -> Analysis:
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
        style_profile = self.store.get_style_profile(session) if self.store else ""

        try:
            intent, tokens_in, tokens_out = self._intent(history_text, relationship)
        except LLMError as exc:
            analysis.error = str(exc)
            analysis.latency_ms = _ms(started)
            return analysis
        analysis.tokens_in += tokens_in
        analysis.tokens_out += tokens_out

        style = self.store.my_recent_messages(session, 8) if self.store else []
        draft_user = build_draft_user(
            history_text,
            intent,
            style,
            relationship,
            style_profile=style_profile,
            avoid=avoid,
        )
        try:
            draft, tokens = self._chat_json(DRAFT_SYSTEM, draft_user, max_tokens=800)
        except LLMError as exc:
            analysis.error = str(exc)
            analysis.latency_ms = _ms(started)
            return analysis
        analysis.tokens_in += tokens[0]
        analysis.tokens_out += tokens[1]
        candidates = self._sanitize(self._parse_candidates(draft), context)
        analysis.candidates = candidates

        analysis.intent = str(intent.get("intent", ""))
        analysis.emotion = str(intent.get("emotion", ""))
        try:
            analysis.urgency = int(intent.get("urgency", 0))
        except (TypeError, ValueError):
            analysis.urgency = 0
        analysis.need = str(intent.get("need", ""))
        analysis.action = str(intent.get("action", ""))
        analysis.warning = self._build_warning(incoming)
        analysis.latency_ms = _ms(started)

        if not candidates:
            analysis.error = "模型没有给出可用候选，请点「重新生成」再试一次"

        if self.store is not None and candidates:
            try:
                self.store.add_analysis(analysis)
            except Exception:
                pass
        return analysis

    def _intent(self, history_text: str, relationship: str) -> tuple[dict, int, int]:
        try:
            result = self.llm.chat(
                INTENT_SYSTEM,
                build_intent_user(history_text, relationship),
                json_mode=True,
                max_tokens=300,
            )
        except LLMError:
            raise
        parsed = extract_json(result.text)
        if isinstance(parsed, dict):
            return parsed, result.prompt_tokens, result.completion_tokens
        return {}, result.prompt_tokens, result.completion_tokens

    def _chat_json(self, system: str, user: str, max_tokens: int = 800) -> tuple[str, tuple[int, int]]:
        """调用并返回文本；解析失败时重试一次，要求只输出 JSON。"""
        result = self.llm.chat(system, user, json_mode=True, max_tokens=max_tokens)
        tokens = (result.prompt_tokens, result.completion_tokens)
        if extract_json(result.text) is not None:
            return result.text, tokens
        retry = self.llm.chat(
            system,
            user + "\n\n注意：上一次输出无法解析。只输出一个合法 JSON 对象，不要任何解释或代码块。",
            json_mode=True,
            max_tokens=max_tokens,
        )
        return retry.text, (
            tokens[0] + retry.prompt_tokens,
            tokens[1] + retry.completion_tokens,
        )

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
        recent = {_normalize(m.text) for m in context[-8:]}
        seen: set[str] = set()
        cleaned: list[Candidate] = []
        for candidate in candidates:
            text = _clean_text(candidate.text)
            if not text or len(text) > MAX_CANDIDATE_CHARS:
                continue
            key = _normalize(text)
            if not key or key in seen:
                continue
            if key in recent:
                continue
            if looks_like_injection(text):
                continue
            seen.add(key)
            cleaned.append(Candidate(tone=candidate.tone, text=text, score=candidate.score))
        if not cleaned:
            return []
        if any(c.score > 0 for c in cleaned):
            top = max(c.score for c in cleaned)
            if top > 100:
                for c in cleaned:
                    c.score = round(c.score / top * 100, 1)
            cleaned.sort(key=lambda c: c.score, reverse=True)
        for idx, candidate in enumerate(cleaned[:3]):
            candidate.selected = idx == 0
        return cleaned[:3]

    def _build_warning(self, incoming: list[Message]) -> str:
        incoming_text = "\n".join(m.text for m in incoming)
        hits = sensitive_topics(incoming_text)
        if any(looks_like_injection(m.text) for m in incoming):
            hits.append("可疑指令")
        if not hits:
            return ""
        return f"涉及敏感内容（{'、'.join(hits)}），发送前请仔细核对，不要直接透露验证码/密码/转账"

    # ---------- 微调 ----------

    def refine(
        self, session: str, history: list[Message], original: str, style: str
    ) -> tuple[str, str]:
        """按风格改写一条候选；返回 (新文本, 错误)。"""
        instruction = REFINE_INSTRUCTIONS.get(style, style)
        context = history[-max(1, self.cfg.privacy.max_context_messages) :]
        if self.cfg.privacy.redact_before_send:
            context = redact_messages(context)
        try:
            result = self.llm.chat(
                REFINE_SYSTEM,
                build_refine_user(format_history(context), original, instruction, session),
                json_mode=True,
                max_tokens=300,
                temperature=0.6,
            )
        except LLMError as exc:
            return "", str(exc)
        parsed = extract_json(result.text)
        if isinstance(parsed, dict):
            text = _clean_text(str(parsed.get("text", "")))
            if text:
                return text, ""
        return "", "模型没有返回可用的改写"

    # ---------- 会话总结 ----------

    def summarize(self, session: str, history: list[Message]) -> SessionSummary:
        summary = SessionSummary(session=session)
        started = time.perf_counter()
        if not history:
            summary.error = "还没有可总结的消息"
            return summary
        context = history[-40:]
        if self.cfg.privacy.redact_before_send:
            context = redact_messages(context)
        try:
            result = self.llm.chat(
                SUMMARY_SYSTEM,
                build_summary_user(format_history(context), session),
                json_mode=True,
                max_tokens=900,
            )
        except LLMError as exc:
            summary.error = str(exc)
            summary.latency_ms = _ms(started)
            return summary
        parsed = extract_json(result.text)
        if not isinstance(parsed, dict):
            summary.error = "模型返回格式异常，请重试"
            summary.latency_ms = _ms(started)
            return summary
        summary.summary = str(parsed.get("summary", "")).strip()
        summary.topics = [str(x) for x in (parsed.get("topics") or []) if str(x).strip()][:6]
        summary.todos = [str(x) for x in (parsed.get("todos") or []) if str(x).strip()][:6]
        summary.risk = str(parsed.get("risk", "")).strip()
        summary.latency_ms = _ms(started)
        if not summary.summary:
            summary.error = "模型没有返回摘要内容"
        return summary

    # ---------- 风格学习 ----------

    def learn_style(self, session: str) -> tuple[str, str]:
        """用自己历史回复生成风格画像并缓存；返回 (画像, 错误)。"""
        if self.store is None:
            return "", "本地存储不可用"
        samples = self.store.my_recent_messages(session, 20)
        if len(samples) < MIN_STYLE_SAMPLES:
            return "", f"我在这个会话里说的话还不到 {MIN_STYLE_SAMPLES} 条，先多聊几句再学"
        try:
            result = self.llm.chat(
                STYLE_SYSTEM, build_style_user(samples), json_mode=True, max_tokens=250
            )
        except LLMError as exc:
            return "", str(exc)
        parsed = extract_json(result.text)
        profile = str(parsed.get("profile", "")).strip() if isinstance(parsed, dict) else ""
        if not profile:
            return "", "模型没有返回风格画像"
        self.store.set_style_profile(session, profile)
        return profile, ""


def _clean_text(text: str) -> str:
    text = text.strip().strip("\"'“”")
    text = re.sub(r"^[\-•\d.、)）\s]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~～…\"'“”‘’]+", "", text or "").lower()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)

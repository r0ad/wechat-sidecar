"""大模型客户端：DeepSeek / OpenAI 兼容 / Ollama / Mock。

只发送「当前会话最近若干条文本」，不发送图片、不发送完整历史。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    """可展示给用户的模型错误。"""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    # ---------- 公开接口 ----------

    def chat(self, system: str, user: str, json_mode: bool = True) -> LLMResult:
        provider = (self.cfg.provider or "deepseek").lower()
        if provider == "mock":
            return self._mock(system, user)
        if provider == "ollama":
            return self._openai_compatible(system, user, json_mode, api_key="ollama")
        return self._openai_compatible(system, user, json_mode, api_key=self._require_key())

    # ---------- 实现 ----------

    def _require_key(self) -> str:
        import os

        key = os.environ.get(self.cfg.api_key_env, "") or self.cfg.api_key
        if not key:
            raise LLMError(
                f"未配置 API Key：请设置环境变量 {self.cfg.api_key_env}，"
                f"或改用 mock（--provider mock）先体验流程"
            )
        return key

    def _openai_compatible(
        self, system: str, user: str, json_mode: bool, api_key: str
    ) -> LLMResult:
        base = self.cfg.base_url.rstrip("/")
        url = f"{base}/chat/completions"
        payload: dict = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.cfg.timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = LLMError(f"模型服务暂时不可用（HTTP {resp.status_code}）")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    detail = _short_error(resp)
                    raise LLMError(f"模型调用失败（HTTP {resp.status_code}）：{detail}")
                data = resp.json()
                text = data["choices"][0]["message"]["content"] or ""
                latency = int((time.perf_counter() - started) * 1000)
                return LLMResult(
                    text=text,
                    provider=self.cfg.provider,
                    model=self.cfg.model,
                    latency_ms=latency,
                )
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = LLMError(f"网络或响应异常：{exc}")
                time.sleep(0.8 * (attempt + 1))
        raise last_error or LLMError("模型调用失败")

    # ---------- Mock：无 Key 也能跑通全流程 ----------

    def _mock(self, system: str, user: str) -> LLMResult:
        started = time.perf_counter()
        time.sleep(0.25)  # 模拟网络延迟，方便看加载态
        if "[INTENT]" in system:
            text = json.dumps(_mock_intent(user), ensure_ascii=False)
        else:
            text = json.dumps(_mock_draft(user), ensure_ascii=False)
        latency = int((time.perf_counter() - started) * 1000)
        return LLMResult(text=text, provider="mock", model="mock", latency_ms=latency)


def _last_incoming(user_prompt: str) -> str:
    lines = [ln.strip() for ln in user_prompt.splitlines() if ln.strip()]
    incoming = [ln for ln in lines if ln.startswith("对方：")]
    if not incoming:
        return ""
    return incoming[-1][len("对方：") :].strip()


def _mock_intent(user_prompt: str) -> dict:
    text = _last_incoming(user_prompt)
    if any(word in text for word in ("?", "？", "吗", "怎么", "为什么", "多少")):
        intent, emotion, urgency = "提问", "好奇", 5
    elif any(word in text for word in ("帮", "能不能", "麻烦", "求")):
        intent, emotion, urgency = "请求帮忙", "期待", 6
    elif any(word in text for word in ("烦", "累", "郁闷", "难过", "气")):
        intent, emotion, urgency = "表达情绪", "低落", 4
    else:
        intent, emotion, urgency = "闲聊", "平静", 3
    return {
        "intent": intent,
        "emotion": emotion,
        "urgency": urgency,
        "need": "希望得到回应" if text else "未知",
        "action": "先回应情绪，再给具体答复",
    }


def _mock_draft(user_prompt: str) -> dict:
    text = _last_incoming(user_prompt)
    snippet = text[:18] if text else "你说的"
    candidates = [
        {"tone": "共情", "text": f"我在的，慢慢说，\"{snippet}\"这事我听着呢", "score": 82},
        {"tone": "轻松", "text": "收到收到，等我捋一下，马上回你", "score": 74},
        {"tone": "直接", "text": "行，我先确认一下，十分钟内给你准信", "score": 68},
    ]
    return {"candidates": candidates, "note": "（Mock 模式：配置 API Key 后才是真实模型生成）"}


def _short_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error", {}).get("message", body))[:200]
    except Exception:
        return (resp.text or "")[:200]

"""大模型客户端：DeepSeek / OpenAI 兼容 / Ollama。

- 统一走 OpenAI 兼容的 /chat/completions；
- Key 优先级：环境变量 → DPAPI 本地加密存储 → 配置文件；
- 只发送「当前会话最近若干条文本」，不发送图片、不发送完整历史。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import LLMConfig
from . import secrets_store


class LLMError(RuntimeError):
    """可展示给用户的模型错误。"""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ConnectionResult:
    ok: bool
    message: str
    latency_ms: int = 0
    model: str = ""
    latency_label: str = ""


class LLMClient:
    def __init__(self, cfg: LLMConfig, data_dir: str | Path | None = None) -> None:
        self.cfg = cfg
        self.data_dir = Path(data_dir) if data_dir else None

    # ---------- 配置与 Key ----------

    def update_config(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    def resolve_key(self) -> str:
        provider = (self.cfg.provider or "deepseek").lower()
        if provider == "ollama":
            return "ollama"
        import os

        key = os.environ.get(self.cfg.api_key_env, "") if self.cfg.api_key_env else ""
        if not key and self.data_dir is not None:
            key = secrets_store.load_api_key(self.data_dir, provider)
        if not key:
            key = self.cfg.api_key
        return key

    def has_key(self) -> bool:
        return bool(self.resolve_key())

    # ---------- 调用 ----------

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        provider = (self.cfg.provider or "deepseek").lower()
        if provider == "mock":
            return self._mock(system, user)
        key = self.resolve_key()
        if not key:
            raise LLMError(
                "还没有配置模型：点悬浮窗右上角 ⚙ 填入 DeepSeek API Key，"
                f"或设置环境变量 {self.cfg.api_key_env or 'API_KEY'}"
            )
        payload: dict = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            data = self._post(payload, key)
        except LLMError as exc:
            # 部分 OpenAI 兼容服务不支持 response_format：去掉后重试一次
            if json_mode and "response_format" in str(exc):
                payload.pop("response_format", None)
                data = self._post(payload, key)
            else:
                raise
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型返回格式异常：{exc}") from exc
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            provider=self.cfg.provider,
            model=str(data.get("model") or self.cfg.model),
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    def test_connection(self) -> ConnectionResult:
        """设置页的「测试连接」：发一个最小请求验证 Key / 地址 / 模型。"""
        provider = (self.cfg.provider or "deepseek").lower()
        if provider == "mock":
            return ConnectionResult(ok=True, message="Mock 模式（仅测试用）", model="mock")
        key = self.resolve_key()
        if not key:
            return ConnectionResult(ok=False, message="未填写 API Key")
        started = time.perf_counter()
        try:
            self._post(
                {
                    "model": self.cfg.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 8,
                    "temperature": 0,
                    "stream": False,
                },
                key,
            )
        except LLMError as exc:
            return ConnectionResult(
                ok=False,
                message=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        latency = int((time.perf_counter() - started) * 1000)
        return ConnectionResult(
            ok=True,
            message=f"连接成功 · {self.cfg.model}",
            latency_ms=latency,
            model=self.cfg.model,
        )

    def list_models(self) -> list[str]:
        """尽量从 /models 拉模型列表；失败返回空列表（用户手填即可）。"""
        provider = (self.cfg.provider or "deepseek").lower()
        if provider == "mock":
            return ["mock"]
        key = self.resolve_key()
        if not key:
            return []
        base = self.cfg.base_url.rstrip("/")
        if not base:
            return []
        try:
            with httpx.Client(timeout=8) as client:
                resp = client.get(
                    f"{base}/models", headers={"Authorization": f"Bearer {key}"}
                )
            if resp.status_code >= 400:
                return []
            data = resp.json()
            return [str(item.get("id")) for item in data.get("data", []) if item.get("id")]
        except Exception:
            return []

    # ---------- 内部 ----------

    def _post(self, payload: dict, api_key: str) -> dict:
        base = self.cfg.base_url.rstrip("/")
        if not base:
            raise LLMError("Base URL 为空，请在设置里填写服务地址")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.cfg.timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = LLMError(f"网络异常：{exc}")
                time.sleep(0.8 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = LLMError(f"模型服务暂时不可用（HTTP {resp.status_code}）")
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code in (401, 403):
                raise LLMError("API Key 无效或没有权限，请检查 Key 是否正确")
            if resp.status_code == 404:
                raise LLMError(
                    f"接口 404：请检查 Base URL 是否以 /v1 结尾（当前 {base}）与模型名"
                )
            if resp.status_code >= 400:
                raise LLMError(f"模型调用失败（HTTP {resp.status_code}）：{_short_error(resp)}")
            try:
                return resp.json()
            except ValueError as exc:
                raise LLMError("模型返回的不是 JSON，请检查 Base URL 是否指向 OpenAI 兼容接口") from exc
        raise last_error or LLMError("模型调用失败")

    # ---------- Mock：仅供单元测试 ----------

    def _mock(self, system: str, user: str) -> LLMResult:
        started = time.perf_counter()
        time.sleep(0.02)
        if "[INTENT]" in system:
            text = json.dumps(_mock_intent(user), ensure_ascii=False)
        elif "[SUMMARY]" in system:
            text = json.dumps(_mock_summary(), ensure_ascii=False)
        elif "[STYLE]" in system:
            text = json.dumps({"profile": "口语化、句子短、常用语气词"}, ensure_ascii=False)
        elif "[REFINE]" in system:
            text = json.dumps({"text": "（mock 改写）好的，我看看"}, ensure_ascii=False)
        else:
            text = json.dumps(_mock_draft(user), ensure_ascii=False)
        return LLMResult(
            text=text,
            provider="mock",
            model="mock",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


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
        {"tone": "共情", "text": f"我在的，慢慢说，「{snippet}」这事我听着呢", "score": 82},
        {"tone": "轻松", "text": "收到收到，等我捋一下，马上回你", "score": 74},
        {"tone": "直接", "text": "行，我先确认一下，十分钟内给你准信", "score": 68},
    ]
    return {"candidates": candidates, "note": "mock"}


def _mock_summary() -> dict:
    return {"summary": "mock 摘要", "topics": ["mock"], "todos": ["mock 待办"], "risk": ""}


def _short_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error", {}).get("message", body))[:200]
    except Exception:
        return (resp.text or "")[:200]

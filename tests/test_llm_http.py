"""用本地 HTTP 桩服务验证 OpenAI 兼容请求的真实收发（不调用外部 API）。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wxassist.config import LLMConfig
from wxassist.llm import LLMClient, LLMError


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(  # type: ignore[attr-defined]
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        status = self.server.status  # type: ignore[attr-defined]
        if status != 200:
            payload = {"error": {"message": "invalid api key"}}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
            return
        content = json.dumps({"candidates": [{"tone": "共情", "text": "ok", "score": 90}]})
        payload = {
            "model": "stub-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    server.requests = []  # type: ignore[attr-defined]
    server.status = 200  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()


def make_client(server, api_key: str = "sk-test") -> LLMClient:
    cfg = LLMConfig(
        provider="custom",
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        model="stub-model",
        api_key=api_key,
        api_key_env="WXASSIST_TEST_UNSET_KEY",
        timeout=5,
    )
    return LLMClient(cfg)


def test_chat_request_and_response(stub_server) -> None:
    client = make_client(stub_server)
    result = client.chat("系统提示", "用户消息", json_mode=True)
    assert json.loads(result.text)["candidates"][0]["text"] == "ok"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7
    record = stub_server.requests[0]  # type: ignore[attr-defined]
    assert record["auth"] == "Bearer sk-test"
    assert record["body"]["model"] == "stub-model"
    assert record["body"]["response_format"] == {"type": "json_object"}
    assert record["body"]["messages"][0]["role"] == "system"


def test_chat_without_json_mode_has_no_response_format(stub_server) -> None:
    client = make_client(stub_server)
    client.chat("sys", "user", json_mode=False)
    assert "response_format" not in stub_server.requests[0]["body"]  # type: ignore[attr-defined]


def test_invalid_key_raises_friendly_error(stub_server) -> None:
    stub_server.status = 401  # type: ignore[attr-defined]
    client = make_client(stub_server)
    with pytest.raises(LLMError, match="API Key 无效"):
        client.chat("sys", "user")


def test_missing_key_raises(stub_server) -> None:
    client = make_client(stub_server, api_key="")
    with pytest.raises(LLMError, match="还没有配置模型"):
        client.chat("sys", "user")


def test_test_connection_ok(stub_server) -> None:
    client = make_client(stub_server)
    result = client.test_connection()
    assert result.ok
    assert result.model == "stub-model"


def test_test_connection_reports_error(stub_server) -> None:
    stub_server.status = 500  # type: ignore[attr-defined]
    client = make_client(stub_server)
    result = client.test_connection()
    assert not result.ok
    assert "不可用" in result.message or "失败" in result.message


def test_base_url_empty() -> None:
    cfg = LLMConfig(provider="custom", base_url="", model="m", api_key="k")
    client = LLMClient(cfg)
    with pytest.raises(LLMError, match="Base URL"):
        client.chat("sys", "user")

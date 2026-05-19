from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from grok2api.server import create_app
from grok2api.xai_client import UpstreamResponse


@dataclass
class FakeXAIClient:
    status_code: int = 200
    body: bytes = b'{"ok":true}'
    headers: dict[str, str] | None = None
    last_method: str | None = None
    last_path: str | None = None
    last_body: Any | None = None

    async def request_json(
        self, method: str, path: str, *, json_body: Any | None = None
    ) -> UpstreamResponse:
        self.last_method = method
        self.last_path = path
        self.last_body = json_body
        return UpstreamResponse(
            status_code=self.status_code,
            headers=self.headers or {"content-type": "application/json", "x-request-id": "req_1"},
            body=self.body,
        )

    async def stream_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        self.last_method = method
        self.last_path = path
        self.last_body = json_body

        async def chunks() -> AsyncIterator[bytes]:
            yield b"event: error\n"
            yield b'data: {"error":{}}\n\n'

        return 200, {"content-type": "text/event-stream"}, chunks()


def test_healthz() -> None:
    client = TestClient(create_app(xai_client=FakeXAIClient()))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_models_forwards_upstream_response() -> None:
    fake = FakeXAIClient(body=b'{"data":[]}')
    client = TestClient(create_app(xai_client=fake))
    response = client.get("/v1/models", headers={"Authorization": "Bearer local"})
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert response.headers["x-request-id"] == "req_1"
    assert fake.last_path == "/models"


def test_responses_forwards_body() -> None:
    fake = FakeXAIClient(body=b'{"id":"resp_1"}')
    client = TestClient(create_app(xai_client=fake))
    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer local"},
        json={"model": "grok", "input": "hi"},
    )
    assert response.status_code == 200
    assert response.json() == {"id": "resp_1"}
    assert fake.last_path == "/responses"
    assert fake.last_body == {"model": "grok", "input": "hi"}


def test_chat_completions_direct_pass_through() -> None:
    fake = FakeXAIClient(body=b'{"id":"chatcmpl_1"}')
    client = TestClient(create_app(xai_client=fake))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer local"},
        json={"model": "grok", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"id": "chatcmpl_1"}
    assert fake.last_path == "/chat/completions"


def test_streaming_response_preserves_sse() -> None:
    fake = FakeXAIClient()
    client = TestClient(create_app(xai_client=fake))
    with client.stream(
        "POST",
        "/v1/responses",
        headers={"Authorization": "Bearer local"},
        json={"model": "grok", "input": "hi", "stream": True},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "".join(response.iter_text()) == 'event: error\ndata: {"error":{}}\n\n'


def test_local_api_key_required_when_configured() -> None:
    client = TestClient(create_app(xai_client=FakeXAIClient(), api_key="secret"))
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_invalid_json_returns_400() -> None:
    client = TestClient(create_app(xai_client=FakeXAIClient()))
    response = client.post(
        "/v1/responses",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400

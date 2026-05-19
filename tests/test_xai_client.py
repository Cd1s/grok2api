from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from grok2api.config import Settings
from grok2api.token_store import TokenState, TokenStore
from grok2api.xai_client import XAIClient


@pytest.mark.asyncio
async def test_request_json_attaches_xai_bearer(tmp_path) -> None:
    store = TokenStore(tmp_path / "auth.json")
    store.save(TokenState(access_token="access"))
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"x-request-id": "req_1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    xai = XAIClient(
        settings=Settings(xai_api_base_url="https://api.test/v1"),
        store=store,
        client=client,
    )
    response = await xai.request_json("POST", "/responses", json_body={"input": "hi"})
    await client.aclose()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert seen[0].headers["Authorization"] == "Bearer access"
    assert str(seen[0].url) == "https://api.test/v1/responses"


@pytest.mark.asyncio
async def test_request_json_refreshes_expired_token(tmp_path) -> None:
    store = TokenStore(tmp_path / "auth.json")
    store.save(TokenState(access_token="old", refresh_token="refresh", expires_at=1))
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == "https://auth.test/token":
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    xai = XAIClient(
        settings=Settings(
            xai_api_base_url="https://api.test/v1",
            xai_token_url="https://auth.test/token",
        ),
        store=store,
        client=client,
    )
    await xai.request_json("GET", "/models")
    await client.aclose()

    assert seen[-1].headers["Authorization"] == "Bearer new"
    assert store.load().access_token == "new"


class IteratorStream(httpx.AsyncByteStream):
    def __init__(self, iterator: AsyncIterator[bytes]) -> None:
        self.iterator = iterator

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.iterator:
            yield chunk


@pytest.mark.asyncio
async def test_stream_json_preserves_chunks(tmp_path) -> None:
    store = TokenStore(tmp_path / "auth.json")
    store.save(TokenState(access_token="access"))

    async def stream_body():
        yield b"event: error\n"
        yield b'data: {"error":{}}\n\n'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=IteratorStream(stream_body()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    xai = XAIClient(
        settings=Settings(xai_api_base_url="https://api.test/v1"),
        store=store,
        client=client,
    )
    status, _headers, chunks = await xai.stream_json(
        "POST", "/responses", json_body={"stream": True}
    )
    assert status == 200
    assert b"".join([chunk async for chunk in chunks]) == b'event: error\ndata: {"error":{}}\n\n'
    await client.aclose()

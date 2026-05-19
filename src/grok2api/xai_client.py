from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import ensure_valid_token
from .config import Settings, get_settings
from .token_store import TokenStore


@dataclass(slots=True)
class UpstreamResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return httpx.Response(self.status_code, content=self.body, headers=self.headers).json()


class XAIClient:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        store: TokenStore | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or TokenStore()
        self._client = client

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> UpstreamResponse:
        token = await ensure_valid_token(self.store, self.settings, client=self._client)
        async with self._client_context() as client:
            response = await client.request(
                method,
                self._url(path),
                json=json_body,
                headers=self._headers(token.access_token),
            )
            return UpstreamResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.content,
            )

    async def stream_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        token = await ensure_valid_token(self.store, self.settings, client=self._client)
        client_cm = self._client_context()
        client = await client_cm.__aenter__()
        stream_cm = client.stream(
            method,
            self._url(path),
            json=json_body,
            headers=self._headers(token.access_token),
        )
        response = await stream_cm.__aenter__()

        async def iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client_cm.__aexit__(None, None, None)

        return response.status_code, dict(response.headers), iterator()

    def _url(self, path: str) -> str:
        clean_path = path if path.startswith("/") else f"/{path}"
        return f"{self.settings.xai_api_base_url}{clean_path}"

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

    def _client_context(self) -> httpx.AsyncClient:
        if self._client is not None:
            return _BorrowedAsyncClient(self._client)
        return httpx.AsyncClient(timeout=None)


class _BorrowedAsyncClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

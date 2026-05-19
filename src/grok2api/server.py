from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import Settings, get_settings
from .openai_compat import STREAM_MEDIA_TYPE, local_auth_valid, response_headers, wants_stream
from .token_store import TokenStoreError
from .xai_client import XAIClient


def create_app(
    *,
    settings: Settings | None = None,
    xai_client: XAIClient | None = None,
    api_key: str | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    client = xai_client or XAIClient(settings=settings)
    local_api_key = api_key if api_key is not None else settings.local_api_key
    app = FastAPI(title="grok2api", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models(authorization: str | None = Header(default=None)) -> Response:
        auth_error = _auth_error(authorization, local_api_key)
        if auth_error:
            return auth_error
        try:
            upstream = await client.request_json("GET", "/models")
        except TokenStoreError as exc:
            return _login_required(exc)
        return _raw_response(upstream.status_code, upstream.headers, upstream.body)

    @app.post("/v1/responses")
    async def responses(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        return await _proxy_json_endpoint(
            request,
            authorization=authorization,
            api_key=local_api_key,
            client=client,
            upstream_path="/responses",
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        return await _proxy_json_endpoint(
            request,
            authorization=authorization,
            api_key=local_api_key,
            client=client,
            upstream_path="/chat/completions",
        )

    return app


async def _proxy_json_endpoint(
    request: Request,
    *,
    authorization: str | None,
    api_key: str | None,
    client: XAIClient,
    upstream_path: str,
) -> Response:
    auth_error = _auth_error(authorization, api_key)
    if auth_error:
        return auth_error
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Request body must be valid JSON",
                    "type": "invalid_request_error",
                }
            },
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Request body must be a JSON object",
                    "type": "invalid_request_error",
                }
            },
        )

    try:
        if wants_stream(payload):
            status_code, headers, stream = await client.stream_json(
                "POST",
                upstream_path,
                json_body=payload,
            )
            return StreamingResponse(
                stream,
                status_code=status_code,
                headers=response_headers(headers),
                media_type=STREAM_MEDIA_TYPE,
            )
        upstream = await client.request_json("POST", upstream_path, json_body=payload)
    except TokenStoreError as exc:
        return _login_required(exc)
    return _raw_response(upstream.status_code, upstream.headers, upstream.body)


def _raw_response(status_code: int, headers: dict[str, str], body: bytes) -> Response:
    forwarded = response_headers(headers)
    media_type = _content_type(forwarded) or "application/json"
    return Response(
        content=body,
        status_code=status_code,
        headers=forwarded,
        media_type=media_type,
    )


def _auth_error(authorization: str | None, api_key: str | None) -> JSONResponse | None:
    if local_auth_valid(authorization, api_key):
        return None
    return JSONResponse(
        status_code=401,
        content={"error": {"message": "Invalid local API key", "type": "authentication_error"}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _login_required(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": {"message": str(exc), "type": "authentication_error"}},
    )


def _content_type(headers: dict[str, Any]) -> str | None:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return str(value)
    return None


app = create_app()

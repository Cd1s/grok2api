from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STREAM_MEDIA_TYPE = "text/event-stream"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}
FORWARDED_RESPONSE_HEADERS = {
    "content-type",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
}


def wants_stream(payload: Mapping[str, Any]) -> bool:
    return payload.get("stream") is True


def response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_HEADERS:
            continue
        if lower in FORWARDED_RESPONSE_HEADERS:
            clean[key] = value
    return clean


def local_auth_valid(authorization: str | None, api_key: str | None) -> bool:
    if not api_key:
        return True
    if not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and token == api_key

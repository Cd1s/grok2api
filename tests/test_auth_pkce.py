from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from grok2api.auth import (
    CallbackResult,
    OAuthError,
    build_authorization_url,
    create_pending_oauth_login,
    exchange_code_for_tokens,
    load_pending_oauth_login,
    parse_callback_url,
    pkce_code_challenge,
    pkce_code_verifier,
    save_pending_oauth_login,
    validate_callback,
)
from grok2api.config import Settings


def test_pkce_challenge_is_sha256_base64url_without_padding() -> None:
    verifier = "abc123"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    assert pkce_code_challenge(verifier) == expected.decode("ascii").rstrip("=")


def test_pkce_verifier_has_valid_length() -> None:
    verifier = pkce_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_authorization_url_contains_expected_params() -> None:
    settings = Settings()
    url = build_authorization_url(
        settings,
        redirect_uri="http://127.0.0.1:56121/callback",
        state="state",
        code_challenge="challenge",
    )
    params = parse_qs(urlparse(url).query)
    assert url.startswith(settings.xai_authorization_url)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [settings.xai_client_id]
    assert params["scope"] == [settings.xai_scope]
    assert params["code_challenge_method"] == ["S256"]
    assert params["plan"] == ["generic"]
    assert params["referrer"] == ["hermes-agent"]


def test_validate_callback_rejects_state_mismatch() -> None:
    with pytest.raises(OAuthError, match="state mismatch"):
        validate_callback(CallbackResult(code="code", state="bad"), "good")


def test_parse_callback_url_accepts_full_url_and_plain_code() -> None:
    parsed = parse_callback_url("http://127.0.0.1:56121/callback?code=abc&state=xyz")
    assert parsed.code == "abc"
    assert parsed.state == "xyz"
    assert parse_callback_url("abc").code == "abc"


def test_pending_oauth_login_round_trip(tmp_path) -> None:
    settings = Settings()
    pending = create_pending_oauth_login(settings)
    path = save_pending_oauth_login(pending, tmp_path / "pending.json")
    loaded = load_pending_oauth_login(path)
    params = parse_qs(urlparse(loaded.authorization_url).query)
    assert loaded.state == pending.state
    assert loaded.code_verifier == pending.code_verifier
    assert loaded.code_challenge == pending.code_challenge
    assert params["state"] == [pending.state]
    assert params["redirect_uri"] == [settings.redirect_uri(settings.redirect_port)]


@pytest.mark.asyncio
async def test_exchange_code_for_tokens_posts_expected_form() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(xai_token_url="https://auth.test/oauth2/token")
    state = await exchange_code_for_tokens(
        settings,
        code="code",
        code_verifier="verifier",
        redirect_uri="http://127.0.0.1:56121/callback",
        code_challenge="challenge",
        client=client,
    )
    await client.aclose()

    body = requests[0].content.decode()
    fields = parse_qs(body)
    assert state.access_token == "access"
    assert fields["grant_type"] == ["authorization_code"]
    assert fields["client_id"] == [settings.xai_client_id]
    assert fields["code"] == ["code"]
    assert fields["code_verifier"] == ["verifier"]
    assert fields["code_challenge"] == ["challenge"]
    assert fields["code_challenge_method"] == ["S256"]

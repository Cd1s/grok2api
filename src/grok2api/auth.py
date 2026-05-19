from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from contextlib import suppress
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .config import Settings, get_settings
from .token_store import TokenState, TokenStore, TokenStoreError, default_pending_oauth_file


class OAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


@dataclass(slots=True)
class PendingOAuthLogin:
    authorization_url: str
    redirect_uri: str
    state: str
    code_verifier: str
    code_challenge: str
    created_at: int


@dataclass(slots=True)
class CallbackServer:
    server: ThreadingHTTPServer
    thread: Thread
    complete: Event
    result_ref: dict[str, CallbackResult | None]

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def wait(self, timeout: int = 300) -> CallbackResult:
        if not self.complete.wait(timeout):
            raise OAuthError("Timed out waiting for xAI OAuth callback")
        result = self.result_ref["result"]
        if result is None:
            raise OAuthError("OAuth callback did not return a result")
        return result

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def pkce_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def pkce_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def oauth_state() -> str:
    return secrets.token_urlsafe(32)


def create_pending_oauth_login(settings: Settings | None = None) -> PendingOAuthLogin:
    settings = settings or get_settings()
    verifier = pkce_code_verifier()
    challenge = pkce_code_challenge(verifier)
    state = oauth_state()
    redirect_uri = settings.redirect_uri(settings.redirect_port)
    authorization_url = build_authorization_url(
        settings,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=challenge,
    )
    return PendingOAuthLogin(
        authorization_url=authorization_url,
        redirect_uri=redirect_uri,
        state=state,
        code_verifier=verifier,
        code_challenge=challenge,
        created_at=int(time.time()),
    )


def save_pending_oauth_login(pending: PendingOAuthLogin, path: Path | None = None) -> Path:
    path = path or default_pending_oauth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(asdict(pending), indent=2, sort_keys=True), encoding="utf-8")
    with suppress(OSError):
        os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    with suppress(OSError):
        os.chmod(path, 0o600)
    return path


def load_pending_oauth_login(path: Path | None = None) -> PendingOAuthLogin:
    path = path or default_pending_oauth_file()
    if not path.exists():
        raise OAuthError("No pending OAuth login found. Run `grok2api login-url` first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PendingOAuthLogin(
        authorization_url=str(payload["authorization_url"]),
        redirect_uri=str(payload["redirect_uri"]),
        state=str(payload["state"]),
        code_verifier=str(payload["code_verifier"]),
        code_challenge=str(
            payload.get("code_challenge") or pkce_code_challenge(str(payload["code_verifier"]))
        ),
        created_at=int(payload.get("created_at", 0)),
    )


def clear_pending_oauth_login(path: Path | None = None) -> None:
    path = path or default_pending_oauth_file()
    with suppress(FileNotFoundError):
        path.unlink()


async def complete_pending_oauth_login(
    callback_url_or_code: str,
    *,
    settings: Settings | None = None,
    store: TokenStore | None = None,
    pending_path: Path | None = None,
) -> TokenState:
    settings = settings or get_settings()
    store = store or TokenStore()
    pending = load_pending_oauth_login(pending_path)
    callback = parse_callback_url(callback_url_or_code)
    code = validate_callback(callback, pending.state, require_state=callback.state is not None)
    token_state = await exchange_code_for_tokens(
        settings,
        code=code,
        code_verifier=pending.code_verifier,
        redirect_uri=pending.redirect_uri,
        code_challenge=pending.code_challenge,
    )
    store.save(token_state)
    clear_pending_oauth_login(pending_path)
    return token_state


def build_authorization_url(
    settings: Settings,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.xai_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.xai_scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "plan": "generic",
        "referrer": settings.xai_referrer,
    }
    return f"{settings.xai_authorization_url}?{urlencode(params)}"


def parse_callback_url(callback_url: str) -> CallbackResult:
    parsed = urlparse(callback_url.strip())
    if (
        not parsed.query
        and callback_url.strip()
        and not callback_url.startswith(("http://", "https://"))
    ):
        return CallbackResult(code=callback_url.strip())
    query = parse_qs(parsed.query)
    return CallbackResult(
        code=_first(query.get("code")),
        state=_first(query.get("state")),
        error=_first(query.get("error")),
        error_description=_first(query.get("error_description")),
    )


def validate_callback(
    result: CallbackResult, expected_state: str, require_state: bool = True
) -> str:
    if result.error:
        detail = f": {result.error_description}" if result.error_description else ""
        raise OAuthError(f"xAI OAuth failed with {result.error}{detail}")
    if require_state and result.state != expected_state:
        raise OAuthError("OAuth state mismatch")
    if not result.code:
        raise OAuthError("OAuth callback did not include an authorization code")
    return result.code


async def exchange_code_for_tokens(
    settings: Settings,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    code_challenge: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> TokenState:
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.xai_client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if code_challenge:
        payload["code_challenge"] = code_challenge
        payload["code_challenge_method"] = "S256"
    token_payload = await _post_token(settings, payload, client=client)
    return TokenState.from_token_response(token_payload)


async def refresh_token(
    settings: Settings,
    state: TokenState,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenState:
    if not state.refresh_token:
        raise TokenStoreError("No refresh token is available. Run `grok2api login` again.")
    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.xai_client_id,
        "refresh_token": state.refresh_token,
    }
    try:
        token_payload = await _post_token(settings, payload, client=client)
    except OAuthError as exc:
        raise TokenStoreError(f"Token refresh failed. Run `grok2api login` again. {exc}") from exc
    return state.merge_refresh_response(token_payload)


async def ensure_valid_token(
    store: TokenStore,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenState:
    state = store.load()
    if not state.needs_refresh(settings.refresh_skew_seconds):
        return state
    refreshed = await refresh_token(settings, state, client=client)
    store.save(refreshed)
    return refreshed


async def login(
    *,
    settings: Settings | None = None,
    store: TokenStore | None = None,
    headless: bool = False,
    timeout: int = 300,
) -> TokenState:
    settings = settings or get_settings()
    store = store or TokenStore()
    verifier = pkce_code_verifier()
    challenge = pkce_code_challenge(verifier)
    state = oauth_state()

    callback_server: CallbackServer | None = None
    try:
        if headless:
            redirect_uri = settings.redirect_uri(settings.redirect_port)
        else:
            callback_server = start_callback_server(settings)
            redirect_uri = settings.redirect_uri(callback_server.port)

        auth_url = build_authorization_url(
            settings,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
        )

        if headless:
            print("Open this URL in your browser:")
            print(auth_url)
            callback_text = await asyncio.to_thread(input, "Paste the final callback URL or code: ")
            callback = parse_callback_url(callback_text)
            code = validate_callback(callback, state, require_state=callback.state is not None)
        else:
            opened = webbrowser.open(auth_url)
            if not opened:
                print("Open this URL in your browser:")
                print(auth_url)
            callback = await asyncio.to_thread(callback_server.wait, timeout)
            code = validate_callback(callback, state)

        token_state = await exchange_code_for_tokens(
            settings,
            code=code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        store.save(token_state)
        return token_state
    finally:
        if callback_server is not None:
            callback_server.shutdown()


def start_callback_server(settings: Settings) -> CallbackServer:
    callback_path = settings.redirect_path
    complete = Event()
    holder: dict[str, CallbackResult | None] = {"result": None}

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, "")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self._send(404, "Not found")
                return
            query = parse_qs(parsed.query)
            holder["result"] = CallbackResult(
                code=_first(query.get("code")),
                state=_first(query.get("state")),
                error=_first(query.get("error")),
                error_description=_first(query.get("error_description")),
            )
            complete.set()
            self._send(
                200,
                "You can close this window and return to grok2api.",
                content_type="text/plain; charset=utf-8",
            )

        def log_message(self, _format: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: str, content_type: str = "text/plain") -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            origin = self.headers.get("Origin")
            if origin in {"https://accounts.x.ai", "https://auth.x.ai"}:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()
            if encoded:
                self.wfile.write(encoded)

    try:
        server = ThreadingHTTPServer((settings.redirect_host, settings.redirect_port), Handler)
    except OSError:
        server = ThreadingHTTPServer((settings.redirect_host, 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return CallbackServer(server=server, thread=thread, complete=complete, result_ref=holder)


def token_metadata(state: TokenState) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "token_type": state.token_type,
        "has_access_token": bool(state.access_token),
        "has_refresh_token": bool(state.refresh_token),
    }
    if state.scope:
        metadata["scope"] = state.scope
    if state.expires_at:
        metadata["expires_at"] = state.expires_at
        metadata["expires_in_seconds"] = max(0, state.expires_at - int(time.time()))
    if state.id_token:
        claims = _decode_jwt_claims(state.id_token)
        for key in ("sub", "email", "name"):
            if key in claims:
                metadata[key] = claims[key]
    return metadata


async def _post_token(
    settings: Settings,
    payload: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    close_client = client is None
    actual_client = client or httpx.AsyncClient(timeout=30)
    try:
        response = await actual_client.post(
            settings.xai_token_url,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if close_client:
            await actual_client.aclose()

    if response.status_code >= 400:
        detail = _safe_error_detail(response)
        raise OAuthError(f"xAI token endpoint returned HTTP {response.status_code}: {detail}")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise OAuthError("xAI token endpoint did not return JSON") from exc
    if not isinstance(data, dict):
        raise OAuthError("xAI token endpoint returned an unexpected payload")
    return data


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _safe_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text[:500]
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False)[:500]
    return str(payload)[:500]


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]

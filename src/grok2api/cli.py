from __future__ import annotations

import asyncio
import json
from typing import Annotated

import httpx
import typer
import uvicorn

from .auth import (
    OAuthError,
    complete_pending_oauth_login,
    create_pending_oauth_login,
    save_pending_oauth_login,
    token_metadata,
)
from .auth import login as oauth_login
from .config import get_settings
from .server import create_app
from .token_store import TokenStore, TokenStoreError
from .xai_client import XAIClient

app = typer.Typer(help="Local OpenAI-compatible API proxy for Grok via xAI OAuth.")


@app.command()
def login(
    headless: Annotated[
        bool,
        typer.Option("--headless", help="Print the auth URL and paste the callback manually."),
    ] = False,
) -> None:
    settings = get_settings()
    try:
        state = asyncio.run(oauth_login(settings=settings, headless=headless))
    except (OAuthError, TokenStoreError) as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    metadata = token_metadata(state)
    typer.echo("Login complete.")
    if metadata.get("expires_in_seconds") is not None:
        typer.echo(f"Access token expires in {metadata['expires_in_seconds']} seconds.")


@app.command("login-url")
def login_url() -> None:
    pending = create_pending_oauth_login(get_settings())
    path = save_pending_oauth_login(pending)
    typer.echo(pending.authorization_url)
    typer.echo(f"Pending login saved to {path}", err=True)


@app.command("login-complete")
def login_complete(callback_url_or_code: str) -> None:
    try:
        state = asyncio.run(
            complete_pending_oauth_login(callback_url_or_code, settings=get_settings())
        )
    except (OAuthError, TokenStoreError) as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    metadata = token_metadata(state)
    typer.echo("Login complete.")
    if metadata.get("expires_in_seconds") is not None:
        typer.echo(f"Access token expires in {metadata['expires_in_seconds']} seconds.")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Allow binding to non-loopback addresses."),
    ] = False,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Local proxy API key. Required for remote binding."),
    ] = None,
) -> None:
    settings = get_settings()
    effective_api_key = api_key if api_key is not None else settings.local_api_key
    if host not in {"127.0.0.1", "localhost", "::1"}:
        if not allow_remote:
            raise typer.BadParameter("Remote binding requires --allow-remote")
        if not effective_api_key:
            raise typer.BadParameter("Remote binding requires --api-key or GROK2API_API_KEY")
    uvicorn.run(create_app(settings=settings, api_key=effective_api_key), host=host, port=port)


@app.command()
def models() -> None:
    async def run() -> None:
        client = XAIClient(settings=get_settings())
        response = await client.request_json("GET", "/models")
        typer.echo(response.body.decode("utf-8", errors="replace"))

    try:
        asyncio.run(run())
    except TokenStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def whoami() -> None:
    store = TokenStore()
    try:
        state = store.load()
    except TokenStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(token_metadata(state), indent=2, sort_keys=True))


@app.command()
def logout() -> None:
    store = TokenStore()
    if store.delete():
        typer.echo("Logged out. Local token file removed.")
    else:
        typer.echo("No local token file found.")


@app.command(hidden=True)
def refresh() -> None:
    async def run() -> None:
        from .auth import ensure_valid_token

        store = TokenStore()
        state = await ensure_valid_token(store, get_settings())
        typer.echo(json.dumps(token_metadata(state), indent=2, sort_keys=True))

    try:
        asyncio.run(run())
    except (TokenStoreError, httpx.HTTPError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

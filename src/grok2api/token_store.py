from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

TOKEN_FILE_ENV = "GROK2API_AUTH_FILE"
APP_NAME = "grok2api"


class TokenStoreError(RuntimeError):
    pass


@dataclass(slots=True)
class TokenState:
    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    token_type: str = "Bearer"
    expires_at: int | None = None
    scope: str | None = None

    @classmethod
    def from_token_response(cls, payload: dict[str, Any], now: int | None = None) -> TokenState:
        access_token = payload.get("access_token")
        if not access_token:
            raise TokenStoreError("OAuth response did not include an access token")

        issued_at = int(now if now is not None else time.time())
        expires_in = payload.get("expires_in")
        expires_at = None
        if expires_in is not None:
            expires_at = issued_at + int(expires_in)

        return cls(
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token"),
            id_token=payload.get("id_token"),
            token_type=payload.get("token_type") or "Bearer",
            expires_at=expires_at,
            scope=payload.get("scope"),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TokenState:
        access_token = payload.get("access_token")
        if not access_token:
            raise TokenStoreError("Stored auth file is missing an access token")
        return cls(
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token"),
            id_token=payload.get("id_token"),
            token_type=payload.get("token_type") or "Bearer",
            expires_at=payload.get("expires_at"),
            scope=payload.get("scope"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "access_token": self.access_token,
            "token_type": self.token_type,
        }
        if self.refresh_token:
            payload["refresh_token"] = self.refresh_token
        if self.id_token:
            payload["id_token"] = self.id_token
        if self.expires_at:
            payload["expires_at"] = self.expires_at
        if self.scope:
            payload["scope"] = self.scope
        return payload

    def needs_refresh(self, skew_seconds: int, now: int | None = None) -> bool:
        if not self.refresh_token or not self.expires_at:
            return False
        return int(now if now is not None else time.time()) >= self.expires_at - skew_seconds

    def merge_refresh_response(self, payload: dict[str, Any], now: int | None = None) -> TokenState:
        merged = self.to_dict()
        merged.update(payload)
        if "refresh_token" not in payload and self.refresh_token:
            merged["refresh_token"] = self.refresh_token
        return TokenState.from_token_response(merged, now=now)


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_auth_file()

    def load(self) -> TokenState:
        if not self.path.exists():
            raise TokenStoreError("Not logged in. Run `grok2api login` first.")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TokenStoreError(f"Auth file is not valid JSON: {self.path}") from exc
        return TokenState.from_dict(payload)

    def save(self, state: TokenState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        with suppress(OSError):
            os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)
        with suppress(OSError):
            os.chmod(self.path, 0o600)

    def delete(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True


def default_auth_file() -> Path:
    override = os.getenv(TOKEN_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "auth.json"

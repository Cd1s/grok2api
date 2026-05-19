from __future__ import annotations

import stat

import pytest

from grok2api.token_store import TokenState, TokenStore, TokenStoreError


def test_token_state_from_response_computes_expiry() -> None:
    state = TokenState.from_token_response(
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 10},
        now=100,
    )
    assert state.expires_at == 110
    assert state.needs_refresh(20, now=95)
    assert not state.needs_refresh(5, now=100)


def test_token_store_round_trip(tmp_path) -> None:
    path = tmp_path / "auth.json"
    store = TokenStore(path)
    store.save(TokenState(access_token="access", refresh_token="refresh", expires_at=200))
    loaded = store.load()
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_token_file_is_actionable(tmp_path) -> None:
    with pytest.raises(TokenStoreError, match="grok2api login"):
        TokenStore(tmp_path / "missing.json").load()


def test_refresh_response_preserves_refresh_token() -> None:
    state = TokenState(access_token="old", refresh_token="refresh", expires_at=100)
    refreshed = state.merge_refresh_response({"access_token": "new", "expires_in": 10}, now=200)
    assert refreshed.access_token == "new"
    assert refreshed.refresh_token == "refresh"
    assert refreshed.expires_at == 210

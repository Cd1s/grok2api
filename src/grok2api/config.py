from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_XAI_ISSUER = "https://auth.x.ai"
DEFAULT_XAI_DISCOVERY_URL = "https://auth.x.ai/.well-known/openid-configuration"
DEFAULT_XAI_AUTHORIZATION_URL = "https://auth.x.ai/oauth2/authorize"
DEFAULT_XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
DEFAULT_XAI_API_BASE_URL = "https://api.x.ai/v1"
DEFAULT_XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DEFAULT_XAI_SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEFAULT_REDIRECT_HOST = "127.0.0.1"
DEFAULT_REDIRECT_PORT = 56121
DEFAULT_REDIRECT_PATH = "/callback"
DEFAULT_REFRESH_SKEW_SECONDS = 120


@dataclass(frozen=True)
class Settings:
    xai_issuer: str = DEFAULT_XAI_ISSUER
    xai_discovery_url: str = DEFAULT_XAI_DISCOVERY_URL
    xai_authorization_url: str = DEFAULT_XAI_AUTHORIZATION_URL
    xai_token_url: str = DEFAULT_XAI_TOKEN_URL
    xai_api_base_url: str = DEFAULT_XAI_API_BASE_URL
    xai_client_id: str = DEFAULT_XAI_CLIENT_ID
    xai_scope: str = DEFAULT_XAI_SCOPE
    redirect_host: str = DEFAULT_REDIRECT_HOST
    redirect_port: int = DEFAULT_REDIRECT_PORT
    redirect_path: str = DEFAULT_REDIRECT_PATH
    refresh_skew_seconds: int = DEFAULT_REFRESH_SKEW_SECONDS
    local_api_key: str | None = None

    def redirect_uri(self, port: int | None = None) -> str:
        actual_port = port if port is not None else self.redirect_port
        return f"http://{self.redirect_host}:{actual_port}{self.redirect_path}"


def get_settings() -> Settings:
    return Settings(
        xai_issuer=os.getenv("GROK2API_XAI_ISSUER", DEFAULT_XAI_ISSUER),
        xai_discovery_url=os.getenv("GROK2API_XAI_DISCOVERY_URL", DEFAULT_XAI_DISCOVERY_URL),
        xai_authorization_url=os.getenv(
            "GROK2API_XAI_AUTHORIZATION_URL", DEFAULT_XAI_AUTHORIZATION_URL
        ),
        xai_token_url=os.getenv("GROK2API_XAI_TOKEN_URL", DEFAULT_XAI_TOKEN_URL),
        xai_api_base_url=os.getenv("GROK2API_XAI_API_BASE_URL", DEFAULT_XAI_API_BASE_URL).rstrip(
            "/"
        ),
        xai_client_id=os.getenv("GROK2API_XAI_CLIENT_ID", DEFAULT_XAI_CLIENT_ID),
        xai_scope=os.getenv("GROK2API_XAI_SCOPE", DEFAULT_XAI_SCOPE),
        redirect_host=DEFAULT_REDIRECT_HOST,
        redirect_port=int(os.getenv("GROK2API_REDIRECT_PORT", str(DEFAULT_REDIRECT_PORT))),
        redirect_path=DEFAULT_REDIRECT_PATH,
        local_api_key=os.getenv("GROK2API_API_KEY"),
    )

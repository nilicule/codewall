"""Configuration read from environment variables.

Everything is env-driven so the same image runs locally (mock data, no
secrets) and in production (real GitHub token + OAuth). See .env.example.
"""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class Config:
    """Snapshot of process configuration, built once at boot."""

    def __init__(self) -> None:
        # GitHub harvesting
        self.github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.github_org = os.environ.get("GITHUB_ORG", "NET2GRID").strip()
        self.window_days = _int("WINDOW_DAYS", 90)
        self.refresh_seconds = _int("REFRESH_SECONDS", 120)
        # In mock mode we tick fast so the wall feels alive without GitHub.
        self.mock_refresh_seconds = _int("MOCK_REFRESH_SECONDS", 3)

        # Mount path when served behind a reverse proxy under a sub-path
        # (e.g. URL_PREFIX=/codewall). Empty = served at the domain root.
        self.url_prefix = os.environ.get("URL_PREFIX", "").strip().rstrip("/")

        # Auth / session
        self.secret_key = os.environ.get("SECRET_KEY", "").strip() or "dev-insecure-change-me"
        # Simple shared-secret gate: one access key, entered once via a login
        # form, grants a signed-cookie session. No GitHub OAuth app required.
        self.access_token = os.environ.get("ACCESS_TOKEN", "").strip()
        self.oauth_client_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
        self.oauth_client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
        self.dev_auth_bypass = _bool("DEV_AUTH_BYPASS", False)

        # Optional single-file persistence (empty = pure in-memory)
        self.cache_persist_path = os.environ.get("CACHE_PERSIST_PATH", "").strip()

    @property
    def use_mock(self) -> bool:
        """No token => serve mock data so the UI runs with zero infrastructure."""
        return not self.github_token

    @property
    def oauth_configured(self) -> bool:
        return bool(self.oauth_client_id and self.oauth_client_secret)


def load_config() -> Config:
    return Config()

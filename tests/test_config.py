"""Config defaults for the Google Workspace viewer gate."""
from __future__ import annotations

from n2g.config import Config


def test_allowed_email_domain_defaults_to_net2grid(monkeypatch):
    monkeypatch.delenv("ALLOWED_EMAIL_DOMAIN", raising=False)
    assert Config().allowed_email_domain == "net2grid.com"


def test_allowed_email_domain_override_is_lowercased(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "Example.COM")
    assert Config().allowed_email_domain == "example.com"


def test_google_oauth_configured_requires_both(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert Config().google_oauth_configured is False
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    assert Config().google_oauth_configured is True

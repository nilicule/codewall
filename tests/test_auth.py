"""Auth gate tests (shared-secret access key) using the Flask test client."""
from __future__ import annotations

from n2g import create_app
from n2g.config import Config


def _app(access_token="", dev_bypass=False, oauth=False, url_prefix=""):
    c = Config()
    c.github_token = ""  # mock data source, no live calls
    c.cache_persist_path = ""
    c.secret_key = "test"
    c.access_token = access_token
    c.dev_auth_bypass = dev_bypass
    c.oauth_client_id = "x" if oauth else ""
    c.oauth_client_secret = "x" if oauth else ""
    c.url_prefix = url_prefix
    return create_app(c)


def test_access_gate_blocks_anonymous():
    client = _app(access_token="s3cret").test_client()
    assert client.get("/api/stats").status_code == 401          # JSON 401 for /api/*
    assert client.get("/").status_code == 302                   # redirect to login
    form = client.get("/login")
    assert form.status_code == 200
    assert b"access key" in form.data.lower()                   # login form shown


def test_access_gate_wrong_secret_rejected():
    client = _app(access_token="s3cret").test_client()
    resp = client.post("/login", data={"token": "nope"})
    assert resp.status_code == 401
    assert client.get("/api/stats").status_code == 401          # still gated


def test_access_gate_correct_secret_grants_session():
    client = _app(access_token="s3cret").test_client()
    resp = client.post("/login", data={"token": "s3cret"})
    assert resp.status_code == 302                              # -> dashboard
    assert client.get("/api/stats").status_code == 200          # now allowed
    client.get("/logout")
    assert client.get("/api/stats").status_code == 401          # session cleared


def test_no_auth_configured_locks_app():
    client = _app().test_client()  # no access token, no oauth, no bypass
    assert client.get("/login").status_code == 503
    assert client.get("/api/stats").status_code == 401


def test_dev_bypass_opens_everything():
    client = _app(dev_bypass=True).test_client()
    assert client.get("/api/stats").status_code == 200


def test_favicon_served_and_referenced():
    client = _app(access_token="s3cret").test_client()
    fav = client.get("/static/favicon.svg")
    assert fav.status_code == 200
    assert "svg" in fav.headers["Content-Type"]
    # both the login page and the dashboard link to it
    assert b"favicon.svg" in client.get("/login").data
    client.post("/login", data={"token": "s3cret"})
    assert b"favicon.svg" in client.get("/").data


def test_url_prefix_makes_redirects_and_base_path_prefixed():
    client = _app(access_token="s3cret", url_prefix="/codewall").test_client()
    # unauthenticated dashboard at the mounted path redirects to the prefixed login
    resp = client.get("/codewall/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/codewall/login")
    # API is reachable under the prefix and gated
    assert client.get("/codewall/api/stats").status_code == 401
    # after login, the dashboard injects the prefix as BASE for the frontend
    assert client.post("/codewall/login", data={"token": "s3cret"}).status_code == 302
    page = client.get("/codewall/")
    assert b'const BASE = "/codewall"' in page.data

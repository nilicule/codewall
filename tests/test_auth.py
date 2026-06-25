"""Auth gate tests (shared-secret access key) using the Flask test client."""
from __future__ import annotations

from n2g import auth, create_app
from n2g.config import Config


def _app(access_token="", dev_bypass=False, google=False,
         allowed_domain="net2grid.com", url_prefix=""):
    c = Config()
    c.github_token = ""  # mock data source, no live calls
    c.cache_persist_path = ""
    c.secret_key = "test"
    c.access_token = access_token
    c.dev_auth_bypass = dev_bypass
    c.google_client_id = "gid" if google else ""
    c.google_client_secret = "gsecret" if google else ""
    c.allowed_email_domain = allowed_domain
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


SLACKBOT_UA = "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"


def test_link_crawler_gets_public_og_card_not_login_redirect():
    client = _app(access_token="s3cret").test_client()
    # a normal anonymous browser is redirected to login (no preview content)
    assert client.get("/").status_code == 302
    # a link-unfurl bot gets a 200 share card with Open Graph tags, no auth
    resp = client.get("/", headers={"User-Agent": SLACKBOT_UA})
    assert resp.status_code == 200
    body = resp.data
    assert b'property="og:image"' in body
    assert b'twitter:card' in body
    assert b"og-image.png" in body


def test_og_image_served_publicly():
    client = _app(access_token="s3cret").test_client()
    img = client.get("/static/og-image.png")
    assert img.status_code == 200                               # no auth gate on static
    assert img.headers["Content-Type"] == "image/png"


def test_og_image_url_is_absolute_and_prefixed_under_proxy():
    client = _app(access_token="s3cret", url_prefix="/codewall").test_client()
    resp = client.get(
        "/codewall/",
        headers={"User-Agent": SLACKBOT_UA, "X-Forwarded-Proto": "https"},
        base_url="https://n2g.dev/",
    )
    # og:image must be an absolute https URL including the proxy sub-path
    assert b'content="https://n2g.dev/codewall/static/og-image.png"' in resp.data


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


def _start_google_login(client):
    """GET /login/google to obtain the server-stored CSRF state, asserting the
    redirect targets Google. Returns the state to replay on /callback."""
    resp = client.get("/login/google")
    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["Location"]
    with client.session_transaction() as sess:
        return sess["oauth_state"]


def test_google_login_page_shows_button_and_og_tags():
    # /login is now a branded HTML card (not an instant redirect), so link
    # unfurls get OG tags and users get a real landing page.
    client = _app(google=True).test_client()
    resp = client.get("/login")
    assert resp.status_code == 200
    body = resp.data
    assert b"Continue with Google" in body          # the sign-in button
    assert b"/login/google" in body                 # button links to the flow
    assert b'property="og:image"' in body           # OG preview restored
    assert b"net2grid.com" in body                  # domain-restriction hint


def test_google_start_redirects_to_google_with_hd_hint():
    client = _app(google=True).test_client()
    resp = client.get("/login/google")
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "hd=net2grid.com" in loc
    assert "scope=openid+email+profile" in loc


def test_google_workspace_user_granted(monkeypatch):
    client = _app(google=True).test_client()
    state = _start_google_login(client)
    monkeypatch.setattr(auth, "_exchange_code", lambda cfg, code: "fake-id-token")
    monkeypatch.setattr(auth, "_verify_id_token", lambda cfg, tok: {
        "email": "ada@net2grid.com", "email_verified": True,
        "hd": "net2grid.com", "name": "Ada Lovelace",
        "picture": "https://example.com/a.png",
    })
    resp = client.get(f"/callback?state={state}&code=abc")
    assert resp.status_code == 302                              # -> dashboard
    assert client.get("/api/stats").status_code == 200          # session granted


def test_google_wrong_domain_rejected(monkeypatch):
    client = _app(google=True).test_client()
    state = _start_google_login(client)
    monkeypatch.setattr(auth, "_exchange_code", lambda cfg, code: "tok")
    monkeypatch.setattr(auth, "_verify_id_token", lambda cfg, tok: {
        "email": "mallory@gmail.com", "email_verified": True, "hd": "gmail.com",
    })
    resp = client.get(f"/callback?state={state}&code=abc")
    assert resp.status_code == 403
    assert client.get("/api/stats").status_code == 401          # still gated


def test_google_unverified_email_rejected(monkeypatch):
    client = _app(google=True).test_client()
    state = _start_google_login(client)
    monkeypatch.setattr(auth, "_exchange_code", lambda cfg, code: "tok")
    monkeypatch.setattr(auth, "_verify_id_token", lambda cfg, tok: {
        "email": "ada@net2grid.com", "email_verified": False, "hd": "net2grid.com",
    })
    resp = client.get(f"/callback?state={state}&code=abc")
    assert resp.status_code == 403


def test_google_callback_rejects_bad_state(monkeypatch):
    client = _app(google=True).test_client()
    _start_google_login(client)
    resp = client.get("/callback?state=forged&code=abc")
    assert resp.status_code == 400

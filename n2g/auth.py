"""Authentication / authorization for viewers of the wall.

Three gates, in precedence order, all ending in a Flask signed-cookie session
(SECRET_KEY) so we stay single-container with no session store:

1. DEV_AUTH_BYPASS=1  -> fake session, no login. Local dev only; logged as insecure.
2. ACCESS_TOKEN set   -> one shared secret entered via a login form. No OAuth
   app needed. Anyone with the secret can view (no per-user identity).
3. Google OAuth     -> Google Workspace login + ALLOWED_EMAIL_DOMAIN check (per-user).

If none are configured the app stays locked (login returns 503).
"""
from __future__ import annotations

import functools
import hmac
import logging
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)

log = logging.getLogger("n2g.auth")

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

bp = Blueprint("auth", __name__)


def _config():
    return current_app.config["N2G"]


def current_user():
    return session.get("user")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        cfg = _config()
        if cfg.dev_auth_bypass and "user" not in session:
            session["user"] = {
                "login": "dev",
                "name": "Dev Bypass",
                "avatar": "",
            }
            log.warning("DEV_AUTH_BYPASS active -> request authorized WITHOUT login")
        if "user" not in session:
            if _wants_json():
                return {"error": "auth_required"}, 401
            if _is_link_crawler():
                # Link-unfurl bots can't log in: serve a public Open Graph card
                # so Slack/Discord/etc. get a preview without exposing the wall.
                # Works for every auth gate (the login route may redirect to
                # Google for OAuth, where a crawler would never see our tags).
                return render_template(
                    "share.html",
                    og_image=url_for("static", filename="og-image.png", _external=True),
                    og_url=url_for("dashboard", _external=True),
                )
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def _wants_json() -> bool:
    return request.path.startswith("/api/")


# User-Agent substrings for the major link-preview crawlers. Matched
# case-insensitively; deliberately broad so new bots fall through to the card.
_CRAWLER_UAS = (
    "slackbot", "twitterbot", "facebookexternalhit", "linkedinbot",
    "discordbot", "whatsapp", "telegrambot", "skypeuripreview",
    "embedly", "pinterest", "redditbot", "bingbot", "googlebot",
    "applebot", "ia_archiver", "vkshare", "mastodon", "bluesky",
)


def _is_link_crawler() -> bool:
    ua = (request.headers.get("User-Agent") or "").lower()
    return any(bot in ua for bot in _CRAWLER_UAS)


@bp.route("/login", methods=["GET", "POST"])
def login():
    cfg = _config()
    if cfg.dev_auth_bypass:
        session["user"] = {"login": "dev", "name": "Dev Bypass", "avatar": ""}
        return redirect(url_for("dashboard"))

    # Shared-secret gate: a single access key entered via a form.
    if cfg.access_token:
        error = None
        if request.method == "POST":
            supplied = (request.form.get("token") or "").strip()
            if supplied and hmac.compare_digest(supplied, cfg.access_token):
                session["user"] = {"login": "viewer", "name": "Viewer", "avatar": ""}
                return redirect(url_for("dashboard"))
            error = "Incorrect access key."
            log.info("access-key login failed")
        return render_template_string(
            LOGIN_HTML,
            error=error,
            og_image=url_for("static", filename="og-image.png", _external=True),
            og_url=url_for("dashboard", _external=True),
        ), (401 if error else 200)

    # Google Workspace OAuth gate.
    if cfg.google_oauth_configured:
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        params = {
            "client_id": cfg.google_client_id,
            "redirect_uri": url_for("auth.callback", _external=True),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # UX hint so Google pre-filters to the workspace; not trusted for
            # enforcement — the hd claim is re-checked in the callback.
            "hd": cfg.allowed_email_domain,
        }
        return redirect(f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}")

    return (
        "Auth is not configured. Set ACCESS_TOKEN (a shared secret), set "
        "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET, or DEV_AUTH_BYPASS=1 for local "
        "development.",
        503,
    )


LOGIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NET2GRID · Activity</title>
<link rel="icon" type="image/svg+xml" href="{{ url_for('static', filename='favicon.svg') }}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="NET2GRID">
<meta property="og:title" content="Org Activity Wall">
<meta property="og:description" content="A live constellation of every commit and pull request across the org — streaming in real time over a 90-day window.">
<meta property="og:image" content="{{ og_image }}">
<meta property="og:image:width" content="2400">
<meta property="og:image:height" content="1260">
<meta property="og:url" content="{{ og_url }}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{{ og_image }}">
<meta name="theme-color" content="#070b12">
<style>
  :root{--void:#070b12;--panel:#0d141e;--hairline:#243446;--ink:#eaf2ff;
    --mute:#5d7088;--grid-green:#3ddc84}
  *{box-sizing:border-box;margin:0;padding:0}
  body{height:100vh;display:flex;align-items:center;justify-content:center;
    background:var(--void);color:var(--ink);
    font-family:"Inter",system-ui,sans-serif}
  .card{width:340px;padding:34px 30px;background:linear-gradient(180deg,var(--panel),#0a1018);
    border:1px solid var(--hairline);border-radius:12px}
  .eyebrow{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:10.5px;
    letter-spacing:.22em;text-transform:uppercase;color:var(--mute)}
  h1{font-size:20px;font-weight:600;letter-spacing:.04em;margin:8px 0 22px;text-transform:uppercase}
  input{width:100%;padding:12px 14px;background:#0a1018;border:1px solid var(--hairline);
    border-radius:8px;color:var(--ink);font-size:14px;outline:none}
  input:focus{border-color:var(--grid-green)}
  button{width:100%;margin-top:14px;padding:12px;border:none;border-radius:8px;
    background:var(--grid-green);color:#06210f;font-weight:600;font-size:14px;cursor:pointer}
  .err{margin-top:14px;color:#ff8a8a;font-size:12.5px}
</style></head><body>
  <form class="card" method="post">
    <div class="eyebrow">NET2GRID</div>
    <h1>Org activity</h1>
    <input type="password" name="token" placeholder="Access key" autofocus autocomplete="current-password">
    <button type="submit">Enter</button>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
  </form>
</body></html>"""


@bp.route("/callback")
def callback():
    cfg = _config()
    if request.args.get("state") != session.pop("oauth_state", None):
        return "Invalid OAuth state.", 400
    code = request.args.get("code")
    if not code:
        return "Missing OAuth code.", 400

    id_token_str = _exchange_code(cfg, code)
    if not id_token_str:
        return "Could not obtain access token.", 502

    claims = _verify_id_token(cfg, id_token_str)
    if not claims:
        return "Could not verify Google identity.", 502

    if not _email_allowed(cfg, claims):
        email = claims.get("email", "unknown")
        log.info("rejected non-domain login %s (need @%s)",
                 email, cfg.allowed_email_domain)
        return (
            f"Access denied: {email} is not a @{cfg.allowed_email_domain} account.",
            403,
        )

    email = claims["email"]
    session["user"] = {
        "login": email,
        "name": claims.get("name") or email,
        "avatar": claims.get("picture", ""),
    }
    log.info("authorized workspace user %s", email)
    return redirect(url_for("dashboard"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------- #
# Google OAuth helpers (server-side only)
# --------------------------------------------------------------------------- #
def _exchange_code(cfg, code: str) -> str | None:
    """Exchange the auth code for an id_token (a signed JWT of the claims)."""
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": cfg.google_client_id,
            "client_secret": cfg.google_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": url_for("auth.callback", _external=True),
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("id_token")


def _verify_id_token(cfg, id_token_str: str) -> dict | None:
    """Cryptographically verify the id_token against Google's public keys.

    Imported lazily so the dependency is only loaded on the OAuth path (and so
    tests can monkeypatch this function without the package installed).
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        return google_id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            cfg.google_client_id,
        )
    except ValueError:
        log.info("id_token verification failed")
        return None


def _email_allowed(cfg, claims: dict) -> bool:
    """Authorize only verified accounts in the allowed hosted domain."""
    if not claims.get("email_verified"):
        return False
    domain = cfg.allowed_email_domain.lower()
    # The hd (hosted-domain) claim is the authoritative workspace signal;
    # the email-suffix check is defense-in-depth.
    if (claims.get("hd") or "").lower() != domain:
        return False
    return (claims.get("email") or "").lower().endswith("@" + domain)

"""Authentication / authorization for viewers of the wall.

Three gates, in precedence order, all ending in a Flask signed-cookie session
(SECRET_KEY) so we stay single-container with no session store:

1. DEV_AUTH_BYPASS=1  -> fake session, no login. Local dev only; logged as insecure.
2. ACCESS_TOKEN set   -> one shared secret entered via a login form. No GitHub
   OAuth app needed. Anyone with the secret can view (no per-user identity).
3. OAuth configured   -> GitHub login + GITHUB_ORG membership check (per-user).

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
    render_template_string,
    request,
    session,
    url_for,
)

log = logging.getLogger("n2g.auth")

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_USER = "https://api.github.com/user"

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
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def _wants_json() -> bool:
    return request.path.startswith("/api/")


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
        return render_template_string(LOGIN_HTML, error=error), (401 if error else 200)

    # GitHub OAuth gate.
    if cfg.oauth_configured:
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        params = {
            "client_id": cfg.oauth_client_id,
            "redirect_uri": url_for("auth.callback", _external=True),
            "scope": "read:org read:user",
            "state": state,
        }
        return redirect(f"{AUTHORIZE_URL}?{urlencode(params)}")

    return (
        "Auth is not configured. Set ACCESS_TOKEN (a shared secret), configure "
        "OAuth, or set DEV_AUTH_BYPASS=1 for local development.",
        503,
    )


LOGIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NET2GRID · Activity</title>
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

    token = _exchange_code(cfg, code)
    if not token:
        return "Could not obtain access token.", 502

    user = _fetch_user(token)
    if not user:
        return "Could not read GitHub profile.", 502

    if not _is_org_member(token, cfg.github_org, user["login"]):
        log.info("rejected non-member %s for org %s", user["login"], cfg.github_org)
        return (
            f"Access denied: {user['login']} is not a member of "
            f"{cfg.github_org}.",
            403,
        )

    session["user"] = {
        "login": user["login"],
        "name": user.get("name") or user["login"],
        "avatar": user.get("avatar_url", ""),
    }
    log.info("authorized org member %s", user["login"])
    return redirect(url_for("dashboard"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------- #
# GitHub helpers (server-side only)
# --------------------------------------------------------------------------- #
def _exchange_code(cfg, code: str) -> str | None:
    resp = requests.post(
        TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": cfg.oauth_client_id,
            "client_secret": cfg.oauth_client_secret,
            "code": code,
            "redirect_uri": url_for("auth.callback", _external=True),
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


def _fetch_user(token: str) -> dict | None:
    resp = requests.get(
        API_USER,
        headers={"Authorization": f"bearer {token}", "Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def _is_org_member(token: str, org: str, login: str) -> bool:
    """Check membership with the user's own token (needs read:org scope)."""
    resp = requests.get(
        f"https://api.github.com/user/memberships/orgs/{org}",
        headers={"Authorization": f"bearer {token}", "Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        return False
    return resp.json().get("state") == "active"

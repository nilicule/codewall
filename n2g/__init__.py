"""NET2GRID GitHub activity wall: Flask app factory + harvester wiring."""
from __future__ import annotations

import logging
import os

from flask import Flask, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import api, auth
from .config import Config, load_config
from .harvester import Harvester
from .snapshot import Store


class _PrefixMiddleware:
    """Mount the app under a sub-path (URL_PREFIX) behind a reverse proxy.

    Sets SCRIPT_NAME so url_for / redirects emit prefixed URLs, and strips the
    prefix from PATH_INFO when the proxy forwards the full path. This works
    whether nginx strips the prefix (proxy_pass with a trailing slash) or
    forwards it intact.
    """

    def __init__(self, app, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        path = environ.get("PATH_INFO", "")
        if path == self.prefix:
            environ["PATH_INFO"] = "/"
        elif path.startswith(self.prefix + "/"):
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.app(environ, start_response)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("n2g")


def create_app(config: Config | None = None) -> Flask:
    config = config or load_config()
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = config.secret_key
    app.config["N2G"] = config

    # Honour proxy headers (X-Forwarded-Proto/Host) so external URLs use the
    # right scheme/host, and mount under URL_PREFIX when behind a sub-path.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    if config.url_prefix:
        app.wsgi_app = _PrefixMiddleware(app.wsgi_app, config.url_prefix)

    store = Store()
    app.config["N2G_STORE"] = store

    harvester = Harvester(config, store)
    app.config["N2G_HARVESTER"] = harvester

    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)

    @app.get("/")
    @auth.login_required
    def dashboard():
        # base_path is the proxy mount point (e.g. /codewall); the frontend
        # prefixes all of its API/login URLs with it.
        return render_template(
            "dashboard.html",
            user=session.get("user"),
            base_path=request.script_root,
            og_image=url_for("static", filename="og-image.png", _external=True),
            og_url=url_for("dashboard", _external=True),
        )

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "updated_at": store.updated_at()}

    _maybe_start_background(app, harvester)
    return app


def _maybe_start_background(app: Flask, harvester: Harvester) -> None:
    """Start the harvest thread exactly once.

    Under the Werkzeug reloader only the child process (WERKZEUG_RUN_MAIN=true)
    starts it; gunicorn (-w 1) and `flask run --no-reload` start it directly.
    """
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        log.info("reloader parent: skipping harvester start")
        return
    harvester.start()

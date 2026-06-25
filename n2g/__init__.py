"""NET2GRID GitHub activity wall: Flask app factory + harvester wiring."""
from __future__ import annotations

import logging
import os

from flask import Flask, render_template, session

from . import api, auth
from .config import Config, load_config
from .harvester import Harvester
from .snapshot import Store

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

    store = Store()
    app.config["N2G_STORE"] = store

    harvester = Harvester(config, store)
    app.config["N2G_HARVESTER"] = harvester

    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)

    @app.get("/")
    @auth.login_required
    def dashboard():
        return render_template("dashboard.html", user=session.get("user"))

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

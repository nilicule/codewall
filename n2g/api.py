"""JSON API consumed by the dashboard frontend.

Every endpoint reads straight from the in-memory snapshot, no per-request
GitHub calls. Response shapes are aligned to what the prototype's mock
generator produced so the frontend changes stay minimal.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from .auth import login_required

bp = Blueprint("api", __name__, url_prefix="/api")


def _store():
    return current_app.config["N2G_STORE"]


def _limit(default: int, cap: int = 500) -> int:
    try:
        n = int(request.args.get("limit", default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


@bp.get("/stats")
@login_required
def stats():
    return jsonify(_store().stats())


@bp.get("/events/recent")
@login_required
def events_recent():
    return jsonify(_store().recent_events(_limit(60)))


@bp.get("/contributors/active")
@login_required
def contributors_active():
    return jsonify(_store().active_contributors())


@bp.get("/repos/top")
@login_required
def repos_top():
    return jsonify(_store().top_repos(_limit(5)))


@bp.get("/heatmap")
@login_required
def heatmap():
    return jsonify(_store().heatmap())

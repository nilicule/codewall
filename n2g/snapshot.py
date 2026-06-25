"""In-memory snapshot of org activity plus the pure shaping logic.

The snapshot is the cache, not a system of record: a rolling WINDOW_DAYS view
of commits and pull requests, refreshed by the background harvester. Raw
commits/PRs are retained so refreshes can trim the window and recompute the
derived views the API serves.

`build_derived` is a pure function (no clock, no globals) so the snapshot
shaping can be unit tested against recorded data. The Store wraps the mutable
state behind a threading.Lock; readers take a quick copy and never hold the
lock across network calls.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

DAY_SECONDS = 86400


# --------------------------------------------------------------------------- #
# Pure shaping helpers (unit tested)
# --------------------------------------------------------------------------- #
def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _events_from(commits: list[dict], prs: list[dict]) -> list[dict]:
    """Flatten raw commits/PRs into the unified event shape the feed consumes."""
    events: list[dict] = []
    for c in commits:
        events.append(
            {
                "ts": c["ts"],
                "kind": "commit",
                "dev": c["dev"],
                "repo": c["repo"],
                "message": c.get("message", ""),
            }
        )
    for p in prs:
        if p.get("opened_at"):
            events.append(
                {
                    "ts": p["opened_at"],
                    "kind": "opened",
                    "dev": p["dev"],
                    "repo": p["repo"],
                    "message": p.get("title", ""),
                }
            )
        if p.get("merged_at"):
            events.append(
                {
                    "ts": p["merged_at"],
                    "kind": "merged",
                    "dev": p["dev"],
                    "repo": p["repo"],
                    "message": p.get("title", ""),
                }
            )
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events


def build_derived(
    commits: list[dict],
    prs: list[dict],
    people: dict[str, dict],
    now: float,
    window_days: int,
) -> dict[str, Any]:
    """Compute all derived views from raw commits/PRs.

    Args:
        commits: [{sha, dev, repo, ts, message}]
        prs:     [{number, title, dev, repo, state, opened_at, merged_at}]
        people:  {login: {name, avatar}} identity lookup
        now:     epoch seconds "now"
        window_days: rolling window size

    Returns dict with: events, contributors, repo_counts, heatmap, stats.
    Window trimming is applied here so callers always get a clean window.
    """
    cutoff = now - window_days * DAY_SECONDS

    # Window trim: keep commits in window; keep PRs that are still open or had
    # activity (opened/merged) inside the window.
    commits = [c for c in commits if c["ts"] >= cutoff]
    kept_prs = []
    for p in prs:
        in_window = (
            (p.get("opened_at") and p["opened_at"] >= cutoff)
            or (p.get("merged_at") and p["merged_at"] >= cutoff)
            or p.get("state") == "OPEN"
        )
        if in_window:
            kept_prs.append(p)

    events = _events_from(commits, kept_prs)

    # Per-repo event counts over the window.
    repo_counts: dict[str, int] = {}
    for e in events:
        repo_counts[e["repo"]] = repo_counts.get(e["repo"], 0) + 1
    repos_top = [
        {"repo": r, "count": n}
        for r, n in sorted(repo_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # Contributors: recency + recent count + most-recent focus.
    by_dev: dict[str, dict] = {}
    for e in events:  # events are newest-first, so first seen == most recent
        dev = e["dev"]
        entry = by_dev.get(dev)
        if entry is None:
            ident = people.get(dev, {})
            by_dev[dev] = {
                "login": dev,
                "name": ident.get("name") or dev,
                "avatar": ident.get("avatar", ""),
                "last_active": e["ts"],
                "kind": e["kind"],
                "repo": e["repo"],
                "count": 1,
            }
        else:
            entry["count"] += 1
    contributors = sorted(
        by_dev.values(), key=lambda c: c["last_active"], reverse=True
    )

    # Heatmap: per-day counts across the window, ending today (oldest -> newest).
    # Anchoring the last cell on today (not "now - window") means events from the
    # current day land on the final cell, which the frontend uses as a playhead.
    day_counts: dict[str, int] = {}
    for e in events:
        day_counts[_day_key(e["ts"])] = day_counts.get(_day_key(e["ts"]), 0) + 1
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    heatmap = []
    for i in range(window_days):
        d = (today - timedelta(days=window_days - 1 - i)).strftime("%Y-%m-%d")
        heatmap.append({"date": d, "count": day_counts.get(d, 0)})

    # Top-line totals.
    commit_events = [e for e in events if e["kind"] == "commit"]
    prs_open = sum(1 for p in kept_prs if p.get("state") == "OPEN")
    prs_merged = sum(1 for p in kept_prs if p.get("merged_at") and p["merged_at"] >= cutoff)
    stats = {
        "commits": len(commit_events),
        "repos_active": len({e["repo"] for e in events}),
        "prs_open": prs_open,
        "prs_merged": prs_merged,
        "people_active": len(by_dev),
    }

    return {
        "events": events,
        "contributors": contributors,
        "repo_counts": repos_top,
        "heatmap": heatmap,
        "stats": stats,
        # trimmed raw kept so the Store can persist a clean window
        "commits": commits,
        "prs": kept_prs,
    }


# --------------------------------------------------------------------------- #
# Thread-safe store
# --------------------------------------------------------------------------- #
class Store:
    """Module-level snapshot guarded by a lock.

    Writers (the harvester) call `replace` with freshly shaped data. Readers
    (request handlers) call the small accessors which copy under the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "events": [],
            "contributors": [],
            "repo_counts": [],
            "heatmap": [],
            "stats": {
                "commits": 0,
                "repos_active": 0,
                "prs_open": 0,
                "prs_merged": 0,
                "people_active": 0,
            },
            "commits": [],
            "prs": [],
            "people": {},
            "updated_at": 0.0,
        }

    def replace(self, derived: dict[str, Any], people: dict[str, dict], updated_at: float) -> None:
        with self._lock:
            self._data = {**derived, "people": people, "updated_at": updated_at}

    # --- raw read for incremental harvesting / persistence -----------------
    def raw(self) -> dict[str, Any]:
        with self._lock:
            return {
                "commits": list(self._data["commits"]),
                "prs": list(self._data["prs"]),
                "people": dict(self._data["people"]),
                "updated_at": self._data["updated_at"],
            }

    def snapshot_for_persist(self) -> dict[str, Any]:
        with self._lock:
            return {
                "commits": list(self._data["commits"]),
                "prs": list(self._data["prs"]),
                "people": dict(self._data["people"]),
                "updated_at": self._data["updated_at"],
            }

    # --- API accessors ------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["stats"])

    def recent_events(self, limit: int) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._data["events"][:limit]]

    def active_contributors(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "login": c["login"],
                    "name": c["name"],
                    "avatar": c["avatar"],
                    "last_active": c["last_active"],
                    "kind": c["kind"],
                    "repo": c["repo"],
                }
                for c in self._data["contributors"]
            ]

    def top_repos(self, limit: int) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._data["repo_counts"][:limit]]

    def heatmap(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._data["heatmap"]]

    def updated_at(self) -> float:
        with self._lock:
            return self._data["updated_at"]

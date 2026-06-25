"""Unit tests for snapshot shaping (window trim, per-repo counts, recency)."""
from __future__ import annotations

from n2g.snapshot import DAY_SECONDS, Store, build_derived

NOW = 1_750_000_000.0  # fixed clock so tests are deterministic
WINDOW = 90


def _commit(sha, dev, repo, ts, msg="msg"):
    return {"sha": sha, "dev": dev, "repo": repo, "ts": ts, "message": msg}


def _pr(num, dev, repo, state, opened, merged=None, title="t"):
    return {
        "number": num, "dev": dev, "repo": repo, "state": state,
        "opened_at": opened, "merged_at": merged, "title": title,
    }


PEOPLE = {
    "remco": {"name": "Remco", "avatar": "a"},
    "bert": {"name": "Bert", "avatar": "b"},
}


def test_window_trimming_drops_old_commits():
    commits = [
        _commit("new", "remco", "core", NOW - 1 * DAY_SECONDS),
        _commit("old", "bert", "core", NOW - 200 * DAY_SECONDS),  # outside window
    ]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    shas = {c["sha"] for c in out["commits"]}
    assert shas == {"new"}
    assert out["stats"]["commits"] == 1


def test_per_repo_counts_and_top_ordering():
    commits = [
        _commit("c1", "remco", "alpha", NOW - 1 * DAY_SECONDS),
        _commit("c2", "remco", "alpha", NOW - 2 * DAY_SECONDS),
        _commit("c3", "bert", "beta", NOW - 1 * DAY_SECONDS),
    ]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    counts = {r["repo"]: r["count"] for r in out["repo_counts"]}
    assert counts == {"alpha": 2, "beta": 1}
    # sorted by count desc
    assert out["repo_counts"][0]["repo"] == "alpha"


def test_contributor_recency_and_focus():
    commits = [
        _commit("c1", "remco", "alpha", NOW - 5 * DAY_SECONDS),
        _commit("c2", "remco", "beta", NOW - 1 * DAY_SECONDS),  # most recent for remco
        _commit("c3", "bert", "alpha", NOW - 10 * DAY_SECONDS),
    ]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    contribs = {c["login"]: c for c in out["contributors"]}
    assert contribs["remco"]["last_active"] == NOW - 1 * DAY_SECONDS
    assert contribs["remco"]["repo"] == "beta"  # most recent focus
    assert contribs["remco"]["kind"] == "commit"
    assert contribs["remco"]["count"] == 2
    # most-recently-active contributor sorts first
    assert out["contributors"][0]["login"] == "remco"
    assert out["stats"]["people_active"] == 2


def test_pr_states_counts():
    prs = [
        _pr(1, "remco", "alpha", "MERGED", NOW - 5 * DAY_SECONDS, NOW - 4 * DAY_SECONDS),
        _pr(2, "bert", "beta", "OPEN", NOW - 3 * DAY_SECONDS),
        _pr(3, "remco", "alpha", "OPEN", NOW - 2 * DAY_SECONDS),
    ]
    out = build_derived([], prs, PEOPLE, NOW, WINDOW)
    assert out["stats"]["prs_open"] == 2
    assert out["stats"]["prs_merged"] == 1
    # opened + merged events: 3 opened + 1 merged = 4 events
    kinds = [e["kind"] for e in out["events"]]
    assert kinds.count("opened") == 3
    assert kinds.count("merged") == 1


def test_old_merged_pr_outside_window_excluded():
    prs = [_pr(9, "remco", "alpha", "MERGED", NOW - 200 * DAY_SECONDS, NOW - 199 * DAY_SECONDS)]
    out = build_derived([], prs, PEOPLE, NOW, WINDOW)
    assert out["stats"]["prs_merged"] == 0
    assert out["prs"] == []


def test_heatmap_length_and_counts():
    commits = [
        _commit("c1", "remco", "alpha", NOW - 1 * DAY_SECONDS),
        _commit("c2", "bert", "alpha", NOW - 1 * DAY_SECONDS),
    ]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    assert len(out["heatmap"]) == WINDOW
    total = sum(d["count"] for d in out["heatmap"])
    assert total == 2  # both events land in the window
    # the strip ends on "today" so current-day events have a cell (the playhead)
    from datetime import datetime, timezone

    today = datetime.fromtimestamp(NOW, tz=timezone.utc).strftime("%Y-%m-%d")
    assert out["heatmap"][-1]["date"] == today
    assert out["heatmap"][0]["date"] < out["heatmap"][-1]["date"]


def test_events_sorted_newest_first():
    commits = [
        _commit("c1", "remco", "alpha", NOW - 5 * DAY_SECONDS),
        _commit("c2", "bert", "beta", NOW - 1 * DAY_SECONDS),
    ]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    ts = [e["ts"] for e in out["events"]]
    assert ts == sorted(ts, reverse=True)


def test_store_accessors_copy_under_lock():
    store = Store()
    commits = [_commit("c1", "remco", "alpha", NOW - 1 * DAY_SECONDS)]
    out = build_derived(commits, [], PEOPLE, NOW, WINDOW)
    store.replace(out, PEOPLE, NOW)
    assert store.stats()["commits"] == 1
    assert store.top_repos(5)[0]["repo"] == "alpha"
    assert store.recent_events(10)[0]["dev"] == "remco"
    assert store.active_contributors()[0]["login"] == "remco"
    assert len(store.heatmap()) == WINDOW
    # mutating a returned copy must not corrupt the store
    store.recent_events(10).clear()
    assert store.recent_events(10)

"""Harvester tests against a recorded GraphQL response (no live API)."""
from __future__ import annotations

import json
from pathlib import Path

from n2g.config import Config
from n2g.github import GitHubSource
from n2g.harvester import Harvester
from n2g.mockdata import MockSource
from n2g.persist import SnapshotStore
from n2g.snapshot import DAY_SECONDS, Store, build_derived

FIXTURE = Path(__file__).parent / "fixtures" / "graphql_page.json"
NOW = 1_781_000_000.0  # ~2026, after the fixture timestamps


def _load():
    return json.loads(FIXTURE.read_text())


def test_github_source_ingests_recorded_page():
    src = GitHubSource(token="x", org="NET2GRID", window_days=90)
    page = _load()
    repos = page["organization"]["repositories"]["nodes"]
    src._ingest_page(repos)

    commits = list(src._commits.values())
    prs = list(src._prs.values())

    assert len(commits) == 3
    assert {c["repo"] for c in commits} == {"energyai-core", "p1-dongle-fw"}
    assert {c["dev"] for c in commits} == {"remco", "bert"}

    # PR identity + state parsed
    by_num = {p["number"]: p for p in prs}
    assert by_num[42]["state"] == "MERGED"
    assert by_num[42]["merged_at"] is not None
    assert by_num[43]["state"] == "OPEN"
    assert by_num[43]["merged_at"] is None

    # people identity map populated with avatars
    assert src._people["remco"]["name"] == "Remco"
    assert src._people["remco"]["avatar"] == "https://x/remco.png"


def test_github_source_shapes_into_snapshot():
    src = GitHubSource(token="x", org="NET2GRID", window_days=90)
    page = _load()
    src._ingest_page(page["organization"]["repositories"]["nodes"])
    commits = list(src._commits.values())
    prs = list(src._prs.values())
    # use a 'now' close to the fixture dates so they fall in-window
    now = src._commits["bbb111"]["ts"] + DAY_SECONDS
    out = build_derived(commits, prs, dict(src._people), now, 90)
    assert out["stats"]["commits"] == 3
    assert out["stats"]["prs_open"] == 1
    assert out["stats"]["prs_merged"] == 1
    assert out["stats"]["repos_active"] == 2


def test_github_source_trim_drops_out_of_window():
    src = GitHubSource(token="x", org="NET2GRID", window_days=90)
    page = _load()
    src._ingest_page(page["organization"]["repositories"]["nodes"])
    # advance 'now' far past the fixture (latest commit + 200 days) so all age out
    far_future = src._commits["bbb111"]["ts"] + 200 * DAY_SECONDS
    src._trim(far_future)
    assert src._commits == {}
    # only the still-OPEN PR survives a window-trim
    assert all(p["state"] == "OPEN" for p in src._prs.values())


def test_mock_source_seeds_and_ticks():
    src = MockSource(window_days=90, seed=1)
    commits1, prs1, people = src.harvest(NOW)
    assert len(commits1) > 1000
    assert len(people) > 30
    # all within window once shaped
    out = build_derived(commits1, prs1, people, NOW, 90)
    assert out["stats"]["commits"] > 0
    assert out["stats"]["people_active"] > 0
    assert len(out["heatmap"]) == 90
    # a second tick adds fresh activity
    commits2, _, _ = src.harvest(NOW + 10)
    assert len(commits2) > len(commits1)


# --------------------------------------------------------------------------- #
# Persistence guards: mock data must never pollute or be restored into a live
# cache (regression: a mock run wrote SQLite, a later live boot served it).
# --------------------------------------------------------------------------- #
def _cfg(tmp_path, token, org="NET2GRID"):
    c = Config()
    c.github_token = token
    c.github_org = org
    c.cache_persist_path = str(tmp_path / "cache.sqlite")
    return c


def test_mock_mode_never_persists(tmp_path):
    harvester = Harvester(_cfg(tmp_path, token=""), Store())  # no token -> mock
    assert harvester.persist is None


def test_live_restore_accepts_legacy_untagged_cache(tmp_path):
    path = str(tmp_path / "cache.sqlite")
    # an older build wrote no org tag; it is real data (mock can no longer
    # persist), so it is restored rather than discarded.
    SnapshotStore(path).save({"commits": [], "prs": [], "people": {}, "updated_at": 123.0})
    store = Store()
    Harvester(_cfg(tmp_path, token="x"), store)._restore_from_persist()
    assert store.updated_at() == 123.0


def test_live_restore_rejects_other_org(tmp_path):
    path = str(tmp_path / "cache.sqlite")
    SnapshotStore(path).save(
        {"commits": [], "prs": [], "people": {}, "updated_at": 9.0, "org": "OTHER", "live": True}
    )
    store = Store()
    Harvester(_cfg(tmp_path, token="x", org="NET2GRID"), store)._restore_from_persist()
    assert store.updated_at() == 0.0


def test_live_restore_accepts_tagged_same_org(tmp_path):
    path = str(tmp_path / "cache.sqlite")
    SnapshotStore(path).save(
        {"commits": [], "prs": [], "people": {}, "updated_at": 555.0, "org": "NET2GRID", "live": True}
    )
    store = Store()
    Harvester(_cfg(tmp_path, token="x", org="NET2GRID"), store)._restore_from_persist()
    assert store.updated_at() == 555.0

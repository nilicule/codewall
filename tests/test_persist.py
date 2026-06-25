"""SnapshotStore: auto-creates parent dirs, round-trips, degrades gracefully."""
from __future__ import annotations

from n2g.persist import SnapshotStore


def test_creates_missing_parent_dirs_and_round_trips(tmp_path):
    path = tmp_path / "nested" / "dir" / "snapshot.sqlite"  # parents do not exist
    store = SnapshotStore(str(path))
    assert store.ok
    assert path.parent.is_dir()
    store.save({"commits": [1, 2], "org": "NET2GRID", "live": True})
    assert store.load() == {"commits": [1, 2], "org": "NET2GRID", "live": True}


def test_unwritable_path_disables_persistence_without_crashing(tmp_path):
    blocker = tmp_path / "blocker"      # a FILE where a directory is expected
    blocker.write_text("x")
    store = SnapshotStore(str(blocker / "sub" / "snapshot.sqlite"))
    assert not store.ok                 # could not create parent -> disabled
    assert store.load() is None         # no crash
    store.save({"commits": []})         # no crash, no-op


def test_empty_db_loads_none(tmp_path):
    store = SnapshotStore(str(tmp_path / "snapshot.sqlite"))
    assert store.ok
    assert store.load() is None         # nothing saved yet

"""Optional single-file SQLite persistence for the snapshot.

This is opt-in via CACHE_PERSIST_PATH and exists only so the cache survives a
restart without a cold re-harvest. It is one file in the container, stdlib
sqlite3, no server, no ORM: a single table holding one JSON blob (the raw
window). Disabled by default (pure in-memory).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

log = logging.getLogger("n2g.persist")


class SnapshotStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY "
                "CHECK (id = 1), blob TEXT NOT NULL)"
            )

    def load(self) -> dict[str, Any] | None:
        try:
            with sqlite3.connect(self.path) as conn:
                row = conn.execute("SELECT blob FROM snapshot WHERE id = 1").fetchone()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning("could not load persisted snapshot: %s", exc)
            return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def save(self, raw: dict[str, Any]) -> None:
        try:
            blob = json.dumps(raw)
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "INSERT INTO snapshot (id, blob) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET blob = excluded.blob",
                    (blob,),
                )
        except (sqlite3.Error, TypeError) as exc:  # pragma: no cover - defensive
            log.warning("could not persist snapshot: %s", exc)

"""Background harvest loop.

A single daemon thread, started once on boot, owns all GitHub I/O. It refreshes
the snapshot, shapes the derived views, optionally persists the raw window, then
sleeps. Request handlers never touch GitHub: they read the in-memory snapshot.

Single-worker requirement: this thread (and the cache it fills) lives inside one
process. Running multiple workers would spawn multiple harvesters, multiplying
API calls and serving inconsistent data. See the README.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .config import Config
from .github import GitHubSource
from .mockdata import MockSource
from .persist import SnapshotStore
from .snapshot import Store, build_derived

log = logging.getLogger("n2g.harvester")

# Below this remaining GraphQL budget we back off to protect the rate limit.
RATE_LOW_WATERMARK = 200


class Harvester:
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        # Persistence is for surviving restarts of the LIVE harvest. Never persist
        # or restore in mock mode: mock data is cheap to regenerate and must not
        # pollute the cache (which would then be restored on a later live boot).
        self.persist = (
            SnapshotStore(config.cache_persist_path)
            if config.cache_persist_path and not config.use_mock
            else None
        )
        if config.use_mock:
            log.warning("no GITHUB_TOKEN set -> serving MOCK data")
            self.source: MockSource | GitHubSource = MockSource(config.window_days)
        else:
            self.source = GitHubSource(
                config.github_token, config.github_org, config.window_days
            )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _now(self) -> float:
        return datetime.now(tz=timezone.utc).timestamp()

    def _restore_from_persist(self) -> None:
        if not self.persist:
            return
        raw = self.persist.load()
        if not raw:
            return
        # Reject a cache that was written for a DIFFERENT org (switching
        # GITHUB_ORG must not show the old org's data). Legacy untagged caches
        # (no org recorded) are accepted: mock data can no longer reach the cache
        # since persistence is disabled in mock mode, so an untagged cache is
        # real data from an older build.
        org_tag = raw.get("org")
        if org_tag is not None and org_tag != self.config.github_org:
            log.warning(
                "ignoring persisted snapshot at %s (org %s != %s)",
                self.config.cache_persist_path,
                org_tag,
                self.config.github_org,
            )
            return
        log.info("restoring snapshot from %s", self.config.cache_persist_path)
        derived = build_derived(
            raw.get("commits", []),
            raw.get("prs", []),
            raw.get("people", {}),
            self._now(),
            self.config.window_days,
        )
        self.store.replace(derived, raw.get("people", {}), raw.get("updated_at", 0.0))

    def refresh_once(self) -> None:
        """One harvest + shape + store + persist cycle."""
        now = self._now()
        commits, prs, people = self.source.harvest(now)
        derived = build_derived(commits, prs, people, now, self.config.window_days)
        self.store.replace(derived, people, now)
        if self.persist:
            snapshot = self.store.snapshot_for_persist()
            snapshot["org"] = self.config.github_org
            snapshot["live"] = True
            self.persist.save(snapshot)

    def _sleep_seconds(self) -> float:
        if self.config.use_mock:
            return self.config.mock_refresh_seconds
        # Back off when the GraphQL budget runs low.
        remaining = getattr(self.source, "last_rate_remaining", None)
        if remaining is not None and remaining < RATE_LOW_WATERMARK:
            log.warning("rate budget low (%s) -> backing off", remaining)
            return max(self.config.refresh_seconds * 4, 600)
        return self.config.refresh_seconds

    def _loop(self) -> None:
        self._restore_from_persist()
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("harvest failed; will retry next cycle")
            self._stop.wait(self._sleep_seconds())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="harvester", daemon=True
        )
        self._thread.start()
        log.info("harvester thread started")

    def stop(self) -> None:
        self._stop.set()

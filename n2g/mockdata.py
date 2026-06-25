"""Mock data source used when no GITHUB_TOKEN is set.

Produces the same shapes the real harvester yields (raw commits + PRs + a
people identity map) so the UI, API and shaping logic can all run with zero
infrastructure. The mock harvest seeds a 90-day backlog once, then appends a
handful of fresh events on every tick so the wall keeps animating locally.
"""
from __future__ import annotations

import itertools
import random

from .snapshot import DAY_SECONDS

PEOPLE = [
    "remco", "bert", "gerasimos", "christos", "anna", "ab", "martijn", "emily",
    "hans", "wouter", "sanne", "lucas", "femke", "daan", "noor", "tijn",
    "joost", "lieke", "bram", "fleur", "sven", "maud", "ravi", "priya",
    "diego", "sofia", "aiden", "claire", "omar", "yuki", "mila", "kees",
    "roos", "jasper", "ilse", "teun", "saar", "gijs",
]
REPOS = [
    "energyai-core", "p1-dongle-fw", "ami-datalink", "roadmaphub", "streamer",
    "ev-agent", "snowpipe-etl", "ismsync", "cra-sbom", "webhook-relay",
    "fargate-stream", "cognito-edge", "kev-notify", "dpa-tools", "terraform-infra",
]
COMMIT_MSGS = [
    "fix rate-limit backoff on graphql sync", "add incremental cursor cache",
    "tune fargate autoscaling thresholds", "patch task-hijacking via min SDK",
    "wire KEV notification flow", "refactor lambda to streaming consumer",
    "bump dependency + close advisory", "add SBOM generation step",
    "handle duplicate-CN cert eviction", "redline pen-test audit clause",
]
PR_TITLES = [
    "Streaming consumer for AMI datalink", "Harden Cognito edge auth",
    "Snowpipe ETL backfill job", "KEV notification webhooks",
    "Terraform module for Fargate", "SBOM generation in CI",
]


class MockSource:
    """Stateful mock GitHub source.

    `harvest()` returns the full current (commits, prs, people) just like the
    real source. The first call seeds the window; later calls add a few recent
    events and trim is handled downstream by `build_derived`.
    """

    def __init__(self, window_days: int, seed: int = 42) -> None:
        self.window_days = window_days
        self.rnd = random.Random(seed)
        self._sha = itertools.count(1)
        self._prnum = itertools.count(1)
        self.commits: list[dict] = []
        self.prs: list[dict] = []
        self.people = {
            login: {"name": login.capitalize(), "avatar": ""} for login in PEOPLE
        }
        self._seeded = False

    def _new_commit(self, ts: float) -> dict:
        return {
            "sha": f"{next(self._sha):040x}",
            "dev": self.rnd.choice(PEOPLE),
            "repo": self.rnd.choice(REPOS),
            "ts": ts,
            "message": self.rnd.choice(COMMIT_MSGS),
        }

    def _new_pr(self, opened_at: float, merged: bool) -> dict:
        return {
            "number": next(self._prnum),
            "title": self.rnd.choice(PR_TITLES),
            "dev": self.rnd.choice(PEOPLE),
            "repo": self.rnd.choice(REPOS),
            "state": "MERGED" if merged else "OPEN",
            "opened_at": opened_at,
            "merged_at": (opened_at + self.rnd.uniform(1, 5) * 3600) if merged else None,
        }

    def _seed(self, now: float) -> None:
        span = self.window_days * DAY_SECONDS
        # Dense commit backlog across the window.
        for _ in range(1400):
            self.commits.append(self._new_commit(now - self.rnd.uniform(0, span)))
        # A spread of PRs, most merged, some still open.
        for _ in range(220):
            opened = now - self.rnd.uniform(0, span)
            self.prs.append(self._new_pr(opened, merged=self.rnd.random() < 0.78))
        self._seeded = True

    def harvest(self, now: float) -> tuple[list[dict], list[dict], dict[str, dict]]:
        if not self._seeded:
            self._seed(now)
        else:
            # Append a small burst of fresh activity each tick.
            for _ in range(self.rnd.randint(2, 6)):
                self.commits.append(self._new_commit(now - self.rnd.uniform(0, 90)))
            if self.rnd.random() < 0.5:
                self.prs.append(
                    self._new_pr(now - self.rnd.uniform(0, 120), merged=self.rnd.random() < 0.4)
                )
        return list(self.commits), list(self.prs), dict(self.people)

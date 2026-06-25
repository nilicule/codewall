"""GitHub GraphQL v4 harvesting.

Uses GraphQL (not REST) and batches repositories per page so we stay efficient
across hundreds of repos. The token is server-side only and never reaches the
client. Harvesting is incremental: commit history is fetched `since` the last
harvest time, and PRs are pulled ordered by most-recently-updated so we only
look at what changed.

`GitHubSource.harvest(now)` returns (commits, prs, people) accumulated in the
rolling window, matching the shape `mockdata.MockSource.harvest` yields.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .snapshot import DAY_SECONDS

log = logging.getLogger("n2g.github")

GRAPHQL_URL = "https://api.github.com/graphql"

# One page of repos, each with its recent commit history and PRs. `since`
# filters commit history server-side so refreshes stay incremental.
REPO_PAGE_QUERY = """
query($org:String!, $cursor:String, $since:GitTimestamp!) {
  rateLimit { remaining cost resetAt }
  organization(login:$org) {
    repositories(first:25, after:$cursor, orderBy:{field:PUSHED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        defaultBranchRef {
          target {
            ... on Commit {
              history(first:100, since:$since) {
                nodes {
                  oid
                  committedDate
                  messageHeadline
                  author { user { login name avatarUrl } name }
                }
              }
            }
          }
        }
        pullRequests(first:30, orderBy:{field:UPDATED_AT, direction:DESC},
                     states:[OPEN, MERGED]) {
          nodes {
            number
            title
            state
            createdAt
            mergedAt
            author { login ... on User { name avatarUrl } }
          }
        }
      }
    }
  }
}
"""


def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    ).timestamp()


def _epoch_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHubSource:
    def __init__(self, token: str, org: str, window_days: int) -> None:
        self.org = org
        self.window_days = window_days
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"bearer {token}",
                "Accept": "application/json",
                "User-Agent": "net2grid-activity-wall",
            }
        )
        # Rolling raw window, keyed for incremental de-duplication.
        self._commits: dict[str, dict] = {}  # oid -> commit
        self._prs: dict[tuple[str, int], dict] = {}  # (repo, number) -> pr
        self._people: dict[str, dict] = {}
        self._last_harvest: float | None = None
        self.last_rate_remaining: int | None = None

    # ----------------------------------------------------------------- #
    def _post(self, variables: dict) -> dict:
        resp = self.session.post(
            GRAPHQL_URL,
            json={"query": REPO_PAGE_QUERY, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload and payload["errors"]:
            log.warning("graphql errors: %s", payload["errors"])
        return payload.get("data") or {}

    def _record_person(self, author: dict | None) -> str | None:
        """Pull a login + identity out of a GraphQL author node."""
        if not author:
            return None
        user = author.get("user") or {}
        login = user.get("login") or author.get("login")
        if not login:
            return None
        name = user.get("name") or author.get("name") or login
        avatar = user.get("avatarUrl") or author.get("avatarUrl") or ""
        self._people[login] = {"name": name, "avatar": avatar}
        return login

    def _ingest_page(self, repos: list[dict]) -> None:
        for repo in repos:
            name = repo["name"]
            ref = repo.get("defaultBranchRef") or {}
            target = ref.get("target") or {}
            history = (target.get("history") or {}).get("nodes") or []
            for c in history:
                login = self._record_person(c.get("author"))
                ts = _iso_to_epoch(c.get("committedDate"))
                if login is None or ts is None:
                    continue
                self._commits[c["oid"]] = {
                    "sha": c["oid"],
                    "dev": login,
                    "repo": name,
                    "ts": ts,
                    "message": c.get("messageHeadline", ""),
                }
            for p in (repo.get("pullRequests") or {}).get("nodes") or []:
                login = self._record_person(p.get("author"))
                if login is None:
                    continue
                self._prs[(name, p["number"])] = {
                    "number": p["number"],
                    "title": p.get("title", ""),
                    "dev": login,
                    "repo": name,
                    "state": p.get("state", "OPEN"),
                    "opened_at": _iso_to_epoch(p.get("createdAt")),
                    "merged_at": _iso_to_epoch(p.get("mergedAt")),
                }

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_days * DAY_SECONDS
        self._commits = {k: c for k, c in self._commits.items() if c["ts"] >= cutoff}
        kept = {}
        for key, p in self._prs.items():
            if (
                p.get("state") == "OPEN"
                or (p.get("opened_at") and p["opened_at"] >= cutoff)
                or (p.get("merged_at") and p["merged_at"] >= cutoff)
            ):
                kept[key] = p
        self._prs = kept

    # ----------------------------------------------------------------- #
    def harvest(self, now: float) -> tuple[list[dict], list[dict], dict[str, dict]]:
        """Pull new activity since the last harvest and return the window."""
        # Incremental: only look back to the last harvest (minus slack) on
        # refreshes; on the first run scan the full window.
        if self._last_harvest is None:
            since = now - self.window_days * DAY_SECONDS
        else:
            since = self._last_harvest - 3600  # 1h overlap to avoid gaps
        since_iso = _epoch_to_iso(since)

        cursor = None
        pages = 0
        while True:
            data = self._post({"org": self.org, "cursor": cursor, "since": since_iso})
            rate = data.get("rateLimit") or {}
            if rate:
                self.last_rate_remaining = rate.get("remaining")
            org = data.get("organization") or {}
            repos_conn = org.get("repositories") or {}
            self._ingest_page(repos_conn.get("nodes") or [])
            page_info = repos_conn.get("pageInfo") or {}
            pages += 1
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        self._last_harvest = now
        self._trim(now)
        log.info(
            "harvest complete: %d pages, %d commits, %d prs, rate_remaining=%s",
            pages,
            len(self._commits),
            len(self._prs),
            self.last_rate_remaining,
        )
        return list(self._commits.values()), list(self._prs.values()), dict(self._people)

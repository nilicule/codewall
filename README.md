# NET2GRID Activity Wall

A self-contained Flask app that shows live GitHub activity across the NET2GRID
organisation: a cinematic spiral constellation hero, a live commit/PR stream, a
"who is working where" roster, a per-repo activity breakdown, a 90-day
contribution heatmap, and an "org pulse" ECG that beats with every event. Viewers
log in once and watch the org work in real time.

The UI is the approved prototype (`dashboard.html`), served as the dashboard
template with its mock data generator swapped for `fetch()` calls to the Flask
API. Everything runs in one container with no external services.

## Quick start (local, no secrets)

With no GitHub token set, the app serves mock data so the whole UI runs with
zero infrastructure.

```bash
uv sync
DEV_AUTH_BYPASS=1 uv run flask --app app run --port 8000
# open http://127.0.0.1:8000
```

`DEV_AUTH_BYPASS=1` skips OAuth and treats every request as an org member. It is
clearly logged as insecure and must never be set in production.

Run the tests:

```bash
uv run pytest tests/test_snapshot.py tests/test_harvester.py tests/test_auth.py  # units
uv run playwright install chromium                                               # one-time
uv run pytest tests/test_smoke.py                                                # browser smoke test
uv run pytest                                                                    # everything
```

The suite never touches the live API (mock data, fake GraphQL fixtures).

## Running against real GitHub

Configuration lives in a `.env` file in the project root (loaded automatically).
Start from the template:

```bash
cp .env.example .env
```

**1. Harvest token.** Create a read-only GitHub token for reading org activity.
A fine-grained PAT with read access to the org's repositories (Contents,
Metadata, Pull requests) is enough; a GitHub App installation token also works.
It is used server-side only and never sent to the client.

**2. Access key (viewer gate).** The simplest gate is one shared secret. Generate
one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

(Prefer per-user GitHub login instead? See "Gating who can view" below.)

**3. Session signing key.**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**4. Fill in `.env`:**

```ini
GITHUB_TOKEN=github_pat_...          # from step 1
GITHUB_ORG=NET2GRID
ACCESS_TOKEN=...                     # from step 2
SECRET_KEY=...                       # from step 3
```

**5. Boot:**

```bash
uv sync
uv run flask --app app run --port 8000      # dev server
# or, for production (single worker, see below):
uv run gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app
```

Open http://127.0.0.1:8000 and enter the access key once. The first full harvest
scans the whole window and can take a couple of minutes across hundreds of repos;
the dashboard shows zeros until it completes, then refreshes incrementally every
`REFRESH_SECONDS`. Serve behind HTTPS in production.

### Gating who can view (pick one)

The dashboard and all `/api/*` routes require a session; only the login route and
static assets are public. The session is a Flask signed cookie (`SECRET_KEY`), so
there is no session store and we stay single-container. Three gates, in precedence
order:

1. **Shared secret (simplest).** Set `ACCESS_TOKEN` to a long random string.
   Viewers open `/login`, enter the key once, and get a signed-cookie session. No
   GitHub OAuth app, no per-user identity: anyone with the key can view. Good for
   an internal wall, especially behind HTTPS.
2. **GitHub OAuth.** Set `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` (OAuth app with
   callback `https://<your-host>/callback`). GitHub login -> verify the user is an
   active member of `GITHUB_ORG` (using the user's `read:org` token) -> session.
   Per-user identity and automatic member-only enforcement.
3. **`DEV_AUTH_BYPASS=1`.** Skips auth entirely for local dev. Insecure; never in
   production.

If none are set, the app stays locked. Note the harvest `GITHUB_TOKEN` is a
separate, server-side, read-only credential (repo Contents/Metadata/Pull requests
read) and is never sent to the client; with the shared-secret gate it needs no
`read:org` scope. You can also drop app auth entirely and put the container behind
an identity-aware proxy or private network (Cloudflare Access, Tailscale, VPN).

## Configuration

All configuration is via environment variables (see `.env.example`). A `.env`
file in the project root is loaded automatically in local dev. Live versus mock
is decided solely by whether `GITHUB_TOKEN` is non-empty. Note that values which
are unset OR empty in the environment are filled from `.env`, so a stray empty
`GITHUB_TOKEN=` export in your shell will not silently shadow the real token and
force mock. To deliberately run mock while a token sits in `.env`, comment that
line out, or run with `N2G_SKIP_DOTENV=1` and an empty `GITHUB_TOKEN`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | (empty) | Server-side token. Empty = serve mock data. |
| `GITHUB_ORG` | `NET2GRID` | Organisation to harvest. |
| `WINDOW_DAYS` | `90` | Rolling activity window. |
| `REFRESH_SECONDS` | `120` | Seconds between real harvests. |
| `MOCK_REFRESH_SECONDS` | `3` | Tick interval in mock mode. |
| `SECRET_KEY` | dev value | Signs the session cookie. Set a strong value in prod. |
| `ACCESS_TOKEN` | (empty) | Shared access key for the simple viewer gate. |
| `OAUTH_CLIENT_ID` | (empty) | GitHub OAuth app client id (alternative gate). |
| `OAUTH_CLIENT_SECRET` | (empty) | GitHub OAuth app client secret. |
| `DEV_AUTH_BYPASS` | `0` | `1` = skip the viewer gate entirely. Local only, insecure. |
| `CACHE_PERSIST_PATH` | (empty) | Path to a SQLite file. Empty = pure in-memory. |
| `N2G_SKIP_DOTENV` | (empty) | `1` = ignore `.env` (used by tests to force mock). |

## How the cache and background refresh work

This dashboard renders a rolling 90-day window of org activity that changes only
every few minutes. That is a cache, not a system of record, so no database is
needed.

* The harvested data lives in a module-level Python object (`n2g.snapshot.Store`)
  guarded by a `threading.Lock`.
* A single daemon `threading.Thread` (`n2g.harvester.Harvester`), started once on
  boot, runs the loop: harvest -> shape -> store -> sleep `REFRESH_SECONDS` ->
  repeat. It is the only code that talks to GitHub.
* Request handlers never call GitHub. They take a quick copy of the snapshot
  under the lock and return it. The lock is never held across network calls.
* Harvesting uses the GitHub GraphQL API v4 (not REST) and batches 25 repos per
  page so we stay efficient across hundreds of repos. It is incremental: commit
  history is fetched `since` the last harvest and PRs are pulled most-recently-
  updated first, so refreshes pull only what changed. The rolling window is
  trimmed on every cycle.
* GraphQL gives 5000 points/hour. The remaining budget is logged after each
  refresh, and the loop backs off (sleeps longer) when the budget runs low.

The frontend polls the JSON API on an interval and animates events through the
prototype's existing render functions (`firePulse`, the roster, the bars, the
feed). Real org activity is sparse (a few events every couple of minutes), so a
continuous animator walks a rolling pool of recent events and loops back to the
start when it reaches the end, keeping the hero beams, the "floor" roster and
the Data Stream feed alive between refreshes; each poll appends genuinely-new
events to the pool and plays them next. The same stream drives the live reactions
across the wall: each animated event fires its hero beam, raises and flashes its
author on the "floor" roster, flashes its repo in "Where the work lands", and
blooms its day in the contribution-density strip, and pumps the "org pulse"
voicebox in the top-left (a KITT-style row of segmented LED columns whose loudness
tracks recent event energy, tinted by event kind). So every panel reacts to real
events rather than sitting
on static totals; the window totals themselves live in a compact caption under the
ECG. The density strip also carries an ambient shimmer: every day breathes faintly,
scaled by that day's real activity, so busy past weeks glimmer while quiet ones stay
dark. The repo bars carry a perpetual sheen so they stay alive when totals hold
steady. The totals and bar widths stay authoritative from their endpoints. Polling,
not websockets, keeps this single-container and simple.

## The single-worker requirement (important)

The state is in memory, so the app MUST run as ONE process. Run a single worker:

```bash
gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app
```

Do NOT run multiple Gunicorn workers. Each worker would get its own copy of the
cache and its own background harvester, which would multiply GitHub API calls and
serve inconsistent data depending on which worker handled a request. Scale via
the cache plus a CDN in front of the app, not via more processes. Use threads
(`--threads`) for concurrency within the one worker. The background harvest
thread starts on app import/boot, not per request.

## JSON API

All endpoints read straight from the in-memory snapshot (no per-request GitHub
calls) and require an authenticated session. Only the login routes and static
assets are public.

| Endpoint | Returns |
| --- | --- |
| `GET /api/stats` | `{commits, repos_active, prs_open, prs_merged, people_active}` |
| `GET /api/events/recent?limit=N` | reverse-chronological commits + PRs: `{ts, kind, dev, repo, message?}` |
| `GET /api/contributors/active` | `{login, name, avatar, last_active, kind, repo}` per active contributor |
| `GET /api/repos/top?limit=5` | `[{repo, count}]` |
| `GET /api/heatmap` | per-day counts for the 90-day strip: `[{date, count}]` |
| `GET /healthz` | liveness + snapshot `updated_at` |

## Optional persistence

By default the cache is pure in-memory and a restart triggers a cold
re-harvest. Set `CACHE_PERSIST_PATH=/data/snapshot.sqlite` to persist the
snapshot across restarts. This is one local SQLite file (stdlib `sqlite3`, no
server, no ORM): a single table holding one JSON blob of the raw window, read
once on boot and written after each refresh. It does not violate the
"no external services" rule.

## Docker

```bash
docker build -t net2grid-wall .
docker run -p 8000:8000 \
  -e GITHUB_TOKEN=... -e GITHUB_ORG=NET2GRID \
  -e ACCESS_TOKEN=... -e SECRET_KEY=... \
  net2grid-wall
# or pass your filled-in .env directly:
docker run -p 8000:8000 --env-file .env net2grid-wall
```

The image runs exactly one Gunicorn worker with eight threads (see above).

## Future: webhooks instead of polling

Today the frontend polls and the harvester refreshes on an interval. To make
updates push-based you could register GitHub organisation webhooks (push,
pull_request) pointing at a new `POST /webhook` route that validates the
signature and applies the single event to the in-memory snapshot under the lock,
then have the frontend receive updates via Server-Sent Events. That would cut
latency and API usage, but it adds a public ingress endpoint and signature
handling, so it is intentionally out of scope here. The current polling model
keeps the system single-container and simple.

## Project layout

```
app.py              entry point (flask --app app / gunicorn app:app)
n2g/
  __init__.py       Flask app factory + harvester boot
  config.py         env-driven configuration
  snapshot.py       in-memory Store (lock) + pure shaping logic
  github.py         GraphQL v4 incremental harvester
  mockdata.py       mock source used when no token is set
  harvester.py      background refresh thread
  auth.py           viewer gate: shared access key or GitHub OAuth
  api.py            JSON API blueprint
  persist.py        optional single-file SQLite persistence
templates/dashboard.html   the prototype, wired to the API
tests/              harvester + snapshot + auth units, Playwright smoke test
```

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
DEV_AUTH_BYPASS=1 uv run flask --app app run --port 5008
# open http://127.0.0.1:5008
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

(Prefer per-user Google Workspace login instead? See "Gating who can view" below.)

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
uv run flask --app app run --port 5008      # dev server
# or, for production (single worker, see below):
uv run gunicorn -w 1 --threads 8 -b 0.0.0.0:5008 app:app
```

Open http://127.0.0.1:5008 and enter the access key once. The first full harvest
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
   OAuth app, no per-user identity: anyone with the key can view. Good for
   an internal wall, especially behind HTTPS.
2. **Google Workspace OAuth.** Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
   (OAuth client with callback `https://<your-host>/callback`). Google login ->
   the signed `id_token` is verified and the account must be a verified member of
   `ALLOWED_EMAIL_DOMAIN` (default `net2grid.com`) -> session. Per-user identity
   and automatic domain-only enforcement.
3. **`DEV_AUTH_BYPASS=1`.** Skips auth entirely for local dev. Insecure; never in
   production.

If none are set, the app stays locked. Note the harvest `GITHUB_TOKEN` is a
separate, server-side, read-only credential (repo Contents/Metadata/Pull requests
read) and is never sent to the client; with the shared-secret gate it needs no
`read:org` scope. You can also drop app auth entirely and put the container behind
an identity-aware proxy or private network (Cloudflare Access, Tailscale, VPN).

### Setting up Google Workspace login

One-time setup in the [Google Cloud Console](https://console.cloud.google.com/),
signed in with a `net2grid.com` account:

1. **Project** — create (or reuse) a project, e.g. `codewall-auth`, and make sure
   it is selected.
2. **OAuth consent screen** (APIs & Services → OAuth consent screen) — set
   **User type: Internal**. This restricts sign-in to `net2grid.com` Workspace
   accounts and skips Google's app-verification process. Fill in the app name and
   support/developer emails. The `openid`, `email`, and `profile` scopes are basic
   and need not be added explicitly.
3. **Credentials** (APIs & Services → Credentials) → **Create Credentials → OAuth
   client ID** → **Application type: Web application**. Under **Authorized redirect
   URIs**, add the callback URL — it must match exactly what the app builds,
   `<scheme>://<host><URL_PREFIX>/callback`:
   - root: `https://your-host.example.com/callback`
   - sub-path (`URL_PREFIX=/codewall`): `https://your-host.example.com/codewall/callback`
   - local: `http://localhost:5008/callback`

   Add every host you use (prod + localhost). The match is exact — scheme, host,
   and path all count, with no trailing slash.
4. **Copy the Client ID and Client secret** into the environment as
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (plus a strong `SECRET_KEY`, and
   optionally `ALLOWED_EMAIL_DOMAIN` if not `net2grid.com`). Leave `ACCESS_TOKEN`
   and `DEV_AUTH_BYPASS` unset — they outrank the Google gate.

Viewers then land on a `/login` card with a **Continue with Google** button. The
`redirect_uri` is derived from the `X-Forwarded-Proto`/`X-Forwarded-Host` headers
(via `ProxyFix`), so a reverse proxy must set those correctly or Google returns
`redirect_uri_mismatch`. No additional Google APIs need enabling — sign-in uses the
default OpenID Connect endpoints.

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
| `GOOGLE_CLIENT_ID` | (empty) | Google OAuth client id (Workspace viewer gate). |
| `GOOGLE_CLIENT_SECRET` | (empty) | Google OAuth client secret. |
| `ALLOWED_EMAIL_DOMAIN` | `net2grid.com` | Workspace domain allowed to sign in. |
| `DEV_AUTH_BYPASS` | `0` | `1` = skip the viewer gate entirely. Local only, insecure. |
| `CACHE_PERSIST_PATH` | (empty) | Path to a SQLite file. Empty = pure in-memory. |
| `URL_PREFIX` | (empty) | Sub-path mount when behind a proxy (e.g. `/codewall`). |
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
gunicorn -w 1 --threads 8 -b 0.0.0.0:5008 app:app
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
re-harvest. Set `CACHE_PERSIST_PATH` to persist the snapshot across restarts.
This is one local SQLite file (stdlib `sqlite3`, no server, no ORM): a single
table holding one JSON blob of the raw window, read once on boot and written
after each refresh. It does not violate the "no external services" rule.

Use a **relative** path so the same value works in both environments:

```ini
CACHE_PERSIST_PATH=data/snapshot.sqlite
```

The parent directory is created automatically. In local dev it resolves under
the project directory (`./data/snapshot.sqlite`, gitignored). In the container
it resolves under `WORKDIR /app` (`/app/data/snapshot.sqlite`); the image
declares `/app/data` as a volume, so mount one to keep the snapshot across
container recreations:

```bash
docker run -p 5008:5008 --env-file .env -v n2g-cache:/app/data net2grid-wall
```

Persistence is also skipped in mock mode, and a path that cannot be opened (for
example an unwritable absolute path like `/data/...` on your laptop) disables
persistence with a warning rather than crashing the app.

## Behind a reverse proxy (sub-path)

To serve the wall under a sub-path (for example `https://host/codewall/`), set
`URL_PREFIX=/codewall`. The app then mounts there: `url_for`, redirects and the
login URL emit `/codewall/...`, and the frontend prefixes all of its API/login
URLs with it (injected as `BASE`). It also honours `X-Forwarded-Proto/Host` so
external URLs use the right scheme and host.

`URL_PREFIX` works whether nginx strips the prefix or forwards it intact:

```nginx
location /codewall/ {
    proxy_pass         http://app:5008/;   # trailing slash: nginx strips /codewall
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_set_header   X-Forwarded-Host  $host;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
}
```

Run the container with `-e URL_PREFIX=/codewall`. Served at the domain root,
leave `URL_PREFIX` empty and nothing changes.

## Docker

```bash
docker build --network=host -t codewall .
docker run -p 5008:5008 --network host \
  -e GITHUB_TOKEN=... -e GITHUB_ORG=NET2GRID \
  -e ACCESS_TOKEN=... -e SECRET_KEY=... \
  net2grid-wall
# or pass your filled-in .env directly:
docker run -p 5008:5008 --network host --env-file .env codewall
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
  auth.py           viewer gate: shared access key or Google Workspace OAuth
  api.py            JSON API blueprint
  persist.py        optional single-file SQLite persistence
templates/dashboard.html   the prototype, wired to the API
tests/              harvester + snapshot + auth units, Playwright smoke test
```

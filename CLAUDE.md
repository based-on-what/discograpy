# CLAUDE.md - DiscograPY

Development reference for AI-assisted work on this codebase.
For end-user documentation see `README.md` / `README.es.md`.

---

## What this project does

Flask web app + CLI that creates Spotify discography playlists.
Given an artist name, it fetches all their albums/EPs/singles via the
Spotify API, filters/deduplicates tracks, and creates a playlist on the
authenticated user's account.

---

## Project layout

```
app.py                      Flask entry point (loads .env, creates app)
playlists.py                CLI entry point
get_token.py                One-time local helper: prints a fresh SPOTIPY_REFRESH_TOKEN
src/
  config.py                 Spotify client factory - only place SpotifyOAuth is constructed
                            (build_spotify_client for the app, build_interactive_auth
                            for get_token.py)
  logging_config.py         UTF-8-safe logging to file + console
  domain/
    album_types.py          7 album-type modes, EP detection logic, playlist suffix
    filters.py              Content filters (live/demo/remix/instrumental), deduplication,
                            and select_tracks(): the pure track-selection pipeline
    models.py               RunSummary dataclass (returned by build_playlist)
  services/
    discography.py          DiscographyService - main orchestrator
    spotify_client.py       SpotifyClient - thin wrapper over spotipy + retries
    musicbrainz.py          Artist metadata enrichment (genre, country); non-fatal on failure
    retry.py                @retry_on_failure decorator with exponential backoff + 429 support
    railway_cache.py        RailwayAwareCacheHandler - auto-updates SPOTIPY_REFRESH_TOKEN
                            in Railway if Spotify rotates it; falls back to MemoryCacheHandler
  web/
    __init__.py             Flask app factory; holds _client and _service singletons
    limiter.py              Flask-Limiter instance + the per-route limit strings
    jobs.py                 In-memory background job store (ThreadPoolExecutor)
    routes.py               All HTTP routes: /, /callback, /api/album-types,
                            /api/search, /api/create, /api/jobs/<id>
  cli/
    runner.py               CLI orchestration
    ui.py                   Spinner, menus, artist/summary display
templates/
  index.html                Single-page frontend (vanilla HTML/CSS/JS, no build step)
```

---

## Architecture - key decisions

### Singletons in `src/web/__init__.py`
`_client` (SpotifyClient) and `_service` (DiscographyService) are module-level
singletons, initialized lazily on first request and reused for the process lifetime.
The `MemoryCacheHandler` in `config.py` keeps the OAuth token in memory - this is
intentional because Railway's filesystem is ephemeral.

**Do not** move client/service construction into request handlers or create new
instances per request - that breaks token caching and concurrency.

### Token flow (production / Railway)
```
SPOTIPY_REFRESH_TOKEN (env var)
  -> config.py: RailwayAwareCacheHandler(token_info={..., expires_at: 0})
  -> SpotifyOAuth with cache_handler (no file I/O)
  -> First API call: spotipy detects expires_at=0, calls Spotify token endpoint
  -> Fresh access token stored in MemoryCacheHandler (memory only)
  -> Auto-refreshed every ~60 min by spotipy internally
  -> If Spotify rotates refresh token: RailwayAwareCacheHandler calls Railway
    GraphQL API to update SPOTIPY_REFRESH_TOKEN env var automatically
```

### Token flow (local development)
With `SPOTIPY_USE_CACHE=true`, uses `CacheFileHandler` (`.spotify_cache`) and
opens a browser for the OAuth flow. Without a `SPOTIPY_REFRESH_TOKEN`, each
server start requires manual re-auth via `/callback`.

---

## Critical Spotify API constraints

These are hard limits that cause 400 errors if exceeded.

| Method | Correct limit | Wrong (do not use) |
|---|---|---|
| `sp.search()` | `limit=50` | ~~`limit=100`~~ |
| `sp.artist_albums()` | `limit=50` | ~~`limit=100`~~ |
| `sp.album_tracks()` | `limit=50` | ~~`limit=100`~~ |
| `sp.playlist_add_items()` | max 100 tracks/call | handled via batching |

The `_paginate()` method in `SpotifyClient` handles multi-page results automatically
using `sp.next()` - never manually increment offsets.

---

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `SPOTIPY_CLIENT_ID` | yes | From Spotify Developer Dashboard |
| `SPOTIPY_CLIENT_SECRET` | yes | From Spotify Developer Dashboard |
| `SPOTIPY_REDIRECT_URI` | yes | Must match Dashboard exactly |
| `SPOTIPY_REFRESH_TOKEN` | yes (web) | Obtain once with `get_token.py` |
| `FLASK_SECRET_KEY` | recommended | Falls back to insecure dev default |
| `SPOTIPY_USE_CACHE` | local dev | `true` enables `.spotify_cache` file |
| `RAILWAY_API_TOKEN` | Railway only | Enables auto-update of refresh token |
| `RAILWAY_PROJECT_ID` | Railway only | Auto-injected by Railway |
| `RAILWAY_ENVIRONMENT_ID` | Railway only | Auto-injected by Railway |
| `RAILWAY_SERVICE_ID` | Railway only | Auto-injected by Railway |

---

## Running locally

```bash
pip install -r requirements.txt

# Web app
python app.py              # dev server on :5000
gunicorn app:app --bind 0.0.0.0:5000  # production-style

# CLI
python playlists.py
python playlists.py --dry-run --verbose

# Get/refresh a SPOTIPY_REFRESH_TOKEN (opens browser)
python get_token.py
```

---

## Spotify OAuth scopes

Always use exactly this scope string (defined as `SCOPE` in `src/config.py`):

```
playlist-modify-public playlist-modify-private ugc-image-upload
```

If you add a scope, re-run `get_token.py` and update `SPOTIPY_REFRESH_TOKEN`.
The `ugc-image-upload` scope is required for the artist-image-as-cover feature.

---

## Content filtering and deduplication

All filter logic lives in `src/domain/filters.py`. `select_tracks()` there is the
whole selection pipeline (ordering, filters, instrumental matching, dedup) as a
pure function; `DiscographyService.collect_tracks()` only fetches each album's
tracks concurrently and delegates to it.

- Filters operate on **both** track name and album name - a "live" album taints
  all its tracks regardless of individual track names.
- `include_instrumentals` has special logic: an instrumental is only kept if its
  non-instrumental counterpart also exists in the collected set. This is pre-computed
  before the per-track loop in `select_tracks()`.
- Deduplication normalizes titles by stripping bracket content and filter keywords,
  then keeps the highest-priority version (LP > EP > Single).
- `include_duplicate_versions=True` disables deduplication entirely - all URIs
  are returned as-is.

---

## Background jobs - /api/create is asynchronous

Building a playlist for a large discography takes minutes. `/api/create` no
longer does the work inline:

```
POST /api/create      validates input, submits to jobs.py, returns 202 {job_id}
GET  /api/jobs/<id>   200 {"status": "pending"}
                      200 {"status": "done",  "result": {...}}
                      200 {"status": "error", "error": ..., "status_code": ...}
                      404 unknown or expired job
```

The poll always answers 200 when the job exists; the *original* HTTP status
travels in the body as `status_code`, and `JobError` extras (e.g.
`auth_required: True`) are merged into the payload. That is how the frontend
keeps its 401/`auth_required` handling. The frontend polls every 2s with a
10-minute ceiling (`waitForJob()` in `templates/index.html`).

The worker thread has no request or app context, so the closure in
`create_playlist()` wraps its body in `with app.app_context():` - anything
touching `current_app` needs it.

An unexpected exception in a job is logged with its traceback by `jobs.py` and
reaches the client only as `"Internal server error"` with `status_code: 500`.
Raise `JobError` for any message the client is meant to see.

Finished jobs are purged one hour after `finished_at`. In-flight jobs are never
purged regardless of how long they run.

---

## Gunicorn configuration - workers must stay at 1

```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300
```

Set in both `railway.toml` and `Procfile`. Keep them in sync.

**`--workers 1` is not a performance choice, it is a correctness constraint.**
Three pieces of state live in process memory: the job store in `jobs.py`, the
`_client`/`_service` singletons, and the OAuth token in `MemoryCacheHandler`.
With multiple workers a client would poll a worker that never saw its job, and
each worker would refresh tokens independently - Spotify rotates refresh tokens,
so the workers would invalidate each other's.

`--threads 8` is the right scaling axis here: the workload is I/O-bound on the
Spotify API, and threads share that in-process state. `--timeout 300` is now
only a safety net, since no request blocks on a long build anymore.

---

## Rate limiting and CORS

Every visitor shares the server owner's Spotify creator token, so the API is
rate-limited per remote address with `Flask-Limiter` (in-memory storage - limits
are per gunicorn worker, which is fine for the single-worker Railway deploy).

| Route | Limit | Defined in |
|---|---|---|
| `/api/search` | 20/minute | `SEARCH_LIMIT` |
| `/api/create` | 5/minute, 30/hour | `CREATE_LIMITS` |
| everything else | 200/hour | `DEFAULT_LIMITS` |

`/api/search` fires on a 350ms input debounce in the frontend, so its limit has
to tolerate a user typing an artist name in bursts - do not lower it below ~20.

`ProxyFix(x_for=1)` is applied in `create_app()` because Railway terminates TLS
in front of gunicorn; without it `remote_addr` is the proxy and every visitor
shares a single bucket. The tradeoff: if the app is ever exposed directly to the
internet with no proxy, a client can spoof `X-Forwarded-For` to evade the limit.

There is **no CORS**. `flask-cors` was removed: the frontend is served by this
same Flask app and calls the API with relative paths, so nothing legitimate is
cross-origin. Do not add `CORS(app)` back - it let any site drive playlist
creation on the owner's account from a visitor's browser.

---

## Error handling conventions

- `SpotifyException` is caught at the route level in `routes.py` and mapped to
  HTTP responses via `SpotifyClient.error_message()`.
- `ValueError` from service layer -> 400 to client.
- `EnvironmentError` (missing env vars) -> 500 to client.
- The `@retry_on_failure` decorator handles 429 (rate limit) and transient errors
  with exponential backoff. It reads `Retry-After` headers. It is applied to all
  `SpotifyClient` methods that make API calls.
- `SpotifyClient.ensure_token()` is the pre-flight check before each API route.
  It returns `False` when there is no usable token: if `SPOTIPY_REFRESH_TOKEN` is
  set but the refresh returns `None`, it does **not** fall back to the expired
  token (that was a bug, now fixed). `_ensure_spotify_token()` in `routes.py` is
  the thin adapter that turns `False` into the 401 payload with
  `auth_required: True`.
- `musicbrainz.lookup_artist_metadata()` caches successful lookups only. A failed
  request is logged and returns the empty fallback without being cached.
- A rotated refresh token is never written to the log. If the Railway update
  fails, the new token lives in memory only: re-run `get_token.py` before the
  next restart.

---

## Deployment (Railway)

Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
Health check: `GET /` -> 200

Railway auto-injects `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`,
`RAILWAY_SERVICE_ID`. The only manual addition needed for the auto-token-update
feature is `RAILWAY_API_TOKEN` (create at railway.com -> Account -> Tokens).

The filesystem on Railway is ephemeral - anything written to disk is lost on
redeploy. The `MemoryCacheHandler` in `config.py` exists precisely to avoid
writing `.cache` files that would vanish between deploys.

---

## Things to avoid

- **Do not** set `limit > 50` on any Spotify search/albums/tracks API call.
- **Do not** add `cache_path` or `CacheFileHandler` in production config - use
  `RailwayAwareCacheHandler` (which extends `MemoryCacheHandler`).
- **Do not** instantiate `SpotifyClient` or `DiscographyService` per-request -
  use the singletons via `get_client()` / `get_service()` in `src/web/__init__.py`.
- **Do not** make `SpotifyClient.ensure_token()` return `True` for an expired
  token when the refresh fails - that lets expired tokens reach the Spotify API
  and causes silent 400 errors.
- **Do not** reach into `svc.client` from the CLI or the routes to create a
  playlist - go through `DiscographyService.publish()` / `build_playlist()`.
- **Do not** log tokens, and do not put exception text in a job's `error` field.
- **Do not** call `sp.auth_manager.get_access_token(check_cache=False)` in a hot
  path - it forces a token refresh on every call.
- **Do not** raise `--workers` above 1 - the job store, the singletons and the
  token cache are all per-process. Scale with `--threads` instead.
- **Do not** add `CORS(app)` back - the frontend is same-origin, and CORS let any
  site drive playlist creation on the owner's account.
- **Do not** touch `current_app` inside a `jobs.py` worker without an app context.

<!-- gitnexus:start -->
# GitNexus - Code Intelligence

This project is indexed by GitNexus as **discograpy** (830 symbols, 1539 relationships, 26 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol - callers, callees, which execution flows it participates in - use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace - use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/discograpy/context` | Codebase overview, check index freshness |
| `gitnexus://repo/discograpy/clusters` | All functional areas |
| `gitnexus://repo/discograpy/processes` | All execution flows |
| `gitnexus://repo/discograpy/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

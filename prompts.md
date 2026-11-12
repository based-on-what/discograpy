# prompts.md - Improvement prompts for DiscograPY

Copy-paste prompts for AI-assisted improvement sessions. Ordered by priority inside
each section. Each prompt is self-contained and scoped to one change (surgical edits,
no drive-by refactors). Do one prompt per session/PR.

Legend: [P1] do first, [P2] high value, [P3] nice to have.

---

## Performance

### [P1] Stop violating the MusicBrainz rate limit and cut /api/search latency

> In `src/services/discography.py`, `enrich_artists()` fires up to 4 parallel
> MusicBrainz lookups via `ThreadPoolExecutor(max_workers=4)`, one per artist, for up
> to 50 artists per search. MusicBrainz allows 1 request/second per IP and returns 503
> when exceeded. Fix this: (1) only enrich the top N artists actually shown by the
> frontend (check `templates/index.html` for how many results the UI renders - pass
> that as a limit), (2) make MusicBrainz calls sequential or throttled to 1 req/s,
> (3) keep the existing `lru_cache` in `src/services/musicbrainz.py`. Do not change
> the response shape of `/api/search`. Measure before/after latency of a search for
> a common name like "John" and report both numbers.

### [P1] Reuse HTTP connections with a shared requests.Session

> `src/services/musicbrainz.py` and `set_playlist_cover_from_artist()` in
> `src/services/spotify_client.py` call `requests.get()` directly, opening a new TCP+TLS
> connection per call. Create a module-level `requests.Session()` in each module and use
> it for all outbound calls. Keep existing timeouts and headers exactly as they are.
> No other behavior changes.

### [P2] Move retry to the per-page call in pagination

> In `src/services/spotify_client.py`, `_paginate()` is decorated with
> `@retry_on_failure(max_retries=5)`, so a failure on page 10 retries the whole
> pagination loop from page 1, re-fetching everything. Restructure so the retry wraps
> only the single `self.sp.next(results)` call (a small private helper), and remove the
> decorator from `_paginate` itself. Behavior on success must be identical. Note the
> initial-page fetches in `get_artist_albums`/`get_album_tracks` already have their own
> retry - don't double-wrap them.

### [P2] Compress and cache static responses

> The app serves `templates/index.html` (46KB) and a constant `/api/album-types`
> payload with no compression or caching headers. Add `flask-compress` (gzip/brotli)
> to the app factory in `src/web/__init__.py`, and add a `Cache-Control` header
> (e.g. `max-age=3600`) to the `/api/album-types` response in `src/web/routes.py`
> since `ALBUM_TYPE_CONFIGS` is static. Pin the new dependency in `requirements.txt`.

### [P3] Cache the scope check in has_scope()

> `SpotifyClient.has_scope()` in `src/services/spotify_client.py` calls
> `auth_manager.get_access_token()` on every `/api/create` request just to read the
> scope string. The scope of a refresh token never changes during the process lifetime.
> Cache the parsed scope set on the instance after the first successful read and reuse
> it. Keep the warning-and-return-False path for failures.

---

## Maintenance

### [P1] Add a test suite for the pure domain logic

> This project has zero tests. Add `pytest` and create `tests/` covering the pure
> functions first - they need no mocks: `src/domain/filters.py` (all marker functions,
> `normalize_title`, `passes_content_filters` including the instrumental-counterpart
> rule, `deduplicate_tracks` priority LP > EP > Single), and `src/domain/album_types.py`
> (`is_ep`, `is_single`, `matches_selection` for all 7 selections, `filter_albums`,
> `get_playlist_suffix` for selections 5 and 6 with partial `actual_types`). Use real
> Spotify-shaped dicts as fixtures. Then add tests for
> `DiscographyService.collect_tracks` with a fake `SpotifyClient`. Target: every branch
> in filters.py and album_types.py covered. Add `pytest` to a dev requirements file,
> not `requirements.txt`.

### [P1] Pin dependencies

> `requirements.txt` lists 7 packages with no versions, so every deploy can pull
> different code. Pin every dependency to the currently-installed version
> (`pip freeze` filtered to the 7 direct deps), and add a short comment block on how
> to upgrade deliberately. Do not add new tooling unless asked.

### [P2] Add CI: lint + tests on every push

> Add a GitHub Actions workflow `.github/workflows/ci.yml` that runs on push and PR:
> (1) `ruff check .` for linting, (2) `pytest`. Add a minimal `pyproject.toml` with
> ruff config matching the existing code style (line length, import order) so the
> first run passes without a giant reformat. Don't add mypy yet - that's a separate
> step.

### [P2] Fix logging: rotation, no log file in repo, gitignore hygiene

> `spotify_discography.log` is 2.4MB in the repo root with unbounded growth, and
> `.spotify_cache` shows as untracked. In `src/logging_config.py`: use
> `RotatingFileHandler` (e.g. 1MB x 3 backups) for local runs, and log to stdout only
> when running on Railway (detect via `RAILWAY_PROJECT_ID`) since the filesystem is
> ephemeral there anyway. Add `*.log` and `.spotify_cache` to `.gitignore` and remove
> the log file from the working tree.

### [P2] Centralize environment configuration

> Environment variables are read via `os.getenv` scattered across `src/config.py`,
> `src/web/routes.py`, `src/services/railway_cache.py`, and `app.py`. Create a single
> frozen `Settings` dataclass (stdlib only, no pydantic) in `src/settings.py` that
> reads and validates all env vars once at startup, with the same missing-variable
> error messages as today. Replace the scattered `os.getenv` calls with it. Keep
> `build_spotify_client()` as the only place `SpotifyOAuth` is constructed.

### [P3] Remove function-level import workarounds in routes.py

> `src/web/routes.py` imports `get_client` and `get_service` inside
> route functions to dodge a circular import with `src/web/__init__.py`. Break the
> cycle properly: move the singleton accessors (`get_client`, `get_service`, `_lock`)
> from `src/web/__init__.py` into a new `src/web/deps.py`, then use normal top-level
> imports in `routes.py`. Verify the app still boots and all routes respond.

### [P3] Split templates/index.html into static assets

> `templates/index.html` is a 46KB single file mixing HTML, CSS, and JS. Extract the
> CSS into `static/style.css` and the JS into `static/app.js`, served via Flask's
> static folder (configure `static_folder` in `create_app()` alongside the existing
> `template_folder`). No build step, no framework, no logic changes - pure extraction.
> Verify the page works end-to-end afterwards.

---

## Scalability

### [P1] Make /api/create non-blocking with a background job

> `/api/create` in `src/web/routes.py` synchronously fetches every album, every track,
> creates the playlist, and uploads a cover - for large discographies this holds a
> gunicorn worker for minutes and times out behind proxies. Convert it to a job model
> with stdlib only (no Celery/Redis - single-process app): POST `/api/create` validates
> input, submits the work to a module-level `ThreadPoolExecutor`, and returns 202 with
> a `job_id`; new GET `/api/jobs/<id>` returns `pending | done(result) | error(message)`
> from an in-memory dict with TTL cleanup. Update the fetch logic in
> `templates/index.html` to poll. Keep the existing error->HTTP mapping in the job
> result. Note the constraint: jobs live in process memory, so this requires
> `gunicorn --workers 1 --threads N` - implement that config change together (see next
> prompt).

### [P1] Configure gunicorn explicitly for this workload

> The Railway start command is bare `gunicorn app:app --bind 0.0.0.0:$PORT`, which
> defaults to 1 sync worker and a 30s timeout - `/api/create` exceeds that today. The
> app is I/O-bound (Spotify API calls) and uses in-process singletons and in-memory
> token cache, so threads scale better than workers here. Set the start command in
> `railway.toml` to use `--workers 1 --threads 8 --timeout 300` and document in
> CLAUDE.md why workers must stay at 1 (in-memory token cache + job store are
> per-process; multiple workers would each refresh tokens independently and Spotify
> rotates refresh tokens).

### [P1] Rate-limit the API and tighten CORS

> Every visitor shares the server owner's Spotify creator token: `/api/create` creates
> playlists on the owner's account and `CORS(app)` in `src/web/__init__.py` allows any
> origin, so any site can drive playlist creation from a visitor's browser. Add
> `Flask-Limiter` with conservative limits (e.g. 10/minute on `/api/search`, 3/minute
> on `/api/create`, keyed by remote address, in-memory storage), and restrict CORS to
> the app's own origin (env var `ALLOWED_ORIGIN`, defaulting to same-origin - note the
> frontend is served by this same Flask app, so cross-origin API access is not needed
> at all; consider removing flask-cors entirely if nothing external calls the API).
> Pin new dependencies.

### [P2] Cache Spotify reads across requests

> All users share one Spotify token, so its rate limit is global - repeated searches
> for the same artist burn it. Add a small TTL cache (`cachetools.TTLCache` or a dict
> with timestamps, given the single-process model) in front of
> `SpotifyClient.search_artists` (TTL ~10 min) and `get_artist_albums` (TTL ~1 h) in
> `src/services/spotify_client.py`. Cap entries (e.g. 500) to bound memory. Do not
> cache `create_playlist` or anything that writes. Make the TTLs module constants.

### [P2] Refuse to boot in production with the default secret key

> `create_app()` in `src/web/__init__.py` falls back to the hardcoded secret
> `"discograpy-dev-secret"` when `FLASK_SECRET_KEY` is unset. On Railway (detect via
> `RAILWAY_PROJECT_ID` env var) raise at startup instead of falling back; keep the
> dev fallback for local runs but log a warning. Update the env var table in CLAUDE.md
> to mark `FLASK_SECRET_KEY` required in production.

### [P3] Add a real health endpoint

> Railway health-checks `GET /`, which renders the full 46KB template. Add a
> `GET /healthz` route in `src/web/routes.py` returning `{"status": "ok"}` with 200,
> no Spotify calls, no template rendering. Update `railway.toml` to use it as the
> health check path.

---

## Suggested order

1. Pin dependencies + tests + CI (safety net first - everything else builds on it)
2. Rate limit + CORS + MusicBrainz throttle (stops active bleeding)
3. Background job + gunicorn config (the big scalability win, do together)
4. Logging, settings, caching, compression (incremental quality)
5. Template split, deps.py, health endpoint, dead param (cleanup)

# DiscograPY — Spotify Discography Playlist Creator

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> [Versión en Español](README.es.md)

DiscograPY creates Spotify discography playlists. It ships as:

- A **web app** (Flask + vanilla HTML/CSS/JS) — search artists, configure filters, get a playlist with embedded preview.
- A **CLI** (`playlists.py`) — same core logic, interactive terminal flow.

Built on the [Spotify Web API](https://developer.spotify.com/documentation/web-api/) via [Spotipy](https://spotipy.readthedocs.io/). Artist metadata (genres, country) enriched from [MusicBrainz](https://musicbrainz.org/).

## Live app

**[discograpy.up.railway.app](https://discograpy.up.railway.app/)**

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Getting a Refresh Token](#getting-a-refresh-token)
- [Usage — Web](#usage--web)
- [Usage — CLI](#usage--cli)
- [Content Filters](#content-filters)
- [Project Structure](#project-structure)
- [Logging](#logging)
- [Deployment](#deployment)
- [Notes & Limitations](#notes--limitations)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

- Search any artist; results show follower count, genres, and country (via MusicBrainz enrichment).
- **7 album type modes:** Everything, LPs only, EPs only, Singles only, Compilations only, EPs + Singles, LPs + EPs + Singles.
- EP detection by track count (4–7 tracks = EP, 1–3 = Single) since Spotify reports both as `single`.
- Smart playlist suffix adjusted when requested types are missing for an artist.
- **Content filters** (web): exclude or include live versions, demos, remixes, and instrumentals per request.
- **Smart deduplication** (default on): keeps the best version of each track by normalized title and album release priority; eliminates regional duplicates and deluxe-edition redundancy.
- Parallel album track fetching (`ThreadPoolExecutor`, up to 8 workers).
- Tracks added in chronological release order.
- Optional **artist image as playlist cover** (`ugc-image-upload` scope required).
- **Dry-run mode** (CLI): full discovery and filtering without creating the playlist.
- Retry logic with exponential backoff and `Retry-After` header support for HTTP 429.
- UTF-8 logging to file and console; compatible with non-Latin artist names.

---

## Requirements

- Python 3.8+
- Spotify Developer account with app credentials
- Python packages: `flask`, `gunicorn`, `spotipy`, `python-dotenv`, `flask-cors`, `requests`, `pycountry`

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Setup

### 1. Create a Spotify Developer app

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).
2. Click **Create an App**, fill in name and description.
3. After creation, note your **Client ID** and **Client Secret**.

### 2. Add Redirect URIs

In app settings → **Edit Settings** → **Redirect URIs**, add:

For local development:

```text
http://127.0.0.1:5000/callback
```

For production (Railway):

```text
https://discograpy.up.railway.app/callback
```

Click **Add** then **Save** for each.

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:5000/callback
SPOTIPY_REFRESH_TOKEN=your_refresh_token_here

# Optional
FLASK_SECRET_KEY=change-me-in-production
SPOTIPY_USE_CACHE=false
```

`SPOTIPY_REFRESH_TOKEN` — required for the web app (server-side playlist creation). Obtain it with `get_token.py` (see below). Without it, each server restart requires re-authentication.

`SPOTIPY_USE_CACHE` — set to `true` to cache the OAuth token in `.spotify_cache` during local development. Keep `false` for production.

> Never commit `.env` to version control — it is listed in `.gitignore`.

---

## Getting a Refresh Token

Run this once locally to authenticate and print your refresh token:

```bash
python get_token.py
```

A browser window will open for Spotify OAuth. After authorizing, the terminal prints:

```text
REFRESH TOKEN: AQA...
```

Copy that value into `SPOTIPY_REFRESH_TOKEN` in your `.env`.

Required scopes granted: `playlist-modify-public playlist-modify-private ugc-image-upload`.

---

## Usage — Web

Start locally:

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

Production-style local run:

```bash
gunicorn app:app --bind 0.0.0.0:5000
```

### Flow

1. **Search** — type an artist name; results show name, followers, genres, and country.
2. **Configure** — select album type, toggle content filters (live, demos, remixes, instrumentals, duplicate versions), optionally use artist image as cover.
3. **Result** — embedded Spotify playlist preview after creation.

---

## Usage — CLI

```bash
python playlists.py [--verbose] [--dry-run]
```

| Flag | Effect |
|---|---|
| `-v` / `--verbose` | Debug-level logging to console |
| `--dry-run` | Discover and filter tracks; skip playlist creation |

### Interactive flow

1. Enter artist name.
2. Select from matching results (shows follower count and genres).
3. Choose album type (0–6).
4. Playlist is created and the URL is printed.

**Note:** CLI uses default content filter settings — live versions, demos, remixes, and instrumentals are excluded; smart deduplication is applied. To override filters, use the web interface.

### Album type options

| # | Label | What's included |
|---|---|---|
| 0 | Everything | All types combined |
| 1 | LPs only | Full-length albums |
| 2 | EPs only | Single-type releases with 4–7 tracks |
| 3 | Singles only | Single-type releases with 1–3 tracks |
| 4 | Compilations only | Compilation releases |
| 5 | EPs + Singles | EPs and Singles, no LPs or Compilations |
| 6 | LPs + EPs + Singles | Everything except Compilations |

---

## Content Filters

Available in the web interface (passed as booleans to `POST /api/create`):

| Filter | Default | Effect when enabled |
|---|---|---|
| `include_live_versions` | off | Include tracks/albums with "live", "en vivo" etc. in the name |
| `include_demos` | off | Include tracks/albums with "demo", "rough mix" etc. |
| `include_remixes` | off | Include remixes, reworks, edits, extended mixes |
| `include_instrumentals` | off | Include instrumental versions (only if original also exists) |
| `include_duplicate_versions` | off | Disable deduplication; keep all versions of each track |
| `use_artist_image_as_cover` | off | Upload artist's Spotify image as playlist cover |

When deduplication is on (default), duplicate tracks are resolved by normalized title comparison — bracket content and filter keywords stripped — keeping the version from the album with the highest release priority (LP > EP > Single).

---

## Project Structure

```
discograpy/
├── app.py                    # Flask entry point
├── playlists.py              # CLI entry point
├── get_token.py              # Local helper: obtain refresh token
├── src/
│   ├── config.py             # Spotify client factory, env var validation
│   ├── logging_config.py     # Logging setup (file + console, UTF-8)
│   ├── domain/
│   │   ├── album_types.py    # Album type config, matching, suffix logic
│   │   ├── filters.py        # Content filters and track deduplication
│   │   └── models.py         # RunSummary dataclass
│   ├── services/
│   │   ├── discography.py    # DiscographyService: orchestrates the full flow
│   │   ├── spotify_client.py # SpotifyClient: Spotipy wrapper + retry
│   │   ├── musicbrainz.py    # MusicBrainz metadata enrichment (genres, country)
│   │   └── retry.py          # retry_on_failure decorator with backoff
│   ├── web/
│   │   ├── __init__.py       # Flask app factory, singleton client/service
│   │   └── routes.py         # HTTP routes and API endpoints
│   └── cli/
│       ├── runner.py         # CLI orchestration logic
│       └── ui.py             # Spinner, menus, artist/summary display
├── templates/
│   └── index.html            # Single-page frontend
├── requirements.txt
├── Procfile                  # Railway/Heroku process definition
├── railway.toml              # Railway deployment config
└── README.md
```

---

## Logging

Log format:

```text
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

| Output | Level |
|---|---|
| Console | `INFO` (or `DEBUG` with `--verbose`) |
| `spotify_discography.log` | `DEBUG` always |

Console stream is reconfigured to UTF-8 with `errors='replace'` for Windows compatibility.

---

## Deployment

Deployed on [Railway](https://railway.app/) using Nixpacks.

Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`

Health check path: `/`

Restart policy: `on_failure`

Required environment variables on Railway: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`, `SPOTIPY_REFRESH_TOKEN`, `FLASK_SECRET_KEY`.

---

## Notes & Limitations

- **Batch size:** Spotify API limit of 100 tracks per add-to-playlist request. Handled automatically with parallel batching (up to 4 concurrent batch uploads).
- **Regional duplicates:** Spotify returns market-specific versions of albums separately. Deduplication reduces this, but enabling `include_duplicate_versions` will include all of them.
- **Playlists are public by default.** To create private playlists, change `public=True` to `public=False` in `src/services/spotify_client.py` inside `create_playlist`.
- **Cover upload** requires `ugc-image-upload` scope in the refresh token. If the token was obtained without it, re-run `get_token.py`.
- **MusicBrainz** requests have a 1.8s timeout and are LRU-cached per process. Enrichment failures are non-fatal; missing metadata falls back to Spotify's own genre data.

---

## Troubleshooting

<details>
<summary>Authentication error / invalid credentials</summary>

1. Check `.env` has correct `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET`.
2. Confirm redirect URI in `.env` matches exactly what's set in the Spotify Developer Dashboard.
3. Re-run `get_token.py` to get a fresh `SPOTIPY_REFRESH_TOKEN`.

</details>

<details>
<summary>Rate limiting (HTTP 429)</summary>

The retry decorator reads the `Retry-After` header and waits accordingly with added jitter. Persistent rate limiting suggests the Spotify app is being throttled. Wait a few minutes and retry.

</details>

<details>
<summary>No content found for selected type</summary>

1. Verify the artist actually has that release type on Spotify.
2. Try option `0` (Everything) to see all available content.
3. The app warns and adjusts the playlist name when a requested type is absent.

</details>

<details>
<summary>Cover upload rejected (401)</summary>

The `SPOTIPY_REFRESH_TOKEN` was generated without the `ugc-image-upload` scope. Re-run `get_token.py` and update the token in your environment.

</details>

<details>
<summary>Import errors (ModuleNotFoundError)</summary>

```bash
pip install -r requirements.txt
python --version  # must be 3.8+
```

</details>

---

## License

MIT License. See [LICENSE](LICENSE).

---

## Author

Developed for automation and music lovers.

- GitHub: [@based-on-what](https://github.com/based-on-what)
- Project: [github.com/based-on-what/discograpy](https://github.com/based-on-what/discograpy)

Acknowledgments: [Spotipy](https://spotipy.readthedocs.io/), [Spotify Web API](https://developer.spotify.com/documentation/web-api/), [MusicBrainz](https://musicbrainz.org/).

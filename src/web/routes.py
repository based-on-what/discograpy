from typing import Any, Dict, Optional

import requests as _requests
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from spotipy.exceptions import SpotifyException

from src.domain import album_types as at
from src.services.spotify_client import SpotifyClient
from src.web import jobs
from src.web.limiter import CREATE_LIMITS, POLL_LIMIT, SEARCH_LIMIT, limiter

bp = Blueprint("main", __name__)

_ALBUM_TYPES_RESPONSE = [
    {
        "index": idx,
        "label": cfg["suffix"],
        "title": cfg["title"],
        "description": cfg["description"],
        "types": cfg["types"],
    }
    for idx, cfg in at.ALBUM_TYPE_CONFIGS.items()
]


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _format_duration(total_ms: int) -> str:
    total_seconds = total_ms // 1000
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _auth_error_payload() -> Dict[str, Any]:
    return {
        "error": (
            "Spotify creator account is not authenticated on the server. "
            "Set SPOTIPY_REFRESH_TOKEN (recommended) or authenticate the server account once."
        ),
        "auth_required": True,
    }


def _ensure_spotify_token(client: SpotifyClient) -> Optional[Dict[str, Any]]:
    return None if client.ensure_token() else _auth_error_payload()


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/callback")
def callback():
    from src.web import get_client

    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return redirect(url_for("main.index", oauth_error=error))
    if not code:
        return redirect(url_for("main.index", oauth_error="missing_code"))

    try:
        client = get_client()
        client.sp.auth_manager.get_access_token(code=code, check_cache=False)
        return redirect(url_for("main.index", oauth_success="1"))
    except Exception:
        current_app.logger.exception("OAuth callback failed")
        return redirect(url_for("main.index", oauth_error="callback_failed"))


@bp.get("/api/album-types")
def album_types():
    return jsonify(_ALBUM_TYPES_RESPONSE)


@bp.post("/api/search")
@limiter.limit(SEARCH_LIMIT)
def search_artist():
    from src.web import get_client, get_service

    data = request.get_json(silent=True) or {}
    artist_name = (data.get("artist_name") or "").strip()

    if not artist_name:
        return _json_error("artist_name is required", 400)

    try:
        # Built inside the try so a missing-env EnvironmentError keeps its
        # message instead of hitting the catch-all 500 handler.
        client = get_client()
        svc = get_service()

        auth_err = _ensure_spotify_token(client)
        if auth_err:
            return jsonify(auth_err), 401

        artists = svc.search_artists(artist_name)
        enriched = svc.enrich_artists(artists)
        current_app.logger.info(
            "Search: %d raw -> %d enriched for %r", len(artists), len(enriched), artist_name
        )
        return jsonify(enriched)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except EnvironmentError as exc:
        return _json_error(str(exc), 500)
    except SpotifyException as exc:
        return _json_error(SpotifyClient.error_message(exc), exc.http_status or 502)


@bp.post("/api/create")
@limiter.limit(CREATE_LIMITS)
def create_playlist():
    from src.web import get_client, get_service

    data = request.get_json(silent=True) or {}

    artist_id = (data.get("artist_id") or "").strip()
    artist_name = (data.get("artist_name") or "").strip()
    album_type_selection = data.get("album_type_selection", 0)
    verbose = bool(data.get("verbose", False))
    include_live_versions = bool(data.get("include_live_versions", False))
    include_demos = bool(data.get("include_demos", False))
    include_remixes = bool(data.get("include_remixes", False))
    include_instrumentals = bool(data.get("include_instrumentals", False))
    include_duplicate_versions = bool(data.get("include_duplicate_versions", False))
    use_artist_image_as_cover = bool(data.get("use_artist_image_as_cover", False))

    if not artist_id or not artist_name:
        return _json_error("artist_id and artist_name are required", 400)

    if not isinstance(album_type_selection, int) or album_type_selection not in at.ALBUM_TYPE_CONFIGS:
        return _json_error("album_type_selection must be an integer between 0 and 6", 400)

    app = current_app._get_current_object()

    def _work() -> Dict[str, Any]:
        # Runs on a jobs.py worker thread, which has no request or app context.
        with app.app_context():
            try:
                # Built inside the try so a missing-env EnvironmentError becomes a
                # JobError with its real message instead of a bare 500.
                client = get_client()
                svc = get_service()

                auth_err = _ensure_spotify_token(client)
                if auth_err:
                    raise jobs.JobError(auth_err["error"], 401, auth_required=True)

                if use_artist_image_as_cover and not client.has_scope("ugc-image-upload"):
                    raise jobs.JobError(
                        "Current Spotify token does not include 'ugc-image-upload'. "
                        "Re-authenticate and regenerate SPOTIPY_REFRESH_TOKEN "
                        "to enable artist cover uploads.",
                        400,
                    )

                summary = svc.build_playlist(
                    artist_id=artist_id,
                    artist_name=artist_name,
                    album_type_selection=album_type_selection,
                    verbose=verbose,
                    include_live_versions=include_live_versions,
                    include_demos=include_demos,
                    include_remixes=include_remixes,
                    include_instrumentals=include_instrumentals,
                    include_duplicate_versions=include_duplicate_versions,
                    use_artist_image_as_cover=use_artist_image_as_cover,
                )
            except ValueError as exc:
                raise jobs.JobError(str(exc), 400) from exc
            except EnvironmentError as exc:
                raise jobs.JobError(str(exc), 500) from exc
            except SpotifyException as exc:
                raise jobs.JobError(
                    SpotifyClient.error_message(exc), exc.http_status or 502
                ) from exc
            except _requests.exceptions.Timeout as exc:
                raise jobs.JobError(
                    "Spotify API timed out - rate limited. Wait a minute and try again.", 429
                ) from exc

        return {
            "artist": summary.artist,
            "playlist_name": summary.playlist_name,
            "playlist_id": summary.playlist_id,
            "playlist_url": summary.playlist_url,
            "albums_included": summary.albums_included,
            "tracks_added": summary.tracks_added,
            "total_length": _format_duration(summary.total_ms),
            "cover_applied": summary.cover_applied,
            "cover_error": summary.cover_error,
            "dry_run": summary.dry_run,
        }

    return jsonify({"job_id": jobs.submit(_work)}), 202


@bp.get("/api/jobs/<job_id>")
@limiter.limit(POLL_LIMIT)
def job_status(job_id: str):
    payload = jobs.get(job_id)
    if payload is None:
        return _json_error("Unknown or expired job", 404)
    return jsonify(payload)

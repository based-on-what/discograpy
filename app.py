import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_cors import CORS
from spotipy.exceptions import SpotifyException

from playlists import SpotifyDiscographyCreator, configure_logging

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "discograpy-dev-secret")
CORS(app)


def _json_error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _creator(dry_run: bool = False, verbose: bool = False) -> SpotifyDiscographyCreator:
    logger = configure_logging(verbose=verbose)
    return SpotifyDiscographyCreator(logger=logger, dry_run=dry_run)


def _build_auth_payload(creator: SpotifyDiscographyCreator) -> Dict[str, Any]:
    return {
        "error": (
            "Spotify creator account is not authenticated on the server. "
            "Set SPOTIPY_REFRESH_TOKEN (recommended) or authenticate the server account once."
        ),
        "auth_required": True,
    }


def _ensure_spotify_token(creator: SpotifyDiscographyCreator) -> Optional[Dict[str, Any]]:
    auth_manager = creator.sp.auth_manager
    token_info = auth_manager.get_cached_token()
    if token_info:
        return None

    refresh_token = os.getenv("SPOTIPY_REFRESH_TOKEN")
    if refresh_token:
        token_info = auth_manager.refresh_access_token(refresh_token)
        if token_info:
            return None

    return _build_auth_payload(creator)


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    app.logger.exception("Unhandled error: %s", error)
    return _json_error("Internal server error", 500)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return redirect(url_for("index", oauth_error=error))

    if not code:
        return redirect(url_for("index", oauth_error="missing_code"))

    try:
        creator = _creator()
        auth_manager = creator.sp.auth_manager
        auth_manager.get_access_token(code=code, check_cache=False)
        return redirect(url_for("index", oauth_success="1"))
    except Exception:
        app.logger.exception("OAuth callback failed")
        return redirect(url_for("index", oauth_error="callback_failed"))


@app.get("/api/album-types")
def album_types():
    options = []
    for idx, cfg in SpotifyDiscographyCreator.ALBUM_TYPE_CONFIGS.items():
        options.append(
            {
                "index": idx,
                "label": cfg["suffix"],
                "description": cfg["description"],
                "types": cfg["types"],
            }
        )
    return jsonify(options)


@app.post("/api/search")
def search_artist():
    data = request.get_json(silent=True) or {}
    artist_name = (data.get("artist_name") or "").strip()

    if not artist_name:
        return _json_error("artist_name is required", 400)

    try:
        creator = _creator()
        auth_response = _ensure_spotify_token(creator)
        if auth_response:
            return jsonify(auth_response), 401

        artists = creator.search_artists(artist_name)
        serialized = []
        for artist in artists:
            if not artist.get("id"):
                continue

            mb_data = creator._lookup_artist_metadata(artist.get("name", ""))
            mb_genres = [genre for genre in (mb_data.get("genres") or []) if genre][:3]
            spotify_genres = [genre for genre in (artist.get("genres") or []) if genre][:3]
            genres = mb_genres or spotify_genres

            serialized.append(
                {
                    "id": artist.get("id"),
                    "name": artist.get("name"),
                    "followers": artist.get("followers", {}).get("total", 0),
                    "genres": genres,
                    "image_url": (artist.get("images") or [{}])[0].get("url"),
                    "country": mb_data.get("country") or "Unknown",
                }
            )
        return jsonify(serialized)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except EnvironmentError as exc:
        return _json_error(str(exc), 500)
    except SpotifyException as exc:
        return _json_error(creator._spotify_error_message(exc), exc.http_status or 502)


@app.post("/api/create")
def create_playlist():
    data = request.get_json(silent=True) or {}

    artist_id = (data.get("artist_id") or "").strip()
    artist_name = (data.get("artist_name") or "").strip()
    album_type_selection = data.get("album_type_selection", 0)
    # Web flow always creates a playlist (dry-run disabled by product decision).
    dry_run = False
    verbose = bool(data.get("verbose", False))
    include_live_versions = bool(data.get("include_live_versions", False))
    include_demos = bool(data.get("include_demos", False))
    include_remixes = bool(data.get("include_remixes", False))
    include_instrumentals = bool(data.get("include_instrumentals", False))
    include_duplicate_versions = bool(data.get("include_duplicate_versions", False))
    use_artist_image_as_cover = bool(data.get("use_artist_image_as_cover", False))

    if not artist_id or not artist_name:
        return _json_error("artist_id and artist_name are required", 400)

    if not isinstance(album_type_selection, int) or album_type_selection not in SpotifyDiscographyCreator.ALBUM_TYPE_CONFIGS:
        return _json_error("album_type_selection must be an integer between 0 and 6", 400)

    creator: Optional[SpotifyDiscographyCreator] = None
    try:
        creator = _creator(dry_run=dry_run, verbose=verbose)
        auth_response = _ensure_spotify_token(creator)
        if auth_response:
            return jsonify(auth_response), 401
        if use_artist_image_as_cover and not creator.has_scope("ugc-image-upload"):
            return _json_error(
                "Current Spotify token does not include 'ugc-image-upload'. Re-authenticate the server account to enable artist cover uploads.",
                400,
            )

        album_types = creator.get_album_types_from_selection(album_type_selection)
        albums = creator._get_artist_albums(artist_id=artist_id, album_types=album_types)
        filtered_albums, actual_types = creator.filter_albums_by_selection(albums, album_type_selection)

        if not filtered_albums:
            return _json_error("No albums found for selected filters", 404)

        suffix = creator.get_playlist_suffix_from_selection(
            album_type_selection,
            actual_types if album_type_selection in (5, 6) else None,
        )
        playlist_name = f"{artist_name} discography {suffix}"

        track_uris = creator._collect_tracks_from_albums(
            filtered_albums,
            verbose=verbose,
            include_live_versions=include_live_versions,
            include_demos=include_demos,
            include_remixes=include_remixes,
            include_instrumentals=include_instrumentals,
            include_duplicate_versions=include_duplicate_versions,
        )
        if not track_uris:
            return _json_error("No tracks found for selected albums", 404)

        playlist_id = None
        playlist_url = None
        cover_applied = False
        cover_error = None

        if not dry_run:
            playlist = creator._create_playlist(playlist_name, creator.PLAYLIST_DESCRIPTION)
            playlist_id = playlist.get("id")
            if playlist_id:
                creator._add_tracks_to_playlist(playlist_id, track_uris)
                if use_artist_image_as_cover:
                    cover_applied, cover_error = creator._set_playlist_cover_from_artist(
                        playlist_id=playlist_id,
                        artist_id=artist_id,
                    )
                playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

        response = {
            "artist": artist_name,
            "playlist_name": playlist_name,
            "playlist_id": playlist_id,
            "playlist_url": playlist_url,
            "albums_included": len(filtered_albums),
            "tracks_added": len(track_uris),
            "cover_applied": cover_applied,
            "cover_error": cover_error,
            "dry_run": dry_run,
        }
        return jsonify(response)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except EnvironmentError as exc:
        return _json_error(str(exc), 500)
    except SpotifyException as exc:
        status = exc.http_status or 502
        message = creator._spotify_error_message(exc) if creator else str(exc)
        return _json_error(message, status)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

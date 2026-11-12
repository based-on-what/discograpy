import base64
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
import spotipy
from spotipy.exceptions import SpotifyException

from src.services.retry import retry_on_failure

_session = requests.Session()

# Spotify's cap on ids per "get several albums" request.
ALBUM_BATCH_SIZE = 20

# The fields album selection and filters.select_tracks read. Everything else is
# dropped on arrival: Spotify sends ~185 market codes per album and per track,
# which dominated a job's memory.
_ALBUM_FIELDS = ("id", "name", "album_type", "total_tracks", "release_date")
_TRACK_FIELDS = ("name", "uri", "duration_ms", "disc_number", "track_number")


class SpotifyClient:
    def __init__(self, sp: spotipy.Spotify, logger: logging.Logger) -> None:
        self.sp = sp
        self.logger = logger
        self._user_id: Optional[str] = None

    def has_scope(self, required_scope: str) -> bool:
        try:
            token_info = self.sp.auth_manager.get_access_token(as_dict=True, check_cache=True) or {}
            raw_scopes = str(token_info.get("scope", "")).split()
            return required_scope in raw_scopes
        except Exception as exc:
            self.logger.warning("Unable to validate token scopes: %s", exc)
            return False

    def ensure_token(self) -> bool:
        """True when a usable access token is available.

        A failed refresh returns False instead of falling back to the expired
        token: Spotify answers an expired token with an opaque 400.
        """
        auth_manager = self.sp.auth_manager
        refresh_token = os.getenv("SPOTIPY_REFRESH_TOKEN")
        try:
            token_info = auth_manager.get_cached_token()
            if token_info and not auth_manager.is_token_expired(token_info):
                return True
            if not refresh_token:
                return False
            if auth_manager.refresh_access_token(refresh_token):
                return True
        except Exception as exc:
            self.logger.warning("Failed to obtain Spotify token: %s", exc)
            return False

        self.logger.warning(
            "SPOTIPY_REFRESH_TOKEN is invalid or expired. "
            "Re-run get_token.py locally and update the Railway variable."
        )
        return False

    @retry_on_failure(max_retries=5)
    def search_artists(self, artist_name: str) -> List[Dict[str, Any]]:
        if not artist_name.strip():
            raise ValueError("Artist name cannot be empty")
        results = self.sp.search(q=f"artist:{artist_name}", type="artist", limit=50)
        return results.get("artists", {}).get("items", [])

    @retry_on_failure(max_retries=5)
    def get_artist(self, artist_id: str) -> Dict[str, Any]:
        return self.sp.artist(artist_id)

    def get_artist_albums(self, artist_id: str, album_types: List[str]) -> List[Dict[str, Any]]:
        # Not retried as a whole: each page request retries on its own, so a
        # failing page does not send the pagination back to page one.
        albums = self._paginate(self._first_albums_page(artist_id, ",".join(album_types)))
        valid = [
            _only(a, _ALBUM_FIELDS)
            for a in albums
            if a.get("release_date")
        ]
        valid.sort(key=lambda a: a["release_date"])
        return valid

    @retry_on_failure(max_retries=5)
    def _first_albums_page(self, artist_id: str, include_groups: str) -> Dict[str, Any]:
        return self.sp.artist_albums(artist_id=artist_id, include_groups=include_groups, limit=50)

    def get_tracks_by_album(self, album_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Tracks of up to ALBUM_BATCH_SIZE albums from a single request.

        An album Spotify answers with null is absent from the result.
        """
        albums = self._albums_batch(album_ids).get("albums") or []
        return {
            album_id: [_only(track, _TRACK_FIELDS) for track in self._paginate(album["tracks"])]
            for album_id, album in zip(album_ids, albums)
            if album
        }

    @retry_on_failure(max_retries=5)
    def _albums_batch(self, album_ids: List[str]) -> Dict[str, Any]:
        return self.sp.albums(album_ids)

    @retry_on_failure(max_retries=5)
    def create_playlist(self, playlist_name: str, description: str) -> Dict[str, Any]:
        if not self._user_id:
            current_user = self.sp.current_user()
            if not current_user or "id" not in current_user:
                raise ValueError("Unable to identify the authenticated Spotify user.")
            self._user_id = current_user["id"]
        return self.sp.user_playlist_create(
            user=self._user_id, name=playlist_name, public=True, description=description
        )

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> None:
        # One batch at a time on purpose: Spotify appends each batch as it
        # arrives, so batches sent concurrently can land out of playlist order.
        batch_size = 100
        for start in range(0, len(track_uris), batch_size):
            self._add_batch(playlist_id, track_uris[start : start + batch_size])

    @retry_on_failure(max_retries=5)
    def _add_batch(self, playlist_id: str, batch: List[str]) -> None:
        self.sp.playlist_add_items(playlist_id=playlist_id, items=batch)

    def set_playlist_cover_from_artist(
        self, playlist_id: str, artist_id: str
    ) -> Tuple[bool, Optional[str]]:
        try:
            artist = self.get_artist(artist_id)
            images = artist.get("images", []) if isinstance(artist, dict) else []
            if not images:
                self.logger.warning("Artist has no Spotify images; skipping playlist cover upload.")
                return False, "Artist has no Spotify images available."

            # Try largest images first for best cover quality; fall back to smaller
            # if a 640px image exceeds Spotify's 256KB upload cap.
            sorted_images = sorted(
                images, key=lambda img: _safe_int(img.get("height")), reverse=True
            )
            for image in sorted_images:
                image_url = image.get("url")
                if not image_url:
                    continue
                response = _session.get(image_url, timeout=5)
                response.raise_for_status()
                if len(response.content) > 256_000:
                    continue
                encoded = base64.b64encode(response.content).decode("ascii")
                self._upload_cover_with_auth_retry(playlist_id, encoded)
                self.logger.info("Playlist cover uploaded from artist image: %s", image_url)
                return True, None
        except (requests.RequestException, SpotifyException, ValueError) as exc:
            self.logger.warning("Failed to set playlist cover from artist image: %s", exc)
            if isinstance(exc, SpotifyException) and exc.http_status == 401:
                return (
                    False,
                    "Spotify rejected cover upload (401). Re-authenticate with "
                    "'ugc-image-upload' and regenerate SPOTIPY_REFRESH_TOKEN.",
                )
            return False, str(exc)
        except Exception as exc:
            self.logger.exception("Unexpected cover upload failure: %s", exc)
            return False, "Unexpected cover upload failure. Check server logs for details."

        self.logger.warning(
            "Could not upload artist image as playlist cover (size/availability constraints)."
        )
        return False, "Could not upload artist image (format/size constraints)."

    @retry_on_failure(max_retries=5)
    def _upload_cover(self, playlist_id: str, image_b64: str) -> None:
        self.sp.playlist_upload_cover_image(playlist_id, image_b64)

    def _upload_cover_with_auth_retry(self, playlist_id: str, image_b64: str) -> None:
        try:
            self._upload_cover(playlist_id, image_b64)
        except SpotifyException as exc:
            if exc.http_status != 401:
                raise
            # Force a fresh token and retry once for transient/stale auth states.
            try:
                self.sp.auth_manager.get_access_token(as_dict=False, check_cache=False)
            except TypeError:
                self.sp.auth_manager.get_access_token()
            self.sp = spotipy.Spotify(auth_manager=self.sp.auth_manager)
            self._upload_cover(playlist_id, image_b64)

    @retry_on_failure(max_retries=5)
    def _next_page(self, results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.sp.next(results)

    def _paginate(self, initial_results: Dict[str, Any], items_key: str = "items") -> List[Dict[str, Any]]:
        if not initial_results:
            return []
        results = initial_results
        if items_key not in results:
            for value in initial_results.values():
                if isinstance(value, dict) and items_key in value:
                    results = value
                    break
        if items_key not in results:
            return []
        all_items = list(results.get(items_key, []))
        while results.get("next"):
            next_page = self._next_page(results)
            if not next_page:
                break
            all_items.extend(next_page.get(items_key, []))
            results = next_page
        return all_items

    @staticmethod
    def error_message(exc: SpotifyException) -> str:
        messages = {
            400: "Bad request sent to Spotify API. Check the selected artist/content and try again.",
            401: "Authentication failed or token expired. Re-authenticate and retry.",
            403: "Insufficient permissions. Verify OAuth scopes and app settings.",
            404: "Resource not found on Spotify.",
            429: "Rate limit reached. Please wait and run the command again.",
            500: "Spotify service error. Please retry in a moment.",
            502: "Spotify temporary gateway error. Please retry.",
            503: "Spotify service unavailable. Please retry later.",
        }
        return messages.get(exc.http_status, f"Spotify API error ({exc.http_status}): {exc}")


def _only(item: Dict[str, Any], fields: Tuple[str, ...]) -> Dict[str, Any]:
    return {field: item[field] for field in fields if field in item}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

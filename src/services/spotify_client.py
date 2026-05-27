import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests
import spotipy
from spotipy.exceptions import SpotifyException

from src.services.retry import retry_on_failure


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

    def search_artists(self, artist_name: str) -> List[Dict[str, Any]]:
        if not artist_name.strip():
            raise ValueError("Artist name cannot be empty")
        results = self.sp.search(q=f"artist:{artist_name}", type="artist", limit=12)
        return results.get("artists", {}).get("items", [])

    @retry_on_failure(max_retries=5)
    def get_artist(self, artist_id: str) -> Dict[str, Any]:
        return self.sp.artist(artist_id)

    @retry_on_failure(max_retries=5)
    def get_artist_albums(self, artist_id: str, album_types: List[str]) -> List[Dict[str, Any]]:
        include_groups = ",".join(album_types)
        results = self.sp.artist_albums(artist_id=artist_id, include_groups=include_groups, limit=50)
        albums = self._paginate(results, "items")
        valid = [a for a in albums if a.get("release_date")]
        valid.sort(key=lambda a: a["release_date"])
        return valid

    @retry_on_failure(max_retries=5)
    def get_album_tracks(self, album_id: str) -> List[Dict[str, Any]]:
        results = self.sp.album_tracks(album_id=album_id, limit=50)
        return self._paginate(results, "items")

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
        batch_size = 100
        batches = [track_uris[i : i + batch_size] for i in range(0, len(track_uris), batch_size)]
        if len(batches) <= 1:
            if batches:
                self._add_batch(playlist_id, batches[0])
            return
        with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
            futures = [pool.submit(self._add_batch, playlist_id, b) for b in batches]
            for f in as_completed(futures):
                f.result()

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

            # Try smaller images first to improve odds of meeting Spotify size limits.
            sorted_images = sorted(images, key=lambda img: _safe_int(img.get("height")))
            for image in sorted_images:
                image_url = image.get("url")
                if not image_url:
                    continue
                response = requests.get(image_url, timeout=5)
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
            next_page = self.sp.next(results)
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


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

"""DiscograPY CLI for creating Spotify discography playlists."""

import argparse
import base64
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache, wraps
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pycountry
import requests
import spotipy
from dotenv import load_dotenv
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Configure application logging with file + console handlers."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler("spotify_discography.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(formatter)

    _safe_reconfigure_stream(console_handler)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return logging.getLogger(__name__)


def _safe_reconfigure_stream(handler: logging.StreamHandler) -> None:
    """Try to enforce UTF-8 console output in a cross-platform safe way."""
    stream = handler.stream
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


def retry_on_failure(max_retries: int = 3, delay: float = 1.0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Retry Spotify calls with exponential backoff and 429 handling."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except SpotifyException as exc:
                    if exc.http_status == 429:
                        retry_after_header = (exc.headers or {}).get("Retry-After")
                        retry_after = int(retry_after_header) if retry_after_header else int(delay * (2**attempt))
                        logging.getLogger(__name__).warning("Rate limited. Waiting %ss", retry_after)
                        time.sleep(retry_after)
                        continue

                    if attempt == max_retries - 1:
                        raise

                    wait_time = delay * (2**attempt)
                    logging.getLogger(__name__).warning(
                        "Spotify API failed in %s (attempt %s/%s). Retrying in %.1fs.",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    time.sleep(wait_time)

        return wrapper

    return decorator


class Spinner:
    """Simple stderr spinner for long-running operations."""

    def __init__(self, message: str) -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "Spinner":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        sys.stderr.write("\r" + " " * 80 + "\r")
        sys.stderr.flush()

    def _run(self) -> None:
        frames = ["|", "/", "-", "\\"]
        idx = 0
        while not self._stop.is_set():
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {self.message}")
            sys.stderr.flush()
            idx += 1
            time.sleep(0.1)


@dataclass
class RunSummary:
    artist: str
    playlist_name: str
    playlist_id: Optional[str]
    albums_included: int
    tracks_added: int
    dry_run: bool


class SpotifyDiscographyCreator:
    """Spotify discography playlist creator with filtering and robust error handling."""

    ALBUM_TYPE_CONFIGS: Dict[int, Dict[str, Any]] = {
        0: {"types": ["album", "single", "compilation"], "suffix": "[EVERYTHING]", "description": "everything"},
        1: {"types": ["album"], "suffix": "[ALBUMS]", "description": "albums"},
        2: {"types": ["single"], "suffix": "[EPs]", "description": "EPs"},
        3: {"types": ["single"], "suffix": "[SINGLES]", "description": "singles"},
        4: {"types": ["album", "single", "compilation"], "suffix": "[COMPILATIONS]", "description": "compilations"},
        5: {"types": ["single"], "suffix": "[EPs + SINGLES]", "description": "EPs and singles"},
        6: {"types": ["album", "single"], "suffix": "[ALBUMS + EPs + SINGLES]", "description": "albums, EPs, and singles"},
    }

    PLAYLIST_DESCRIPTION = (
        "Made with DiscograPY, an open-source Spotify discography creator. "
        "Find the project at: https://github.com/based-on-what/discograpy"
    )

    def __init__(self, logger: logging.Logger, dry_run: bool = False) -> None:
        self.logger = logger
        self.dry_run = dry_run
        self.user_id: Optional[str] = None

        load_dotenv()
        self._setup_spotify_client()

    def _setup_spotify_client(self) -> None:
        client_id = os.getenv("SPOTIPY_CLIENT_ID")
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
        redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

        missing = [name for name, value in {
            "SPOTIPY_CLIENT_ID": client_id,
            "SPOTIPY_CLIENT_SECRET": client_secret,
            "SPOTIPY_REDIRECT_URI": redirect_uri,
        }.items() if not value]

        if missing:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

        # Keep both public and private playlist scopes for future flexibility.
        scope = "playlist-modify-public playlist-modify-private ugc-image-upload"
        use_cache = os.getenv("SPOTIPY_USE_CACHE", "false").lower() in {"1", "true", "yes"}
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            cache_path=".spotify_cache" if use_cache else None,
            open_browser=use_cache,
        )
        self.sp = spotipy.Spotify(auth_manager=auth)
        self.logger.info("Spotify client initialized successfully")

    def has_scope(self, required_scope: str) -> bool:
        try:
            token_info = self.sp.auth_manager.get_access_token(as_dict=True, check_cache=True) or {}
            raw_scopes = str(token_info.get("scope", "")).split()
            return required_scope in raw_scopes
        except Exception as exc:
            self.logger.warning("Unable to validate token scopes: %s", exc)
            return False

    @staticmethod
    def _supports_color() -> bool:
        return sys.stdout.isatty() and os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"

    def _print_header(self) -> None:
        title = "DiscograPY - Spotify Discography Playlist Creator"
        if self._supports_color():
            color = "\033[96m"
            reset = "\033[0m"
        else:
            color = ""
            reset = ""
        border = "╔" + "═" * (len(title) + 2) + "╗"
        line = f"║ {title} ║"
        bottom = "╚" + "═" * (len(title) + 2) + "╝"
        print(f"{color}{border}\n{line}\n{bottom}{reset}")

    def _display_menu(self, title: str, options: List[str]) -> None:
        print(f"\n=== {title} ===")
        for idx, option in enumerate(options):
            print(f"{idx}: {option}")

    def _get_numeric_input(self, prompt: str, min_val: int, max_val: int) -> int:
        while True:
            try:
                value = int(input(prompt))
                if min_val <= value <= max_val:
                    return value
                print(f"Please select a valid number between {min_val} and {max_val}.")
            except ValueError:
                print("Please enter a valid number.")

    def display_album_type_menu(self) -> int:
        options = [
            "Everything (all album types combined)",
            "Albums only",
            "EPs only",
            "Singles only",
            "Compilations only",
            "EPs + Singles",
            "Albums + EPs + Singles",
        ]
        self._display_menu("Album Type Selection", options)
        return self._get_numeric_input("\nSelect album type (0-6): ", 0, 6)

    def get_album_types_from_selection(self, selection: int) -> List[str]:
        return self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])["types"]

    def get_selection_description(self, selection: int) -> str:
        return self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])["description"]

    def get_playlist_suffix_from_selection(self, selection: int, actual_types: Optional[Set[str]] = None) -> str:
        config = self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])
        if actual_types is None:
            return config["suffix"]

        if selection == 5:
            if {"ep", "single"}.issubset(actual_types):
                return "[EPs + SINGLES]"
            if "ep" in actual_types:
                return "[EPs]"
            if "single" in actual_types:
                return "[SINGLES]"

        if selection == 6:
            parts: List[str] = []
            if "album" in actual_types:
                parts.append("ALBUMS")
            if "ep" in actual_types:
                parts.append("EPs")
            if "single" in actual_types:
                parts.append("SINGLES")
            if parts:
                return f"[{' + '.join(parts)}]"

        return config["suffix"]

    def _is_ep(self, album: Dict[str, Any]) -> bool:
        return album.get("album_type", "").lower() == "single" and 4 <= album.get("total_tracks", 0) <= 7

    def _is_single(self, album: Dict[str, Any]) -> bool:
        return album.get("album_type", "").lower() == "single" and album.get("total_tracks", 0) <= 3

    def _matches_selection_filter(self, album: Dict[str, Any], selection: int) -> bool:
        album_type = album.get("album_type", "").lower()
        if selection == 0:
            return True
        if selection == 1:
            return album_type == "album"
        if selection == 2:
            return self._is_ep(album)
        if selection == 3:
            return self._is_single(album)
        if selection == 4:
            return album_type == "compilation"
        if selection == 5:
            return self._is_ep(album) or self._is_single(album)
        if selection == 6:
            return album_type == "album" or self._is_ep(album) or self._is_single(album)
        return True

    def filter_albums_by_selection(self, albums: List[Dict[str, Any]], selection: int) -> Tuple[List[Dict[str, Any]], Set[str]]:
        filtered = [album for album in albums if self._matches_selection_filter(album, selection)]
        actual_types: Set[str] = set()
        for album in filtered:
            album_type = album.get("album_type", "").lower()
            if album_type == "album":
                actual_types.add("album")
            elif self._is_ep(album):
                actual_types.add("ep")
            elif self._is_single(album):
                actual_types.add("single")
            elif album_type == "compilation":
                actual_types.add("compilation")
        return filtered, actual_types

    def _check_missing_types_and_warn(self, selection: int, actual_types: Set[str]) -> None:
        expected_map = {5: {"ep", "single"}, 6: {"album", "ep", "single"}}
        expected = expected_map.get(selection)
        if not expected:
            return
        missing = expected - actual_types
        if not missing:
            return

        map_name = {"album": "Albums", "ep": "EPs", "single": "Singles"}
        missing_text = ", ".join(map_name[item] for item in sorted(missing))
        print(f"\n⚠ Warning: {missing_text} not found on Spotify for this artist.")
        print("Using available types instead.")

    @retry_on_failure(max_retries=3)
    def _paginate_spotify_results(self, initial_results: Dict[str, Any], items_key: str = "items") -> List[Dict[str, Any]]:
        if not initial_results:
            return []

        results = initial_results
        if items_key not in results:
            for key, value in initial_results.items():
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

    def search_artists(self, artist_name: str) -> List[Dict[str, Any]]:
        if not artist_name.strip():
            raise ValueError("Artist name cannot be empty")
        results = self.sp.search(q=f"artist:{artist_name}", type="artist", limit=12)
        return results.get("artists", {}).get("items", [])

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _country_code_to_name(code: str) -> Optional[str]:
        if not code:
            return None
        country = pycountry.countries.get(alpha_2=code.upper())
        return country.name if country else None

    @staticmethod
    @lru_cache(maxsize=2048)
    def _lookup_artist_metadata_cached(artist_name: str) -> Dict[str, Any]:
        if not artist_name:
            return {"genres": [], "country": None}

        endpoint = "https://musicbrainz.org/ws/2/artist/"
        headers = {"User-Agent": "DiscograPY/1.0 (https://github.com/based-on-what/discograpy)"}
        params = {"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 5}

        try:
            response = requests.get(endpoint, params=params, headers=headers, timeout=1.8)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return {"genres": [], "country": None}

        candidates = payload.get("artists", []) if isinstance(payload, dict) else []
        if not candidates:
            return {"genres": [], "country": None}

        normalized_name = artist_name.strip().casefold()
        exact_match = next(
            (
                artist
                for artist in candidates
                if str(artist.get("name", "")).strip().casefold() == normalized_name
            ),
            None,
        )
        chosen = exact_match or max(candidates, key=lambda item: SpotifyDiscographyCreator._safe_int(item.get("score", 0)))

        country_name = None
        area = chosen.get("area")
        if isinstance(area, dict):
            area_name = area.get("name")
            if area_name:
                country_name = str(area_name)

        if not country_name:
            begin_area = chosen.get("begin-area")
            if isinstance(begin_area, dict):
                area_name = begin_area.get("name")
                if area_name:
                    country_name = str(area_name)

        if not country_name:
            country_name = SpotifyDiscographyCreator._country_code_to_name(str(chosen.get("country", "")))

        raw_genres = chosen.get("genres", [])
        if not raw_genres:
            raw_genres = chosen.get("tags", [])

        ranked_genres = sorted(
            [genre for genre in raw_genres if isinstance(genre, dict) and genre.get("name")],
            key=lambda genre: SpotifyDiscographyCreator._safe_int(genre.get("count", 0)),
            reverse=True,
        )
        genres = [str(genre.get("name")) for genre in ranked_genres[:3]]

        return {"genres": genres, "country": country_name}

    def _lookup_artist_metadata(self, artist_name: str) -> Dict[str, Any]:
        return self._lookup_artist_metadata_cached((artist_name or "").strip())

    def display_artists(self, artists: List[Dict[str, Any]]) -> None:
        print(f"\nFound {len(artists)} artists:")
        for idx, artist in enumerate(artists, 1):
            followers = artist.get("followers", {}).get("total", 0)
            genres = ", ".join(artist.get("genres", [])[:3]) or "n/a"
            print(f"{idx}. {artist.get('name', 'Unknown')} | Followers: {followers} | Genres: {genres}")

    def select_artist(self, artists: List[Dict[str, Any]]) -> Tuple[str, str]:
        selection = self._get_numeric_input(f"\nSelect artist number (1-{len(artists)}): ", 1, len(artists))
        selected = artists[selection - 1]
        return selected["id"], selected["name"]

    def handle_no_artists_found(self) -> bool:
        print("\nNo artists found with that name.")
        self._display_menu("Options", ["Try another artist search", "Exit"])
        return self._get_numeric_input("\nSelect option (0-1): ", 0, 1) == 0

    @retry_on_failure(max_retries=3)
    def _get_artist_albums(self, artist_id: str, album_types: List[str]) -> List[Dict[str, Any]]:
        include_groups = ",".join(album_types)
        results = self.sp.artist_albums(artist_id=artist_id, include_groups=include_groups, limit=50)
        albums = self._paginate_spotify_results(results, "items")
        valid = [album for album in albums if album.get("release_date")]
        valid.sort(key=lambda item: item["release_date"])
        return valid

    @retry_on_failure(max_retries=3)
    def _get_album_tracks(self, album_id: str) -> List[Dict[str, Any]]:
        results = self.sp.album_tracks(album_id=album_id, limit=50)
        return self._paginate_spotify_results(results, "items")

    @staticmethod
    def _title_has_live_marker(text: str) -> bool:
        return bool(re.search(r"\b(live|en vivo|acoustic live)\b", text, flags=re.IGNORECASE))

    @staticmethod
    def _title_has_demo_marker(text: str) -> bool:
        return bool(re.search(r"\b(demo|rough mix|work tape|unreleased demo)\b", text, flags=re.IGNORECASE))

    @staticmethod
    def _title_has_remix_marker(text: str) -> bool:
        return bool(re.search(r"\b(remix|rework|edit|extended mix|club mix|dub mix)\b", text, flags=re.IGNORECASE))

    @staticmethod
    def _title_has_instrumental_marker(text: str) -> bool:
        return bool(re.search(r"\b(instrumental)\b", text, flags=re.IGNORECASE))

    @staticmethod
    def _normalize_title_for_comparison(text: str) -> str:
        normalized = text.lower()
        normalized = re.sub(r"[\[\(].*?[\]\)]", " ", normalized)
        normalized = re.sub(
            r"\b(live|en vivo|acoustic live|demo|rough mix|work tape|unreleased demo|remix|rework|edit|extended mix|club mix|dub mix|instrumental)\b",
            " ",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return " ".join(normalized.split())

    def _album_release_priority(self, album: Dict[str, Any]) -> int:
        album_type = str(album.get("album_type", "")).lower()
        if album_type == "album":
            return 3
        if self._is_ep(album):
            return 2
        if self._is_single(album):
            return 1
        return 0

    def _track_passes_content_filters(
        self,
        track_name: str,
        album_name: str,
        include_live_versions: bool,
        include_demos: bool,
        include_remixes: bool,
        include_instrumentals: bool,
        included_non_instrumental_bases: Set[str],
    ) -> bool:
        searchable = f"{track_name} {album_name}"
        normalized_base = self._normalize_title_for_comparison(track_name)

        if not include_live_versions and self._title_has_live_marker(searchable):
            return False
        if not include_demos and self._title_has_demo_marker(searchable):
            return False
        if not include_remixes and self._title_has_remix_marker(searchable):
            return False
        if self._title_has_instrumental_marker(searchable):
            if not include_instrumentals:
                return False
            if normalized_base and normalized_base not in included_non_instrumental_bases:
                return False
            return True
        if normalized_base:
            included_non_instrumental_bases.add(normalized_base)
        return True

    def _collect_tracks_from_albums(
        self,
        albums: List[Dict[str, Any]],
        verbose: bool = False,
        include_live_versions: bool = False,
        include_demos: bool = False,
        include_remixes: bool = False,
        include_instrumentals: bool = False,
        include_duplicate_versions: bool = False,
    ) -> List[str]:
        included_non_instrumental_bases: Set[str] = set()
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(self._get_album_tracks, album["id"]): album
                for album in albums
                if album.get("id")
            }

            ordered_results: List[Tuple[str, Dict[str, Any], str, List[Dict[str, Any]]]] = []
            for future in as_completed(future_map):
                album = future_map[future]
                tracks = future.result()
                ordered_results.append((album.get("release_date", ""), album, album.get("name", "Unknown"), tracks))

            ordered_results.sort(key=lambda item: item[0])

            track_candidates: List[Tuple[str, str, int, int]] = []
            order_index = 0

            for _, album, album_name, tracks in ordered_results:
                track_uris: List[str] = []
                skipped_tracks = 0
                sorted_tracks = sorted(
                    tracks,
                    key=lambda item: (self._safe_int(item.get("disc_number", 1)), self._safe_int(item.get("track_number", 0))),
                )
                for track in sorted_tracks:
                    uri = track.get("uri")
                    track_name = str(track.get("name", ""))
                    if not uri:
                        continue
                    if not self._track_passes_content_filters(
                        track_name=track_name,
                        album_name=album_name,
                        include_live_versions=include_live_versions,
                        include_demos=include_demos,
                        include_remixes=include_remixes,
                        include_instrumentals=include_instrumentals,
                        included_non_instrumental_bases=included_non_instrumental_bases,
                    ):
                        skipped_tracks += 1
                        continue
                    track_uris.append(uri)
                    normalized_base = self._normalize_title_for_comparison(track_name) or track_name.strip().lower()
                    track_candidates.append((normalized_base, uri, self._album_release_priority(album), order_index))
                    order_index += 1

                self.logger.info(
                    "Processed album: %s (%s kept, %s filtered)",
                    album_name,
                    len(track_uris),
                    skipped_tracks,
                )

        if include_duplicate_versions:
            return [uri for _, uri, _, _ in track_candidates]

        best_by_track: Dict[str, Tuple[str, int, int]] = {}
        for normalized_base, uri, priority, appearance_order in track_candidates:
            existing = best_by_track.get(normalized_base)
            if existing is None:
                best_by_track[normalized_base] = (uri, priority, appearance_order)
                continue
            _, existing_priority, _ = existing
            if priority > existing_priority:
                best_by_track[normalized_base] = (uri, priority, appearance_order)

        deduped = sorted(best_by_track.values(), key=lambda item: item[2])
        return [uri for uri, _, _ in deduped]

    @retry_on_failure(max_retries=3)
    def _create_playlist(self, playlist_name: str, description: str) -> Dict[str, Any]:
        if not self.user_id:
            current_user = self.sp.current_user()
            if not current_user or "id" not in current_user:
                raise ValueError("Unable to identify the authenticated Spotify user.")
            self.user_id = current_user["id"]
        return self.sp.user_playlist_create(user=self.user_id, name=playlist_name, public=True, description=description)

    @retry_on_failure(max_retries=3)
    def _add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> None:
        batch_size = 100
        for start in range(0, len(track_uris), batch_size):
            batch = track_uris[start : start + batch_size]
            self.sp.playlist_add_items(playlist_id=playlist_id, items=batch)

    @retry_on_failure(max_retries=3)
    def _get_artist(self, artist_id: str) -> Dict[str, Any]:
        return self.sp.artist(artist_id)

    @retry_on_failure(max_retries=3)
    def _upload_playlist_cover(self, playlist_id: str, image_b64: str) -> None:
        self.sp.playlist_upload_cover_image(playlist_id, image_b64)

    def _upload_playlist_cover_with_auth_retry(self, playlist_id: str, image_b64: str) -> None:
        try:
            self._upload_playlist_cover(playlist_id, image_b64)
        except SpotifyException as exc:
            if exc.http_status != 401:
                raise
            # Force a fresh token and retry once for transient/stale auth states.
            try:
                self.sp.auth_manager.get_access_token(as_dict=False, check_cache=False)
            except TypeError:
                self.sp.auth_manager.get_access_token()
            self.sp = spotipy.Spotify(auth_manager=self.sp.auth_manager)
            self._upload_playlist_cover(playlist_id, image_b64)

    def _set_playlist_cover_from_artist(self, playlist_id: str, artist_id: str) -> Tuple[bool, Optional[str]]:
        try:
            artist = self._get_artist(artist_id)
            images = artist.get("images", []) if isinstance(artist, dict) else []
            if not images:
                self.logger.warning("Artist has no Spotify images; skipping playlist cover upload.")
                return False, "Artist has no Spotify images available."

            # Try smaller images first to improve odds of meeting Spotify size limits.
            sorted_images = sorted(images, key=lambda img: self._safe_int(img.get("height")))
            for image in sorted_images:
                image_url = image.get("url")
                if not image_url:
                    continue
                response = requests.get(image_url, timeout=5)
                response.raise_for_status()
                if len(response.content) > 256_000:
                    continue

                encoded = base64.b64encode(response.content).decode("ascii")
                self._upload_playlist_cover_with_auth_retry(playlist_id, encoded)
                self.logger.info("Playlist cover uploaded from artist image: %s", image_url)
                return True, None
        except (requests.RequestException, SpotifyException, ValueError) as exc:
            self.logger.warning("Failed to set playlist cover from artist image: %s", exc)
            if isinstance(exc, SpotifyException) and exc.http_status == 401:
                return (
                    False,
                    "Spotify rejected cover upload (401). Re-authenticate with 'ugc-image-upload' and regenerate SPOTIPY_REFRESH_TOKEN.",
                )
            return False, str(exc)

        self.logger.warning("Could not upload artist image as playlist cover (size/availability constraints).")
        return False, "Could not upload artist image (format/size constraints)."

    def _spotify_error_message(self, exc: SpotifyException) -> str:
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

    def _print_summary(self, summary: RunSummary) -> None:
        playlist_url = (
            f"https://open.spotify.com/playlist/{summary.playlist_id}"
            if summary.playlist_id
            else "n/a (dry-run)"
        )
        rows = [
            ("Artist", summary.artist),
            ("Playlist", summary.playlist_name),
            ("Albums included", str(summary.albums_included)),
            ("Tracks added", str(summary.tracks_added)),
            ("Playlist URL", playlist_url),
        ]

        key_width = max(len(k) for k, _ in rows)
        print("\nSummary")
        print("-" * (key_width + 40))
        for key, value in rows:
            print(f"{key:<{key_width}} : {value}")
        print("-" * (key_width + 40))

    def create_discography_playlist(self, verbose: bool = False) -> None:
        self._print_header()

        while True:
            artist_name_input = input("\nEnter artist name: ").strip()
            if not artist_name_input:
                print("Artist name cannot be empty.")
                continue

            with Spinner("Searching artists..."):
                artists = self.search_artists(artist_name_input)

            if not artists:
                if self.handle_no_artists_found():
                    continue
                return

            self.display_artists(artists)
            artist_id, selected_artist_name = self.select_artist(artists)

            while True:
                album_type_selection = self.display_album_type_menu()

                album_types = self.get_album_types_from_selection(album_type_selection)
                with Spinner("Retrieving albums..."):
                    albums = self._get_artist_albums(artist_id, album_types)

                filtered_albums, actual_types = self.filter_albums_by_selection(albums, album_type_selection)

                if verbose:
                    for album in filtered_albums:
                        year = str(album.get("release_date", ""))[:4] or "n/a"
                        self.logger.debug("Album selected: %s (%s)", album.get("name", "Unknown"), year)

                if not filtered_albums:
                    print(f"\nNo {self.get_selection_description(album_type_selection)} found for this artist with current filters.")
                    self._display_menu("Options", ["Try with a different artist", "Try with a different album type selection"])
                    retry_choice = self._get_numeric_input("\nSelect option (0-1): ", 0, 1)
                    if retry_choice == 0:
                        break
                    continue

                if album_type_selection in (5, 6):
                    self._check_missing_types_and_warn(album_type_selection, actual_types)

                suffix = self.get_playlist_suffix_from_selection(
                    album_type_selection,
                    actual_types if album_type_selection in (5, 6) else None,
                )
                playlist_name = f"{selected_artist_name} discography {suffix}"

                with Spinner("Collecting tracks..."):
                    track_uris = self._collect_tracks_from_albums(filtered_albums, verbose=verbose)

                if not track_uris:
                    print("No tracks found to process with the current filters.")
                    return

                playlist_id: Optional[str] = None
                if self.dry_run:
                    print("\nDry-run enabled: playlist will not be created and tracks will not be uploaded.")
                else:
                    with Spinner("Creating playlist..."):
                        playlist = self._create_playlist(playlist_name, self.PLAYLIST_DESCRIPTION)
                        playlist_id = playlist.get("id")

                    with Spinner("Adding tracks to playlist..."):
                        self._add_tracks_to_playlist(playlist_id, track_uris)

                summary = RunSummary(
                    artist=selected_artist_name,
                    playlist_name=playlist_name,
                    playlist_id=playlist_id,
                    albums_included=len(filtered_albums),
                    tracks_added=len(track_uris),
                    dry_run=self.dry_run,
                )
                self._print_summary(summary)
                return

    def run(self, verbose: bool = False) -> None:
        try:
            self.create_discography_playlist(verbose=verbose)
        except SpotifyException as exc:
            message = self._spotify_error_message(exc)
            self.logger.error("Spotify API error: %s", exc)
            print(f"\n✗ {message}")
        except EnvironmentError as exc:
            self.logger.error("Environment configuration error: %s", exc)
            print(f"\n✗ {exc}")
        except KeyboardInterrupt:
            self.logger.info("Process interrupted by user")
            print("\n\nProcess cancelled by user.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Spotify playlist from an artist discography.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging on console.")
    parser.add_argument("--dry-run", action="store_true", help="Run discovery and filtering without creating a playlist.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger = configure_logging(verbose=args.verbose)
    creator = SpotifyDiscographyCreator(logger=logger, dry_run=args.dry_run)
    creator.run(verbose=args.verbose)


if __name__ == "__main__":
    main()

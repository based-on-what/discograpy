"""DiscograPY CLI for creating Spotify discography playlists."""

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache, wraps
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
        scope = "playlist-modify-public playlist-modify-private"
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            cache_path=".spotify_cache",
            open_browser=True,
        )
        self.sp = spotipy.Spotify(auth_manager=auth)
        self.logger.info("Spotify client initialized successfully")

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

    @retry_on_failure(max_retries=3)
    def _get_related_artists(self, artist_id: str) -> List[Dict[str, Any]]:
        data = self.sp.artist_related_artists(artist_id)
        return data.get("artists", []) if data else []

    def _infer_genres(self, artist: Dict[str, Any], use_related: bool = True) -> List[str]:
        artist_genres = [genre for genre in artist.get("genres", []) if genre]
        if artist_genres:
            return artist_genres[:3]

        if not use_related:
            return []

        artist_id = artist.get("id")
        if not artist_id:
            return []

        try:
            related = self._get_related_artists(artist_id)
        except SpotifyException:
            return []

        ranked: Dict[str, int] = {}
        for related_artist in related:
            for genre in related_artist.get("genres", []):
                ranked[genre] = ranked.get(genre, 0) + 1

        return [genre for genre, _ in sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:3]]

    @staticmethod
    @lru_cache(maxsize=2048)
    def _lookup_artist_country_cached(artist_name: str) -> Optional[str]:
        if not artist_name:
            return None

        endpoint = "https://musicbrainz.org/ws/2/artist/"
        params = {"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 5}
        headers = {"User-Agent": "DiscograPY/1.0 (https://github.com/based-on-what/discograpy)"}

        try:
            response = requests.get(endpoint, params=params, headers=headers, timeout=1.2)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return None
        except ValueError:
            return None

        candidates = payload.get("artists", []) if isinstance(payload, dict) else []
        if not candidates:
            return None

        normalized_name = artist_name.strip().casefold()
        exact_match = next(
            (
                artist
                for artist in candidates
                if str(artist.get("name", "")).strip().casefold() == normalized_name
            ),
            None,
        )
        chosen = exact_match or max(candidates, key=lambda item: int(item.get("score", 0)))

        country = chosen.get("country")
        if country:
            return country

        area = chosen.get("area")
        if isinstance(area, dict):
            area_name = area.get("name")
            if area_name:
                return str(area_name)
        return None

    def _lookup_artist_country(self, artist_name: str) -> Optional[str]:
        return self._lookup_artist_country_cached((artist_name or "").strip())

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

    def _collect_tracks_from_albums(self, albums: List[Dict[str, Any]], verbose: bool = False) -> List[str]:
        all_track_uris: List[str] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(self._get_album_tracks, album["id"]): album
                for album in albums
                if album.get("id")
            }

            ordered_results: List[Tuple[str, str, List[Dict[str, Any]]]] = []
            for future in as_completed(future_map):
                album = future_map[future]
                tracks = future.result()
                ordered_results.append((album.get("release_date", ""), album.get("name", "Unknown"), tracks))

            ordered_results.sort(key=lambda item: item[0])

            for _, album_name, tracks in ordered_results:
                track_uris = [track["uri"] for track in tracks if track.get("uri")]
                all_track_uris.extend(track_uris)
                self.logger.info("Processed album: %s (%s tracks)", album_name, len(track_uris))

        return all_track_uris

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

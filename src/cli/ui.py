import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src.domain.models import RunSummary


class Spinner:
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


def supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"


def print_header() -> None:
    title = "DiscograPY - Spotify Discography Playlist Creator"
    color = "\033[96m" if supports_color() else ""
    reset = "\033[0m" if supports_color() else ""
    border = "╔" + "═" * (len(title) + 2) + "╗"
    line = f"║ {title} ║"
    bottom = "╚" + "═" * (len(title) + 2) + "╝"
    print(f"{color}{border}\n{line}\n{bottom}{reset}")


def display_menu(title: str, options: List[str]) -> None:
    print(f"\n=== {title} ===")
    for idx, option in enumerate(options):
        print(f"{idx}: {option}")


def get_numeric_input(prompt: str, min_val: int, max_val: int) -> int:
    while True:
        try:
            value = int(input(prompt))
            if min_val <= value <= max_val:
                return value
            print(f"Please select a valid number between {min_val} and {max_val}.")
        except ValueError:
            print("Please enter a valid number.")


def display_artists(artists: List[Dict[str, Any]]) -> None:
    print(f"\nFound {len(artists)} artists:")
    for idx, artist in enumerate(artists, 1):
        followers = artist.get("followers", {}).get("total", 0)
        genres = ", ".join(artist.get("genres", [])[:3]) or "n/a"
        print(f"{idx}. {artist.get('name', 'Unknown')} | Followers: {followers} | Genres: {genres}")


def select_artist(artists: List[Dict[str, Any]]) -> Tuple[str, str]:
    selection = get_numeric_input(f"\nSelect artist number (1-{len(artists)}): ", 1, len(artists))
    selected = artists[selection - 1]
    return selected["id"], selected["name"]


def print_summary(summary: RunSummary) -> None:
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

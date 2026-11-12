import re
from functools import lru_cache
from typing import Any, Dict, List, Set, Tuple

from src.domain.album_types import album_release_priority

_RE_LIVE = re.compile(r"\b(live|en vivo|acoustic live)\b", re.IGNORECASE)
_RE_DEMO = re.compile(r"\b(demo|rough mix|work tape|unreleased demo)\b", re.IGNORECASE)
_RE_REMIX = re.compile(r"\b(remix|rework|edit|extended mix|club mix|dub mix)\b", re.IGNORECASE)
_RE_INSTRUMENTAL = re.compile(r"\b(instrumental)\b", re.IGNORECASE)
_RE_BRACKETS = re.compile(r"[\[\(].*?[\]\)]")
_RE_KEYWORDS = re.compile(
    r"\b(live|en vivo|acoustic live|demo|rough mix|work tape|unreleased demo|"
    r"remix|rework|edit|extended mix|club mix|dub mix|instrumental)\b",
    re.IGNORECASE,
)
_RE_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def has_live_marker(text: str) -> bool:
    return bool(_RE_LIVE.search(text))


def has_demo_marker(text: str) -> bool:
    return bool(_RE_DEMO.search(text))


def has_remix_marker(text: str) -> bool:
    return bool(_RE_REMIX.search(text))


def has_instrumental_marker(text: str) -> bool:
    return bool(_RE_INSTRUMENTAL.search(text))


def normalize_title(text: str) -> str:
    normalized = _RE_BRACKETS.sub(" ", text.lower())
    normalized = _RE_KEYWORDS.sub(" ", normalized)
    normalized = _RE_NON_ALNUM.sub(" ", normalized)
    return " ".join(normalized.split())


def passes_content_filters(
    track_name: str,
    album_name: str,
    include_live_versions: bool,
    include_demos: bool,
    include_remixes: bool,
    include_instrumentals: bool,
    all_non_instrumental_bases: Set[str],
) -> bool:
    searchable = f"{track_name} {album_name}"
    normalized_base = normalize_title(track_name)

    if not include_live_versions and has_live_marker(searchable):
        return False
    if not include_demos and has_demo_marker(searchable):
        return False
    if not include_remixes and has_remix_marker(searchable):
        return False
    if has_instrumental_marker(searchable):
        if not include_instrumentals:
            return False
        if normalized_base and normalized_base not in all_non_instrumental_bases:
            return False
        return True
    return True


def deduplicate_tracks(
    track_candidates: List[Tuple[str, str, int, int]],
) -> List[str]:
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


def select_tracks(
    albums_with_tracks: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]],
    include_live_versions: bool = False,
    include_demos: bool = False,
    include_remixes: bool = False,
    include_instrumentals: bool = False,
    include_duplicate_versions: bool = False,
) -> Tuple[List[str], int, List[Tuple[str, int, int]]]:
    """Pick the playlist's track URIs from already-fetched albums.

    Returns (uris, total duration in ms, per-album (name, kept, filtered) counts).
    """
    ordered = sorted(albums_with_tracks, key=lambda pair: pair[0].get("release_date", ""))
    normalize = lru_cache(maxsize=None)(normalize_title)

    # Collected up front so an instrumental finds its vocal counterpart even
    # when that counterpart sits on a later album.
    non_instrumental_bases: Set[str] = set()
    for album, tracks in ordered:
        album_name = album.get("name", "Unknown")
        for track in tracks:
            name = str(track.get("name", ""))
            if not has_instrumental_marker(f"{name} {album_name}"):
                base = normalize(name)
                if base:
                    non_instrumental_bases.add(base)

    uri_to_duration: Dict[str, int] = {}
    track_candidates: List[Tuple[str, str, int, int]] = []
    album_counts: List[Tuple[str, int, int]] = []

    for album, tracks in ordered:
        album_name = album.get("name", "Unknown")
        priority = album_release_priority(album)
        kept = 0
        filtered = 0
        sorted_tracks = sorted(
            tracks,
            key=lambda t: (
                _safe_int(t.get("disc_number", 1)),
                _safe_int(t.get("track_number", 0)),
            ),
        )
        for track in sorted_tracks:
            uri = track.get("uri")
            track_name = str(track.get("name", ""))
            if not uri:
                continue
            if not passes_content_filters(
                track_name=track_name,
                album_name=album_name,
                include_live_versions=include_live_versions,
                include_demos=include_demos,
                include_remixes=include_remixes,
                include_instrumentals=include_instrumentals,
                all_non_instrumental_bases=non_instrumental_bases,
            ):
                filtered += 1
                continue
            kept += 1
            uri_to_duration[uri] = int(track.get("duration_ms") or 0)
            normalized_base = normalize(track_name) or track_name.strip().lower()
            track_candidates.append((normalized_base, uri, priority, len(track_candidates)))
        album_counts.append((album_name, kept, filtered))

    if include_duplicate_versions:
        uris = [uri for _, uri, _, _ in track_candidates]
    else:
        uris = deduplicate_tracks(track_candidates)
    return uris, sum(uri_to_duration.get(uri, 0) for uri in uris), album_counts


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

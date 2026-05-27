import re
from typing import Dict, List, Set, Tuple

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

from typing import Any, Dict, List, Optional, Set, Tuple

ALBUM_TYPE_CONFIGS: Dict[int, Dict[str, Any]] = {
    0: {
        "types": ["album", "single", "compilation"],
        "suffix": "[EVERYTHING]",
        "title": "Everything",
        "description": "Include absolutely everything available on Spotify: LPs, EPs, Singles, and Compilations.",
    },
    1: {
        "types": ["album"],
        "suffix": "[ALBUMS]",
        "title": "LPs only",
        "description": "Include full-length albums only (LPs / album releases).",
    },
    2: {
        "types": ["single"],
        "suffix": "[EPs]",
        "title": "EPs only",
        "description": "Include only EP releases (single-type releases with 4-7 tracks).",
    },
    3: {
        "types": ["single"],
        "suffix": "[SINGLES]",
        "title": "Singles only",
        "description": "Include only singles (single-type releases with up to 3 tracks).",
    },
    4: {
        "types": ["album", "single", "compilation"],
        "suffix": "[COMPILATIONS]",
        "title": "Compilations only",
        "description": "Include only compilation releases.",
    },
    5: {
        "types": ["single"],
        "suffix": "[EPs + SINGLES]",
        "title": "EPs + Singles",
        "description": "Include EP releases and Singles, excluding LPs and Compilations.",
    },
    6: {
        "types": ["album", "single"],
        "suffix": "[ALBUMS + EPs + SINGLES]",
        "title": "LPs + EPs + Singles",
        "description": "Include LP albums, EP releases, and Singles (no Compilations).",
    },
}


def is_ep(album: Dict[str, Any]) -> bool:
    return album.get("album_type", "").lower() == "single" and 4 <= album.get("total_tracks", 0) <= 7


def is_single(album: Dict[str, Any]) -> bool:
    return album.get("album_type", "").lower() == "single" and album.get("total_tracks", 0) <= 3


def matches_selection(album: Dict[str, Any], selection: int) -> bool:
    album_type = album.get("album_type", "").lower()
    if selection == 0:
        return True
    if selection == 1:
        return album_type == "album"
    if selection == 2:
        return is_ep(album)
    if selection == 3:
        return is_single(album)
    if selection == 4:
        return album_type == "compilation"
    if selection == 5:
        return is_ep(album) or is_single(album)
    if selection == 6:
        return album_type == "album" or is_ep(album) or is_single(album)
    return True


def filter_albums(
    albums: List[Dict[str, Any]], selection: int
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    filtered = [a for a in albums if matches_selection(a, selection)]
    actual_types: Set[str] = set()
    for album in filtered:
        album_type = album.get("album_type", "").lower()
        if album_type == "album":
            actual_types.add("album")
        elif is_ep(album):
            actual_types.add("ep")
        elif is_single(album):
            actual_types.add("single")
        elif album_type == "compilation":
            actual_types.add("compilation")
    return filtered, actual_types


def get_playlist_suffix(selection: int, actual_types: Optional[Set[str]] = None) -> str:
    config = ALBUM_TYPE_CONFIGS.get(selection, ALBUM_TYPE_CONFIGS[0])
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


def get_types_for_selection(selection: int) -> List[str]:
    return ALBUM_TYPE_CONFIGS.get(selection, ALBUM_TYPE_CONFIGS[0])["types"]


def get_selection_title(selection: int) -> str:
    return ALBUM_TYPE_CONFIGS.get(selection, ALBUM_TYPE_CONFIGS[0])["title"]


def album_release_priority(album: Dict[str, Any]) -> int:
    album_type = str(album.get("album_type", "")).lower()
    if album_type == "album":
        return 3
    if is_ep(album):
        return 2
    if is_single(album):
        return 1
    return 0

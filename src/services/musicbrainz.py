from functools import lru_cache
from typing import Any, Dict, List, Optional

import pycountry
import requests


@lru_cache(maxsize=2048)
def lookup_release_date(artist_name: str, album_title: str) -> Optional[str]:
    """Return earliest known release date from MusicBrainz, or None on miss/error."""
    if not artist_name or not album_title:
        return None

    endpoint = "https://musicbrainz.org/ws/2/release/"
    headers = {"User-Agent": "DiscograPY/1.0 (https://github.com/based-on-what/discograpy)"}
    params = {
        "query": f'release:"{album_title}" AND artist:"{artist_name}"',
        "fmt": "json",
        "limit": 5,
    }

    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=2.5)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    releases = payload.get("releases", []) if isinstance(payload, dict) else []
    if not releases:
        return None

    norm_title = album_title.strip().casefold()
    exact = [r for r in releases if str(r.get("title", "")).strip().casefold() == norm_title]
    candidates = exact or releases

    best = max(candidates, key=lambda r: _safe_int(r.get("score", 0)))
    date = best.get("date")
    return str(date) if date else None


@lru_cache(maxsize=2048)
def lookup_artist_metadata(artist_name: str) -> Dict[str, Any]:
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
        (a for a in candidates if str(a.get("name", "")).strip().casefold() == normalized_name),
        None,
    )
    chosen = exact_match or max(candidates, key=lambda a: _safe_int(a.get("score", 0)))

    return {
        "genres": _extract_genres(chosen),
        "country": _extract_country(chosen),
    }


def _extract_country(artist: Dict[str, Any]) -> Optional[str]:
    for area_key in ("area", "begin-area"):
        area = artist.get(area_key)
        if isinstance(area, dict):
            name = area.get("name")
            if name:
                return str(name)
    code = str(artist.get("country", ""))
    if code:
        country = pycountry.countries.get(alpha_2=code.upper())
        return country.name if country else None
    return None


def _extract_genres(artist: Dict[str, Any]) -> List[str]:
    raw = artist.get("genres") or artist.get("tags", [])
    ranked = sorted(
        [g for g in raw if isinstance(g, dict) and g.get("name")],
        key=lambda g: _safe_int(g.get("count", 0)),
        reverse=True,
    )
    return [str(g["name"]) for g in ranked[:3]]


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

import logging
import threading
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

import pycountry
import requests

logger = logging.getLogger(__name__)

_session = requests.Session()

# MusicBrainz allows 1 request/second per IP; exceeding it returns 503.
_MIN_REQUEST_INTERVAL = 1.0
_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    with _throttle_lock:
        wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def lookup_artist_metadata(artist_name: str) -> Dict[str, Any]:
    if not artist_name:
        return {"genres": [], "country": None}
    try:
        return _fetch_artist_metadata(artist_name)
    except (requests.RequestException, ValueError) as exc:
        logger.warning("MusicBrainz lookup failed for %r: %s", artist_name, exc)
        return {"genres": [], "country": None}


# Raises on a failed request instead of returning the empty fallback: lru_cache
# does not store exceptions, so one timeout cannot pin an artist to "no
# metadata" for the rest of the process.
@lru_cache(maxsize=2048)
def _fetch_artist_metadata(artist_name: str) -> Dict[str, Any]:
    endpoint = "https://musicbrainz.org/ws/2/artist/"
    headers = {"User-Agent": "DiscograPY/1.0 (https://github.com/based-on-what/discograpy)"}
    params = {"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 5}

    _throttle()
    response = _session.get(endpoint, params=params, headers=headers, timeout=1.8)
    response.raise_for_status()
    payload = response.json()

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

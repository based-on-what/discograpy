import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple

from src.domain import album_types as at
from src.domain import filters as fl
from src.domain.models import RunSummary
from src.services import musicbrainz
from src.services.spotify_client import SpotifyClient

PLAYLIST_DESCRIPTION = (
    "Made with DiscograPY, an open-source Spotify discography creator. "
    "Find the project at: https://github.com/based-on-what/discograpy"
)


class DiscographyService:
    def __init__(
        self,
        client: SpotifyClient,
        logger: logging.Logger,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.logger = logger
        self.dry_run = dry_run

    def search_artists(self, artist_name: str) -> List[Dict[str, Any]]:
        return self.client.search_artists(artist_name)

    def enrich_artists(self, artists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        valid = [a for a in artists if a.get("id")]

        def _enrich(artist: Dict[str, Any]) -> Dict[str, Any]:
            mb_data = musicbrainz.lookup_artist_metadata((artist.get("name") or "").strip())
            mb_genres = [g for g in (mb_data.get("genres") or []) if g][:3]
            spotify_genres = [g for g in (artist.get("genres") or []) if g][:3]
            return {
                "id": artist.get("id"),
                "name": artist.get("name"),
                "followers": artist.get("followers", {}).get("total", 0),
                "genres": mb_genres or spotify_genres,
                "image_url": (artist.get("images") or [{}])[0].get("url"),
                "location": mb_data.get("country") or "Unknown",
            }

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_enrich, a): a["id"] for a in valid}
            enriched: Dict[str, Any] = {}
            for f in as_completed(futures):
                artist_id = futures[f]
                try:
                    enriched[artist_id] = f.result()
                except Exception as exc:
                    self.logger.warning("Enrichment failed for %s: %s", artist_id, exc)

        return [enriched[a["id"]] for a in valid if a["id"] in enriched]

    def get_filtered_albums(
        self, artist_id: str, selection: int
    ) -> Tuple[List[Dict[str, Any]], Set[str]]:
        album_type_list = at.get_types_for_selection(selection)
        albums = self.client.get_artist_albums(artist_id=artist_id, album_types=album_type_list)
        return at.filter_albums(albums, selection)

    def collect_tracks(
        self,
        albums: List[Dict[str, Any]],
        verbose: bool = False,
        include_live_versions: bool = False,
        include_demos: bool = False,
        include_remixes: bool = False,
        include_instrumentals: bool = False,
        include_duplicate_versions: bool = False,
    ) -> Tuple[List[str], int]:
        uri_to_duration: Dict[str, int] = {}
        max_workers = min(8, len(albums) or 1)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self.client.get_album_tracks, album["id"]): album
                for album in albums
                if album.get("id")
            }
            ordered: List[Tuple[str, Dict[str, Any], str, List[Dict[str, Any]]]] = []
            for future in as_completed(future_map):
                album = future_map[future]
                try:
                    tracks = future.result()
                except Exception as exc:
                    self.logger.warning(
                        "Skipping album %r — failed to fetch tracks: %s",
                        album.get("name", album.get("id")),
                        exc,
                    )
                    continue
                ordered.append(
                    (album.get("release_date", ""), album, album.get("name", "Unknown"), tracks)
                )

        ordered.sort(key=lambda x: x[0])

        norm_cache: Dict[str, str] = {}

        def _norm(name: str) -> str:
            if name not in norm_cache:
                norm_cache[name] = fl.normalize_title(name)
            return norm_cache[name]

        # Pre-compute all non-instrumental base names so instrumental matching is
        # independent of album chronological order.
        all_non_instrumental_bases: Set[str] = set()
        for _, _album, _album_name, _tracks in ordered:
            for _track in _tracks:
                _name = str(_track.get("name", ""))
                if not fl.has_instrumental_marker(f"{_name} {_album_name}"):
                    _base = _norm(_name)
                    if _base:
                        all_non_instrumental_bases.add(_base)

        track_candidates: List[Tuple[str, str, int, int]] = []
        order_index = 0

        for _, album, album_name, tracks in ordered:
            kept: List[str] = []
            skipped = 0
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
                if not fl.passes_content_filters(
                    track_name=track_name,
                    album_name=album_name,
                    include_live_versions=include_live_versions,
                    include_demos=include_demos,
                    include_remixes=include_remixes,
                    include_instrumentals=include_instrumentals,
                    all_non_instrumental_bases=all_non_instrumental_bases,
                ):
                    skipped += 1
                    continue
                kept.append(uri)
                uri_to_duration[uri] = int(track.get("duration_ms") or 0)
                normalized_base = _norm(track_name) or track_name.strip().lower()
                track_candidates.append(
                    (normalized_base, uri, at.album_release_priority(album), order_index)
                )
                order_index += 1

            self.logger.info(
                "Processed album: %s (%s kept, %s filtered)", album_name, len(kept), skipped
            )

        if include_duplicate_versions:
            uris = [uri for _, uri, _, _ in track_candidates]
            return uris, sum(uri_to_duration.get(u, 0) for u in uris)

        uris = fl.deduplicate_tracks(track_candidates)
        return uris, sum(uri_to_duration.get(u, 0) for u in uris)

    def build_playlist(
        self,
        artist_id: str,
        artist_name: str,
        album_type_selection: int,
        verbose: bool = False,
        include_live_versions: bool = False,
        include_demos: bool = False,
        include_remixes: bool = False,
        include_instrumentals: bool = False,
        include_duplicate_versions: bool = False,
        use_artist_image_as_cover: bool = False,
    ) -> RunSummary:
        filtered_albums, actual_types = self.get_filtered_albums(artist_id, album_type_selection)
        if not filtered_albums:
            raise ValueError("No albums found for selected filters")

        suffix = at.get_playlist_suffix(
            album_type_selection,
            actual_types if album_type_selection in (5, 6) else None,
        )
        playlist_name = f"{artist_name} discography {suffix}"

        track_uris, total_ms = self.collect_tracks(
            filtered_albums,
            verbose=verbose,
            include_live_versions=include_live_versions,
            include_demos=include_demos,
            include_remixes=include_remixes,
            include_instrumentals=include_instrumentals,
            include_duplicate_versions=include_duplicate_versions,
        )
        if not track_uris:
            raise ValueError("No tracks found for selected albums")

        playlist_id: Optional[str] = None
        cover_applied = False
        cover_error: Optional[str] = None

        if not self.dry_run:
            playlist = self.client.create_playlist(playlist_name, PLAYLIST_DESCRIPTION)
            playlist_id = playlist.get("id")
            if playlist_id:
                self.client.add_tracks_to_playlist(playlist_id, track_uris)
                if use_artist_image_as_cover:
                    cover_applied, cover_error = self.client.set_playlist_cover_from_artist(
                        playlist_id=playlist_id,
                        artist_id=artist_id,
                    )

        return RunSummary(
            artist=artist_name,
            playlist_name=playlist_name,
            playlist_id=playlist_id,
            albums_included=len(filtered_albums),
            tracks_added=len(track_uris),
            total_ms=total_ms,
            dry_run=self.dry_run,
            cover_applied=cover_applied,
            cover_error=cover_error,
        )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

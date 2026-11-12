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
    "Find the website at https://discograpy.up.railway.app "
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

    # The frontend renders search results in a scrollable list that shows ~5
    # artists without scrolling; MusicBrainz allows 1 req/s, so only the top
    # results are worth the lookup cost.
    MB_ENRICH_LIMIT = 5

    def enrich_artists(
        self, artists: List[Dict[str, Any]], mb_limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        valid = [a for a in artists if a.get("id")]
        limit = self.MB_ENRICH_LIMIT if mb_limit is None else mb_limit

        def _enrich(artist: Dict[str, Any], with_musicbrainz: bool) -> Dict[str, Any]:
            mb_data: Dict[str, Any] = {"genres": [], "country": None}
            if with_musicbrainz:
                try:
                    mb_data = musicbrainz.lookup_artist_metadata(
                        (artist.get("name") or "").strip()
                    )
                except Exception as exc:
                    self.logger.warning("Enrichment failed for %s: %s", artist.get("id"), exc)
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

        # Sequential on purpose: lookup_artist_metadata throttles to 1 req/s
        # (MusicBrainz limit), so parallel workers would only queue on the lock.
        return [_enrich(a, with_musicbrainz=i < limit) for i, a in enumerate(valid)]

    def get_filtered_albums(
        self, artist_id: str, selection: int
    ) -> Tuple[List[Dict[str, Any]], Set[str]]:
        album_type_list = at.get_types_for_selection(selection)
        albums = self.client.get_artist_albums(artist_id=artist_id, album_types=album_type_list)
        return at.filter_albums(albums, selection)

    def collect_tracks(
        self,
        albums: List[Dict[str, Any]],
        include_live_versions: bool = False,
        include_demos: bool = False,
        include_remixes: bool = False,
        include_instrumentals: bool = False,
        include_duplicate_versions: bool = False,
    ) -> Tuple[List[str], int]:
        max_workers = min(8, len(albums) or 1)
        albums_with_tracks: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self.client.get_album_tracks, album["id"]): album
                for album in albums
                if album.get("id")
            }
            for future in as_completed(future_map):
                album = future_map[future]
                try:
                    tracks = future.result()
                except Exception as exc:
                    self.logger.warning(
                        "Skipping album %r - failed to fetch tracks: %s",
                        album.get("name", album.get("id")),
                        exc,
                    )
                    continue
                albums_with_tracks.append((album, tracks))

        uris, total_ms, album_counts = fl.select_tracks(
            albums_with_tracks,
            include_live_versions=include_live_versions,
            include_demos=include_demos,
            include_remixes=include_remixes,
            include_instrumentals=include_instrumentals,
            include_duplicate_versions=include_duplicate_versions,
        )
        for album_name, kept, filtered in album_counts:
            self.logger.info(
                "Processed album: %s (%s kept, %s filtered)", album_name, kept, filtered
            )
        return uris, total_ms

    def publish(
        self,
        playlist_name: str,
        track_uris: List[str],
        artist_id: Optional[str] = None,
        use_artist_image_as_cover: bool = False,
    ) -> Tuple[Optional[str], bool, Optional[str]]:
        """Create the playlist on Spotify. Returns (playlist_id, cover_applied, cover_error)."""
        if self.dry_run:
            return None, False, None

        playlist_id = self.client.create_playlist(playlist_name, PLAYLIST_DESCRIPTION).get("id")
        if not playlist_id:
            return None, False, None

        self.client.add_tracks_to_playlist(playlist_id, track_uris)
        if not (use_artist_image_as_cover and artist_id):
            return playlist_id, False, None

        cover_applied, cover_error = self.client.set_playlist_cover_from_artist(
            playlist_id=playlist_id,
            artist_id=artist_id,
        )
        return playlist_id, cover_applied, cover_error

    def build_playlist(
        self,
        artist_id: str,
        artist_name: str,
        album_type_selection: int,
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

        playlist_name = at.playlist_name(artist_name, album_type_selection, actual_types)

        track_uris, total_ms = self.collect_tracks(
            filtered_albums,
            include_live_versions=include_live_versions,
            include_demos=include_demos,
            include_remixes=include_remixes,
            include_instrumentals=include_instrumentals,
            include_duplicate_versions=include_duplicate_versions,
        )
        if not track_uris:
            raise ValueError("No tracks found for selected albums")

        playlist_id, cover_applied, cover_error = self.publish(
            playlist_name,
            track_uris,
            artist_id=artist_id,
            use_artist_image_as_cover=use_artist_image_as_cover,
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


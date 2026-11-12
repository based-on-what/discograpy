import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional, Set, Tuple

from src.domain import album_types as at
from src.domain import filters as fl
from src.domain.models import RunSummary
from src.services import musicbrainz
from src.services.spotify_client import ALBUM_BATCH_SIZE, SpotifyClient

PLAYLIST_DESCRIPTION = (
    "Made with DiscograPY, an open-source Spotify discography creator. "
    "Find the website at https://discograpy.up.railway.app "
)

# Shared by every job in the process, so the fan-out against the one Spotify
# token is capped here no matter how many playlists are being built at once.
MAX_CONCURRENT_FETCHES = 8
_FETCH_POOL = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_FETCHES, thread_name_prefix="discograpy-fetch"
)

# MusicBrainz allows 1 request/second, so one worker is the whole queue. A
# search waits this long for its lookups and answers with whatever is ready;
# the rest keep running and are cached for the next search of that artist.
MB_ENRICH_BUDGET_SECONDS = 1.5
_MB_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discograpy-mb")
# Lookups allowed to wait in that queue. Past it a search skips the lookup
# instead of queueing work nobody will be around to see.
_MB_BACKLOG = threading.BoundedSemaphore(10)


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

        backlog = _MB_BACKLOG

        def _lookup_finished(lookup: "Future[Dict[str, Any]]") -> None:
            backlog.release()
            if lookup.exception() is not None:
                self.logger.warning("MusicBrainz lookup failed: %s", lookup.exception())

        metadata: Dict[int, Dict[str, Any]] = {}
        pending: Dict[int, "Future[Dict[str, Any]]"] = {}
        for index, artist in enumerate(valid[: max(limit, 0)]):
            name = (artist.get("name") or "").strip()
            known = musicbrainz.cached_artist_metadata(name)
            if known is not None:
                metadata[index] = known
            elif backlog.acquire(blocking=False):
                pending[index] = _MB_POOL.submit(musicbrainz.lookup_artist_metadata, name)
                pending[index].add_done_callback(_lookup_finished)

        finished, _ = wait(pending.values(), timeout=MB_ENRICH_BUDGET_SECONDS)
        for index, lookup in pending.items():
            if lookup in finished and lookup.exception() is None:
                metadata[index] = lookup.result()

        def _enrich(index: int, artist: Dict[str, Any]) -> Dict[str, Any]:
            mb_data = metadata.get(index, {"genres": [], "country": None})
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

        return [_enrich(index, artist) for index, artist in enumerate(valid)]

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
        fetchable = [album for album in albums if album.get("id")]
        batches = [
            fetchable[start : start + ALBUM_BATCH_SIZE]
            for start in range(0, len(fetchable), ALBUM_BATCH_SIZE)
        ]
        albums_with_tracks: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []

        futures = [
            _FETCH_POOL.submit(self._fetch_tracks, [album["id"] for album in batch])
            for batch in batches
        ]
        # Read back in submit order, not completion order, so albums released
        # on the same day always reach select_tracks in the same order.
        for batch, future in zip(batches, futures):
            tracks_by_album = future.result()
            for album in batch:
                if album["id"] not in tracks_by_album:
                    self.logger.warning(
                        "Skipping album %r - Spotify returned no tracks for it",
                        album.get("name", album["id"]),
                    )
                    continue
                albums_with_tracks.append((album, tracks_by_album[album["id"]]))

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

    def _fetch_tracks(self, album_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        try:
            return self.client.get_tracks_by_album(album_ids)
        except Exception as exc:
            self.logger.warning(
                "Batch of %d albums failed (%s); fetching them one by one", len(album_ids), exc
            )

        # One bad album must not take the other nineteen of its batch with it.
        tracks_by_album: Dict[str, List[Dict[str, Any]]] = {}
        for album_id in album_ids:
            try:
                tracks_by_album.update(self.client.get_tracks_by_album([album_id]))
            except Exception as exc:
                self.logger.warning("Failed to fetch tracks of album %s: %s", album_id, exc)
        return tracks_by_album

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


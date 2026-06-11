import logging

import pytest

from src.services.discography import DiscographyService

logger = logging.getLogger("tests")


class FakeSpotifyClient:
    """Only what collect_tracks needs: get_album_tracks keyed by album id."""

    def __init__(self, tracks_by_album, failing_album_ids=()):
        self.tracks_by_album = tracks_by_album
        self.failing_album_ids = set(failing_album_ids)

    def get_album_tracks(self, album_id):
        if album_id in self.failing_album_ids:
            raise RuntimeError("boom")
        return self.tracks_by_album[album_id]


def track(name, uri=None, duration_ms=200_000, disc=1, num=1):
    return {
        "name": name,
        "uri": uri or f"spotify:track:{name.lower().replace(' ', '-')}",
        "duration_ms": duration_ms,
        "disc_number": disc,
        "track_number": num,
    }


def album(album_id, name, album_type="album", total_tracks=10, release_date="2020-01-01"):
    return {
        "id": album_id,
        "name": name,
        "album_type": album_type,
        "total_tracks": total_tracks,
        "release_date": release_date,
    }


def make_service(tracks_by_album, failing=()):
    client = FakeSpotifyClient(tracks_by_album, failing)
    return DiscographyService(client=client, logger=logger, dry_run=True)


class TestCollectTracks:
    def test_collects_in_album_then_track_order(self):
        albums = [album("a1", "First", release_date="2019-01-01")]
        tracks = {
            "a1": [track("Two", num=2), track("One", num=1)],
        }
        uris, total = make_service(tracks).collect_tracks(albums)
        assert uris == ["spotify:track:one", "spotify:track:two"]
        assert total == 400_000

    def test_albums_sorted_chronologically(self):
        albums = [
            album("new", "Newer", release_date="2021-01-01"),
            album("old", "Older", release_date="2019-01-01"),
        ]
        tracks = {"new": [track("N")], "old": [track("O")]}
        uris, _ = make_service(tracks).collect_tracks(albums)
        assert uris == ["spotify:track:o", "spotify:track:n"]

    def test_dedup_keeps_album_over_single(self):
        albums = [
            album("s1", "Hit - Single", album_type="single", total_tracks=1,
                  release_date="2019-01-01"),
            album("a1", "The LP", album_type="album", release_date="2020-01-01"),
        ]
        tracks = {
            "s1": [track("Hit", uri="uri:single")],
            "a1": [track("Hit", uri="uri:album")],
        }
        uris, total = make_service(tracks).collect_tracks(albums)
        assert uris == ["uri:album"]
        assert total == 200_000

    def test_include_duplicate_versions_keeps_all(self):
        albums = [
            album("s1", "Hit - Single", album_type="single", total_tracks=1,
                  release_date="2019-01-01"),
            album("a1", "The LP", album_type="album", release_date="2020-01-01"),
        ]
        tracks = {
            "s1": [track("Hit", uri="uri:single")],
            "a1": [track("Hit", uri="uri:album")],
        }
        uris, total = make_service(tracks).collect_tracks(
            albums, include_duplicate_versions=True
        )
        assert sorted(uris) == ["uri:album", "uri:single"]
        assert total == 400_000

    def test_live_tracks_filtered_by_default(self):
        albums = [album("a1", "Studio Album")]
        tracks = {"a1": [track("Song"), track("Song (Live)", uri="uri:live", num=2)]}
        uris, _ = make_service(tracks).collect_tracks(albums)
        assert uris == ["spotify:track:song"]

    def test_live_album_taints_all_tracks(self):
        albums = [album("a1", "Concert (Live at Wembley)")]
        tracks = {"a1": [track("Plain Song")]}
        uris, _ = make_service(tracks).collect_tracks(albums)
        assert uris == []

    def test_instrumental_kept_only_with_counterpart_across_albums(self):
        albums = [
            album("a1", "Vocal Album", release_date="2019-01-01"),
            album("a2", "Instrumentals", release_date="2020-01-01"),
        ]
        tracks = {
            "a1": [track("Song", uri="uri:vocal")],
            "a2": [
                track("Song (Instrumental)", uri="uri:inst"),
                track("Orphan (Instrumental)", uri="uri:orphan", num=2),
            ],
        }
        # include_duplicate_versions=True: otherwise "Song (Instrumental)" dedups
        # against "Song" (same normalized base) and the filter result is invisible.
        uris, _ = make_service(tracks).collect_tracks(
            albums, include_instrumentals=True, include_duplicate_versions=True
        )
        assert "uri:vocal" in uris
        assert "uri:inst" in uris
        assert "uri:orphan" not in uris

    def test_instrumental_dedups_against_vocal_counterpart(self):
        albums = [
            album("a1", "Vocal Album", release_date="2019-01-01"),
            album("a2", "Instrumentals", release_date="2020-01-01"),
        ]
        tracks = {
            "a1": [track("Song", uri="uri:vocal")],
            "a2": [track("Song (Instrumental)", uri="uri:inst")],
        }
        uris, _ = make_service(tracks).collect_tracks(albums, include_instrumentals=True)
        assert uris == ["uri:vocal"]

    def test_failed_album_fetch_is_skipped(self):
        albums = [
            album("bad", "Broken", release_date="2019-01-01"),
            album("good", "Works", release_date="2020-01-01"),
        ]
        tracks = {"good": [track("Song")]}
        uris, _ = make_service(tracks, failing={"bad"}).collect_tracks(albums)
        assert uris == ["spotify:track:song"]

    def test_tracks_without_uri_skipped(self):
        albums = [album("a1", "Album")]
        tracks = {"a1": [{"name": "No URI", "duration_ms": 1000}]}
        uris, total = make_service(tracks).collect_tracks(albums)
        assert uris == []
        assert total == 0

    def test_albums_without_id_skipped(self):
        albums = [{"name": "No ID", "album_type": "album", "release_date": "2020-01-01"}]
        uris, total = make_service({}).collect_tracks(albums)
        assert uris == []
        assert total == 0

    def test_disc_number_orders_before_track_number(self):
        albums = [album("a1", "Double Album")]
        tracks = {
            "a1": [
                track("D2T1", disc=2, num=1),
                track("D1T2", disc=1, num=2),
                track("D1T1", disc=1, num=1),
            ]
        }
        uris, _ = make_service(tracks).collect_tracks(albums)
        assert uris == ["spotify:track:d1t1", "spotify:track:d1t2", "spotify:track:d2t1"]


class TestBuildPlaylistDryRun:
    def test_raises_when_no_albums(self):
        service = make_service({})
        service.get_filtered_albums = lambda artist_id, selection: ([], set())
        with pytest.raises(ValueError, match="No albums"):
            service.build_playlist("artist-id", "Artist", 0)

    def test_dry_run_summary(self):
        albums = [album("a1", "The LP")]
        tracks = {"a1": [track("Song")]}
        service = make_service(tracks)
        service.get_filtered_albums = lambda artist_id, selection: (albums, {"album"})
        summary = service.build_playlist("artist-id", "Artist", 1)
        assert summary.dry_run is True
        assert summary.playlist_id is None
        assert summary.playlist_name == "Artist discography [ALBUMS]"
        assert summary.tracks_added == 1
        assert summary.albums_included == 1

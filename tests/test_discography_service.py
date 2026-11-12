import logging
import threading
import time

from types import SimpleNamespace

import pytest

from src.services import discography, musicbrainz
from src.services.discography import DiscographyService

logger = logging.getLogger("tests")


class FakeSpotifyClient:
    """Only what collect_tracks needs: get_tracks_by_album keyed by album id."""

    def __init__(self, tracks_by_album, failing_album_ids=()):
        self.tracks_by_album = tracks_by_album
        self.failing_album_ids = set(failing_album_ids)
        self.requested_batches = []

    def get_tracks_by_album(self, album_ids):
        self.requested_batches.append(list(album_ids))
        if self.failing_album_ids.intersection(album_ids):
            raise RuntimeError("boom")
        return {
            album_id: self.tracks_by_album[album_id]
            for album_id in album_ids
            if album_id in self.tracks_by_album
        }


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

    def test_album_spotify_does_not_return_is_skipped(self):
        albums = [
            album("bad", "Broken", release_date="2019-01-01"),
            album("good", "Works", release_date="2020-01-01"),
        ]
        tracks = {"good": [track("Song")]}
        uris, _ = make_service(tracks).collect_tracks(albums)
        assert uris == ["spotify:track:song"]

    def test_albums_are_requested_twenty_at_a_time(self):
        albums = [album(f"a{n:02d}", f"Album {n}", release_date=f"20{n:02d}-01-01") for n in range(45)]
        tracks = {a["id"]: [track(f"Song {a['id']}")] for a in albums}
        service = make_service(tracks)

        uris, _ = service.collect_tracks(albums)

        assert len(uris) == 45
        assert sorted(len(batch) for batch in service.client.requested_batches) == [5, 20, 20]

    def test_a_failed_batch_only_loses_the_album_that_keeps_failing(self):
        albums = [album(f"a{n:02d}", f"Album {n}", release_date=f"20{n:02d}-01-01") for n in range(21)]
        tracks = {a["id"]: [track(f"Song {a['id']}")] for a in albums}

        uris, _ = make_service(tracks, failing={"a00"}).collect_tracks(albums)

        assert uris == [f"spotify:track:song-a{n:02d}" for n in range(1, 21)]

    def test_albums_released_the_same_day_keep_the_order_spotify_listed_them_in(self):
        albums = [album(f"a{n:02d}", f"Album {n}") for n in range(45)]
        tracks = {a["id"]: [track(f"Song {a['id']}")] for a in albums}

        class LastBatchAnswersFirst(FakeSpotifyClient):
            def get_tracks_by_album(self, album_ids):
                if len(album_ids) == 20:
                    time.sleep(0.05)
                return super().get_tracks_by_album(album_ids)

        service = DiscographyService(client=LastBatchAnswersFirst(tracks), logger=logger, dry_run=True)
        uris, _ = service.collect_tracks(albums)

        assert uris == [f"spotify:track:song-a{n:02d}" for n in range(45)]

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


class TestFetchConcurrency:
    def test_jobs_running_at_once_share_one_cap_on_spotify_requests(self):
        lock = threading.Lock()
        in_flight = []
        peaks = []

        class SlowClient(FakeSpotifyClient):
            def get_tracks_by_album(self, album_ids):
                with lock:
                    in_flight.append(1)
                    peaks.append(len(in_flight))
                time.sleep(0.05)
                with lock:
                    in_flight.pop()
                return super().get_tracks_by_album(album_ids)

        albums = [album(f"a{n:02d}", f"Album {n}") for n in range(60)]
        tracks = {a["id"]: [track(f"Song {a['id']}")] for a in albums}
        service = DiscographyService(client=SlowClient(tracks), logger=logger, dry_run=True)

        jobs = [threading.Thread(target=service.collect_tracks, args=(albums,)) for _ in range(4)]
        for job in jobs:
            job.start()
        for job in jobs:
            job.join()

        assert max(peaks) <= discography.MAX_CONCURRENT_FETCHES


class TestEnrichArtists:
    ARTIST = {"id": "ar1", "name": "Artist", "genres": ["pop"], "followers": {"total": 7}}

    def test_top_results_get_musicbrainz_location_and_genres(self, monkeypatch):
        monkeypatch.setattr(
            musicbrainz, "lookup_artist_metadata", lambda name: {"genres": ["rock"], "country": "Chile"}
        )

        enriched = make_service({}).enrich_artists([self.ARTIST])

        assert enriched == [
            {"id": "ar1", "name": "Artist", "followers": 7, "genres": ["rock"], "image_url": None,
             "location": "Chile"}
        ]

    def test_results_past_the_limit_skip_musicbrainz(self, monkeypatch):
        looked_up = []
        monkeypatch.setattr(
            musicbrainz, "lookup_artist_metadata",
            lambda name: looked_up.append(name) or {"genres": [], "country": "Chile"},
        )
        artists = [{"id": f"ar{n}", "name": f"Artist {n}"} for n in range(3)]

        enriched = make_service({}).enrich_artists(artists, mb_limit=2)

        assert looked_up == ["Artist 0", "Artist 1"]
        assert [a["location"] for a in enriched] == ["Chile", "Chile", "Unknown"]

    def test_a_slow_musicbrainz_does_not_hold_the_search_past_its_budget(self, slow_musicbrainz):
        started = time.perf_counter()
        enriched = make_service({}).enrich_artists([self.ARTIST])

        assert time.perf_counter() - started < 1
        assert enriched[0]["location"] == "Unknown"
        assert enriched[0]["genres"] == ["pop"]

    def test_a_lookup_that_outlives_the_budget_is_ready_for_the_next_search(self, slow_musicbrainz):
        service = make_service({})
        service.enrich_artists([self.ARTIST])

        slow_musicbrainz.finish_pending_lookups()
        enriched = service.enrich_artists([self.ARTIST])

        assert enriched[0]["location"] == "Chile"
        assert slow_musicbrainz.requested == ["Artist"]

    def test_an_artist_already_looked_up_does_not_queue_behind_a_slow_lookup(self, slow_musicbrainz):
        service = make_service({})
        service.enrich_artists([self.ARTIST])
        slow_musicbrainz.finish_pending_lookups()
        slow_musicbrainz.block_again()
        service.enrich_artists([{"id": "ar2", "name": "Someone Slow"}])

        started = time.perf_counter()
        enriched = service.enrich_artists([self.ARTIST])

        assert time.perf_counter() - started < 0.04
        assert enriched[0]["location"] == "Chile"

    def test_searches_stop_queueing_lookups_once_the_backlog_is_full(self, slow_musicbrainz, monkeypatch):
        monkeypatch.setattr(discography, "_MB_BACKLOG", threading.BoundedSemaphore(2))
        artists = [{"id": f"ar{n}", "name": f"Artist {n}"} for n in range(5)]

        make_service({}).enrich_artists(artists)
        slow_musicbrainz.finish_pending_lookups()

        assert slow_musicbrainz.requested == ["Artist 0", "Artist 1"]

    def test_a_negative_limit_looks_nothing_up(self, slow_musicbrainz):
        make_service({}).enrich_artists([self.ARTIST], mb_limit=-1)
        slow_musicbrainz.finish_pending_lookups()

        assert slow_musicbrainz.requested == []


class SlowMusicBrainz:
    """A MusicBrainz that answers only when told to, at the HTTP layer so the real cache is used."""

    def __init__(self):
        self.requested = []
        self._release = threading.Event()

    def get(self, endpoint, params, headers, timeout):
        name = params["query"].removeprefix('artist:"').removesuffix('"')
        self.requested.append(name)
        self._release.wait(5)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"artists": [{"name": name, "score": 100, "area": {"name": "Chile"}}]},
        )

    def finish_pending_lookups(self):
        self._release.set()
        discography._MB_POOL.submit(lambda: None).result(timeout=5)

    def block_again(self):
        self._release.clear()


@pytest.fixture
def slow_musicbrainz(monkeypatch):
    fake = SlowMusicBrainz()
    monkeypatch.setattr(musicbrainz, "_session", fake)
    monkeypatch.setattr(musicbrainz, "_throttle", lambda: None)
    monkeypatch.setattr(musicbrainz, "_cache", {})
    monkeypatch.setattr(discography, "MB_ENRICH_BUDGET_SECONDS", 0.05)
    yield fake
    fake.finish_pending_lookups()


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


class PublishingClient(FakeSpotifyClient):
    def __init__(self, tracks_by_album, playlist=None, cover=(True, None)):
        super().__init__(tracks_by_album)
        self.playlist = {"id": "pl1"} if playlist is None else playlist
        self.cover = cover
        self.added = []
        self.cover_requests = []

    def create_playlist(self, playlist_name, description):
        return self.playlist

    def add_tracks_to_playlist(self, playlist_id, track_uris):
        self.added.append((playlist_id, track_uris))

    def set_playlist_cover_from_artist(self, playlist_id, artist_id):
        self.cover_requests.append((playlist_id, artist_id))
        return self.cover


def publishing_service(client):
    service = DiscographyService(client=client, logger=logger, dry_run=False)
    service.get_filtered_albums = lambda artist_id, selection: ([album("a1", "The LP")], {"album"})
    return service


class TestBuildPlaylistPublishes:
    def test_adds_the_collected_tracks_to_the_new_playlist(self):
        client = PublishingClient({"a1": [track("Song")]})

        summary = publishing_service(client).build_playlist("artist-id", "Artist", 1)

        assert summary.playlist_id == "pl1"
        assert client.added == [("pl1", ["spotify:track:song"])]
        assert client.cover_requests == []
        assert summary.cover_applied is False

    def test_cover_is_requested_only_when_asked(self):
        client = PublishingClient({"a1": [track("Song")]}, cover=(False, "too big"))

        summary = publishing_service(client).build_playlist(
            "artist-id", "Artist", 1, use_artist_image_as_cover=True
        )

        assert client.cover_requests == [("pl1", "artist-id")]
        assert summary.cover_applied is False
        assert summary.cover_error == "too big"

    def test_nothing_is_added_when_spotify_returns_no_playlist_id(self):
        client = PublishingClient({"a1": [track("Song")]}, playlist={"name": "no id"})

        summary = publishing_service(client).build_playlist(
            "artist-id", "Artist", 1, use_artist_image_as_cover=True
        )

        assert summary.playlist_id is None
        assert client.added == []
        assert client.cover_requests == []


class TestPublish:
    def test_returns_the_playlist_id_and_cover_outcome(self):
        client = PublishingClient({})
        service = DiscographyService(client=client, logger=logger)

        result = service.publish("Artist discography", ["uri:a"], artist_id="artist-id",
                                 use_artist_image_as_cover=True)

        assert result == ("pl1", True, None)
        assert client.added == [("pl1", ["uri:a"])]

    def test_dry_run_publishes_nothing(self):
        client = PublishingClient({})
        service = DiscographyService(client=client, logger=logger, dry_run=True)

        assert service.publish("Artist discography", ["uri:a"]) == (None, False, None)
        assert client.added == []

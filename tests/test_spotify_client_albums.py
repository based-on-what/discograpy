import logging
import threading
from types import SimpleNamespace

import pytest
from spotipy.exceptions import SpotifyException

from src.services import retry
from src.services.spotify_client import SpotifyClient


def make_client(**sp_methods):
    return SpotifyClient(sp=SimpleNamespace(**sp_methods), logger=logging.getLogger("tests"))


def track(name, **extra):
    return {"name": name, "uri": f"uri:{name}", "duration_ms": 1000, "disc_number": 1, "track_number": 1, **extra}


def album_with(tracks, next_page=None):
    return {"id": "ignored", "tracks": {"items": tracks, "next": next_page}}


@pytest.fixture(autouse=True)
def no_backoff_wait(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda seconds: None)


class TestGetTracksByAlbum:
    def test_one_request_returns_the_tracks_of_every_album(self):
        requests = []

        def albums(ids):
            requests.append(ids)
            return {"albums": [album_with([track("A")]), album_with([track("B")])]}

        result = make_client(albums=albums).get_tracks_by_album(["al1", "al2"])

        assert requests == [["al1", "al2"]]
        assert [t["name"] for t in result["al1"]] == ["A"]
        assert [t["name"] for t in result["al2"]] == ["B"]

    def test_an_album_with_more_than_one_page_of_tracks_is_followed(self):
        second_page = {"items": [track("Second")], "next": None}
        client = make_client(
            albums=lambda ids: {"albums": [album_with([track("First")], next_page="page-2")]},
            next=lambda page: second_page,
        )

        result = client.get_tracks_by_album(["al1"])

        assert [t["name"] for t in result["al1"]] == ["First", "Second"]

    def test_an_album_spotify_answers_with_null_is_left_out(self):
        client = make_client(albums=lambda ids: {"albums": [None, album_with([track("B")])]})

        assert list(client.get_tracks_by_album(["gone", "al2"])) == ["al2"]

    def test_a_transient_error_is_retried(self):
        outcomes = [SpotifyException(502, -1, "bad gateway"), {"albums": [album_with([track("A")])]}]

        def albums(ids):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert list(make_client(albums=albums).get_tracks_by_album(["al1"])) == ["al1"]

    def test_only_the_fields_the_playlist_needs_are_kept(self):
        heavy = track("A", available_markets=["AR", "CL"], artists=[{"id": "x"}])
        client = make_client(albums=lambda ids: {"albums": [album_with([heavy])]})

        assert client.get_tracks_by_album(["al1"])["al1"] == [track("A")]


class TestGetArtistAlbums:
    def test_a_page_that_keeps_failing_does_not_refetch_the_pages_before_it(self):
        calls = {"first_page": 0, "next": 0}

        def artist_albums(**kwargs):
            calls["first_page"] += 1
            return {"items": [{"id": "al1", "release_date": "2020-01-01"}], "next": "page-2"}

        def next_page(page):
            calls["next"] += 1
            raise SpotifyException(500, -1, "server error")

        client = make_client(artist_albums=artist_albums, next=next_page)

        with pytest.raises(SpotifyException):
            client.get_artist_albums("ar1", ["album"])

        assert calls == {"first_page": 1, "next": 5}

    def test_a_transient_error_on_the_first_page_is_retried(self):
        outcomes = [
            SpotifyException(502, -1, "bad gateway"),
            {"items": [{"id": "al1", "release_date": "2020-01-01"}], "next": None},
        ]

        def artist_albums(**kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        albums = make_client(artist_albums=artist_albums).get_artist_albums("ar1", ["album"])

        assert [a["id"] for a in albums] == ["al1"]


    def test_only_the_fields_the_selection_needs_are_kept(self):
        heavy = {
            "id": "al1", "name": "LP", "album_type": "album", "total_tracks": 10,
            "release_date": "2020-01-01", "available_markets": ["AR", "CL"], "images": [{"url": "x"}],
        }
        client = make_client(artist_albums=lambda **kwargs: {"items": [heavy], "next": None})

        assert client.get_artist_albums("ar1", ["album"]) == [
            {"id": "al1", "name": "LP", "album_type": "album", "total_tracks": 10, "release_date": "2020-01-01"}
        ]


class TestAddTracksToPlaylist:
    def test_batches_reach_spotify_in_playlist_order_even_when_the_first_is_slow(self):
        landed = []
        lock = threading.Lock()
        a_later_batch_landed = threading.Event()

        def playlist_add_items(playlist_id, items):
            if items[0] == "uri:0":
                # Holds the first batch back long enough for any batch sent
                # concurrently to overtake it.
                a_later_batch_landed.wait(timeout=0.2)
            else:
                a_later_batch_landed.set()
            with lock:
                landed.append(items[0])

        uris = [f"uri:{n}" for n in range(250)]
        make_client(playlist_add_items=playlist_add_items).add_tracks_to_playlist("pl1", uris)

        assert landed == ["uri:0", "uri:100", "uri:200"]

    def test_no_batch_is_larger_than_the_spotify_limit(self):
        sizes = []
        client = make_client(playlist_add_items=lambda playlist_id, items: sizes.append(len(items)))

        client.add_tracks_to_playlist("pl1", [f"uri:{n}" for n in range(250)])

        assert sizes == [100, 100, 50]

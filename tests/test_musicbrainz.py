import pytest
import requests

from src.services import musicbrainz


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


@pytest.fixture
def responses(monkeypatch):
    """Queue what the MusicBrainz HTTP call does next: raise it or return it."""
    queue = []

    def fake_get(*args, **kwargs):
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(musicbrainz._session, "get", fake_get)
    monkeypatch.setattr(musicbrainz, "_throttle", lambda: None)
    return queue


FOUND = {"artists": [{"name": "Timeout Then Found", "score": 100, "country": "GB"}]}


class TestLookupArtistMetadata:
    def test_a_failed_lookup_is_retried_on_the_next_call(self, responses):
        responses.extend([requests.Timeout("slow"), FOUND])

        first = musicbrainz.lookup_artist_metadata("Timeout Then Found")
        second = musicbrainz.lookup_artist_metadata("Timeout Then Found")

        assert first == {"genres": [], "country": None}
        assert second["country"] == "United Kingdom"

    def test_a_successful_lookup_is_cached(self, responses):
        responses.append({"artists": [{"name": "Cached Artist", "score": 100, "country": "GB"}]})

        first = musicbrainz.lookup_artist_metadata("Cached Artist")
        second = musicbrainz.lookup_artist_metadata("Cached Artist")

        assert first == second
        assert responses == []

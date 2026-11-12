import logging
from types import SimpleNamespace

import pytest
from spotipy.exceptions import SpotifyException

from src.services import retry
from src.services.spotify_client import SpotifyClient


def client_whose_search(outcomes):
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = SpotifyClient(sp=SimpleNamespace(search=search), logger=logging.getLogger("tests"))
    return client, calls


@pytest.fixture(autouse=True)
def no_backoff_wait(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda seconds: None)


class TestSearchArtists:
    def test_a_transient_spotify_error_is_retried(self):
        found = {"artists": {"items": [{"id": "ar1"}]}}
        client, calls = client_whose_search([SpotifyException(502, -1, "bad gateway"), found])

        assert client.search_artists("Radiohead") == [{"id": "ar1"}]
        assert len(calls) == 2

    def test_search_stays_within_the_spotify_page_limit(self):
        client, calls = client_whose_search([{"artists": {"items": []}}])

        client.search_artists("Radiohead")

        assert calls == [{"q": "artist:Radiohead", "type": "artist", "limit": 50}]

    def test_empty_name_is_rejected_without_calling_spotify(self):
        client, calls = client_whose_search([])

        with pytest.raises(ValueError):
            client.search_artists("   ")
        assert calls == []

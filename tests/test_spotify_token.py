import logging
from types import SimpleNamespace

import pytest

from src.services.spotify_client import SpotifyClient
from src.web import create_app
from src.web.routes import _ensure_spotify_token


class FakeAuthManager:
    def __init__(self, cached=None, refreshed=None, cache_error=None):
        self.cached = cached
        self.refreshed = refreshed
        self.cache_error = cache_error
        self.refresh_calls = []

    def get_cached_token(self):
        if self.cache_error:
            raise self.cache_error
        return self.cached

    def is_token_expired(self, token_info):
        return token_info["expired"]

    def refresh_access_token(self, refresh_token):
        self.refresh_calls.append(refresh_token)
        return self.refreshed


def client_with(auth_manager):
    return SpotifyClient(sp=SimpleNamespace(auth_manager=auth_manager), logger=logging.getLogger("tests"))


@pytest.fixture
def app_context():
    with create_app().app_context():
        yield


@pytest.fixture
def refresh_token(monkeypatch):
    monkeypatch.setenv("SPOTIPY_REFRESH_TOKEN", "rt-env")


@pytest.fixture
def no_refresh_token(monkeypatch):
    monkeypatch.delenv("SPOTIPY_REFRESH_TOKEN", raising=False)


@pytest.mark.usefixtures("app_context")
class TestEnsureSpotifyTokenRoutePreflight:
    def test_valid_cached_token_passes_without_refreshing(self, refresh_token):
        auth = FakeAuthManager(cached={"expired": False})

        assert _ensure_spotify_token(client_with(auth)) is None
        assert auth.refresh_calls == []

    def test_expired_token_is_refreshed_with_the_env_refresh_token(self, refresh_token):
        auth = FakeAuthManager(cached={"expired": True}, refreshed={"access_token": "new"})

        assert _ensure_spotify_token(client_with(auth)) is None
        assert auth.refresh_calls == ["rt-env"]

    def test_failed_refresh_never_falls_back_to_the_expired_token(self, refresh_token):
        auth = FakeAuthManager(cached={"expired": True}, refreshed=None)

        payload = _ensure_spotify_token(client_with(auth))

        assert payload["auth_required"] is True
        assert "not authenticated" in payload["error"]

    def test_no_token_and_no_refresh_token_requires_auth(self, no_refresh_token):
        auth = FakeAuthManager(cached=None)

        assert _ensure_spotify_token(client_with(auth))["auth_required"] is True
        assert auth.refresh_calls == []

    def test_auth_manager_failure_requires_auth(self, refresh_token):
        auth = FakeAuthManager(cache_error=RuntimeError("token endpoint down"))

        assert _ensure_spotify_token(client_with(auth))["auth_required"] is True


class TestEnsureToken:
    def test_true_with_a_valid_token(self, refresh_token):
        assert client_with(FakeAuthManager(cached={"expired": False})).ensure_token() is True

    def test_false_when_the_refresh_fails(self, refresh_token):
        auth = FakeAuthManager(cached={"expired": True}, refreshed=None)

        assert client_with(auth).ensure_token() is False

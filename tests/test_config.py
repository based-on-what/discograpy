from spotipy.cache_handler import CacheFileHandler

from src import config


class TestBuildInteractiveAuth:
    def test_opens_a_browser_and_caches_to_disk_with_the_app_scope(self, monkeypatch):
        monkeypatch.setenv("SPOTIPY_CLIENT_ID", "id")
        monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "secret")
        monkeypatch.setenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

        auth = config.build_interactive_auth()

        assert auth.scope == config.SCOPE
        assert auth.open_browser is True
        assert isinstance(auth.cache_handler, CacheFileHandler)
        assert auth.cache_handler.cache_path == ".spotify_cache"

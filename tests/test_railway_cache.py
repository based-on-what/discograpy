import logging

from src.services import railway_cache
from src.services.railway_cache import RailwayAwareCacheHandler


class TestRotatedRefreshToken:
    def test_failed_railway_update_does_not_log_the_token(self, monkeypatch, caplog):
        monkeypatch.setattr(railway_cache, "_update_railway_var", lambda name, value: False)
        handler = RailwayAwareCacheHandler(token_info={"refresh_token": "rt-old"})

        with caplog.at_level(logging.DEBUG):
            handler.save_token_to_cache({"access_token": "at", "refresh_token": "rt-rotated-secret"})

        assert "rt-rotated-secret" not in caplog.text
        assert "SPOTIPY_REFRESH_TOKEN" in caplog.text
        assert handler.get_cached_token()["refresh_token"] == "rt-rotated-secret"

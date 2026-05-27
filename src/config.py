import os

import spotipy
from spotipy.oauth2 import SpotifyOAuth


def build_spotify_client() -> spotipy.Spotify:
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

    missing = [
        name
        for name, value in {
            "SPOTIPY_CLIENT_ID": client_id,
            "SPOTIPY_CLIENT_SECRET": client_secret,
            "SPOTIPY_REDIRECT_URI": redirect_uri,
        }.items()
        if not value
    ]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

    scope = "playlist-modify-public playlist-modify-private ugc-image-upload"
    use_cache = os.getenv("SPOTIPY_USE_CACHE", "false").lower() in {"1", "true", "yes"}
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_path=".spotify_cache" if use_cache else None,
        open_browser=use_cache,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=15)

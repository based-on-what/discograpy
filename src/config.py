import os

import spotipy
from spotipy.oauth2 import SpotifyOAuth

SCOPE = "playlist-modify-public playlist-modify-private ugc-image-upload"


def build_spotify_client() -> spotipy.Spotify:
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")
    refresh_token = os.getenv("SPOTIPY_REFRESH_TOKEN")

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

    if refresh_token:
        # En Railway el filesystem es efímero: MemoryCacheHandler evita depender
        # de un archivo .cache que desaparece en cada redeploy.
        # RailwayAwareCacheHandler además actualiza SPOTIPY_REFRESH_TOKEN en Railway
        # si Spotify rota el refresh token — requiere RAILWAY_API_TOKEN configurado.
        from src.services.railway_cache import RailwayAwareCacheHandler

        cache_handler = RailwayAwareCacheHandler(
            token_info={
                "access_token": "",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh_token,
                "expires_at": 0,   # Fuerza refresh del access token en el primer uso
                "scope": SCOPE,
            }
        )
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPE,
            cache_handler=cache_handler,
            open_browser=False,
        )
    else:
        # Flujo local / desarrollo: usa cache en disco y abre browser
        use_cache = os.getenv("SPOTIPY_USE_CACHE", "false").lower() in {"1", "true", "yes"}
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPE,
            cache_path=".spotify_cache" if use_cache else None,
            open_browser=use_cache,
        )

    return spotipy.Spotify(auth_manager=auth, requests_timeout=15)

# get_token.py  (run locally, not on Railway)
import os

from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from src.config import SCOPE

load_dotenv()

auth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope=SCOPE,
    cache_path=".spotify_cache",
    open_browser=True,
)

token = auth.get_access_token(as_dict=True)
print("REFRESH TOKEN:", token["refresh_token"])

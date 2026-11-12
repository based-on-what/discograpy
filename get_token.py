# get_token.py  (run locally, not on Railway)
from dotenv import load_dotenv

from src.config import build_interactive_auth

load_dotenv()

token = build_interactive_auth().get_access_token(as_dict=True)
print("REFRESH TOKEN:", token["refresh_token"])

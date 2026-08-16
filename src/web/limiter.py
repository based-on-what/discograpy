"""Rate limiting for the public API.

Every visitor shares the server owner's Spotify creator token, so /api/create
writes playlists to the owner's account. Limits are per remote address and
deliberately conservative.

This module must not import from ``src.web`` — ``routes.py`` imports the
limiter at module level and ``src.web.create_app`` calls ``init_app``.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# /api/search fires on a 350ms input debounce in the frontend, so the search
# limit has to tolerate a user typing an artist name in a few bursts.
SEARCH_LIMIT = "20 per minute"

# /api/create is the expensive one: it writes to the owner's Spotify account.
CREATE_LIMITS = "5 per minute; 30 per hour"

DEFAULT_LIMITS = ["200 per hour"]

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=DEFAULT_LIMITS,
    storage_uri="memory://",
    strategy="fixed-window",
)

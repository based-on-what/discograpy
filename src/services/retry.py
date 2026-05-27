import logging
import random
import time
from functools import wraps
from typing import Any, Callable, Optional

from spotipy.exceptions import SpotifyException


def retry_on_failure(max_retries: int = 3, delay: float = 1.0) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[SpotifyException] = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except SpotifyException as exc:
                    last_exc = exc
                    if exc.http_status == 429:
                        retry_after_header = (exc.headers or {}).get("Retry-After")
                        retry_after = (
                            max(2, int(retry_after_header))
                            if retry_after_header
                            else int(delay * (2**attempt))
                        )
                        jitter = random.uniform(0.5, 2.0)
                        logging.getLogger(__name__).warning(
                            "Rate limited. Waiting %.1fs", retry_after + jitter
                        )
                        time.sleep(retry_after + jitter)
                        continue

                    if attempt == max_retries - 1:
                        raise

                    wait_time = delay * (2**attempt)
                    logging.getLogger(__name__).warning(
                        "Spotify API failed in %s (attempt %s/%s). Retrying in %.1fs.",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    time.sleep(wait_time)
            if last_exc is not None:
                raise last_exc

        return wrapper

    return decorator

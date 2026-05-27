import logging
import sys


def configure_logging(verbose: bool = False) -> logging.Logger:
    root = logging.getLogger()
    if root.handlers:
        return logging.getLogger(__name__)
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler("spotify_discography.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(formatter)

    _safe_reconfigure_stream(console_handler)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return logging.getLogger(__name__)


def _safe_reconfigure_stream(handler: logging.StreamHandler) -> None:
    stream = handler.stream
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass

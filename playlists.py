"""DiscograPY CLI entry point."""

import argparse
import logging

from dotenv import load_dotenv

# Re-export for any external code that imports configure_logging from here.
from src.logging_config import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Spotify playlist from an artist discography."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose debug logging on console."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run discovery and filtering without creating a playlist.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    configure_logging(verbose=args.verbose)

    from src.cli.runner import run
    from src.config import build_spotify_client
    from src.services.discography import DiscographyService
    from src.services.spotify_client import SpotifyClient

    sp = build_spotify_client()
    logger = logging.getLogger("discography")
    client = SpotifyClient(sp=sp, logger=logger)
    svc = DiscographyService(client=client, logger=logger, dry_run=args.dry_run)
    run(svc, verbose=args.verbose)


if __name__ == "__main__":
    main()

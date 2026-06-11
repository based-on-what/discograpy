import logging
from typing import Optional, Set

from spotipy.exceptions import SpotifyException

from src.cli import ui
from src.domain import album_types as at
from src.domain.models import RunSummary
from src.services.discography import PLAYLIST_DESCRIPTION, DiscographyService
from src.services.spotify_client import SpotifyClient


def run(svc: DiscographyService, verbose: bool = False) -> None:
    try:
        _create_discography_playlist(svc, verbose=verbose)
    except SpotifyException as exc:
        message = SpotifyClient.error_message(exc)
        logging.getLogger(__name__).error("Spotify API error: %s", exc)
        print(f"\n✗ {message}")
    except EnvironmentError as exc:
        logging.getLogger(__name__).error("Environment configuration error: %s", exc)
        print(f"\n✗ {exc}")
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Process interrupted by user")
        print("\n\nProcess cancelled by user.")


def _create_discography_playlist(svc: DiscographyService, verbose: bool = False) -> None:
    ui.print_header()

    while True:
        artist_name_input = input("\nEnter artist name: ").strip()
        if not artist_name_input:
            print("Artist name cannot be empty.")
            continue

        with ui.Spinner("Searching artists..."):
            artists = svc.search_artists(artist_name_input)

        if not artists:
            print("\nNo artists found with that name.")
            ui.display_menu("Options", ["Try another artist search", "Exit"])
            if ui.get_numeric_input("\nSelect option (0-1): ", 0, 1) == 0:
                continue
            return

        ui.display_artists(artists)
        artist_id, selected_name = ui.select_artist(artists)

        while True:
            options = [
                "Everything (all album types combined)",
                "Albums only",
                "EPs only",
                "Singles only",
                "Compilations only",
                "EPs + Singles",
                "Albums + EPs + Singles",
            ]
            ui.display_menu("Album Type Selection", options)
            album_type_selection = ui.get_numeric_input("\nSelect album type (0-6): ", 0, 6)

            with ui.Spinner("Retrieving albums..."):
                filtered_albums, actual_types = svc.get_filtered_albums(artist_id, album_type_selection)

            if verbose:
                logger = logging.getLogger(__name__)
                for album in filtered_albums:
                    year = str(album.get("release_date", ""))[:4] or "n/a"
                    logger.debug("Album selected: %s (%s)", album.get("name", "Unknown"), year)

            if not filtered_albums:
                title = at.get_selection_title(album_type_selection)
                print(f"\nNo {title} found for this artist with current filters.")
                ui.display_menu(
                    "Options",
                    ["Try with a different artist", "Try with a different album type selection"],
                )
                retry_choice = ui.get_numeric_input("\nSelect option (0-1): ", 0, 1)
                if retry_choice == 0:
                    break
                continue

            if album_type_selection in (5, 6):
                _warn_missing_types(album_type_selection, actual_types)

            with ui.Spinner("Collecting tracks..."):
                track_uris, _ = svc.collect_tracks(filtered_albums, verbose=verbose)

            if not track_uris:
                print("No tracks found to process with the current filters.")
                return

            suffix = at.get_playlist_suffix(
                album_type_selection,
                actual_types if album_type_selection in (5, 6) else None,
            )
            playlist_name = f"{selected_name} discography {suffix}"

            playlist_id: Optional[str] = None
            if svc.dry_run:
                print("\nDry-run enabled: playlist will not be created and tracks will not be uploaded.")
            else:
                with ui.Spinner("Creating playlist..."):
                    playlist = svc.client.create_playlist(playlist_name, PLAYLIST_DESCRIPTION)
                    playlist_id = playlist.get("id")

                with ui.Spinner("Adding tracks to playlist..."):
                    svc.client.add_tracks_to_playlist(playlist_id, track_uris)

            summary = RunSummary(
                artist=selected_name,
                playlist_name=playlist_name,
                playlist_id=playlist_id,
                albums_included=len(filtered_albums),
                tracks_added=len(track_uris),
                total_ms=0,
                dry_run=svc.dry_run,
            )
            ui.print_summary(summary)
            return


def _warn_missing_types(selection: int, actual_types: Set[str]) -> None:
    expected_map = {5: {"ep", "single"}, 6: {"album", "ep", "single"}}
    expected = expected_map.get(selection)
    if not expected:
        return
    missing = expected - actual_types
    if not missing:
        return
    map_name = {"album": "Albums", "ep": "EPs", "single": "Singles"}
    missing_text = ", ".join(map_name[item] for item in sorted(missing))
    logging.getLogger(__name__).warning(
        "%s not found on Spotify for this artist. Using available types instead.", missing_text
    )

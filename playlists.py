import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
import os
import time
import logging
from typing import List, Dict, Generator, Tuple
from functools import wraps
from concurrent.futures import ThreadPoolExecutor

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('spotify_discography.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Decorator for retrying API calls with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        delay: Initial delay in seconds between retries (default: 1.0)

    Returns:
        Decorated function that retries on failure with exponential backoff

    Note:
        - For HTTP 429 (rate limit), uses Retry-After header if available
        - For other errors, uses exponential backoff: delay * (2 ** attempt)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except SpotifyException as e:
                    if e.http_status == 429:  # Rate limit
                        retry_after = int(e.headers.get('Retry-After', delay * (2 ** attempt)))
                        logger.warning(f"Rate limited. Waiting {retry_after} seconds")
                        time.sleep(retry_after)
                    elif attempt == max_retries - 1:
                        logger.error(f"API call failed after {max_retries} attempts: {e}")
                        raise
                    else:
                        wait_time = delay * (2 ** attempt)
                        logger.warning(f"API call failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                        time.sleep(wait_time)
                except Exception as e:
                    logger.error(f"Unexpected error in API call: {e}")
                    raise
            return None
        return wrapper
    return decorator


class SpotifyDiscographyCreator:
    """Optimized Spotify discography playlist creator with robust error handling and performance improvements."""

    def __init__(self):
        """Initialize Spotify client with authentication."""
        load_dotenv()
        self._setup_spotify_client()
        self.user_id = None

    def _setup_spotify_client(self) -> None:
        """Setup Spotify client with proper error handling."""
        try:
            auth = SpotifyOAuth(
                client_id=os.getenv('SPOTIPY_CLIENT_ID'),
                client_secret=os.getenv('SPOTIPY_CLIENT_SECRET'),
                redirect_uri=os.getenv('SPOTIPY_REDIRECT_URI'),
                scope="playlist-modify-public"
            )
            self.sp = spotipy.Spotify(auth_manager=auth)
            logger.info("Spotify client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Spotify client: {e}")
            raise

    @retry_on_failure(max_retries=3)
    def _search_artists_paginated(self, artist_name: str) -> Generator[Dict, None, None]:
        """Generator for paginated artist search to optimize memory usage.

        Args:
            artist_name: Name of the artist to search for

        Yields:
            Artist dictionaries from Spotify search results

        Raises:
            Exception: If search fails after retries
        """
        try:
            results = self.sp.search(q=f'artist:{artist_name}', type='artist', limit=50)

            if not results or 'artists' not in results:
                logger.warning("Invalid search response received")
                return

            # Yield current batch
            for artist in results['artists']['items']:
                yield artist

            # Continue pagination
            while results['artists']['next']:
                results = self.sp.next(results['artists'])
                if results and 'artists' in results:
                    for artist in results['artists']['items']:
                        yield artist
                else:
                    logger.warning("Invalid pagination response received")
                    break

        except Exception as e:
            logger.error(f"Error during artist search: {e}")
            raise

    def search_artists(self, artist_name: str) -> List[Dict]:
        """Search for artists with improved error handling and memory optimization.

        Args:
            artist_name: Name of the artist to search for

        Returns:
            List of artist dictionaries matching the search query

        Raises:
            ValueError: If artist_name is empty
            Exception: If search fails after retries
        """
        if not artist_name or not artist_name.strip():
            raise ValueError("Artist name cannot be empty")

        logger.info(f"Searching for artist: {artist_name}")

        try:
            # Use generator to avoid loading all results into memory at once
            artists = list(self._search_artists_paginated(artist_name.strip()))

            if not artists:
                logger.info("No artists found with that name")
                return []

            logger.info(f"Found {len(artists)} artists")
            return artists

        except Exception as e:
            logger.error(f"Failed to search artists: {e}")
            raise

    def display_artists(self, artists: List[Dict]) -> None:
        """Display found artists to user.

        Args:
            artists: List of artist dictionaries to display
        """
        print(f"\nFound {len(artists)} artists:")
        for i, artist in enumerate(artists, 1):
            print(f"{i}. {artist['name']}")

    def select_artist(self, artists: List[Dict]) -> Tuple[str, str]:
        """Handle artist selection with robust input validation.

        Args:
            artists: List of artist dictionaries to choose from

        Returns:
            Tuple of (artist_id, artist_name) for the selected artist

        Raises:
            KeyboardInterrupt: If user cancels the selection
        """
        while True:
            try:
                selection = int(input(f"\nSelect artist number (1-{len(artists)}): "))
                if 1 <= selection <= len(artists):
                    selected_artist = artists[selection-1]
                    artist_id = selected_artist['id']
                    artist_name = selected_artist['name']
                    logger.info(f"Selected artist: {artist_name} (ID: {artist_id})")
                    return artist_id, artist_name
                else:
                    print("Please select a valid number.")
            except ValueError:
                print("Please enter a valid number.")
            except KeyboardInterrupt:
                logger.info("User cancelled selection")
                raise

    @retry_on_failure(max_retries=3)
    def _get_artist_albums(self, artist_id: str) -> List[Dict]:
        """Get all albums for an artist with pagination and validation.

        Args:
            artist_id: Spotify artist ID

        Returns:
            List of album dictionaries sorted by release date

        Raises:
            Exception: If album retrieval fails after retries
        """
        try:
            albums = []
            results = self.sp.artist_albums(artist_id, album_type='album', limit=50)

            if not results or 'items' not in results:
                logger.warning("Invalid album search response")
                return []

            albums.extend(results['items'])

            # Handle pagination
            while results.get('next'):
                results = self.sp.next(results)
                if results and 'items' in results:
                    albums.extend(results['items'])
                else:
                    logger.warning("Invalid album pagination response")
                    break

            # Filter out invalid albums and sort by release date
            valid_albums = [album for album in albums if album.get('release_date')]
            valid_albums.sort(key=lambda album: album['release_date'])

            logger.info(f"Retrieved {len(valid_albums)} albums")
            return valid_albums

        except Exception as e:
            logger.error(f"Failed to get artist albums: {e}")
            raise

    @retry_on_failure(max_retries=3)
    def _get_album_tracks(self, album_uri: str) -> List[str]:
        """Get all tracks from an album with pagination.

        Args:
            album_uri: Spotify album URI

        Returns:
            List of track URIs from the album

        Note:
            Returns empty list on error instead of raising exception
            to prevent failure of entire discography process
        """
        try:
            track_uris = []
            results = self.sp.album_tracks(album_uri)

            if not results or 'items' not in results:
                logger.warning(f"Invalid album tracks response for {album_uri}")
                return []

            # Add tracks from current page
            for track in results['items']:
                if track and track.get('uri'):
                    track_uris.append(track['uri'])

            # Handle pagination
            while results.get('next'):
                results = self.sp.next(results)
                if results and 'items' in results:
                    for track in results['items']:
                        if track and track.get('uri'):
                            track_uris.append(track['uri'])
                else:
                    break

            return track_uris

        except Exception as e:
            logger.error(f"Failed to get album tracks for {album_uri}: {e}")
            return []  # Return empty list instead of failing entire process

    def _get_album_tracks_threaded(self, albums: List[Dict]) -> List[str]:
        """Get tracks from all albums using threading for better performance.

        Args:
            albums: List of album dictionaries containing 'uri' and 'name' keys

        Returns:
            List of track URIs in the same order as the input albums
        """
        all_track_uris = []

        # Use ThreadPoolExecutor for concurrent album processing
        with ThreadPoolExecutor(max_workers=5) as executor:  # Limit concurrent requests
            # Submit all album track requests and maintain order with a list
            futures = [
                executor.submit(self._get_album_tracks, album['uri'])
                for album in albums
            ]

            # Process results in order to maintain album chronology
            for i, future in enumerate(futures):
                try:
                    track_uris = future.result(timeout=30)  # 30 second timeout
                    all_track_uris.extend(track_uris)
                    logger.info(f"Processed album: {albums[i]['name']} ({len(track_uris)} tracks)")
                except Exception as e:
                    logger.error(f"Failed to process album {albums[i]['name']}: {e}")

        return all_track_uris

    @retry_on_failure(max_retries=3)
    def _create_playlist(self, playlist_name: str) -> Dict:
        """Create playlist with error handling.

        Args:
            playlist_name: Name for the new playlist

        Returns:
            Playlist dictionary containing 'id' and other metadata

        Raises:
            ValueError: If user info or playlist creation fails
            Exception: If playlist creation fails after retries
        """
        try:
            if not self.user_id:
                user_info = self.sp.current_user()
                if not user_info or 'id' not in user_info:
                    raise ValueError("Unable to get current user information")
                self.user_id = user_info['id']

            playlist = self.sp.user_playlist_create(
                user=self.user_id,
                name=playlist_name,
                public=True
            )

            if not playlist or 'id' not in playlist:
                raise ValueError("Invalid playlist creation response")

            logger.info(f"Created playlist: {playlist_name} (ID: {playlist['id']})")
            return playlist

        except Exception as e:
            logger.error(f"Failed to create playlist: {e}")
            raise

    @retry_on_failure(max_retries=3)
    def _add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> None:
        """Add tracks to playlist in batches with error handling.

        Args:
            playlist_id: Spotify playlist ID
            track_uris: List of track URIs to add

        Note:
            Automatically handles Spotify's 100-track batch limit

        Raises:
            Exception: If any batch fails to add after retries
        """
        if not track_uris:
            logger.warning("No tracks to add to playlist")
            return

        batch_size = 100  # Spotify API limit
        total_batches = (len(track_uris) + batch_size - 1) // batch_size

        for i, batch_start in enumerate(range(0, len(track_uris), batch_size)):
            batch = track_uris[batch_start:batch_start + batch_size]
            try:
                self.sp.playlist_add_items(playlist_id=playlist_id, items=batch)
                logger.info(f"Added batch {i + 1}/{total_batches} ({len(batch)} tracks)")
            except Exception as e:
                logger.error(f"Failed to add batch {i + 1}: {e}")
                raise

    def create_discography_playlist(self) -> None:
        """Main method to create discography playlist with full error handling.

        This method orchestrates the entire workflow:
        1. Prompts user for artist name
        2. Searches and displays matching artists
        3. Allows user to select the correct artist
        4. Fetches all albums sorted by release date
        5. Creates a new playlist
        6. Fetches all tracks from albums (parallelized)
        7. Adds tracks to the playlist in batches

        Raises:
            KeyboardInterrupt: If user cancels the process
            Exception: For any unrecoverable errors
        """
        try:
            # Get artist name
            artist_name = input("Enter artist name: ").strip()
            if not artist_name:
                print("Artist name cannot be empty.")
                return

            # Search for artists
            artists = self.search_artists(artist_name)
            if not artists:
                print("No artists found with that name.")
                print("Exiting in 30 seconds...")
                time.sleep(30)
                return

            # Display and select artist
            self.display_artists(artists)
            artist_id, selected_artist_name = self.select_artist(artists)

            # Get albums
            logger.info("Retrieving artist albums...")
            albums = self._get_artist_albums(artist_id)
            if not albums:
                print("No albums found for this artist.")
                return

            # Create playlist
            playlist_name = f"{selected_artist_name} discography"
            playlist = self._create_playlist(playlist_name)

            # Get all tracks using threading for better performance
            logger.info("Retrieving album tracks...")
            track_uris = self._get_album_tracks_threaded(albums)

            if not track_uris:
                print("No tracks found to add to playlist.")
                return

            # Add tracks to playlist
            logger.info(f"Adding {len(track_uris)} tracks to playlist...")
            self._add_tracks_to_playlist(playlist['id'], track_uris)

            print(f"'{playlist_name}' playlist created successfully!")
            logger.info(f"Successfully created playlist with {len(track_uris)} tracks")

        except KeyboardInterrupt:
            logger.info("Process interrupted by user")
            print("\nProcess cancelled by user.")
        except Exception as e:
            logger.error(f"Failed to create discography playlist: {e}")
            print(f"An error occurred: {e}")
            raise


def main():
    """Main entry point with error handling."""
    try:
        creator = SpotifyDiscographyCreator()
        creator.create_discography_playlist()
    except Exception as e:
        logger.error(f"Application failed: {e}")
        print("Application encountered an error. Check logs for details.")


if __name__ == "__main__":
    main()

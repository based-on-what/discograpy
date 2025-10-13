import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
import os
import time
import logging
from typing import List, Dict, Generator, Tuple, Optional, Set
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

    # Class constants for album type configurations
    ALBUM_TYPE_CONFIGS = {
        0: {
            'types': ['album', 'single', 'compilation'],
            'suffix': '[EVERYTHING]',
            'description': 'everything',
            'filter_func': lambda self, album: True  # Accept all
        },
        1: {
            'types': ['album'],
            'suffix': '[ALBUMS]',
            'description': 'albums',
            'filter_func': lambda self, album: album.get('album_type', '').lower() == 'album'
        },
        2: {
            'types': ['single'],
            'suffix': '[EPs]',
            'description': 'EPs',
            'filter_func': lambda self, album: self._is_ep(album)
        },
        3: {
            'types': ['single'],
            'suffix': '[SINGLES]',
            'description': 'singles',
            'filter_func': lambda self, album: self._is_single(album)
        },
        4: {
            'types': ['album', 'single', 'compilation'],
            'suffix': '[COMPILATIONS]',
            'description': 'compilations',
            'filter_func': lambda self, album: album.get('album_type', '').lower() == 'compilation'
        },
        5: {
            'types': ['single'],
            'suffix': '[EPs + SINGLES]',
            'description': 'EPs and singles',
            'filter_func': lambda self, album: self._is_ep(album) or self._is_single(album)
        },
        6: {
            'types': ['album', 'single'],
            'suffix': '[ALBUMS + EPs + SINGLES]',
            'description': 'albums, EPs, and singles',
            'filter_func': lambda self, album: (
                album.get('album_type', '').lower() == 'album' or 
                self._is_ep(album) or 
                self._is_single(album)
            )
        }
    }

    PLAYLIST_DESCRIPTION = (
        "Made with DiscograPY, an open-source Spotify discography creator. "
        "Find the project at: https://github.com/based-on-what/discograpy"
    )

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

    def _is_ep(self, album: Dict) -> bool:
        """Determine if an album is an EP based on track count.
        
        Args:
            album: Album dictionary from Spotify API
            
        Returns:
            True if album is an EP (4-7 tracks and album_type is 'single')
        """
        album_type = album.get('album_type', '').lower()
        total_tracks = album.get('total_tracks', 0)
        return album_type == 'single' and 4 <= total_tracks <= 7

    def _is_single(self, album: Dict) -> bool:
        """Determine if an album is a single based on track count.
        
        Args:
            album: Album dictionary from Spotify API
            
        Returns:
            True if album is a single (1-3 tracks and album_type is 'single')
        """
        album_type = album.get('album_type', '').lower()
        total_tracks = album.get('total_tracks', 0)
        return album_type == 'single' and total_tracks <= 3

    def _display_menu(self, title: str, options: List[str]) -> None:
        """Display a formatted menu with title and options.
        
        Args:
            title: Menu title
            options: List of menu option strings
        """
        print(f"\n=== {title} ===")
        for i, option in enumerate(options):
            print(f"{i}: {option}")

    def _get_numeric_input(self, prompt: str, min_val: int, max_val: int) -> int:
        """Get validated numeric input from user within a range.
        
        Args:
            prompt: Input prompt to display
            min_val: Minimum valid value (inclusive)
            max_val: Maximum valid value (inclusive)
            
        Returns:
            Valid integer input from user
            
        Raises:
            KeyboardInterrupt: If user cancels input
        """
        while True:
            try:
                value = int(input(prompt))
                if min_val <= value <= max_val:
                    return value
                else:
                    print(f"Please select a valid number between {min_val} and {max_val}.")
            except ValueError:
                print("Please enter a valid number.")
            except KeyboardInterrupt:
                logger.info("User cancelled input")
                raise

    def display_album_type_menu(self) -> int:
        """Display album type selection menu and get user choice.

        Returns:
            Integer representing the user's album type selection (0-6)

        Raises:
            KeyboardInterrupt: If user cancels the selection
        """
        options = [
            "Everything (all album types combined)",
            "Albums only",
            "EPs only",
            "Singles only",
            "Compilations only",
            "EPs + Singles",
            "Albums + EPs + Singles"
        ]
        
        self._display_menu("Album Type Selection", options)
        selection = self._get_numeric_input("\nSelect album type (0-6): ", 0, 6)
        logger.info(f"Selected album type option: {selection}")
        return selection

    def get_album_types_from_selection(self, selection: int) -> List[str]:
        """Convert user selection to Spotify album type parameters.

        Args:
            selection: Integer representing user's choice (0-6)

        Returns:
            List of album type strings for Spotify API
        """
        config = self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])
        return config['types']

    def get_playlist_suffix_from_selection(self, selection: int, actual_types: Optional[Set[str]] = None) -> str:
        """Get playlist name suffix based on album type selection and actual content.

        Args:
            selection: Integer representing user's choice (0-6)
            actual_types: Optional set of actual album types found (for mixed selections)

        Returns:
            String suffix for playlist name adjusted for actual content
        """
        config = self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])
        
        # If no actual_types provided, return the default suffix
        if actual_types is None:
            return config['suffix']
        
        # For mixed selections, adjust suffix based on what was actually found
        if selection in [5, 6]:  # Mixed selections
            has_albums = 'album' in actual_types
            has_eps = 'ep' in actual_types
            has_singles = 'single' in actual_types
            
            if selection == 5:  # EPs + Singles
                if has_eps and has_singles:
                    return '[EPs + SINGLES]'
                elif has_eps:
                    return '[EPs]'
                elif has_singles:
                    return '[SINGLES]'
            elif selection == 6:  # Albums + EPs + Singles
                parts = []
                if has_albums:
                    parts.append('ALBUMS')
                if has_eps:
                    parts.append('EPs')
                if has_singles:
                    parts.append('SINGLES')
                
                if parts:
                    return f"[{' + '.join(parts)}]"
        
        return config['suffix']

    def get_selection_description(self, selection: int) -> str:
        """Get human-readable description of the selection.

        Args:
            selection: Integer representing user's choice (0-6)

        Returns:
            String description of the selection
        """
        config = self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])
        return config['description']

    def filter_albums_by_selection(self, albums: List[Dict], selection: int) -> Tuple[List[Dict], Set[str]]:
        """Filter albums based on user's selection.

        Args:
            albums: List of album dictionaries
            selection: Integer representing user's choice (0-6)

        Returns:
            Tuple of (filtered albums list, set of actual types found)
        """
        config = self.ALBUM_TYPE_CONFIGS.get(selection, self.ALBUM_TYPE_CONFIGS[0])
        filter_func = config['filter_func']
        
        filtered = [album for album in albums if filter_func(self, album)]
        
        # Determine actual types found for mixed selections
        actual_types = set()
        for album in filtered:
            album_type = album.get('album_type', '').lower()
            if album_type == 'album':
                actual_types.add('album')
            elif self._is_ep(album):
                actual_types.add('ep')
            elif self._is_single(album):
                actual_types.add('single')
            elif album_type == 'compilation':
                actual_types.add('compilation')
        
        return filtered, actual_types

    def _check_missing_types_and_warn(self, selection: int, actual_types: Set[str]) -> None:
        """Check for missing album types in mixed selections and warn user.
        
        Args:
            selection: Integer representing user's choice (0-6)
            actual_types: Set of actual album types found
        """
        # Only check for mixed selections
        if selection == 5:  # EPs + Singles
            expected = {'ep', 'single'}
            missing = expected - actual_types
            if missing:
                missing_str = ' and '.join(m.upper() + 's' if m != 'ep' else 'EPs' for m in missing)
                print(f"\n⚠ Warning: {missing_str} not found on Spotify for this artist.")
                print("Using available types instead.")
                logger.warning(f"Missing types for selection {selection}: {missing}")
        
        elif selection == 6:  # Albums + EPs + Singles
            expected = {'album', 'ep', 'single'}
            missing = expected - actual_types
            if missing:
                missing_names = []
                for m in missing:
                    if m == 'album':
                        missing_names.append('Albums')
                    elif m == 'ep':
                        missing_names.append('EPs')
                    elif m == 'single':
                        missing_names.append('Singles')
                
                missing_str = ', '.join(missing_names)
                print(f"\n⚠ Warning: {missing_str} not found on Spotify for this artist.")
                print("Using available types instead.")
                logger.warning(f"Missing types for selection {selection}: {missing}")

    def handle_no_content_found(self, artist_name: str, selection: int) -> int:
        """Handle the case when no content is found for the selected criteria.

        Args:
            artist_name: Name of the artist
            selection: Integer representing user's album type choice (0-6)

        Returns:
            0 to retry with different artist, 1 to retry with different album type

        Raises:
            KeyboardInterrupt: If user cancels the selection
        """
        selection_description = self.get_selection_description(selection)
        print(f"\nNo {selection_description} found for this artist on Spotify.")
        
        options = [
            "Try with a different artist",
            "Try with a different album type selection"
        ]
        
        self._display_menu("Options", options)
        choice = self._get_numeric_input("\nSelect option (0-1): ", 0, 1)
        
        logger.info(f"User chose option {choice} after no content found")
        return choice

    @retry_on_failure(max_retries=3)
    def _paginate_spotify_results(self, initial_results: Dict, items_key: str = 'items') -> List[Dict]:
        """Generic pagination handler for Spotify API results.
        
        Args:
            initial_results: Initial API response containing items and pagination info
            items_key: Key name for items in the response (default: 'items')
            
        Returns:
            List of all items from paginated results
            
        Raises:
            Exception: If pagination fails after retries
        """
        if not initial_results:
            logger.warning("Empty initial results provided to pagination handler")
            return []
        
        # Handle nested results (e.g., search results have 'artists': {'items': ...})
        if items_key not in initial_results:
            # Try to find the items in nested structure
            for key in initial_results:
                if isinstance(initial_results[key], dict) and items_key in initial_results[key]:
                    initial_results = initial_results[key]
                    break
        
        if items_key not in initial_results:
            logger.warning(f"No '{items_key}' key found in results")
            return []
        
        all_items = list(initial_results[items_key])
        results = initial_results
        
        # Continue pagination
        while results.get('next'):
            try:
                results = self.sp.next(results)
                if results and items_key in results:
                    all_items.extend(results[items_key])
                else:
                    logger.warning("Invalid pagination response received")
                    break
            except Exception as e:
                logger.error(f"Error during pagination: {e}")
                raise
        
        logger.debug(f"Paginated {len(all_items)} total items")
        return all_items

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
            results = self.sp.search(q=f'artist:{artist_name}', type='artist', limit=50)
            
            if not results or 'artists' not in results:
                logger.warning("Invalid search response received")
                return []
            
            # Use generic pagination handler
            artists = self._paginate_spotify_results(results, 'items')

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
        selection = self._get_numeric_input(
            f"\nSelect artist number (1-{len(artists)}): ",
            1,
            len(artists)
        )
        
        selected_artist = artists[selection - 1]
        artist_id = selected_artist['id']
        artist_name = selected_artist['name']
        logger.info(f"Selected artist: {artist_name} (ID: {artist_id})")
        return artist_id, artist_name

    def handle_no_artists_found(self) -> bool:
        """Handle the case when no artists are found.

        Returns:
            True if user wants to retry, False if user wants to exit

        Raises:
            KeyboardInterrupt: If user cancels the selection
        """
        print("\nNo artists found with that name.")
        
        options = [
            "Try another artist search",
            "Exit"
        ]
        
        self._display_menu("Options", options)
        choice = self._get_numeric_input("\nSelect option (0-1): ", 0, 1)
        
        if choice == 0:
            logger.info("User chose to retry artist search")
            return True
        else:
            logger.info("User chose to exit")
            return False

    @retry_on_failure(max_retries=3)
    def _get_artist_albums(self, artist_id: str, album_types: List[str]) -> List[Dict]:
        """Get all albums for an artist with pagination and validation.

        Args:
            artist_id: Spotify artist ID
            album_types: List of album types to retrieve

        Returns:
            List of album dictionaries sorted by release date

        Raises:
            Exception: If album retrieval fails after retries
        """
        try:
            album_type_str = ','.join(album_types)
            results = self.sp.artist_albums(artist_id, album_type=album_type_str, limit=50)

            if not results or 'items' not in results:
                logger.warning("Invalid album search response")
                return []

            # Use generic pagination handler
            albums = self._paginate_spotify_results(results, 'items')

            # Filter out invalid albums and sort by release date
            valid_albums = [album for album in albums if album.get('release_date')]
            valid_albums.sort(key=lambda album: album['release_date'])

            logger.info(f"Retrieved {len(valid_albums)} albums for artist {artist_id}")
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
            results = self.sp.album_tracks(album_uri)

            if not results or 'items' not in results:
                logger.warning(f"Invalid album tracks response for {album_uri}")
                return []

            # Use generic pagination handler
            tracks = self._paginate_spotify_results(results, 'items')
            
            # Extract URIs from track objects
            track_uris = [track['uri'] for track in tracks if track and track.get('uri')]

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
    def _create_playlist(self, playlist_name: str, description: str) -> Dict:
        """Create playlist with error handling.

        Args:
            playlist_name: Name for the new playlist
            description: Description for the new playlist

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
                public=True,
                description=description
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
        4. Prompts user for album type selection
        5. Fetches all albums sorted by release date
        6. Filters albums based on user selection
        7. Handles missing album types appropriately
        8. Creates a new playlist with description
        9. Fetches all tracks from albums (parallelized)
        10. Adds tracks to the playlist in batches

        Raises:
            KeyboardInterrupt: If user cancels the process
            Exception: For any unrecoverable errors
        """
        try:
            artist_id = None
            selected_artist_name = None
            
            # Artist selection loop
            while True:
                # Get artist name
                artist_name = input("\nEnter artist name: ").strip()
                if not artist_name:
                    print("Artist name cannot be empty.")
                    continue

                # Search for artists
                artists = self.search_artists(artist_name)
                if not artists:
                    # Handle no artists found with retry option
                    if not self.handle_no_artists_found():
                        return
                    continue  # Retry artist search
                
                # Display and select artist
                self.display_artists(artists)
                artist_id, selected_artist_name = self.select_artist(artists)
                break

            # Album type and content loop
            while True:
                # Get album type selection
                album_type_selection = self.display_album_type_menu()
                album_types = self.get_album_types_from_selection(album_type_selection)

                # Get albums
                logger.info("Retrieving artist albums...")
                print("\nRetrieving albums...")
                albums = self._get_artist_albums(artist_id, album_types)
                
                # Filter albums based on selection
                filtered_albums, actual_types = self.filter_albums_by_selection(albums, album_type_selection)
                
                if not filtered_albums:
                    # No content found for this selection
                    logger.warning(f"No content found for selection: {self.get_selection_description(album_type_selection)}")
                    retry_choice = self.handle_no_content_found(selected_artist_name, album_type_selection)
                    
                    if retry_choice == 0:  # Try another artist
                        # Reset and go back to artist selection
                        artist_id = None
                        selected_artist_name = None
                        # Restart the entire process
                        self.create_discography_playlist()
                        return
                    elif retry_choice == 1:  # Try another album type
                        continue  # Continue to album type selection
                else:
                    # Content found
                    # For mixed selections, check if any types are missing and warn
                    if album_type_selection in [5, 6]:
                        self._check_missing_types_and_warn(album_type_selection, actual_types)
                    
                    # Adjust playlist suffix based on actual content found
                    playlist_suffix = self.get_playlist_suffix_from_selection(
                        album_type_selection, 
                        actual_types if album_type_selection in [5, 6] else None
                    )
                    
                    break

            # Create playlist with description
            playlist_name = f"{selected_artist_name} discography {playlist_suffix}"
            playlist = self._create_playlist(playlist_name, self.PLAYLIST_DESCRIPTION)
            print(f"\nCreated playlist: {playlist_name}")

            # Get all tracks using threading for better performance
            logger.info("Retrieving album tracks...")
            print("Retrieving tracks from albums...")
            track_uris = self._get_album_tracks_threaded(filtered_albums)

            if not track_uris:
                print("No tracks found to add to playlist.")
                logger.warning("No tracks retrieved from albums")
                return

            # Add tracks to playlist
            logger.info(f"Adding {len(track_uris)} tracks to playlist...")
            print(f"Adding {len(track_uris)} tracks to playlist...")
            self._add_tracks_to_playlist(playlist['id'], track_uris)

            print(f"\n✓ '{playlist_name}' playlist created successfully with {len(track_uris)} tracks!")
            logger.info(f"Successfully created playlist with {len(track_uris)} tracks")

        except KeyboardInterrupt:
            logger.info("Process interrupted by user")
            print("\n\nProcess cancelled by user.")
        except Exception as e:
            logger.error(f"Failed to create discography playlist: {e}", exc_info=True)
            print(f"\n✗ An error occurred: {e}")
            print("Check spotify_discography.log for details.")
            raise


def main():
    """Main entry point with error handling."""
    try:
        print("=" * 60)
        print("DiscograPY - Spotify Discography Playlist Creator")
        print("=" * 60)
        
        creator = SpotifyDiscographyCreator()
        creator.create_discography_playlist()
    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        logger.error(f"Application failed: {e}", exc_info=True)
        print("\n✗ Application encountered an error. Check logs for details.")


if __name__ == "__main__":
    main()

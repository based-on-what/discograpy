# 🎵 Spotify Discography Playlist Creator

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This Python script uses the **Spotify Web API** (via [Spotipy](https://spotipy.readthedocs.io/)) to **automatically generate a playlist containing all the albums of a given artist in release order**.

The script has been **optimized for performance, scalability, error handling, and logging**, making it suitable for handling artists with large discographies while minimizing API rate-limit issues.

## 📋 Table of Contents

- [Features](#-features)
- [Requirements](#️-requirements)
- [Installation](#-installation)
- [Setup](#-setup)
- [Usage](#-usage)
- [First Run](#-first-run)
- [Project Structure](#-project-structure)
- [Logging](#-logging)
- [Technical Improvements](#-technical-improvements)
- [Performance Optimization](#-performance-optimization)
- [Permissions Required](#-permissions-required)
- [Example Output](#-example-output)
- [Notes & Limitations](#️-notes--limitations)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)
- [Author](#-author)

---

## ✨ Features

- Search for any artist on Spotify and select from all matching results.  
- **Choose album types:** Everything, Albums only, EPs only, Singles only, Compilations only, or combinations (EPs + Singles, Albums + EPs + Singles).
- Retrieve **all albums** (with pagination support).  
- **Smart filtering:** Distinguishes between albums, EPs (4-7 tracks), and singles (1-3 tracks).
- **Intelligent missing type handling:** Warns users when selected album types aren't available and adjusts playlist names accordingly.
- Collect **all tracks** from those albums using **threaded requests** for speed.  
- **Retry logic with exponential backoff** to handle API failures and rate limits gracefully.  
- Create a **new playlist in your account** with the full discography or selected type.  
- Tracks added in **correct release order**.  
- Professional **logging system** with timestamps, log levels, and **UTF-8 encoding support** for international characters.  
- Robust **error handling** to deal with Spotify API quirks, with interactive retry options.

---

## ⚙️ Requirements

- **Python 3.8+**  
- **Spotify Developer Account** with API credentials  
- Required Python packages:
  - `spotipy` (Spotify Web API wrapper)
  - `python-dotenv` (Environment variable management)

---

## 📦 Installation

### Option 1: Using requirements.txt (Recommended)

```bash
pip install -r requirements.txt
```

### Option 2: Manual installation

```bash
pip install spotipy python-dotenv
```

---

## 📂 Setup

### Step 1: Create Spotify Developer Application

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click **"Create an App"**
4. Fill in the app name and description
5. Accept the Terms of Service

### Step 2: Get Your Credentials

After creating your app, you'll see:
- **Client ID**
- **Client Secret** (click "Show Client Secret" to reveal)

### Step 3: Set Redirect URI

1. In your app settings, click **"Edit Settings"**
2. Add the following to **Redirect URIs**:

```
http://localhost:8888/callback/
```

3. Click **"Add"** and then **"Save"**

### Step 4: Configure Environment Variables

Create a `.env` file in the project root directory:

```env
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://localhost:8888/callback/
```

⚠️ **Important:** Never commit your `.env` file to version control. It's already included in `.gitignore`.

---

## 🚀 Usage

Run the script:

```bash
python playlists.py
```

### Interactive Prompts

1. **Enter artist name:**
   ```
   Enter artist name: System of a Down
   ```

2. **Select from matching artists** (if multiple found):
   ```
   Found 2 artists:
   1. System of a Down
   2. System of a Down Tribute
   
   Select artist number (1-2): 1
   ```

3. The script will then:
   - ✅ Prompt for album type selection (Everything, Albums only, EPs only, Singles only, Compilations only, EPs + Singles, or Albums + EPs + Singles)
   - ✅ Fetch all albums by that artist matching the selected type (with pagination)
   - ✅ Check for missing album types and warn if selected types aren't available
   - ✅ Adjust playlist name based on actual content found
   - ✅ Sort them by release date
   - ✅ Collect all tracks from each album (using parallel processing with 5 concurrent workers)
   - ✅ Create a new playlist: `[Artist Name] discography [TYPE]`
   - ✅ Add all tracks in chronological order

---

## 🔐 First Run

On your first run, you'll need to authenticate with Spotify:

1. The script will open a browser window automatically
2. Log in to Spotify (if not already logged in)
3. Click **"Agree"** to grant permissions
4. You'll be redirected to `http://localhost:8888/callback/...`
5. **Copy the entire URL** from your browser
6. **Paste it back** into the terminal when prompted
7. Press Enter

The script will save your authentication token in a `.cache` file. Future runs won't require this step unless the token expires or is deleted.

---

## 📁 Project Structure

```
discography/
├── playlists.py           # Main script
├── requirements.txt       # Python dependencies
├── .env                   # Your API credentials (create this)
├── .gitignore            # Git ignore rules
├── README.md             # This file
├── .cache                # Spotify auth token (auto-generated)
└── spotify_discography.log  # Log file (auto-generated)
```

---

## 🧠 Logging

The script uses Python's `logging` module for professional logging with both console and file output.

### UTF-8 Encoding Support

The logging system now includes **UTF-8 encoding support** with error handling for Windows compatibility:
- Console output uses UTF-8 encoding with 'replace' error handling
- Properly displays international characters and special symbols (✓, ✗, ⚠, etc.)
- File logging also uses UTF-8 encoding for comprehensive log files
- Compatible with artists using non-Latin characters (e.g., Japanese, Korean, Cyrillic)

### Log Locations

- **Console:** Real-time `INFO` level messages
- **File:** `spotify_discography.log` (includes all levels)

### Log Format

```
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

Example log output:

```
2025-09-21 12:00:00 - __main__ - INFO - Spotify client initialized successfully
2025-09-21 12:00:01 - __main__ - INFO - Found 3 artists
2025-09-21 12:00:05 - __main__ - WARNING - Rate limited. Waiting 7 seconds
2025-09-21 12:00:20 - __main__ - INFO - Successfully created playlist with 145 tracks
```

### Log Levels

- **INFO:** Normal operation messages
- **WARNING:** Rate limits, retries, non-critical issues
- **ERROR:** Failed operations, exceptions

---

## 📊 Technical Improvements

This script includes several optimizations compared to a basic implementation:

| Feature | Implementation | Benefit |
|---------|---------------|---------|
| **Scalability** | Handles large discographies with thousands of tracks | Works with any artist size |
| **Parallel Processing** | Uses `ThreadPoolExecutor` (5 workers) for concurrent album fetching | 3-5x faster |
| **Resilience** | Retry logic with exponential backoff for API errors | Handles transient failures |
| **Memory Efficiency** | Generator-based pagination | Low memory footprint |
| **Professional Logging** | Structured logging via `logging` module with UTF-8 encoding support | Easy debugging and international character support |
| **User Experience** | Interactive artist selection with validation and retry options | Clear and intuitive |
| **Error Handling** | Comprehensive try-except blocks with specific error messages | Graceful failures |
| **Smart Type Detection** | Warns when selected album types are missing and adjusts playlist names | Accurate playlist naming and user awareness |

---

## 💡 Performance Optimization

The script is highly optimized for speed and efficiency:

### Optimization Strategies

- **🔄 Parallel Processing**  
  Uses `ThreadPoolExecutor` with 5 concurrent workers to fetch album tracks simultaneously
  
- **📦 Batch Processing**  
  Adds tracks to playlists in batches of 100 (Spotify's API limit)
  
- **🧠 Memory Efficiency**  
  Generators for pagination prevent loading all data into memory at once
  
- **⏱️ Efficient Ordering**  
  Maintains chronological album order while processing in parallel
  
- **🔁 Smart Retry Logic**  
  Exponential backoff prevents hammering the API during failures
  
- **📊 Rate Limit Handling**  
  Respects Spotify's `Retry-After` header for HTTP 429 responses

### Performance Benchmarks

| Discography Size | Processing Time |
|-----------------|----------------|
| < 100 tracks    | 30-60 seconds  |
| 100-500 tracks  | 1-2 minutes    |
| 500-1000 tracks | 2-3 minutes    |
| 1000+ tracks    | 2-5 minutes    |

*Times may vary based on network speed and Spotify API response times.*

---

## 🔒 Permissions Required

The script requires the following Spotify OAuth scope:

- **`playlist-modify-public`** - Create and modify public playlists

### How It Works

- Spotipy handles OAuth 2.0 authentication automatically
- Your authentication token is cached in `.cache` file
- Token refresh happens automatically when expired
- No need to re-authenticate for future runs

### Privacy Note

If you prefer **private playlists**, you can modify the script:

```python
# In playlists.py, line 664, change:
public=True  # to:
public=False
```

The script also adds a description to each playlist linking back to the project repository.

---

## ✅ Example Output

### Console Output

```
Enter artist name: System of a Down

Found 1 artist:
1. System of a Down

Select artist number (1-1): 1

=== Album Type Selection ===
0: Everything (all album types combined)
1: Albums only
2: EPs only
3: Singles only
4: Compilations only
5: EPs + Singles
6: Albums + EPs + Singles

Select album type (0-6): 1

Retrieving albums...
Created playlist: System of a Down discography [ALBUMS]

Retrieving tracks from albums...
Adding 73 tracks to playlist...

✓ 'System of a Down discography [ALBUMS]' playlist created successfully with 73 tracks!
```

### Example with Missing Types Warning

When selecting a combination that includes types not available for an artist:

```
Select album type (0-6): 5

Retrieving albums...

⚠ Warning: EPs not found on Spotify for this artist.
Using available types instead.

Created playlist: Artist Name discography [SINGLES]

Retrieving tracks from albums...
Adding 12 tracks to playlist...

✓ 'Artist Name discography [SINGLES]' playlist created successfully with 12 tracks!
```

### What Happens Behind the Scenes

1. ✅ Spotify client initialized
2. ✅ Artist search performed
3. ✅ User selects correct artist
4. ✅ User selects album type (albums, EPs, singles, compilations, or combinations)
5. ✅ All albums retrieved and filtered by type
6. ✅ Missing album types detected and user warned if necessary
7. ✅ Playlist name adjusted based on actual content found
8. ✅ Albums sorted by release date
9. ✅ Playlist created in your account with descriptive name
10. ✅ Album tracks fetched in parallel (5 concurrent workers)
11. ✅ Tracks added to playlist in batches of 100
12. ✅ Success message displayed

The playlist appears **instantly** in your Spotify account and can be accessed from any device.

---

## ⚠️ Notes & Limitations

### API Limitations

- **Batch Size:** Spotify API limits to 100 tracks per request (handled automatically)
- **Rate Limiting:** The script respects rate limits with automatic retry and backoff
- **Album Types:** You can choose specific album types (albums, EPs, singles, compilations) or combine them

### Duplicate Handling

- Regional versions and deluxe editions may create duplicate albums
- All versions returned by Spotify are included in the playlist
- Consider this when creating playlists for artists with many special editions

### Playlist Permissions

- Script creates **public playlists** by default
- Your Spotify account must have permission to create playlists
- To create private playlists, modify line 338 in `playlists.py`

### Important Considerations

- 🔴 **Authentication Required:** First run requires browser-based OAuth flow
- 🟡 **Network Dependent:** Requires stable internet connection
- 🟢 **Safe to Interrupt:** Can safely cancel with `Ctrl+C` (playlist may be partial)

---

## 🐛 Troubleshooting

### Common Issues and Solutions

<details>
<summary><b>🔴 Authentication Error</b></summary>

**Symptoms:**
- Unable to connect to Spotify
- Invalid credentials error

**Solutions:**
1. Verify your `.env` file contains valid credentials
2. Ensure redirect URI matches **exactly** what you set in Spotify Developer Dashboard
3. Delete `.cache` file and re-authenticate
4. Check for typos in your Client ID and Client Secret
5. Ensure your Spotify Developer app is active

</details>

<details>
<summary><b>🟡 Rate Limiting (HTTP 429)</b></summary>

**Symptoms:**
- Script pauses frequently
- "Rate limited" warnings in logs

**Solutions:**
1. The script handles this automatically with exponential backoff
2. For persistent issues, reduce `MAX_CONCURRENT_WORKERS` in line 125 of `playlists.py` (change from 5 to 3)
3. Add artificial delays between operations
4. Wait a few minutes before retrying

</details>

<details>
<summary><b>🟢 No Content Found</b></summary>

**Symptoms:**
- "No [albums/EPs/singles/etc.] found for this artist"

**Solutions:**
1. Verify the artist has the selected album type on Spotify
2. Try selecting "Everything" to see all available content
3. Some artists may not have all content types (e.g., no EPs or compilations)
4. Try searching with exact artist name as shown on Spotify
5. Check if the artist has region-specific content restrictions
6. The script will automatically warn you when types are missing and adjust the playlist name
7. Choose option 1 to retry with a different album type, or option 0 to search for a different artist

</details>

<details>
<summary><b>🔵 Tracks Not Added</b></summary>

**Symptoms:**
- Playlist created but empty or incomplete

**Solutions:**
1. Check your Spotify account has permission to create playlists
2. Verify you're not hitting Spotify's rate limits
3. Review `spotify_discography.log` for detailed error messages
4. Check your internet connection stability
5. Try running the script again (idempotent operations)

</details>

<details>
<summary><b>🟣 Import Errors</b></summary>

**Symptoms:**
- `ModuleNotFoundError: No module named 'spotipy'`
- `ModuleNotFoundError: No module named 'dotenv'`

**Solutions:**
1. Install dependencies: `pip install -r requirements.txt`
2. Ensure you're using Python 3.8 or higher: `python --version`
3. Check if you're in the correct virtual environment
4. Try: `python3 -m pip install spotipy python-dotenv`

</details>

### Still Having Issues?

1. **Check the log file:** `spotify_discography.log` contains detailed error information
2. **Enable debug logging:** Modify line 14 in `playlists.py`: `level=logging.DEBUG`
3. **Test API credentials:** Use Spotify's [Web API Console](https://developer.spotify.com/console/)
4. **Verify network:** Ensure you can access `api.spotify.com`

---

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

### Reporting Issues

1. Check existing issues first
2. Provide detailed description
3. Include error messages and logs
4. Specify Python version and OS

### Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow PEP 8 style guidelines
- Add type hints to new functions
- Update documentation for any changes
- Test with multiple artists and scenarios
- Add logging for new features

---

## 📄 License

This project is licensed under the **MIT License**.

```
MIT License

Copyright (c) 2025 Spotify Discography Playlist Creator

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 👨‍💻 Author

**Developed with ❤️ for automation and music lovers.**

### Connect

- GitHub: [@based-on-what](https://github.com/based-on-what)

### Acknowledgments

- [Spotipy](https://spotipy.readthedocs.io/) - Excellent Python wrapper for Spotify Web API
- [Spotify Web API](https://developer.spotify.com/documentation/web-api/) - Comprehensive music data platform
- All contributors and users of this project

---

## 📌 Quick Start Checklist

Before running the script, ensure you have:

- [x] Python 3.8+ installed (`python --version`)
- [x] Dependencies installed (`pip install -r requirements.txt`)
- [x] Spotify Developer app created at [developer.spotify.com](https://developer.spotify.com/dashboard)
- [x] `.env` file configured with `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, and `SPOTIPY_REDIRECT_URI`
- [x] Redirect URI set to `http://localhost:8888/callback/` in Spotify app settings
- [x] Ready for first-time authentication flow

---

## 💡 Pro Tips

### For Power Users

- **Large Discographies:** For artists with 1000+ tracks, the script typically completes in 2-5 minutes
- **Caching:** Consider implementing local caching for frequently accessed artists
- **Batch Operations:** Process multiple artists by creating a wrapper script
- **Custom Workers:** Adjust `MAX_CONCURRENT_WORKERS` constant (line 125) based on your network speed
- **Timeouts:** Adjust `ALBUM_FETCH_TIMEOUT` constant (line 126) for slower connections

### Advanced Configuration

The script uses class constants for easy configuration. You can modify these values in `playlists.py`:

```python
# Performance constants (lines 125-127)
MAX_CONCURRENT_WORKERS = 5  # Number of parallel album fetches (increase for faster processing)
ALBUM_FETCH_TIMEOUT = 30    # Timeout in seconds for fetching album tracks
SPOTIFY_BATCH_SIZE = 100    # Spotify API limit for adding tracks (do not exceed 100)

# For more advanced customization:
# Increase parallelism for faster processing (if network allows)
with ThreadPoolExecutor(max_workers=10) as executor:  # Line 626

# Adjust retry attempts for unstable connections
@retry_on_failure(max_retries=5, delay=2.0)  # Lines 392, 540, 576, 639, 678
```

---

**Happy listening! 🎧**

*Star ⭐ this repository if you find it useful!*

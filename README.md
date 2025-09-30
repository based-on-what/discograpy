# Spotify Discography Playlist Creator

This Python script uses the **Spotify Web API** (via [Spotipy](https://spotipy.readthedocs.io/)) to **automatically generate a playlist containing all the albums of a given artist in release order**.

The script has been **optimized for performance, scalability, error handling, and logging**, making it suitable for handling artists with large discographies while minimizing API rate-limit issues.

---

## ✨ Features

- Search for any artist on Spotify and select from all matching results.  
- Retrieve **all albums** (with pagination support).  
- Collect **all tracks** from those albums using **threaded requests** for speed.  
- **Retry logic with exponential backoff** to handle API failures and rate limits gracefully.  
- Create a **new playlist in your account** with the full discography.  
- Tracks added in **correct release order**.  
- Professional **logging system** with timestamps and log levels.  
- Robust **error handling** to deal with Spotify API quirks.

---

## ⚙️ Requirements

- Python **3.8+**  
- Spotify Developer Account with API credentials  
- Dependencies:
  - `spotipy`
  - `python-dotenv`

Install the dependencies:

```bash
pip install spotipy python-dotenv
```

---

## 📂 Setup

1. Create a Spotify developer application at the Spotify Developer Dashboard.  
2. Get your `CLIENT_ID`, `CLIENT_SECRET`, and set a `REDIRECT_URI`.

Example redirect URI:

```
http://localhost:8888/callback/
```

3. Create a `.env` file in the project root with your credentials:

```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://localhost:8888/callback/
```

---

## ▶️ Run the script

```bash
python playlists.py
```

---

## 🚀 Usage

When prompted, enter the artist name:

```
Enter artist name: Radiohead
```

If multiple matches are found, you will see a numbered list:

```
Found 3 artists:
1. Radiohead
2. Radioslave
3. Radio Company

Select artist number (1-3):
```

Select the correct artist number by entering it.

The script will:

- Fetch all albums by that artist (pagination supported)
- Sort them by release date
- Collect all tracks from each album (parallelized fetches)
- Create a new playlist in your account with the format:

```
[Artist Name] discography
```

- Add all tracks in chronological order.

---

## 🧠 Logging

Logs are written to:

- Console (INFO level)  
- `spotify_discography.log` file

Log format includes: `timestamp - module - loglevel - message`

Example log messages:

```
2025-09-21 12:00:00 - __main__ - INFO - Spotify client initialized successfully
2025-09-21 12:00:01 - __main__ - INFO - Found 3 artists
2025-09-21 12:00:05 - __main__ - WARNING - Rate limited. Waiting 7 seconds
2025-09-21 12:00:20 - __main__ - INFO - Successfully created playlist with 145 tracks
```

---

## 📊 Technical Improvements vs. Basic Script

- **Scalability:** Handles large discographies with thousands of tracks.  
- **Performance:** Uses `ThreadPoolExecutor` to fetch albums and tracks in parallel.  
- **Resilience:** Implements retry logic with exponential backoff for API errors and rate limits.  
- **Memory Efficiency:** Uses generators for pagination (does not load everything into memory at once).  
- **Logging:** Professional logging via the `logging` module instead of `print`.  
- **User Experience:** Interactive prompts for selecting the correct artist when multiple matches appear.

---

## 🔒 Permissions Required

The script requires the following Spotify OAuth scopes:

- `playlist-modify-public`

Spotipy handles token refresh automatically after the initial authentication.

---

## ✅ Example Output

Console example after creating a playlist:

```
Found 1 artist:
1. Radiohead

Select artist number (1-1): 1
Album: Pablo Honey
Album: The Bends
Album: OK Computer
...
'Radiohead discography' playlist created successfully!
```

The playlist appears instantly in your Spotify account.

---

## ⚠️ Notes & Limitations

- Spotify API has a limit of 100 tracks per batch. The script handles this automatically.  
- Duplicate albums (regional versions, deluxe editions) are included if Spotify returns them.  
- Only **album** type releases are included (no singles, compilations, or EPs).  
- If Spotify rate-limits (HTTP 429), the script reads the `Retry-After` header (or uses exponential backoff) and retries.  
- The script assumes your account has permission to create public playlists. If you want private playlists, adjust the playlist creation call to create a private playlist.

---


## 👨‍💻 Author

Developed with ❤️ for automation and music lovers.

---

## 📌 Quick Checklist Before Running

- [ ] Python 3.8+ installed  
- [ ] `spotipy` and `python-dotenv` installed (`pip install spotipy python-dotenv`)  
- [ ] Spotify developer app created and `.env` configured with `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, and `SPOTIPY_REDIRECT_URI`  
- [ ] `playlists.py` (the script) placed in the project root

---

## 🛠️ Tip

If you plan to run this for very large discographies frequently, consider:

- Caching album/track metadata locally to avoid repeated API calls.
- Using exponential backoff settings tuned to your usage patterns.
- Splitting playlist creation into smaller batches and verifying after each batch.

---

## 🐛 Troubleshooting

### Common Issues

**Authentication Error:**
- Ensure your `.env` file contains valid credentials
- Check that the redirect URI matches exactly what you set in the Spotify Developer Dashboard
- Delete any `.cache` files and re-authenticate

**Rate Limiting (HTTP 429):**
- The script automatically handles rate limits with exponential backoff
- If you encounter persistent rate limits, reduce the `max_workers` in the ThreadPoolExecutor (line 230)
- Consider adding delays between operations

**No Albums Found:**
- Verify the artist has albums (not just singles/EPs) on Spotify
- The script only includes albums of type 'album' (excludes singles, compilations, and EPs)
- Try searching with the exact artist name as it appears on Spotify

**Tracks Not Added:**
- Check that your Spotify account has permission to create playlists
- Ensure you're not hitting Spotify's rate limits
- Check the log file (`spotify_discography.log`) for detailed error messages

**Import Errors:**
- Run `pip install spotipy python-dotenv` to install dependencies
- Ensure you're using Python 3.8 or higher

---

## 💡 Performance Optimization

The script has been optimized for performance with:

- **Parallel Processing:** Uses ThreadPoolExecutor with 5 workers to fetch album tracks concurrently
- **Efficient Ordering:** Maintains chronological order while processing albums in parallel
- **Memory Efficiency:** Uses generators for pagination to avoid loading all data into memory
- **Batch Processing:** Adds tracks to playlists in batches of 100 (Spotify's limit)
- **Retry Logic:** Implements exponential backoff to handle transient failures

For large discographies (1000+ tracks), the script typically completes in 2-5 minutes.


Happy listening! 🎧

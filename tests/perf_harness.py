"""Measurement harness: build_playlist against a fake Spotify with fixed latency.

Makes no network calls. Not collected by pytest (the name does not match test_*).

    python -m tests.perf_harness

Reports, per scenario: wall time, Spotify calls by endpoint, peak concurrent
requests, tracemalloc peak, and a fingerprint of the resulting track list so a
before/after comparison can prove the output did not change.
"""

import cProfile
import hashlib
import io
import json
import logging
import pstats
import random
import statistics
import threading
import time
import tracemalloc
from collections import Counter
from types import SimpleNamespace

from spotipy.exceptions import SpotifyException

from src.domain import filters
from src.services import retry
from src.services.discography import DiscographyService
from src.services.spotify_client import ALBUM_BATCH_SIZE, SpotifyClient

LATENCY_SECONDS = 0.1
PAGE_SIZE = 50
# Real track/album objects carry one ISO code per market Spotify serves.
MARKETS = [f"M{i:03d}" for i in range(180)]
WORDS = "night river stone glass echo summer ghost wire ocean paper silver hollow north ember".split()
SUFFIXES = ["", "", "", "", "", "", " - Live", " (Remix)", " (Instrumental)", " - Demo", " (Remastered 2011)"]

_real_sleep = time.sleep
_logger = logging.getLogger("perf")
_logger.setLevel(logging.WARNING)


def make_releases(lps, eps, singles, seed=42):
    rng = random.Random(seed)
    titles = [f"{a} {b}" for a in WORDS for b in WORDS if a != b]
    releases = []

    def add(kind_id, album_type, track_count, year):
        album_id = f"{kind_id}{len(releases):04d}"
        album = {
            "id": album_id,
            "name": f"{rng.choice(titles).title()} {album_id}",
            "album_type": album_type,
            "total_tracks": track_count,
            "release_date": f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "available_markets": MARKETS,
        }
        tracks = [
            {
                "name": rng.choice(titles).title() + rng.choice(SUFFIXES),
                "uri": f"spotify:track:{album_id}-{n}",
                "duration_ms": rng.randint(120_000, 360_000),
                "disc_number": 1,
                "track_number": n,
                "available_markets": MARKETS,
                "artists": [{"id": "artist", "name": "Artist", "uri": "spotify:artist:artist"}],
            }
            for n in range(1, track_count + 1)
        ]
        releases.append((album, tracks))

    for i in range(lps):
        # Every tenth LP is a deluxe edition that needs a second page of tracks.
        add("lp", "album", 60 if i % 10 == 9 else 14, 1990 + i)
    for i in range(eps):
        add("ep", "single", 5, 1990 + i)
    for i in range(singles):
        add("sg", "single", rng.randint(1, 2), 1990 + i % 35)
    return releases


class FakeSpotify:
    """Stands in for spotipy.Spotify; every call costs LATENCY_SECONDS."""

    def __init__(self, releases, fail_album_page_offset=None):
        self.catalog = [album for album, _ in releases]
        self.tracks = {album["id"]: tracks for album, tracks in releases}
        self.fail_album_page_offset = fail_album_page_offset
        self.calls = Counter()
        self.in_flight = 0
        self.peak_in_flight = 0
        self._lock = threading.Lock()

    def _call(self, name):
        with self._lock:
            self.calls[name] += 1
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        _real_sleep(LATENCY_SECONDS)
        with self._lock:
            self.in_flight -= 1

    @staticmethod
    def _raw_page(kind, key, items, offset):
        end = offset + PAGE_SIZE
        return {"items": items[offset:end], "next": f"{kind}|{key}|{end}" if end < len(items) else None}

    def _page(self, kind, key, items, offset):
        return _as_response_body(self._raw_page(kind, key, items, offset))

    def artist_albums(self, artist_id, include_groups="", limit=PAGE_SIZE):
        self._call("artist_albums")
        return self._page("albums", include_groups, self._albums_in(include_groups), 0)

    def albums(self, album_ids):
        self._call("albums")
        by_id = {album["id"]: album for album in self.catalog}
        return _as_response_body(
            {
                "albums": [
                    {**by_id[album_id], "tracks": self._raw_page("tracks", album_id, self.tracks[album_id], 0)}
                    for album_id in album_ids
                ]
            }
        )

    def next(self, result):
        kind, key, offset = result["next"].split("|")
        self._call(f"next:{kind}")
        if kind == "albums":
            if int(offset) == self.fail_album_page_offset:
                raise SpotifyException(500, -1, "injected failure")
            return self._page(kind, key, self._albums_in(key), int(offset))
        return self._page(kind, key, self.tracks[key], int(offset))

    def _albums_in(self, include_groups):
        groups = set(include_groups.split(","))
        return [a for a in self.catalog if a["album_type"] in groups]

    def current_user(self):
        self._call("current_user")
        return {"id": "me"}

    def user_playlist_create(self, user, name, public, description):
        self._call("user_playlist_create")
        return {"id": "playlist"}

    def playlist_add_items(self, playlist_id, items):
        self._call("playlist_add_items")


def _as_response_body(payload):
    # Round-trip through JSON so each call allocates fresh objects, like a real response body.
    return json.loads(json.dumps(payload))


def build(fake, **options):
    service = DiscographyService(SpotifyClient(fake, _logger), _logger)
    return service.build_playlist("artist", "Artist", 0, **options)


def fingerprint(releases):
    service = DiscographyService(SpotifyClient(FakeSpotify(releases), _logger), _logger)
    albums, _ = service.get_filtered_albums("artist", 0)
    uris, total_ms = service.collect_tracks(albums)
    return hashlib.sha256("\n".join(uris).encode()).hexdigest()[:16], len(uris), total_ms


def measure(name, releases):
    walls = []
    for _ in range(3):
        fake = FakeSpotify(releases)
        started = time.perf_counter()
        build(fake)
        walls.append(time.perf_counter() - started)

    tracemalloc.start()
    build(FakeSpotify(releases))
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    service = DiscographyService(SpotifyClient(FakeSpotify(releases), _logger), _logger)
    album_ids = [album["id"] for album, _ in releases]
    tracks_by_album = {}
    for start in range(0, len(album_ids), ALBUM_BATCH_SIZE):
        tracks_by_album.update(service.client.get_tracks_by_album(album_ids[start : start + ALBUM_BATCH_SIZE]))
    fetched = [(album, tracks_by_album[album["id"]]) for album, _ in releases]
    select_times = []
    for _ in range(5):
        started = time.perf_counter()
        filters.select_tracks(fetched)
        select_times.append(time.perf_counter() - started)

    digest, track_count, total_ms = fingerprint(releases)
    calls = dict(sorted(fake.calls.items()))
    print(f"\n== {name}: {len(releases)} releases, {sum(len(t) for _, t in releases)} tracks")
    print(f"wall median (3 runs): {statistics.median(walls):.2f}s   runs: {[round(w, 2) for w in walls]}")
    print(f"spotify calls: {sum(calls.values())}   {calls}")
    print(f"sequential floor at {LATENCY_SECONDS}s/call: {sum(calls.values()) * LATENCY_SECONDS:.1f}s")
    print(f"peak concurrent requests (one job): {fake.peak_in_flight}")
    print(f"tracemalloc peak: {peak_bytes / 1e6:.1f} MB")
    print(f"select_tracks alone, median (5 runs): {statistics.median(select_times) * 1000:.1f} ms")
    print(f"result: {track_count} tracks, {total_ms} ms, sha256[:16]={digest}")
    return fetched


def profile_select_tracks(fetched):
    profiler = cProfile.Profile()
    profiler.enable()
    filters.select_tracks(fetched)
    profiler.disable()
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("tottime").print_stats(8)
    print("\n== cProfile select_tracks (large), top 8 by own time")
    print("\n".join(line for line in out.getvalue().splitlines() if line.strip())[:2400])


def measure_concurrent_jobs(releases, jobs=4):
    fake = FakeSpotify(releases)
    threads = [threading.Thread(target=build, args=(fake,)) for _ in range(jobs)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"\n== {jobs} jobs at once (jobs.MAX_WORKERS) on one shared token")
    print(f"peak concurrent requests: {fake.peak_in_flight}   wall: {time.perf_counter() - started:.2f}s")


def measure_retry_amplification(releases):
    """One album page that always answers 500: how many requests and how much backoff follow."""
    slept = []
    real_time = retry.time
    retry.time = SimpleNamespace(sleep=slept.append)
    fake = FakeSpotify(releases, fail_album_page_offset=PAGE_SIZE)
    try:
        build(fake)
    except SpotifyException:
        pass
    finally:
        retry.time = real_time
    print("\n== artist_albums page 2 always fails with 500")
    print(f"spotify calls before giving up: {sum(fake.calls.values())}   {dict(fake.calls)}")
    print(f"backoff the job would have slept: {sum(slept):.0f}s over {len(slept)} sleeps")


def measure_search(concurrent_searches=3):
    """Uncached /api/search enrichment with MusicBrainz answering in LATENCY_SECONDS."""
    from src.services import musicbrainz

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"artists": []}

    def fake_get(*args, **kwargs):
        _real_sleep(LATENCY_SECONDS)
        return FakeResponse()

    real_session = musicbrainz._session
    musicbrainz._session = SimpleNamespace(get=fake_get)
    service = DiscographyService(SpotifyClient(FakeSpotify([]), _logger), _logger)
    durations = []

    def search(prefix):
        artists = [{"id": f"{prefix}{i}", "name": f"{prefix} artist {i}"} for i in range(50)]
        started = time.perf_counter()
        service.enrich_artists(artists)
        durations.append(time.perf_counter() - started)

    try:
        search("solo")
        solo = durations.pop()
        threads = [threading.Thread(target=search, args=(f"user{n}",)) for n in range(concurrent_searches)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        musicbrainz._session = real_session
    print("\n== /api/search enrichment, uncached names")
    print(f"one search: {solo:.2f}s")
    print(f"{concurrent_searches} searches at once, each took: {sorted(round(d, 2) for d in durations)}")


if __name__ == "__main__":
    measure_search()
    large = make_releases(lps=30, eps=30, singles=240)
    measure("small", make_releases(lps=5, eps=0, singles=5))
    fetched_large = measure("large", large)
    profile_select_tracks(fetched_large)
    measure_concurrent_jobs(large)
    measure_retry_amplification(large)

import logging

import pytest

from src.cli import runner
from src.services.discography import DiscographyService

ALBUM = {
    "id": "al1",
    "name": "The LP",
    "album_type": "album",
    "total_tracks": 10,
    "release_date": "2020-01-01",
}
TRACK = {"name": "Song", "uri": "uri:song", "duration_ms": 1000, "disc_number": 1, "track_number": 1}


class FakeSpotifyClient:
    def __init__(self, tracks=(TRACK,)):
        self.tracks = list(tracks)
        self.created = []
        self.added = []

    def search_artists(self, artist_name):
        return [{"id": "ar1", "name": "Radiohead", "followers": {"total": 1}, "genres": []}]

    def get_artist_albums(self, artist_id, album_types):
        return [ALBUM]

    def get_album_tracks(self, album_id):
        return self.tracks

    def create_playlist(self, playlist_name, description):
        self.created.append(playlist_name)
        return {"id": "pl1"}

    def add_tracks_to_playlist(self, playlist_id, track_uris):
        self.added.append((playlist_id, track_uris))


@pytest.fixture
def answers(monkeypatch):
    def feed(*lines):
        pending = iter(lines)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(pending))

    return feed


def run_cli(client, dry_run=False):
    service = DiscographyService(client=client, logger=logging.getLogger("tests"), dry_run=dry_run)
    runner.run(service)


class TestCreateFromCli:
    def test_creates_the_playlist_and_adds_the_tracks(self, answers, capsys):
        client = FakeSpotifyClient()
        answers("Radiohead", "1", "1")

        run_cli(client)

        assert client.created == ["Radiohead discography [ALBUMS]"]
        assert client.added == [("pl1", ["uri:song"])]
        assert "https://open.spotify.com/playlist/pl1" in capsys.readouterr().out

    def test_dry_run_creates_nothing(self, answers, capsys):
        client = FakeSpotifyClient()
        answers("Radiohead", "1", "1")

        run_cli(client, dry_run=True)

        assert client.created == []
        assert client.added == []
        assert "n/a (dry-run)" in capsys.readouterr().out

    def test_stops_when_every_track_is_filtered_out(self, answers, capsys):
        client = FakeSpotifyClient(tracks=[{**TRACK, "name": "Song (Live)"}])
        answers("Radiohead", "1", "1")

        run_cli(client)

        assert client.created == []
        assert "No tracks found" in capsys.readouterr().out

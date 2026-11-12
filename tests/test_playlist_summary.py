from src.cli import ui
from src.domain.album_types import playlist_name
from src.domain.models import RunSummary


def summary(playlist_id):
    return RunSummary(
        artist="Radiohead",
        playlist_name="Radiohead discography [ALBUMS]",
        playlist_id=playlist_id,
        albums_included=9,
        tracks_added=120,
        total_ms=0,
        dry_run=playlist_id is None,
    )


class TestPlaylistUrl:
    def test_url_points_at_the_created_playlist(self):
        assert summary("pl123").playlist_url == "https://open.spotify.com/playlist/pl123"

    def test_no_url_without_a_playlist(self):
        assert summary(None).playlist_url is None


class TestPlaylistName:
    def test_uses_the_selection_suffix(self):
        assert playlist_name("Radiohead", 1, {"album"}) == "Radiohead discography [ALBUMS]"

    def test_combined_selection_narrows_to_the_types_found(self):
        assert playlist_name("Radiohead", 5, {"ep"}) == "Radiohead discography [EPs]"


class TestPrintSummary:
    def test_shows_the_playlist_url(self, capsys):
        ui.print_summary(summary("pl123"))

        out = capsys.readouterr().out
        assert "Playlist URL    : https://open.spotify.com/playlist/pl123" in out
        assert "Tracks added    : 120" in out

    def test_dry_run_has_no_url(self, capsys):
        ui.print_summary(summary(None))

        assert "Playlist URL    : n/a (dry-run)" in capsys.readouterr().out

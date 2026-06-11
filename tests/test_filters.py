import pytest

from src.domain.filters import (
    deduplicate_tracks,
    has_demo_marker,
    has_instrumental_marker,
    has_live_marker,
    has_remix_marker,
    normalize_title,
    passes_content_filters,
)


class TestMarkers:
    @pytest.mark.parametrize(
        "text",
        ["Live at Wembley", "En Vivo en Buenos Aires", "Acoustic Live Session", "LIVE"],
    )
    def test_live_positive(self, text):
        assert has_live_marker(text)

    @pytest.mark.parametrize("text", ["Alive", "Deliver", "Sliver of Hope", ""])
    def test_live_negative(self, text):
        assert not has_live_marker(text)

    @pytest.mark.parametrize(
        "text",
        ["Demo", "Rough Mix 1982", "Work Tape", "Unreleased Demo"],
    )
    def test_demo_positive(self, text):
        assert has_demo_marker(text)

    @pytest.mark.parametrize("text", ["Demolition Man", "Democracy", ""])
    def test_demo_negative(self, text):
        assert not has_demo_marker(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Song (Remix)",
            "Song Rework",
            "Radio Edit",
            "Extended Mix",
            "Club Mix",
            "Dub Mix",
        ],
    )
    def test_remix_positive(self, text):
        assert has_remix_marker(text)

    @pytest.mark.parametrize("text", ["Editorial", "Remixer's Delight Mixtape", ""])
    def test_remix_negative(self, text):
        assert not has_remix_marker(text)

    def test_instrumental_positive(self):
        assert has_instrumental_marker("Song (Instrumental)")

    @pytest.mark.parametrize("text", ["Instrument", "Mental", ""])
    def test_instrumental_negative(self, text):
        assert not has_instrumental_marker(text)


class TestNormalizeTitle:
    def test_strips_brackets_and_parens(self):
        assert normalize_title("Song Title (2011 Remaster) [Deluxe]") == "song title"

    def test_strips_filter_keywords(self):
        assert normalize_title("Song Title - Live Remix") == "song title"

    def test_collapses_non_alnum(self):
        assert normalize_title("So-Called   Life!!!") == "so called life"

    def test_lowercases(self):
        assert normalize_title("HELLO World") == "hello world"

    def test_empty_when_only_markers(self):
        assert normalize_title("(Live)") == ""
        assert normalize_title("Instrumental") == ""


def _passes(track, album="Some Album", live=False, demos=False, remixes=False,
            instrumentals=False, bases=frozenset()):
    return passes_content_filters(
        track_name=track,
        album_name=album,
        include_live_versions=live,
        include_demos=demos,
        include_remixes=remixes,
        include_instrumentals=instrumentals,
        all_non_instrumental_bases=set(bases),
    )


class TestPassesContentFilters:
    def test_plain_track_passes(self):
        assert _passes("Plain Song")

    def test_live_track_name_rejected(self):
        assert not _passes("Song (Live)")

    def test_live_album_name_taints_track(self):
        assert not _passes("Plain Song", album="Concert (Live at Wembley)")

    def test_live_kept_when_included(self):
        assert _passes("Song (Live)", live=True)

    def test_demo_rejected_and_included(self):
        assert not _passes("Song (Demo)")
        assert _passes("Song (Demo)", demos=True)

    def test_remix_rejected_and_included(self):
        assert not _passes("Song (Remix)")
        assert _passes("Song (Remix)", remixes=True)

    def test_instrumental_rejected_by_default(self):
        assert not _passes("Song (Instrumental)")

    def test_instrumental_kept_with_counterpart(self):
        assert _passes("Song (Instrumental)", instrumentals=True, bases={"song"})

    def test_instrumental_rejected_without_counterpart(self):
        assert not _passes("Song (Instrumental)", instrumentals=True, bases={"other"})

    def test_instrumental_with_empty_base_kept(self):
        # "Instrumental" alone normalizes to "" — empty base skips the counterpart check.
        assert _passes("Instrumental", instrumentals=True, bases=set())

    def test_instrumental_album_taints_track(self):
        assert not _passes("Plain Song", album="Album (Instrumental)")


class TestDeduplicateTracks:
    def test_higher_priority_wins(self):
        candidates = [
            ("song", "uri:single", 1, 0),
            ("song", "uri:album", 3, 1),
        ]
        assert deduplicate_tracks(candidates) == ["uri:album"]

    def test_first_wins_on_equal_priority(self):
        candidates = [
            ("song", "uri:first", 3, 0),
            ("song", "uri:second", 3, 1),
        ]
        assert deduplicate_tracks(candidates) == ["uri:first"]

    def test_lower_priority_does_not_replace(self):
        candidates = [
            ("song", "uri:album", 3, 0),
            ("song", "uri:ep", 2, 1),
        ]
        assert deduplicate_tracks(candidates) == ["uri:album"]

    def test_output_sorted_by_appearance_order(self):
        candidates = [
            ("b", "uri:b", 1, 1),
            ("a", "uri:a", 1, 0),
            ("c", "uri:c", 1, 2),
        ]
        assert deduplicate_tracks(candidates) == ["uri:a", "uri:b", "uri:c"]

    def test_winner_keeps_its_own_appearance_order(self):
        # "song" first appears at order 0 (single) but the album version at order 2 wins,
        # so it sorts by 2 — after "other" at order 1.
        candidates = [
            ("song", "uri:single", 1, 0),
            ("other", "uri:other", 3, 1),
            ("song", "uri:album", 3, 2),
        ]
        assert deduplicate_tracks(candidates) == ["uri:other", "uri:album"]

    def test_empty_input(self):
        assert deduplicate_tracks([]) == []

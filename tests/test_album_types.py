import pytest

from src.domain.album_types import (
    album_release_priority,
    filter_albums,
    get_playlist_suffix,
    get_selection_title,
    get_types_for_selection,
    is_ep,
    is_single,
    matches_selection,
)


def album(album_type="album", total_tracks=10, **extra):
    return {"album_type": album_type, "total_tracks": total_tracks, **extra}


LP = album("album", 12)
EP = album("single", 5)
SINGLE = album("single", 2)
COMPILATION = album("compilation", 20)
LONG_SINGLE = album("single", 8)  # neither EP nor single by track count


class TestIsEp:
    @pytest.mark.parametrize("tracks,expected", [(3, False), (4, True), (7, True), (8, False)])
    def test_track_count_boundaries(self, tracks, expected):
        assert is_ep(album("single", tracks)) is expected

    def test_requires_single_type(self):
        assert not is_ep(album("album", 5))

    def test_missing_fields(self):
        assert not is_ep({})


class TestIsSingle:
    @pytest.mark.parametrize("tracks,expected", [(1, True), (3, True), (4, False)])
    def test_track_count_boundaries(self, tracks, expected):
        assert is_single(album("single", tracks)) is expected

    def test_requires_single_type(self):
        assert not is_single(album("album", 2))

    def test_missing_track_count_counts_as_zero(self):
        assert is_single({"album_type": "single"})


class TestMatchesSelection:
    def test_everything(self):
        for a in (LP, EP, SINGLE, COMPILATION, LONG_SINGLE):
            assert matches_selection(a, 0)

    def test_albums_only(self):
        assert matches_selection(LP, 1)
        assert not matches_selection(EP, 1)
        assert not matches_selection(COMPILATION, 1)

    def test_eps_only(self):
        assert matches_selection(EP, 2)
        assert not matches_selection(SINGLE, 2)
        assert not matches_selection(LP, 2)

    def test_singles_only(self):
        assert matches_selection(SINGLE, 3)
        assert not matches_selection(EP, 3)
        assert not matches_selection(LP, 3)

    def test_compilations_only(self):
        assert matches_selection(COMPILATION, 4)
        assert not matches_selection(LP, 4)

    def test_eps_and_singles(self):
        assert matches_selection(EP, 5)
        assert matches_selection(SINGLE, 5)
        assert not matches_selection(LP, 5)
        assert not matches_selection(LONG_SINGLE, 5)

    def test_albums_eps_singles(self):
        assert matches_selection(LP, 6)
        assert matches_selection(EP, 6)
        assert matches_selection(SINGLE, 6)
        assert not matches_selection(COMPILATION, 6)

    def test_unknown_selection_matches_all(self):
        assert matches_selection(COMPILATION, 99)


class TestFilterAlbums:
    def test_filters_and_collects_actual_types(self):
        filtered, actual = filter_albums([LP, EP, SINGLE, COMPILATION], 0)
        assert filtered == [LP, EP, SINGLE, COMPILATION]
        assert actual == {"album", "ep", "single", "compilation"}

    def test_selection_narrows_types(self):
        filtered, actual = filter_albums([LP, EP, SINGLE, COMPILATION], 5)
        assert filtered == [EP, SINGLE]
        assert actual == {"ep", "single"}

    def test_unclassifiable_single_adds_no_type(self):
        filtered, actual = filter_albums([LONG_SINGLE], 0)
        assert filtered == [LONG_SINGLE]
        assert actual == set()

    def test_empty_input(self):
        assert filter_albums([], 1) == ([], set())


class TestGetPlaylistSuffix:
    def test_no_actual_types_returns_config_suffix(self):
        assert get_playlist_suffix(1) == "[ALBUMS]"
        assert get_playlist_suffix(0) == "[EVERYTHING]"

    def test_unknown_selection_falls_back_to_everything(self):
        assert get_playlist_suffix(99) == "[EVERYTHING]"

    def test_selection_5_both(self):
        assert get_playlist_suffix(5, {"ep", "single"}) == "[EPs + SINGLES]"

    def test_selection_5_eps_only(self):
        assert get_playlist_suffix(5, {"ep"}) == "[EPs]"

    def test_selection_5_singles_only(self):
        assert get_playlist_suffix(5, {"single"}) == "[SINGLES]"

    def test_selection_5_empty_falls_back(self):
        assert get_playlist_suffix(5, set()) == "[EPs + SINGLES]"

    def test_selection_6_all(self):
        assert get_playlist_suffix(6, {"album", "ep", "single"}) == "[ALBUMS + EPs + SINGLES]"

    def test_selection_6_partial(self):
        assert get_playlist_suffix(6, {"album", "single"}) == "[ALBUMS + SINGLES]"
        assert get_playlist_suffix(6, {"ep"}) == "[EPs]"

    def test_selection_6_empty_falls_back(self):
        assert get_playlist_suffix(6, set()) == "[ALBUMS + EPs + SINGLES]"

    def test_other_selection_ignores_actual_types(self):
        assert get_playlist_suffix(1, {"ep", "single"}) == "[ALBUMS]"


class TestConfigAccessors:
    def test_types_for_selection(self):
        assert get_types_for_selection(1) == ["album"]
        assert get_types_for_selection(99) == ["album", "single", "compilation"]

    def test_selection_title(self):
        assert get_selection_title(3) == "Singles only"
        assert get_selection_title(99) == "Everything"


class TestAlbumReleasePriority:
    def test_priorities(self):
        assert album_release_priority(LP) == 3
        assert album_release_priority(EP) == 2
        assert album_release_priority(SINGLE) == 1
        assert album_release_priority(COMPILATION) == 0
        assert album_release_priority(LONG_SINGLE) == 0

import os

os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")
os.environ.setdefault("SPOTIFY_REFRESH_TOKEN", "test")

import pytest

from clients.spotify import SpotifyClient
from server import _interleave


TRACK_ID = "3n3Ppam7vgaVa1iaRUc9Lp"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


class TestInterleave:
    def test_empty(self):
        assert _interleave([]) == []

    def test_single_group(self):
        assert _interleave([["a", "b", "c"]]) == ["a", "b", "c"]

    def test_even_groups(self):
        assert _interleave([["a1", "a2"], ["b1", "b2"]]) == ["a1", "b1", "a2", "b2"]

    def test_uneven_groups(self):
        # Longer group's trailing items come out alone at the end.
        result = _interleave([["a1", "a2", "a3"], ["b1"]])
        assert result == ["a1", "b1", "a2", "a3"]

    def test_empty_inner_group_skipped(self):
        assert _interleave([["a1"], [], ["c1"]]) == ["a1", "c1"]

    def test_three_groups_round_robin(self):
        result = _interleave([["a1", "a2"], ["b1", "b2"], ["c1", "c2"]])
        assert result == ["a1", "b1", "c1", "a2", "b2", "c2"]


class TestParseTrackRef:
    def test_uri_passthrough(self):
        uri = f"spotify:track:{TRACK_ID}"
        assert SpotifyClient.parse_track_ref(uri) == uri

    def test_open_url(self):
        url = f"https://open.spotify.com/track/{TRACK_ID}"
        assert SpotifyClient.parse_track_ref(url) == f"spotify:track:{TRACK_ID}"

    def test_open_url_with_query(self):
        url = f"https://open.spotify.com/track/{TRACK_ID}?si=abc123"
        assert SpotifyClient.parse_track_ref(url) == f"spotify:track:{TRACK_ID}"

    def test_bare_id(self):
        assert SpotifyClient.parse_track_ref(TRACK_ID) == f"spotify:track:{TRACK_ID}"

    def test_strips_whitespace(self):
        assert (
            SpotifyClient.parse_track_ref(f"  {TRACK_ID}  ")
            == f"spotify:track:{TRACK_ID}"
        )

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            SpotifyClient.parse_track_ref("not a track")

    def test_rejects_wrong_length_id(self):
        with pytest.raises(ValueError):
            SpotifyClient.parse_track_ref("abc123")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            SpotifyClient.parse_track_ref("")


class TestParsePlaylistId:
    def test_uri(self):
        uri = f"spotify:playlist:{PLAYLIST_ID}"
        assert SpotifyClient.parse_playlist_id(uri) == PLAYLIST_ID

    def test_open_url(self):
        url = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
        assert SpotifyClient.parse_playlist_id(url) == PLAYLIST_ID

    def test_open_url_with_query(self):
        url = f"https://open.spotify.com/playlist/{PLAYLIST_ID}?si=xyz"
        assert SpotifyClient.parse_playlist_id(url) == PLAYLIST_ID

    def test_bare_id(self):
        assert SpotifyClient.parse_playlist_id(PLAYLIST_ID) == PLAYLIST_ID

    def test_returns_none_for_name(self):
        # Names are resolved by lookup, not by this parser.
        assert SpotifyClient.parse_playlist_id("My Favorites") is None

    def test_returns_none_for_empty(self):
        assert SpotifyClient.parse_playlist_id("") is None

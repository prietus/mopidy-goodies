from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import isrc
from mopidy_goodies.cache import LRU
from mopidy_goodies.isrc import NoTracks, album_rank, tracks_by_isrc


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(isrc, "_cache", LRU(8))


def _t(track_id, album_id, album, available=True):
    return NS(id=track_id, name="Bohemian Rhapsody", available=available,
              artist=NS(id=8992, name="Queen"), album=NS(id=album_id, name=album))


class _Session:
    def __init__(self, tracks):
        self.tracks = tracks
        self.calls = 0

    def get_tracks_by_isrc(self, code):
        self.calls += 1
        if not self.tracks:
            raise LookupError(code)
        return self.tracks


@pytest.mark.parametrize("name,rank", [
    ("A Night At The Opera", 0),
    ("A Night At The Opera (Deluxe Edition)", 1),
    ("Bohemian Rhapsody (The Original Soundtrack)", 2),
    ("The Platinum Collection", 3),
    ("Greatest Hits", 3),
    ("Anthems", 3),
])
def test_album_rank(name, rank):
    assert album_rank(name) == rank


def test_original_album_first_and_unavailable_dropped():
    session = _Session([
        _t(1, 10, "Bohemian Rhapsody (The Original Soundtrack)"),
        _t(2, 20, "The Platinum Collection"),
        _t(3, 30, "A Night At The Opera (Deluxe Edition)"),
        _t(4, 40, "A Night At The Opera"),
        _t(5, 50, "A Night At The Opera", available=False),
    ])
    result = tracks_by_isrc(session, "gbum71029604")
    assert result["isrc"] == "GBUM71029604"
    assert [t["album"] for t in result["tracks"]] == [
        "A Night At The Opera", "A Night At The Opera (Deluxe Edition)",
        "Bohemian Rhapsody (The Original Soundtrack)", "The Platinum Collection"]
    assert result["tracks"][0] == {
        "uri": "tidal:track:8992:40:4", "title": "Bohemian Rhapsody", "artist": "Queen",
        "artist_uri": "tidal:artist:8992", "album": "A Night At The Opera", "album_uri": "tidal:album:40"}
    tracks_by_isrc(session, "GBUM71029604")
    assert session.calls == 1  # cached


def test_no_tracks():
    with pytest.raises(NoTracks):
        tracks_by_isrc(_Session([]), "XXXX00000000")
    with pytest.raises(NoTracks):
        tracks_by_isrc(_Session([_t(1, 10, "A", available=False)]), "XXXX00000001")

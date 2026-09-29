from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import local, radio
from mopidy_goodies.cache import LRU
from mopidy_goodies.radio import NoSeed


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(radio, "_cache", LRU(8))
    monkeypatch.setattr("mopidy_goodies.isrc._cache", LRU(8))


def _t(tid, name, artist="Queen", artist_id=8992, album_id=40, album="A Night At The Opera", available=True):
    return NS(id=tid, name=name, available=available, artist=NS(id=artist_id, name=artist),
              album=NS(id=album_id, name=album))


class _Seed:
    def __init__(self, track=None, name=None):
        self.track = track
        self.name = name if name is not None else track.name
        self.artist = track.artist if track else None
        self.radio_limit = None

    def get_track_radio(self, limit):
        self.radio_limit = limit
        return [_t(1, "Stairway to Heaven", "Led Zeppelin", 1, 7, "Led Zeppelin IV"),
                _t(2, "Gone", available=False)]

    def get_radio(self, limit):
        self.radio_limit = limit
        return [_t(3, "Another One Bites The Dust", album_id=50, album="The Game")]


class _Session:
    def __init__(self):
        self.tracks = {"534050397": _Seed(_t(534050397, "Bohemian Rhapsody"))}
        self.artists = {"8992": _Seed(name="Queen")}
        self.searches = []

    def track(self, tid):
        return self.tracks[str(tid)]

    def artist(self, aid):
        return self.artists[str(aid)]

    def get_tracks_by_isrc(self, isrc):
        return [_t(534050397, "Bohemian Rhapsody")]

    def search(self, query, models=None, limit=None):
        self.searches.append(query)
        if "Queen" in query and "Rhapsody" in query:
            return {"tracks": [_t(9, "Bohemian Rhapsody - Remastered 2011")]}
        if query == "Queen":
            return {"artists": [NS(id=8992, name="Queen"), NS(id=1, name="Queen Latifah")]}
        return {"tracks": [], "artists": []}


class _Core:
    def __init__(self, tracks):
        self.library = self
        self._tracks = tracks

    def lookup(self, uris):
        return NS(get=lambda: {uris[0]: self._tracks})


def test_tidal_track_seed():
    s = _Session()
    r = radio.radio(s, None, {}, "tidal:track:8992:40:534050397", limit=50)
    assert r["seed"] == {"kind": "track", "title": "Bohemian Rhapsody", "artist": "Queen"}
    assert [t["uri"] for t in r["tracks"]] == ["tidal:track:1:7:1"]  # unavailable dropped
    assert s.tracks["534050397"].radio_limit == 50


def test_tidal_artist_seed():
    r = radio.radio(_Session(), None, {}, "tidal:artist:8992")
    assert r["seed"] == {"kind": "artist", "title": None, "artist": "Queen"}
    assert r["tracks"][0]["album"] == "The Game"


def test_local_track_by_isrc(monkeypatch):
    monkeypatch.setattr(local, "available", lambda config: True)
    monkeypatch.setattr(local, "read_tags", lambda path: {"isrc": "GBUM71029604", "credits": []})
    s = _Session()
    r = radio.radio(s, None, {"local": {"media_dir": "/tmp"}}, "local:track:rips/q/01.flac")
    assert r["seed"]["title"] == "Bohemian Rhapsody" and not s.searches


def test_local_track_by_search_without_isrc(monkeypatch):
    monkeypatch.setattr(local, "available", lambda config: False)
    s = _Session()
    s.tracks["9"] = _Seed(_t(9, "Bohemian Rhapsody - Remastered 2011"))
    core = _Core([NS(name="Bohemian Rhapsody", artists=[NS(name="Queen")])])
    r = radio.radio(s, core, {}, "local:track:rips/q/01.flac")
    assert r["seed"]["title"] == "Bohemian Rhapsody - Remastered 2011"


def test_local_artist_by_name():
    tracks = [NS(name="x", artists=[NS(name="Queen")]), NS(name="y", artists=[NS(name="Queen"), NS(name="David Bowie")])]
    r = radio.radio(_Session(), _Core(tracks), {}, "local:artist:md5:abc")
    assert r["seed"]["artist"] == "Queen"


def test_unknown_seeds():
    with pytest.raises(NoSeed):
        radio.radio(_Session(), None, {}, "file:///x.flac")
    with pytest.raises(NoSeed):
        radio.radio(_Session(), _Core([NS(name="x", artists=[NS(name="Nobody Known")])]), {}, "local:artist:md5:z")

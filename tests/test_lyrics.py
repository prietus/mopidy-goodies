from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import lyrics as mod
from mopidy_goodies.cache import LRU
from mopidy_goodies.lyrics import LyricsNotFound, lyrics
from mopidy_goodies.radio import NoSeed


class _Requests:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, path, **_):
        from tidalapi.exceptions import ObjectNotFound

        self.calls.append(path)
        if path not in self.routes:
            raise ObjectNotFound(path)
        return NS(json=lambda: self.routes[path])


class _Session:
    def __init__(self, routes):
        self.request = _Requests(routes)


FULL = {
    "trackId": 8992,
    "lyricsProvider": "MUSIXMATCH",
    "lyrics": "Is this the real life?\nIs this just fantasy?\n",
    "subtitles": "[00:00.50] Is this the real life?\n[00:04.10] Is this just fantasy?",
    "isRightToLeft": False,
}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(mod, "_cache", LRU(8))


def test_tidal_track_uri_uses_the_id_at_the_end_of_the_uri():
    session = _Session({"tracks/534050397/lyrics": FULL})
    out = lyrics(session, None, {}, "tidal:track:8992:534050382:534050397")
    assert out == {
        "track_id": "534050397",
        "text": "Is this the real life?\nIs this just fantasy?",
        "subtitles": "[00:00.50] Is this the real life?\n[00:04.10] Is this just fantasy?",
        "provider": "MUSIXMATCH",
        "right_to_left": False,
    }


def test_plain_only_lyrics_have_null_subtitles():
    session = _Session({"tracks/1/lyrics": {**FULL, "subtitles": None}})
    out = lyrics(session, None, {}, "tidal:track:9:9:1")
    assert out["subtitles"] is None and out["text"].startswith("Is this")


def test_missing_or_empty_lyrics_raise_and_the_miss_is_cached():
    session = _Session({"tracks/2/lyrics": {**FULL, "lyrics": "", "subtitles": " "}})
    for uri in ("tidal:track:9:9:404", "tidal:track:9:9:2"):
        with pytest.raises(LyricsNotFound):
            lyrics(session, None, {}, uri)
        calls = len(session.request.calls)
        with pytest.raises(LyricsNotFound):
            lyrics(session, None, {}, uri)
        assert len(session.request.calls) == calls, "second miss must not hit Tidal"


def test_hits_are_cached():
    session = _Session({"tracks/1/lyrics": FULL})
    first = lyrics(session, None, {}, "tidal:track:9:9:1")
    assert lyrics(session, None, {}, "tidal:track:9:9:1") is first
    assert session.request.calls == ["tracks/1/lyrics"]


def test_local_track_is_matched_to_tidal_first(monkeypatch):
    seen = {}

    def fake_tidal_track(session, core, config, uri):
        seen["uri"] = uri
        return NS(id=777)

    monkeypatch.setattr(mod, "tidal_track", fake_tidal_track)
    session = _Session({"tracks/777/lyrics": FULL})
    out = lyrics(session, "core", {"local": {}}, "local:track:a/b.flac")
    assert seen["uri"] == "local:track:a/b.flac" and out["track_id"] == "777"


def test_local_track_not_on_tidal_is_not_found(monkeypatch):
    def no_match(*_):
        raise NoSeed("x")

    monkeypatch.setattr(mod, "tidal_track", no_match)
    with pytest.raises(LyricsNotFound):
        lyrics(_Session({}), "core", {}, "local:track:a/b.flac")

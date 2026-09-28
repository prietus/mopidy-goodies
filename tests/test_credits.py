import pytest

from mopidy_goodies import credits
from mopidy_goodies.credits import AlbumNotFound, album_credits


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Request:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def request(self, method, path, params=None, **_):
        self.calls.append((method, path, dict(params or {})))
        return _Resp(self.pages[params["offset"]])


class _Session:
    def __init__(self, pages):
        self.request = _Request(pages)


def _entry(n, kind="track", credits_=None):
    return {
        "type": kind,
        "item": {"id": 100 + n, "title": f"T{n}", "trackNumber": n, "volumeNumber": 1, "version": None},
        "credits": credits_ or [],
    }


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(credits, "_cache", credits._LRU(8))


def test_maps_tracks_and_credits():
    session = _Session({0: {"totalNumberOfItems": 2, "items": [
        _entry(1, credits_=[
            {"type": "Producer", "contributors": [{"name": "Richard Bock", "id": 8021563}]},
            {"type": "Trumpet", "contributors": [{"name": "Chet Baker", "id": None}, {"name": ""}]},
            {"contributors": [{"name": "no role"}]},
        ]),
        _entry(2, kind="video"),
    ]}})
    result = album_credits(session, 158152)
    assert result["album_id"] == "158152"
    assert result["tracks"] == [{
        "id": "101", "title": "T1", "version": None, "track_num": 1, "volume_num": 1,
        "credits": [
            {"role": "Producer", "contributors": [{"name": "Richard Bock", "id": "8021563"}]},
            {"role": "Trumpet", "contributors": [{"name": "Chet Baker", "id": None}]},
        ],
    }]
    method, path, params = session.request.calls[0]
    assert (method, path) == ("GET", "albums/158152/items/credits")
    assert params["includeContributors"] == "true"


def test_pages_until_total():
    session = _Session({
        0: {"totalNumberOfItems": 3, "items": [_entry(1), _entry(2)]},
        2: {"totalNumberOfItems": 3, "items": [_entry(3)]},
    })
    result = album_credits(session, 1)
    assert [t["track_num"] for t in result["tracks"]] == [1, 2, 3]
    assert [c[2]["offset"] for c in session.request.calls] == [0, 2]


def test_caches_by_album():
    session = _Session({0: {"totalNumberOfItems": 1, "items": [_entry(1)]}})
    first = album_credits(session, 7)
    assert album_credits(session, "7") is first
    assert len(session.request.calls) == 1


def test_not_found():
    from tidalapi.exceptions import ObjectNotFound

    class _Missing:
        def request(self, *a, **k):
            raise ObjectNotFound("nope")

    session = _Session({})
    session.request = _Missing()
    with pytest.raises(AlbumNotFound):
        album_credits(session, 1)

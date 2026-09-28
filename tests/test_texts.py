import pytest

from mopidy_goodies import texts
from mopidy_goodies.cache import LRU
from mopidy_goodies.texts import TextNotFound, album_review, artist_bio, clean


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Requests:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, path, **_):
        from tidalapi.exceptions import ObjectNotFound

        self.calls.append(path)
        if path not in self.routes:
            raise ObjectNotFound(path)
        return _Resp(self.routes[path])


class _Session:
    def __init__(self, routes):
        self.request = _Requests(routes)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(texts, "_cache", LRU(8))


def test_clean_strips_tidal_markup():
    raw = 'The [wimpLink albumId="1"]Album[/wimpLink] by [wimpLink artistId="2"]Baker[/wimpLink].<br/><br/><br/>Next <i>para</i>.'
    assert clean(raw) == "The Album by Baker.\n\nNext para."


def test_album_review():
    session = _Session({"albums/5/review": {"text": "Great [wimpLink artistId=\"2\"]Chet[/wimpLink].", "source": "TiVo", "lastUpdated": "2026-01-01"}})
    assert album_review(session, 5) == {"album_id": "5", "text": "Great Chet.", "source": "TiVo", "last_updated": "2026-01-01"}
    album_review(session, "5")
    assert session.request.calls == ["albums/5/review"]  # cached


def test_album_review_missing():
    with pytest.raises(TextNotFound):
        album_review(_Session({}), 5)


def test_album_review_empty_text_is_missing():
    with pytest.raises(TextNotFound):
        album_review(_Session({"albums/5/review": {"text": "  ", "source": "TiVo"}}), 5)


def test_artist_bio_with_picture():
    session = _Session({
        "artists/1301": {"name": "Chet Baker", "picture": "7f74-69dd"},
        "artists/1301/bio": {"text": "Cool jazz.", "source": "TiVo", "lastUpdated": "x"},
    })
    assert artist_bio(session, 1301) == {
        "artist_id": "1301", "name": "Chet Baker",
        "image": "https://resources.tidal.com/images/7f74/69dd/750x750.jpg",
        "text": "Cool jazz.", "source": "TiVo", "last_updated": "x",
    }


def test_artist_bio_without_bio_or_picture():
    session = _Session({"artists/9": {"name": "Nobody", "picture": None}})
    bio = artist_bio(session, 9)
    assert (bio["name"], bio["image"], bio["text"]) == ("Nobody", None, None)


def test_unknown_artist():
    with pytest.raises(TextNotFound):
        artist_bio(_Session({}), 9)

"""Per-track credits (producers, composers, musicians + instruments) for Tidal albums.

tidalapi has no wrapper for credits, so we call the same endpoint the Tidal
apps use through the session's raw request helper. Credits don't change, so
responses are kept in a small in-memory LRU.
"""
import collections
import threading

_PAGE = 100
_CACHE_SIZE = 256


class AlbumNotFound(LookupError):
    pass


class _LRU:
    def __init__(self, size):
        self._size = size
        self._data = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key, value):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)


_cache = _LRU(_CACHE_SIZE)


def album_credits(session, album_id):
    """``{"album_id", "tracks": [{"id", "title", "version", "track_num", "volume_num",
    "credits": [{"role", "contributors": [{"name", "id"}]}]}]}``.

    Raises AlbumNotFound if Tidal doesn't know the album.
    """
    album_id = str(album_id)
    if (cached := _cache.get(album_id)) is not None:
        return cached

    items, offset = [], 0
    while True:
        page = _fetch_page(session, album_id, offset)
        batch = page.get("items") or []
        items += batch
        offset += len(batch)
        if not batch or offset >= page.get("totalNumberOfItems", 0):
            break

    result = {
        "album_id": album_id,
        "tracks": [_track(it) for it in items if it.get("type") == "track"],
    }
    _cache.put(album_id, result)
    return result


def _fetch_page(session, album_id, offset):
    try:
        from tidalapi.exceptions import ObjectNotFound
    except ImportError:  # pragma: no cover - older tidalapi
        ObjectNotFound = ()
    try:
        resp = session.request.request(
            "GET",
            f"albums/{album_id}/items/credits",
            params={
                "limit": _PAGE,
                "offset": offset,
                "replace": "true",
                "includeContributors": "true",
            },
        )
    except ObjectNotFound as e:
        raise AlbumNotFound(album_id) from e
    return resp.json()


def _track(entry):
    item = entry.get("item") or {}
    return {
        "id": str(item.get("id", "")),
        "title": item.get("title"),
        "version": item.get("version"),
        "track_num": item.get("trackNumber"),
        "volume_num": item.get("volumeNumber"),
        "credits": [
            {
                "role": c.get("type"),
                "contributors": [
                    {"name": p.get("name"), "id": _str_or_none(p.get("id"))}
                    for p in c.get("contributors") or []
                    if p.get("name")
                ],
            }
            for c in entry.get("credits") or []
            if c.get("type")
        ],
    }


def _str_or_none(v):
    return None if v is None else str(v)

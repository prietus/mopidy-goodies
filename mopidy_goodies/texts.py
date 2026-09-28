"""Editorial texts from Tidal: album reviews and artist biographies.

Both come from Tidal's own endpoints (the text is licensed from e.g. TiVo, so
we pass ``source`` through for attribution). The text embeds Tidal-internal
links (``[wimpLink artistId="1301"]Baker[/wimpLink]``) and ``<br/>`` breaks;
we return plain text. Cached in memory — these change rarely.
"""
import re

from .cache import LRU

_LINK = re.compile(r"\[wimpLink[^\]]*\](.*?)\[/wimpLink\]", re.S)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_IMAGE = "https://resources.tidal.com/images/{}/750x750.jpg"

_cache = LRU(512)


class TextNotFound(LookupError):
    """Tidal has no review/bio for this id (or doesn't know the id)."""


def album_review(session, album_id):
    """``{"album_id", "text", "source", "last_updated"}``."""
    album_id = str(album_id)
    return _cached(("review", album_id), lambda: {
        "album_id": album_id,
        **_text(_get(session, f"albums/{album_id}/review")),
    })


def artist_bio(session, artist_id):
    """``{"artist_id", "name", "image", "text", "source", "last_updated"}``.

    ``text`` may be null when the artist has a picture but no bio.
    """
    artist_id = str(artist_id)

    def load():
        artist = _get(session, f"artists/{artist_id}")
        try:
            bio = _text(_get(session, f"artists/{artist_id}/bio"))
        except TextNotFound:
            bio = {"text": None, "source": None, "last_updated": None}
        picture = artist.get("picture")
        return {
            "artist_id": artist_id,
            "name": artist.get("name"),
            "image": _IMAGE.format(picture.replace("-", "/")) if picture else None,
            **bio,
        }

    return _cached(("bio", artist_id), load)


def clean(text):
    """Tidal markup → plain text (links keep their label, <br/> → newline)."""
    text = _LINK.sub(r"\1", text or "")
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _text(payload):
    text = clean(payload.get("text"))
    if not text:
        raise TextNotFound()
    return {
        "text": text,
        "source": payload.get("source") or None,
        "last_updated": payload.get("lastUpdated"),
    }


def _cached(key, load):
    if (hit := _cache.get(key)) is not None:
        return hit
    value = load()
    _cache.put(key, value)
    return value


def _get(session, path):
    try:
        from tidalapi.exceptions import ObjectNotFound
    except ImportError:  # pragma: no cover - older tidalapi
        ObjectNotFound = ()
    try:
        return session.request.request("GET", path).json()
    except ObjectNotFound as e:
        raise TextNotFound(path) from e

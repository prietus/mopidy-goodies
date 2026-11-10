"""Tidal's own lyrics for a track.

Tidal licenses lyrics (the provider is passed through for attribution) and
serves them per track: the plain text and, for most tracks, ``subtitles`` — the
same lines with ``[mm:ss.xx]`` timestamps (LRC, line by line; Tidal has no
word-by-word timing here). tidalapi exposes it as ``Track.lyrics()``;
mopidy-tidal doesn't use it.

The track is given as a Mopidy URI: ``tidal:track:…`` directly, ``local:track:…``
matched to the same recording on Tidal as for radio (ISRC tag, else artist +
title). Cached in memory, misses included — lyrics don't change and clients ask
again on every play.
"""
from .cache import LRU
from .radio import NoSeed, tidal_track

_cache = LRU(512)
_MISS = object()


class LyricsNotFound(LookupError):
    """Tidal has no lyrics for this track (or the track isn't on Tidal)."""


def lyrics(session, core, config, uri):
    """``{"track_id", "text", "subtitles", "provider", "right_to_left"}``.

    ``subtitles`` is null when Tidal only has the plain text. Raises
    LyricsNotFound when it has neither.
    """
    hit = _cache.get(uri)
    if hit is _MISS:
        raise LyricsNotFound(uri)
    if hit is not None:
        return hit
    try:
        result = _load(session, core, config, uri)
    except LyricsNotFound:
        _cache.put(uri, _MISS)
        raise
    _cache.put(uri, result)
    return result


def _load(session, core, config, uri):
    if uri.startswith("tidal:track:"):
        track_id = uri.split(":")[-1]
    else:
        try:
            track_id = tidal_track(session, core, config, uri).id
        except NoSeed as e:
            raise LyricsNotFound(uri) from e
    payload = _get(session, f"tracks/{track_id}/lyrics")
    text = (payload.get("lyrics") or "").strip()
    subtitles = (payload.get("subtitles") or "").strip()
    if not text and not subtitles:
        raise LyricsNotFound(uri)
    return {
        "track_id": str(track_id),
        "text": text or None,
        "subtitles": subtitles or None,
        "provider": payload.get("lyricsProvider") or None,
        "right_to_left": bool(payload.get("isRightToLeft")),
    }


def _get(session, path):
    try:
        from tidalapi.exceptions import ObjectNotFound
    except ImportError:  # pragma: no cover - older tidalapi
        ObjectNotFound = ()
    try:
        return session.request.request("GET", path).json()
    except ObjectNotFound as e:
        raise LyricsNotFound(path) from e

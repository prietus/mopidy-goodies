"""Tidal radio seeded from any Mopidy track or artist URI.

Tidal can build a "radio" of similar tracks from a track or an artist; tidalapi
exposes both but mopidy-tidal doesn't. Seeds:

- ``tidal:track:…`` / ``tidal:artist:<id>`` — used directly.
- ``local:track:…`` — its ISRC tag finds the same recording on Tidal; without
  one, a Tidal search by artist + title.
- ``local:artist:…`` — the artist's name, searched on Tidal.
"""
import difflib
import re

from . import local
from .cache import LRU
from .isrc import NoTracks, tracks_by_isrc

_cache = LRU(128)


class NoSeed(LookupError):
    """The URI can't be matched to anything on Tidal."""


def radio(session, core, config, uri, limit=100):
    """``{"seed": {"kind", "title", "artist"}, "tracks": [{"uri", "title", "artist", "album", "album_uri"}]}``."""
    key = (uri, limit)
    if (hit := _cache.get(key)) is not None:
        return hit
    kind, seed = _resolve(session, core, config, uri)
    found = seed.get_track_radio(limit=limit) if kind == "track" else seed.get_radio(limit=limit)
    result = {
        "seed": {
            "kind": kind,
            "title": seed.name if kind == "track" else None,
            "artist": (seed.artist.name if seed.artist else None) if kind == "track" else seed.name,
        },
        "tracks": [_track(t) for t in found if getattr(t, "available", True) and t.album is not None],
    }
    _cache.put(key, result)
    return result


def _resolve(session, core, config, uri):
    parts = uri.split(":")
    if uri.startswith("tidal:track:"):
        return "track", session.track(parts[-1])
    if uri.startswith("tidal:artist:"):
        return "artist", session.artist(parts[-1])
    if uri.startswith("local:track:"):
        return "track", _local_track(session, core, config, uri)
    if uri.startswith("local:artist:"):
        return "artist", _local_artist(session, core, uri)
    raise NoSeed(uri)


def _local_track(session, core, config, uri):
    if local.available(config):
        try:
            isrc = local.read_tags(local.track_path(config["local"]["media_dir"], uri)).get("isrc")
        except Exception:
            isrc = None
        if isrc:
            try:
                first = tracks_by_isrc(session, isrc)["tracks"][0]
                return session.track(first["uri"].split(":")[-1])
            except (NoTracks, IndexError):
                pass
    track = _lookup_one(core, uri)
    artist = ", ".join(a.name for a in (track.artists or []) if a.name)
    return _search_track(session, artist, track.name or "")


def _local_artist(session, core, uri):
    import tidalapi

    tracks = core.library.lookup(uris=[uri]).get().get(uri) or []
    names = [a.name for t in tracks for a in (t.artists or []) if a.name]
    if not names:
        raise NoSeed(uri)
    name = max(set(names), key=names.count)  # the artist, not a guest on one track
    found = session.search(name, models=[tidalapi.Artist], limit=5).get("artists", [])
    best = next((a for a in found if _norm(a.name) == _norm(name)), None)
    if best is None:
        raise NoSeed(uri)
    return session.artist(best.id)  # search results can be partial objects


def _lookup_one(core, uri):
    tracks = core.library.lookup(uris=[uri]).get().get(uri) or []
    if not tracks:
        raise NoSeed(uri)
    return tracks[0]


def _search_track(session, artist, title):
    import tidalapi

    found = session.search(f"{artist} {title}", models=[tidalapi.Track], limit=10).get("tracks", [])
    want_title, want_artist = _norm(_clean(title)), _norm(artist)
    for t in found:
        t_artist = _norm(t.artist.name if t.artist else "")
        if (difflib.SequenceMatcher(None, _norm(_clean(t.name)), want_title).ratio() >= 0.85
                and (want_artist in t_artist or t_artist in want_artist)):
            return session.track(t.id)  # search results can be partial objects
    raise NoSeed(f"{artist} — {title}")


def _clean(title):
    return re.sub(r"\s*[\(\[].*$|\s+-\s+.*$", "", title or "")


def _norm(s):
    return re.sub(r"[\W_]+", " ", (s or "").lower()).strip()


def _track(t):
    artist_id = t.artist.id if t.artist is not None else 0
    return {
        "uri": f"tidal:track:{artist_id}:{t.album.id}:{t.id}",
        "title": t.name,
        "artist": t.artist.name if t.artist is not None else None,
        "album": t.album.name,
        "album_uri": f"tidal:album:{t.album.id}",
    }

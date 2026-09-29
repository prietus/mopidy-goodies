"""Find a recording on Tidal by ISRC — e.g. a song identified by Shazam — and
pick the most useful album for it.

The same ISRC appears on the original album, deluxe reissues, compilations
and soundtracks. Tidal's ISRC lookup doesn't say which is which (album type
and release date come back empty), so we rank by name: plain albums first,
then editions ("Deluxe", "Remaster"…), then soundtracks and compilations.
Tidal's own order breaks ties.
"""
import re

from .cache import LRU

_COMPILATION = re.compile(
    r"\b(greatest|hits|best of|collection|anthems?|essentials?|gold|platinum|"
    r"anthology|singles|epic|ultimate|definitive)\b", re.I)
_SOUNDTRACK = re.compile(r"soundtrack|o\.s\.t\.?|motion picture", re.I)
_EDITION = re.compile(r"deluxe|remaster|edition|expanded|anniversary|bonus|live", re.I)

_cache = LRU(512)


class NoTracks(LookupError):
    """Tidal has no available track with this ISRC."""


def album_rank(name):
    """Lower is better: 0 plain album, 1 edition, 2 soundtrack, 3 compilation."""
    name = name or ""
    if _COMPILATION.search(name):
        return 3
    if _SOUNDTRACK.search(name):
        return 2
    if _EDITION.search(name):
        return 1
    return 0


def tracks_by_isrc(session, isrc):
    """``{"isrc", "tracks": [{"uri", "title", "artist", "artist_uri", "album", "album_uri"}]}``,
    best album first. Raises NoTracks."""
    isrc = isrc.upper()
    if (hit := _cache.get(isrc)) is not None:
        return hit
    try:
        found = session.get_tracks_by_isrc(isrc)
    except Exception as e:  # ObjectNotFound, or an error from Tidal
        raise NoTracks(isrc) from e
    tracks = [t for t in found if getattr(t, "available", True) and t.album is not None]
    if not tracks:
        raise NoTracks(isrc)
    ranked = sorted(enumerate(tracks), key=lambda it: (album_rank(it[1].album.name), it[0]))
    result = {"isrc": isrc, "tracks": [_track(t) for _, t in ranked]}
    _cache.put(isrc, result)
    return result


def _track(t):
    artist = t.artist
    artist_id = artist.id if artist is not None else 0
    return {
        "uri": f"tidal:track:{artist_id}:{t.album.id}:{t.id}",
        "title": t.name,
        "artist": artist.name if artist is not None else None,
        "artist_uri": f"tidal:artist:{artist_id}" if artist is not None else None,
        "album": t.album.name,
        "album_uri": f"tidal:album:{t.album.id}",
    }

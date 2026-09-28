"""Metadata for mopidy-local albums, read from the files themselves, plus a
match to the same music on Tidal.

mopidy-local's Track model drops most tags (no ISRC, barcode, label, credits…),
so we resolve the album's tracks to files under ``[local] media_dir`` and read
the tags with mutagen: Picard-style Vorbis comments (FLAC) and ID3 (DSF).

Tidal matching, strongest first (the result is stored in SQLite):

1. ``BARCODE`` → Tidal album by UPC. Exact edition, but Tidal often lacks
   regional pressings (e.g. Japanese CDs).
2. ``ISRC`` of each track → Tidal tracks → vote for the album that shares
   the most recordings. ISRCs identify recordings, not editions, so this
   finds the music even when the pressing isn't on Tidal.
3. ``MUSICBRAINZ_ALBUMID`` → the release's barcode on MusicBrainz → (1).

Misses are remembered too and retried after ``_RETRY_MISS`` seconds.
"""
import difflib
import importlib.util
import json
import logging
import os
import pathlib
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from . import __version__
from .credits import album_credits

logger = logging.getLogger(__name__)

_RETRY_MISS = 7 * 24 * 3600
_MAX_ISRC_LOOKUPS = 8
_MB_UA = f"mopidy-goodies/{__version__} ( https://github.com/prietus/mopidy-goodies )"

# Picard / Vorbis credit keys → role label shown to clients.
_VORBIS_ROLES = {
    "COMPOSER": "Composer", "LYRICIST": "Lyricist", "WRITER": "Writer",
    "ARRANGER": "Arranger", "CONDUCTOR": "Conductor", "PRODUCER": "Producer",
    "ENGINEER": "Engineer", "MIXER": "Mixing Engineer", "REMIXER": "Remixer",
}
_PERFORMER = re.compile(r"^\s*(.*?)\s*\(([^()]*)\)\s*$")  # "Name (instrument)"


class NotLocal(LookupError):
    """The URI isn't a mopidy-local album we can resolve to files."""


# ── tags ───────────────────────────────────────────────────────────────


def read_tags(path):
    """Normalised tags of one audio file: ``{"isrc", "barcode", …, "credits": [...]}``."""
    import mutagen
    from mutagen._vorbis import VComment

    f = mutagen.File(path)
    tags = f.tags if f is not None else None
    if hasattr(tags, "getall"):
        return _from_id3(tags)
    if isinstance(tags, VComment):
        return _from_vorbis({k.upper(): list(tags[k]) for k in tags.keys()})
    return {"credits": []}  # untagged, or a container we don't map (MP4, APE…)


def _first(values):
    for v in values or []:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _from_vorbis(t):
    credits = {}
    for raw in t.get("PERFORMER", []):
        m = _PERFORMER.match(raw)
        name, role = (m[1], m[2].strip().capitalize()) if m else (raw.strip(), "Performer")
        _add(credits, role or "Performer", name)
    for key, role in _VORBIS_ROLES.items():
        for name in t.get(key, []):
            _add(credits, role, name)
    return {
        "isrc": _first(t.get("ISRC")),
        "barcode": _first(t.get("BARCODE") or t.get("UPC")),
        "catalog_number": _first(t.get("CATALOGNUMBER")),
        "label": _first(t.get("LABEL") or t.get("ORGANIZATION")),
        "country": _first(t.get("RELEASECOUNTRY")),
        "media": _first(t.get("MEDIA")),
        "musicbrainz_album_id": _first(t.get("MUSICBRAINZ_ALBUMID")),
        "credits": _credit_list(credits),
    }


def _from_id3(tags):
    def txxx(desc):
        frame = tags.get(f"TXXX:{desc}")
        return _first(frame.text) if frame else None

    def text(fid):
        frame = tags.get(fid)
        return _first(frame.text) if frame else None

    credits = {}
    for fid, role in (("TCOM", "Composer"), ("TEXT", "Lyricist"), ("TPE3", "Conductor")):
        frame = tags.get(fid)
        for name in (frame.text if frame else []):
            _add(credits, role, str(name))
    for fid in ("TMCL", "TIPL"):  # people lists: [role, name] pairs
        frame = tags.get(fid)
        for role, name in (frame.people if frame else []):
            _add(credits, role.strip().capitalize() or "Performer", name)
    return {
        "isrc": text("TSRC"),
        "barcode": txxx("BARCODE"),
        "catalog_number": txxx("CATALOGNUMBER"),
        "label": text("TPUB"),
        "country": txxx("MusicBrainz Album Release Country"),
        "media": txxx("MEDIA") or text("TMED"),
        "musicbrainz_album_id": txxx("MusicBrainz Album Id"),
        "credits": _credit_list(credits),
    }


def _add(credits, role, name):
    name = (name or "").strip()
    if name and name not in credits.setdefault(role, []):
        credits[role].append(name)


def _credit_list(credits):
    return [
        {"role": role, "contributors": [{"name": n, "id": None} for n in names]}
        for role, names in credits.items()
    ]


# ── album assembly ─────────────────────────────────────────────────────


def track_path(media_dir, uri):
    if not uri.startswith("local:track:"):
        raise NotLocal(uri)
    rel = urllib.parse.unquote(uri[len("local:track:"):])
    path = (pathlib.Path(media_dir) / rel).resolve()
    if pathlib.Path(media_dir).resolve() not in path.parents:
        raise NotLocal(uri)  # never read outside the media dir
    return path


def album_info(core, config, session, album_uri, db_path):
    """Everything Pymote shows for a local album. ``session`` may be None (no Tidal)."""
    if not album_uri.startswith("local:album:"):
        raise NotLocal(album_uri)
    tracks = core.library.lookup(uris=[album_uri]).get().get(album_uri) or []
    if not tracks:
        raise NotLocal(album_uri)
    media_dir = config["local"]["media_dir"]

    per_track = []
    for t in tracks:
        try:
            tags = read_tags(track_path(media_dir, t.uri))
        except Exception as e:  # unreadable file: keep going with the rest
            logger.debug("goodies: can't read tags of %s: %s", t.uri, e)
            tags = {"credits": []}
        per_track.append((t, tags))

    edition = {
        key: _first(tags.get(key) for _, tags in per_track)
        for key in ("label", "catalog_number", "barcode", "country", "media", "musicbrainz_album_id")
    }
    isrcs = [tags.get("isrc") for _, tags in per_track]

    tidal = None
    if session is not None:
        tidal = _cached_match(db_path, album_uri, lambda: match_tidal(
            session,
            album_name=tracks[0].album.name if tracks[0].album else "",
            barcode=edition["barcode"],
            isrcs=[i for i in isrcs if i],
            mbid=edition["musicbrainz_album_id"],
            track_count=len(tracks),
        ))

    credits, source = _merge_credits(session, per_track, tidal)
    return {
        "uri": album_uri,
        "edition": edition,
        "tidal": tidal,
        "credits_source": source,
        "credits": {"album_id": album_uri, "tracks": credits},
    }


def _merge_credits(session, per_track, tidal):
    """Tag credits win; tracks without them borrow Tidal's by ISRC."""
    tidal_by_isrc = {}
    if tidal and session is not None:
        try:
            for t in album_credits(session, tidal["album_id"])["tracks"]:
                if t.get("isrc"):
                    tidal_by_isrc[t["isrc"].upper()] = t["credits"]
        except Exception as e:
            logger.warning("goodies: Tidal credits for %s failed: %s", tidal["album_id"], e)

    out, used = [], set()
    for t, tags in per_track:
        credits = tags.get("credits") or []
        if credits:
            used.add("tags")
        elif tags.get("isrc") and (tc := tidal_by_isrc.get(tags["isrc"].upper())):
            credits = tc
            used.add("tidal")
        out.append({
            "id": t.uri, "title": t.name, "version": None,
            "track_num": t.track_no, "volume_num": t.disc_no,
            "isrc": tags.get("isrc"), "credits": credits,
        })
    source = "mixed" if len(used) == 2 else (used.pop() if used else None)
    return out, source


# ── Tidal matching ─────────────────────────────────────────────────────


def match_tidal(session, *, album_name, barcode, isrcs, mbid, track_count):
    """``{"album_id", "artist_id", "title", "method", "score"}`` or None."""
    if barcode and (m := _by_barcode(session, barcode, "barcode")):
        return m
    if isrcs and (m := _by_isrc(session, isrcs, album_name, track_count)):
        return m
    if mbid and not barcode and (mb_barcode := _musicbrainz_barcode(mbid)):
        if m := _by_barcode(session, mb_barcode, "musicbrainz"):
            return m
    return None


def _by_barcode(session, barcode, method):
    try:
        albums = session.get_albums_by_barcode(barcode)
    except Exception:  # ObjectNotFound when Tidal lacks that pressing
        return None
    return _result(albums[0], method, 1.0) if albums else None


def _by_isrc(session, isrcs, album_name, track_count):
    queried, votes, albums = 0, {}, {}
    for isrc in list(dict.fromkeys(i.upper() for i in isrcs))[:_MAX_ISRC_LOOKUPS]:
        queried += 1
        try:
            found = session.get_tracks_by_isrc(isrc)
        except Exception:
            continue
        for album in {t.album.id: t.album for t in found if t.album is not None}.values():
            votes[album.id] = votes.get(album.id, 0) + 1
            albums[album.id] = album
    if not votes:
        return None

    def rank(album_id):
        album = albums[album_id]
        similarity = difflib.SequenceMatcher(None, _norm(album_name), _norm(album.name)).ratio()
        size_gap = abs((getattr(album, "num_tracks", None) or track_count) - track_count)
        return (votes[album_id], similarity, -size_gap)

    best = max(votes, key=rank)
    return _result(albums[best], "isrc", round(votes[best] / queried, 2))


def _result(album, method, score):
    artist = getattr(album, "artist", None)
    return {
        "album_id": str(album.id),
        "artist_id": str(artist.id) if artist is not None else None,
        "title": album.name,
        "method": method,
        "score": score,
    }


def _norm(s):
    return re.sub(r"[\W_]+", " ", (s or "").lower()).strip()


_mb_lock = threading.Lock()
_mb_last = 0.0


def _musicbrainz_barcode(mbid):
    global _mb_last
    with _mb_lock:  # MusicBrainz allows ~1 request/second per client
        time.sleep(max(0.0, _mb_last + 1.1 - time.time()))
        _mb_last = time.time()
    url = f"https://musicbrainz.org/ws/2/release/{urllib.parse.quote(mbid)}?fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": _MB_UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (json.load(resp).get("barcode") or "").strip() or None
    except Exception as e:
        logger.debug("goodies: MusicBrainz %s failed: %s", mbid, e)
        return None


# ── match cache (SQLite) ───────────────────────────────────────────────


def db_path_from_config(config):
    base = pathlib.Path(config["core"]["data_dir"]) / "goodies"
    base.mkdir(parents=True, exist_ok=True)
    return base / "local.db"


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tidal_matches ("
        " album_uri TEXT PRIMARY KEY,"
        " result    TEXT,"            # JSON of match_tidal(), NULL for a miss
        " matched_at INTEGER NOT NULL)"
    )
    return conn


def _cached_match(db_path, album_uri, compute):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT result, matched_at FROM tidal_matches WHERE album_uri = ?", (album_uri,)
        ).fetchone()
        if row and (row[0] is not None or time.time() - row[1] < _RETRY_MISS):
            return json.loads(row[0]) if row[0] else None
        result = compute()
        conn.execute(
            "INSERT OR REPLACE INTO tidal_matches VALUES (?, ?, ?)",
            (album_uri, json.dumps(result) if result else None, int(time.time())),
        )
        conn.commit()
        return result
    finally:
        conn.close()


def forget_match(db_path, album_uri):
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM tidal_matches WHERE album_uri = ?", (album_uri,))
        conn.commit()
    finally:
        conn.close()


def available(config):
    """mopidy-local enabled, its media dir readable, and mutagen installed."""
    try:
        media_ok = bool(config["local"]["enabled"]) and os.path.isdir(config["local"]["media_dir"])
    except (KeyError, TypeError):
        return False
    return media_ok and importlib.util.find_spec("mutagen") is not None

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
4. Search "artist album" on Tidal, accepting only a near-exact artist + title.

``same_album`` says whether the Tidal album is the same record (title match),
not just shares recordings with it (a compilation or box set): clients should
only show the review / bio when it's true. Credits by ISRC are right either way.

Misses are remembered too and retried after ``_RETRY_MISS`` seconds.

Local artists get the same treatment so clients can show a portrait and a
biography (mopidy-local has no artist images): the Tidal artist credited on
the recordings of their files (by ISRC), else a Tidal artist with exactly
the same name. Matches share the SQLite cache, keyed by URI.

mopidy-local also splits one artist into several when their files disagree:
the URI hashes the name *and* the MusicBrainz id, so Picard-tagged and
untagged albums of the same band are two artists, as are "AC/DC" and "AC-DC".
``artist_groups`` lists which local artists are the same one, for display.
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
import unicodedata
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request

from . import __version__
from .credits import album_credits
from .texts import artist_bio

logger = logging.getLogger(__name__)

_RETRY_MISS = 7 * 24 * 3600
_MAX_ISRC_LOOKUPS = 8
_SAME_TITLE = 0.8     # normalised-title similarity to call it the same album
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
    performers = tags.get("TXXX:PERFORMER")  # some taggers mirror Vorbis PERFORMER here
    for raw in (performers.text if performers else []):
        m = _PERFORMER.match(str(raw))
        if m:
            _add(credits, m[2].strip().capitalize() or "Performer", m[1])
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
        "label": text("TPUB") or txxx("LABEL"),
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
            artist_name=_album_artist(tracks[0]),
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


def _album_artist(track):
    artists = (track.album.artists if track.album else None) or track.artists or []
    return next(iter(artists)).name if artists else ""


def _merge_credits(session, per_track, tidal):
    """Tag credits win; tracks without them borrow Tidal's for the same recording
    (by ISRC) or, when Tidal has the same album, for the same title."""
    tidal_tracks = []
    if tidal and session is not None:
        try:
            tidal_tracks = album_credits(session, tidal["album_id"])["tracks"]
        except Exception as e:
            logger.warning("goodies: Tidal credits for %s failed: %s", tidal["album_id"], e)
    by_isrc = {t["isrc"].upper(): t["credits"] for t in tidal_tracks if t.get("isrc")}
    by_title = {_norm_title(t.get("title")): t["credits"] for t in tidal_tracks} if tidal and tidal.get("same_album") else {}

    out, used = [], set()
    for t, tags in per_track:
        credits = tags.get("credits") or []
        if credits:
            used.add("tags")
        elif tags.get("isrc") and (tc := by_isrc.get(tags["isrc"].upper())):
            credits = tc
            used.add("tidal")
        elif by_title and (tc := _best_title(t.name, by_title)):
            credits = tc
            used.add("tidal")
        out.append({
            "id": t.uri, "title": t.name, "version": None,
            "track_num": t.track_no, "volume_num": t.disc_no,
            "isrc": tags.get("isrc"), "credits": credits,
        })
    source = "mixed" if len(used) == 2 else (used.pop() if used else None)
    return out, source


def _best_title(title, by_title):
    key = _norm_title(title)
    if key in by_title:
        return by_title[key]
    best = max(by_title, key=lambda k: _similar(key, k), default=None)
    return by_title[best] if best is not None and _similar(key, best) >= 0.9 else None


# ── Tidal matching ─────────────────────────────────────────────────────


def match_tidal(session, *, album_name, artist_name="", barcode, isrcs, mbid, track_count):
    """``{"album_id", "artist_id", "title", "method", "score", "same_album"}`` or None."""
    if barcode and (m := _by_barcode(session, barcode, "barcode")):
        return m
    # A compilation found by ISRC is kept as a fallback (its credits are still the
    # right recordings) while we look for the record itself.
    recordings = _by_isrc(session, isrcs, album_name, track_count) if isrcs else None
    if recordings and recordings["same_album"]:
        return recordings
    if mbid and not barcode and (mb_barcode := _musicbrainz_barcode(mbid)):
        if m := _by_barcode(session, mb_barcode, "musicbrainz"):
            return m
    if album_name and artist_name and (m := _by_search(session, artist_name, album_name)):
        return m
    return recordings


def _by_barcode(session, barcode, method):
    try:
        albums = session.get_albums_by_barcode(barcode)
    except Exception:  # ObjectNotFound when Tidal lacks that pressing
        return None
    return _result(albums[0], method, 1.0, same_album=True) if albums else None


def _by_isrc(session, isrcs, album_name, track_count):
    wanted = list(dict.fromkeys(i.upper() for i in isrcs))[:_MAX_ISRC_LOOKUPS]

    def lookup(isrc):
        try:
            return session.get_tracks_by_isrc(isrc)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lookup, wanted))

    votes, albums = {}, {}
    for found in results:
        for album in {t.album.id: t.album for t in found if t.album is not None}.values():
            votes[album.id] = votes.get(album.id, 0) + 1
            albums[album.id] = album
    if not votes:
        return None

    title = _norm_title(album_name)

    def rank(album_id):
        album = albums[album_id]
        similarity = _similar(title, _norm_title(album.name))
        size_gap = abs((getattr(album, "num_tracks", None) or track_count) - track_count)
        # The same record beats a compilation that happens to share more tracks.
        return (similarity >= _SAME_TITLE, votes[album_id], similarity, -size_gap)

    best = max(votes, key=rank)
    same = _similar(title, _norm_title(albums[best].name)) >= _SAME_TITLE
    return _result(albums[best], "isrc", round(votes[best] / len(wanted), 2), same_album=same)


def _by_search(session, artist_name, album_name):
    import tidalapi

    try:
        found = session.search(f"{artist_name} {_norm_title(album_name)}", models=[tidalapi.Album], limit=10)
    except Exception as e:
        logger.debug("goodies: Tidal search failed: %s", e)
        return None
    title, artist = _norm_title(album_name), _norm(artist_name)
    best, best_sim = None, 0.0
    for album in found.get("albums", []):
        album_artist = _norm(getattr(getattr(album, "artist", None), "name", ""))
        if _similar(artist, album_artist) < 0.85:
            continue
        sim = _similar(title, _norm_title(album.name))
        if sim > best_sim:
            best, best_sim = album, sim
    if best is None or best_sim < 0.9:
        return None
    return _result(best, "search", round(best_sim, 2), same_album=True)


def _result(album, method, score, *, same_album):
    artist = getattr(album, "artist", None)
    return {
        "album_id": str(album.id),
        "artist_id": str(artist.id) if artist is not None else None,
        "title": album.name,
        "method": method,
        "score": score,
        "same_album": same_album,
    }


def _norm(s):
    return re.sub(r"[\W_]+", " ", (s or "").lower()).strip()


_EDITION_NOISE = re.compile(
    r"[(\[{][^)\]}]*[)\]}]"                    # anything in brackets: (Deluxe), [Japan], {2016}
    r"|\b(remaster(ed)?|deluxe|expanded|anniversary|edition|version|bonus tracks?)\b"
)


def _norm_title(s):
    """Album/track title without year prefixes and edition noise, for comparison."""
    s = _EDITION_NOISE.sub(" ", (s or "").lower())
    # "1984 Purple Rain", "1969 - Led Zeppelin II" — but keep a title that *is* a year ("1984").
    without_year = re.sub(r"^\s*(19|20)\d\d\s*[.\-–:]*\s*", "", s)
    return _norm(without_year) or _norm(s)


def _similar(a, b):
    if not a or not b:
        return 0.0
    if a == b or (min(len(a), len(b)) >= 5 and (a in b or b in a)):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ── local artists ──────────────────────────────────────────────────────

_MAX_ARTIST_ISRCS = 4
# Placeholders taggers use for no particular artist: never matched.
_NO_ARTIST = {"various artists", "various", "va", "anonymous", "unknown", "unknown artist"}
_ROLE_SUFFIX = re.compile(r"\s*\([^()]*\)\s*$")  # "Abdel Rahman El Bacha(piano)"


def artist_info(core, config, session, artist_uri, db_path):
    """Name, Tidal match, portrait and bio of a local artist. ``session`` may be None."""
    if not artist_uri.startswith("local:artist:"):
        raise NotLocal(artist_uri)
    tracks = core.library.lookup(uris=[artist_uri]).get().get(artist_uri) or []
    if not tracks:
        raise NotLocal(artist_uri)
    name = _artist_name(tracks, artist_uri)

    tidal = None
    if session is not None and name and _norm(name) not in _NO_ARTIST:
        tidal = _cached_match(db_path, artist_uri, lambda: match_tidal_artist(
            session, name=name, isrcs=_artist_isrcs(config["local"]["media_dir"], tracks),
        ))

    bio = None
    if tidal:
        try:
            bio = artist_bio(session, tidal["artist_id"])
        except Exception as e:  # no bio and no picture: still return the match
            logger.debug("goodies: Tidal bio for %s failed: %s", tidal["artist_id"], e)
    return {
        "uri": artist_uri,
        "name": name,
        "tidal": tidal,
        "image": (bio or {}).get("image"),
        "text": (bio or {}).get("text"),
        "source": (bio or {}).get("source"),
    }


def _artist_name(tracks, artist_uri):
    """The artist's own spelling, wherever the files credit them."""
    for t in tracks:
        people = list(t.artists or []) + list((t.album.artists if t.album else None) or [])
        people += list(getattr(t, "composers", None) or []) + list(getattr(t, "performers", None) or [])
        for a in people:
            if a.uri == artist_uri and a.name:
                return a.name
    return ""


def _artist_isrcs(media_dir, tracks):
    """ISRCs of a few of the artist's tracks, one per album first so a single
    odd record (a guest spot, a tribute) can't decide the vote."""
    by_album, rest = {}, []
    for t in tracks:
        album = t.album.uri if t.album else None
        (rest if album in by_album else by_album.setdefault(album, [])).append(t)
    picks = [ts[0] for ts in by_album.values()] + rest
    isrcs = []
    for t in picks:
        if len(isrcs) >= _MAX_ARTIST_ISRCS:
            break
        try:
            isrc = read_tags(track_path(media_dir, t.uri)).get("isrc")
        except Exception:
            continue
        if isrc and isrc.upper() not in isrcs:
            isrcs.append(isrc.upper())
    return isrcs


def match_tidal_artist(session, *, name, isrcs):
    """``{"artist_id", "name", "method"}`` or None."""
    wanted = _norm(_ROLE_SUFFIX.sub("", name)) or _norm(name)
    if isrcs and (m := _artist_by_isrc(session, isrcs, wanted)):
        return m
    return _artist_by_search(session, wanted)


def _artist_by_isrc(session, isrcs, wanted):
    def lookup(isrc):
        try:
            return session.get_tracks_by_isrc(isrc)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lookup, isrcs))

    votes, artists = {}, {}
    for found in results:
        credited = {}
        for t in found:
            for a in (getattr(t, "artists", None) or [getattr(t, "artist", None)]):
                if a is not None and _similar(wanted, _norm(a.name)) >= 0.85:
                    credited[a.id] = a
        for a in credited.values():  # one vote per recording, however many editions
            votes[a.id] = votes.get(a.id, 0) + 1
            artists[a.id] = a
    if not votes:
        return None
    # Ties (a duo credited together) go to the closest name; plain ratio here, since
    # _similar() scores every member contained in "Bill Evans & Jim Hall" as 1.0.
    best = max(votes, key=lambda i: (votes[i], difflib.SequenceMatcher(None, wanted, _norm(artists[i].name)).ratio()))
    return {"artist_id": str(best), "name": artists[best].name, "method": "isrc"}


def _artist_by_search(session, wanted):
    import tidalapi

    try:
        found = session.search(wanted, models=[tidalapi.Artist], limit=10)
    except Exception as e:
        logger.debug("goodies: Tidal artist search failed: %s", e)
        return None
    # Only the exact name: a near miss is someone else as often as not.
    for a in found.get("artists", []):
        if _norm(a.name) == wanted:
            return {"artist_id": str(a.id), "name": a.name, "method": "search"}
    return None


# ── local artist groups ────────────────────────────────────────────────

_TYPO_SIMILARITY = 0.85   # names this close are one artist if Tidal agrees…
_TYPO_EDITS = 2           # …and a slip of the keyboard apart, not a different soloist


def artist_key(name):
    """What tells two spellings of an artist apart, once case, accents, punctuation,
    a leading "The" and "&" vs "and" are ignored: "AC/DC" = "ac-dc" = "ACDC"."""
    s = unicodedata.normalize("NFKD", (name or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s*(&|\+)\s*", " and ", s)
    s = re.sub(r"^\s*the\s+", "", s)
    return re.sub(r"[\W_]+", "", s)


def artist_groups(core, db_path):
    """Local artists that are the same artist: ``[{"name", "uris"}]``, only groups of two
    or more, the URI whose spelling is shown first.

    Spellings that differ only in form (case, accents, punctuation…) always group.
    Real typos ("Led Zepplin") group only when the names are a couple of keystrokes
    apart *and* both were matched to the same Tidal artist — closeness alone would
    merge "Berliner Philharmoniker" with "Wiener Philharmoniker", and Tidal alone
    every credit naming the same conductor. Only stored matches are used.
    """
    refs = core.library.browse(uri="local:directory?type=artist").get() or []
    by_key = {}
    for ref in refs:
        if key := artist_key(ref.name):
            by_key.setdefault(key, []).append(ref)
    keys = list(by_key)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    # Typos: within each Tidal artist, union the close spellings.
    matched = _stored_artist_matches(db_path, [r.uri for refs_ in by_key.values() for r in refs_])
    by_tidal = {}
    for key, refs_ in by_key.items():
        for tidal_id in {matched[r.uri] for r in refs_ if r.uri in matched}:
            by_tidal.setdefault(tidal_id, []).append(key)
    for group_keys in by_tidal.values():
        for i, a in enumerate(group_keys):
            for b in group_keys[i + 1:]:
                if _is_typo(a, b):
                    parent[find(a)] = find(b)

    merged = {}
    for key in keys:
        merged.setdefault(find(key), []).extend(by_key[key])
    groups = [refs_ for refs_ in merged.values() if len(refs_) > 1]
    if not groups:
        return []

    # Show a MusicBrainz-tagged spelling, else one not in all caps, else the one with most tracks.
    uris = [r.uri for refs_ in groups for r in refs_]
    tracks = core.library.lookup(uris=uris).get() or {}

    def weight(ref):
        ts = tracks.get(ref.uri) or []
        tagged = any(
            a.uri == ref.uri and getattr(a, "musicbrainz_id", None)
            for t in ts for a in list(t.artists or []) + list((t.album.artists if t.album else None) or [])
        )
        return (tagged, not ref.name.isupper(), len(ts), -len(ref.name))

    out = []
    for refs_ in groups:
        refs_ = sorted(refs_, key=weight, reverse=True)
        out.append({"name": _tidy_name(refs_[0].name), "uris": [r.uri for r in refs_]})
    return sorted(out, key=lambda g: artist_key(g["name"]))


def _is_typo(a, b):
    """Close keys a couple of keystrokes apart. Long credits sharing a long prefix
    ("Petrenko, … Choir & Victor Galler" / "& Dmitry Kharpov") are similar but not typos."""
    if abs(len(a) - len(b)) > _TYPO_EDITS or difflib.SequenceMatcher(None, a, b).ratio() < _TYPO_SIMILARITY:
        return False
    return _edit_distance(a, b) <= _TYPO_EDITS


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _tidy_name(name):
    """Drop the stray separators some taggers leave at the end ("Diana Krall , ")."""
    return re.sub(r"[\s,;/\-]+$", "", name).strip() or name


def _stored_artist_matches(db_path, uris):
    """``{uri: tidal_artist_id}`` for the local artists already matched (never matches now)."""
    if not uris:
        return {}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT album_uri, result FROM tidal_matches WHERE album_uri LIKE 'local:artist:%' AND result IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    wanted = set(uris)
    return {uri: json.loads(result)["artist_id"] for uri, result in rows if uri in wanted}


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
        " album_uri TEXT PRIMARY KEY,"    # any local URI: albums and artists
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


def forget_match(db_path, uri):
    """Drop the stored match of a local album or artist."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM tidal_matches WHERE album_uri = ?", (uri,))
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

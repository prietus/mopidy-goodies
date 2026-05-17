"""HTTP endpoints exposed by mopidy-tidal-goodies.

Routes mounted under ``/tidal_goodies/`` (Mopidy http convention takes the
ext_name as prefix). Two feature groups:

  Tidal favorites — requires mopidy-tidal logged in. 503 if the backend
  isn't loaded at all; 403 if it's loaded but the session is unauthenticated
  (the operator needs to play a Tidal track in mopidy to trigger login).

      POST   /tidal_goodies/favorites/albums          { "id": "12345" }
      DELETE /tidal_goodies/favorites/albums/12345
      GET    /tidal_goodies/favorites/albums

  Stats — works for any backend (independent of mopidy-tidal).

      GET    /tidal_goodies/stats/recent?limit=50
      GET    /tidal_goodies/stats/most-played?limit=50&since=<unix>
      GET    /tidal_goodies/stats/top-artists?limit=10&since=<unix>
      GET    /tidal_goodies/stats/top-albums?limit=10&since=<unix>
      GET    /tidal_goodies/stats/by-genre?limit=10&since=<unix>
      GET    /tidal_goodies/stats/by-day-of-week
      GET    /tidal_goodies/stats/by-hour
      GET    /tidal_goodies/stats/totals

  Audio:

      GET    /tidal_goodies/audio/output
      GET    /tidal_goodies/audio/active

  Discovery:

      GET    /tidal_goodies/_health
"""
import json
import logging
import sqlite3

from tornado.web import HTTPError, RequestHandler

from . import __version__, audio
from .stats import db_path_from_config
from .tidal import TidalBackendMissing, TidalNotLoggedIn, TidalUnavailable, get_session

logger = logging.getLogger(__name__)

# Tidal entity kinds we expose. Each must map to:
#   - tidalapi.Favorites methods named add_<kind>, remove_<kind>, <kind>s
KINDS = ("album", "track", "artist", "playlist")


def factory(config, core):
    common = {"core": core, "config": config}
    return [
        (r"/_health", HealthHandler, common),
        (
            r"/favorites/(album|track|artist|playlist)s",
            FavoritesCollectionHandler,
            common,
        ),
        (
            r"/favorites/(album|track|artist|playlist)s/([^/]+)",
            FavoritesItemHandler,
            common,
        ),
        (r"/stats/recent", StatsRecentHandler, common),
        (r"/stats/most-played", StatsMostPlayedHandler, common),
        (r"/stats/top-artists", StatsTopArtistsHandler, common),
        (r"/stats/top-albums", StatsTopAlbumsHandler, common),
        (r"/stats/by-genre", StatsByGenreHandler, common),
        (r"/stats/by-day-of-week", StatsByDayOfWeekHandler, common),
        (r"/stats/by-hour", StatsByHourHandler, common),
        (r"/stats/totals", StatsTotalsHandler, common),
        (r"/audio/output", AudioOutputHandler, common),
        (r"/audio/active", AudioActiveHandler, common),
    ]


class _Base(RequestHandler):
    def initialize(self, core, config):
        self.core = core
        self.config = config

    def write_error(self, status_code, **kwargs):
        self.set_header("Content-Type", "application/json")
        msg = self._reason
        # Tornado stores our HTTPError reason; surface it to clients as JSON.
        self.finish(json.dumps({"error": msg}))

    def _session(self):
        try:
            return get_session(self.core)
        except TidalNotLoggedIn as e:
            # 403 rather than 503: it's not a transient service-side outage,
            # it's that the upstream Tidal session is unauthenticated and the
            # operator has to trigger mopidy-tidal's login flow.
            raise HTTPError(403, reason=str(e))
        except TidalBackendMissing as e:
            raise HTTPError(503, reason=str(e))
        except TidalUnavailable as e:
            raise HTTPError(503, reason=str(e))

    def _stats_db(self):
        # Each request opens its own connection — SQLite is fine with that
        # under WAL, and Tornado handlers may run on the IOLoop thread which
        # is different from the actor's.
        path = db_path_from_config(self.config)
        return sqlite3.connect(str(path))


class HealthHandler(_Base):
    def get(self):
        try:
            get_session(self.core)
            tidal_active = True
        except TidalUnavailable:
            tidal_active = False
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "version": __version__,
            "features": {
                "favorites": True,
                "favorites_active": tidal_active,
                "stats": True,
                "audio": True,
            },
        }))


class FavoritesCollectionHandler(_Base):
    def get(self, kind):
        session = self._session()
        items = getattr(session.user.favorites, f"{kind}s")()
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps([_summarize(kind, x) for x in items]))

    def post(self, kind):
        session = self._session()
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError as e:
            raise HTTPError(400, reason=f"invalid JSON: {e}")
        item_id = body.get("id")
        if not item_id:
            raise HTTPError(400, reason="missing 'id' in body")
        getattr(session.user.favorites, f"add_{kind}")(item_id)
        self.set_status(204)


class FavoritesItemHandler(_Base):
    def delete(self, kind, item_id):
        session = self._session()
        getattr(session.user.favorites, f"remove_{kind}")(item_id)
        self.set_status(204)


def _summarize(kind, x):
    """Tidalapi objects don't JSON-serialize cleanly; pull a small summary."""
    summary = {"id": str(getattr(x, "id", ""))}
    name = getattr(x, "name", None)
    if name:
        summary["name"] = name
    artist = getattr(x, "artist", None)
    if artist is not None:
        summary["artist"] = getattr(artist, "name", None)
    return summary


# ── stats ──────────────────────────────────────────────────────────────


class StatsRecentHandler(_Base):
    def get(self):
        limit = _safe_int(self.get_query_argument("limit", "50"), default=50, lo=1, hi=500)
        conn = self._stats_db()
        try:
            rows = conn.execute(
                "SELECT played_at, track_uri, track_name, artist, album,"
                " duration_ms, played_ms, completed"
                " FROM plays ORDER BY played_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        items = [
            {
                "played_at": r[0],
                "track_uri": r[1],
                "name": r[2],
                "artist": r[3],
                "album": r[4],
                "duration_ms": r[5],
                "played_ms": r[6],
                "completed": bool(r[7]),
            }
            for r in rows
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsMostPlayedHandler(_Base):
    def get(self):
        limit = _safe_int(self.get_query_argument("limit", "50"), default=50, lo=1, hi=500)
        since_arg = self.get_query_argument("since", None)
        params: list = []
        where = ""
        if since_arg:
            ts = _safe_int(since_arg, default=0, lo=0)
            if ts:
                where = "WHERE played_at >= ?"
                params.append(ts)
        params.append(limit)
        conn = self._stats_db()
        try:
            rows = conn.execute(
                f"SELECT track_uri,"
                f" max(track_name), max(artist), max(album),"
                f" COUNT(*) as plays,"
                f" SUM(played_ms) as total_ms"
                f" FROM plays {where}"
                f" GROUP BY track_uri"
                f" ORDER BY plays DESC, total_ms DESC"
                f" LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        items = [
            {
                "track_uri": r[0],
                "name": r[1],
                "artist": r[2],
                "album": r[3],
                "plays": r[4],
                "total_played_ms": r[5] or 0,
            }
            for r in rows
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsTotalsHandler(_Base):
    def get(self):
        conn = self._stats_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*),"
                " COALESCE(SUM(played_ms), 0),"
                " COUNT(DISTINCT track_uri),"
                " COUNT(DISTINCT artist),"
                " COUNT(DISTINCT artist || '|' || album),"
                " (SELECT COUNT(*) FROM plays WHERE completed=1)"
                " FROM plays WHERE artist != ''"
            ).fetchone()
        finally:
            conn.close()
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "total_plays": row[0],
            "total_played_ms": row[1],
            "unique_tracks": row[2],
            "unique_artists": row[3],
            "unique_albums": row[4],
            "completed_plays": row[5],
        }))


# ── grouped stats (top-artists / top-albums / by-genre / by-day / by-hour) ──


def _since_clause(self):
    """Returns (where_sql, params) for an optional ?since=<unix>. Empty when
    no/invalid ``since``."""
    raw = self.get_query_argument("since", None)
    if not raw:
        return "", []
    try:
        ts = int(raw)
        if ts <= 0:
            return "", []
        return "AND played_at >= ?", [ts]
    except ValueError:
        return "", []


class StatsTopArtistsHandler(_Base):
    def get(self):
        limit = _safe_int(self.get_query_argument("limit", "10"), 10, 1, 200)
        where, params = _since_clause(self)
        params.append(limit)
        conn = self._stats_db()
        try:
            rows = conn.execute(
                f"SELECT artist,"
                f" COUNT(*) as plays,"
                f" SUM(played_ms) as total_ms,"
                f" MAX(album_uri) as sample_album_uri"
                f" FROM plays WHERE artist != '' {where}"
                f" GROUP BY artist"
                f" ORDER BY total_ms DESC, plays DESC"
                f" LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        items = [
            {
                "artist": r[0],
                "plays": r[1],
                "total_played_ms": r[2] or 0,
                "sample_album_uri": r[3],
            }
            for r in rows
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsTopAlbumsHandler(_Base):
    def get(self):
        limit = _safe_int(self.get_query_argument("limit", "10"), 10, 1, 200)
        where, params = _since_clause(self)
        params.append(limit)
        conn = self._stats_db()
        try:
            rows = conn.execute(
                f"SELECT artist, album, MAX(album_uri) as album_uri,"
                f" COUNT(*) as plays, SUM(played_ms) as total_ms"
                f" FROM plays WHERE album != '' {where}"
                f" GROUP BY artist, album"
                f" ORDER BY total_ms DESC, plays DESC"
                f" LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        items = [
            {
                "artist": r[0],
                "album": r[1],
                "album_uri": r[2],
                "plays": r[3],
                "total_played_ms": r[4] or 0,
            }
            for r in rows
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsByGenreHandler(_Base):
    def get(self):
        limit = _safe_int(self.get_query_argument("limit", "20"), 20, 1, 200)
        where, params = _since_clause(self)
        params.append(limit)
        conn = self._stats_db()
        try:
            rows = conn.execute(
                f"SELECT genre, COUNT(*) as plays, SUM(played_ms) as total_ms"
                f" FROM plays WHERE genre IS NOT NULL AND genre != '' {where}"
                f" GROUP BY genre"
                f" ORDER BY total_ms DESC, plays DESC"
                f" LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        items = [
            {"genre": r[0], "plays": r[1], "total_played_ms": r[2] or 0}
            for r in rows
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsByDayOfWeekHandler(_Base):
    """Bucketed by local-time day. 0=Sunday..6=Saturday (sqlite %w)."""

    def get(self):
        conn = self._stats_db()
        try:
            rows = conn.execute(
                "SELECT CAST(strftime('%w', played_at, 'unixepoch', 'localtime') AS INTEGER) as dow,"
                " COUNT(*) as plays,"
                " COALESCE(SUM(played_ms), 0) as total_ms"
                " FROM plays"
                " GROUP BY dow"
                " ORDER BY dow"
            ).fetchall()
        finally:
            conn.close()
        # Fill 0..6 with zeros for missing days.
        bucket = {int(r[0]): {"plays": r[1], "total_played_ms": r[2]} for r in rows}
        items = [
            {"dow": d, "plays": bucket.get(d, {}).get("plays", 0),
             "total_played_ms": bucket.get(d, {}).get("total_played_ms", 0)}
            for d in range(7)
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


class StatsByHourHandler(_Base):
    """Bucketed by local-time hour. 0..23."""

    def get(self):
        conn = self._stats_db()
        try:
            rows = conn.execute(
                "SELECT CAST(strftime('%H', played_at, 'unixepoch', 'localtime') AS INTEGER) as hour,"
                " COUNT(*) as plays,"
                " COALESCE(SUM(played_ms), 0) as total_ms"
                " FROM plays"
                " GROUP BY hour"
                " ORDER BY hour"
            ).fetchall()
        finally:
            conn.close()
        bucket = {int(r[0]): {"plays": r[1], "total_played_ms": r[2]} for r in rows}
        items = [
            {"hour": h, "plays": bucket.get(h, {}).get("plays", 0),
             "total_played_ms": bucket.get(h, {}).get("total_played_ms", 0)}
            for h in range(24)
        ]
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(items))


# ── audio ──────────────────────────────────────────────────────────────


class AudioOutputHandler(_Base):
    """Configured audio sink + (for ALSA) the human-readable card name."""

    def get(self):
        info = audio.describe(self.config.get("audio") if self.config else None)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(info))


class AudioActiveHandler(_Base):
    """Runtime ALSA params + static chain analysis (bit-perfect or not).

    Combines ``/audio/output`` (config + card lookup) with the live PCM
    params from ``/proc/asound`` so a client can render a single
    "what's the DAC seeing right now" panel.
    """

    def get(self):
        cfg = self.config.get("audio") if self.config else None
        info = audio.runtime(cfg)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(info))


def _safe_int(s, default, lo=None, hi=None):
    try:
        v = int(s)
    except (TypeError, ValueError):
        return default
    if lo is not None and v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v

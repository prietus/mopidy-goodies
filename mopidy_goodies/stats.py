"""Listening history capture + storage.

Hooks into Mopidy's CoreListener events from a Pykka frontend actor and writes
each play to SQLite. The HTTP handlers in ``handlers.py`` read from the same
DB to serve recent / most-played / totals views.

We record on ``track_playback_ended`` because that's when ``time_position``
reflects the actual played duration. ``track_playback_paused`` /
``track_playback_resumed`` aren't needed — Mopidy collapses pauses into the
final ``time_position`` on the ended event.

Independent from mopidy-tidal: this works for any backend (local, file,
spotify, podcast, ...) so a server with no Tidal still gets stats.
"""
import logging
import pathlib
import sqlite3
import time

import pykka
from mopidy.core import CoreListener

logger = logging.getLogger(__name__)


def db_path_from_config(config) -> pathlib.Path:
    """Resolve the SQLite path under Mopidy's data_dir/<ext_name>/."""
    base = pathlib.Path(config["core"]["data_dir"]) / "goodies"
    base.mkdir(parents=True, exist_ok=True)
    return base / "history.db"


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS plays (
  id            INTEGER PRIMARY KEY,
  played_at     INTEGER NOT NULL,           -- unix seconds
  track_uri     TEXT NOT NULL,
  track_name    TEXT NOT NULL DEFAULT '',
  artist        TEXT NOT NULL DEFAULT '',
  album         TEXT NOT NULL DEFAULT '',
  album_uri     TEXT,                       -- for cover lookup; nullable
  genre         TEXT,                       -- nullable; missing for old rows
  duration_ms   INTEGER NOT NULL DEFAULT 0,
  played_ms     INTEGER NOT NULL DEFAULT 0,
  completed     INTEGER NOT NULL DEFAULT 0  -- 1 if played >= 50% (scrobble-ish)
)
"""

INDICES = (
    "CREATE INDEX IF NOT EXISTS idx_plays_played_at ON plays(played_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_plays_track_uri ON plays(track_uri)",
    "CREATE INDEX IF NOT EXISTS idx_plays_artist ON plays(artist)",
    "CREATE INDEX IF NOT EXISTS idx_plays_genre ON plays(genre)",
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema upgrades for DBs from earlier versions."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(plays)")}
    if "album_uri" not in cols:
        conn.execute("ALTER TABLE plays ADD COLUMN album_uri TEXT")
    if "genre" not in cols:
        conn.execute("ALTER TABLE plays ADD COLUMN genre TEXT")


def open_db(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Order matters: CREATE TABLE first (no-op when it exists), then migrate
    # any pre-v0.3 DB to add new columns, finally create indices that may
    # reference those new columns.
    conn.execute(CREATE_TABLE)
    _migrate(conn)
    for stmt in INDICES:
        conn.execute(stmt)
    return conn


class PlaybackHistoryFrontend(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        super().__init__()
        self.config = config
        self.core = core
        self.path = db_path_from_config(config)
        self.conn: sqlite3.Connection | None = None

    def on_start(self):
        try:
            self.conn = open_db(self.path)
            logger.info("mopidy-goodies stats: history at %s", self.path)
        except Exception as e:
            logger.exception("mopidy-goodies stats: failed to open DB: %s", e)
            self.conn = None

    def on_stop(self):
        if self.conn is not None:
            self.conn.close()

    # CoreListener events ────────────────────────────────────────────────

    def track_playback_ended(self, tl_track, time_position):
        if self.conn is None or tl_track is None or tl_track.track is None:
            return
        track = tl_track.track
        artist = ", ".join(a.name for a in (track.artists or []) if a.name)
        album = track.album.name if track.album and track.album.name else ""
        album_uri = track.album.uri if track.album and track.album.uri else None
        genre = track.genre or None
        duration_ms = int(track.length or 0)
        played_ms = int(time_position or 0)
        # Scrobble-like rule: ≥50% of length OR ≥4 minutes counts as completed.
        completed = 1 if (
            duration_ms > 0
            and (played_ms >= duration_ms // 2 or played_ms >= 240_000)
        ) else 0
        try:
            self.conn.execute(
                "INSERT INTO plays (played_at, track_uri, track_name, artist,"
                " album, album_uri, genre, duration_ms, played_ms, completed)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    int(time.time()),
                    track.uri,
                    track.name or "",
                    artist,
                    album,
                    album_uri,
                    genre,
                    duration_ms,
                    played_ms,
                    completed,
                ),
            )
        except Exception as e:
            logger.warning("mopidy-goodies stats: insert failed: %s", e)

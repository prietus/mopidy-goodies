import json
import sqlite3
import time

import pytest
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from mopidy_goodies import handlers, local, stats


def _config(tmp_path):
    return {"core": {"data_dir": str(tmp_path)}, "local": {"enabled": True, "media_dir": str(tmp_path)}}


def _insert(conn, played_at, artist, album, label=None, played_ms=60_000, completed=1, uri="u"):
    conn.execute(
        "INSERT INTO plays (played_at, track_uri, track_name, artist, album, album_uri, genre, label,"
        " duration_ms, played_ms, completed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (played_at, uri, "t", artist, album, None, None, label, played_ms, played_ms, completed),
    )


def test_migrates_old_db_without_label(tmp_path):
    path = tmp_path / "history.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE plays (id INTEGER PRIMARY KEY, played_at INTEGER NOT NULL,"
                " track_uri TEXT NOT NULL, track_name TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',"
                " album TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,"
                " played_ms INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0)")
    old.commit()
    old.close()
    conn = stats.open_db(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(plays)")}
    assert {"album_uri", "genre", "label"} <= cols


def test_label_from_local_tags(tmp_path, monkeypatch):
    front = stats.PlaybackHistoryFrontend.__new__(stats.PlaybackHistoryFrontend)
    front.config = _config(tmp_path)
    monkeypatch.setattr(local, "available", lambda config: True)
    monkeypatch.setattr(local, "read_tags", lambda path: {"label": "Analogue Productions", "credits": []})
    assert front._label("local:track:dsf/A/01.dsf") == "Analogue Productions"
    assert front._label("tidal:track:1") is None

    def boom(path):
        raise OSError("gone")

    monkeypatch.setattr(local, "read_tags", boom)
    assert front._label("local:track:dsf/A/01.dsf") is None


class StatsHTTPTest(AsyncHTTPTestCase):
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp_path = tmp_path

    def get_app(self):
        return Application([])  # routes added in setUp once tmp_path is known

    def setUp(self):
        super().setUp()
        cfg = _config(self.tmp_path)
        common = {"core": None, "config": cfg}
        self._app.add_handlers(r".*", [
            (r"/stats/totals", handlers.StatsTotalsHandler, common),
            (r"/stats/top-labels", handlers.StatsTopLabelsHandler, common),
            (r"/stats/by-hour", handlers.StatsByHourHandler, common),
            (r"/stats/by-day-of-week", handlers.StatsByDayOfWeekHandler, common),
        ])
        conn = stats.open_db(stats.db_path_from_config(cfg))
        now = int(time.time())
        self.recent = now - 3600
        old = now - 40 * 86400
        _insert(conn, old, "Queen", "Jazz", label="EMI", completed=0, uri="a")
        _insert(conn, self.recent, "Queen", "Jazz", label="EMI", uri="a")
        _insert(conn, self.recent, "Bob Marley", "Exodus", label="Analogue Productions", played_ms=200_000, uri="b")
        _insert(conn, self.recent, "Chet Baker", "Sings", label=None, uri="c")
        conn.close()

    def _get(self, path):
        resp = self.fetch(path)
        assert resp.code == 200
        return json.loads(resp.body)

    def test_totals_all_time_and_since(self):
        all_time = self._get("/stats/totals")
        assert (all_time["total_plays"], all_time["completed_plays"], all_time["unique_artists"]) == (4, 3, 3)
        week = self._get(f"/stats/totals?since={self.recent - 10}")
        assert (week["total_plays"], week["completed_plays"]) == (3, 3)

    def test_top_labels(self):
        labels = self._get("/stats/top-labels")
        # Ordered by listening time (200 s beats 2 × 60 s), unlabelled plays left out.
        assert [(x["label"], x["plays"], x["total_played_ms"]) for x in labels] == [
            ("Analogue Productions", 1, 200_000), ("EMI", 2, 120_000)]
        since = self._get(f"/stats/top-labels?since={self.recent - 10}")
        assert {x["label"]: x["plays"] for x in since} == {"Analogue Productions": 1, "EMI": 1}

    def test_buckets_respect_since(self):
        hours = self._get(f"/stats/by-hour?since={self.recent - 10}")
        assert len(hours) == 24 and sum(h["plays"] for h in hours) == 3
        days = self._get("/stats/by-day-of-week")
        assert len(days) == 7 and sum(d["plays"] for d in days) == 4

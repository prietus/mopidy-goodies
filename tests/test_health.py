import json

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from mopidy_goodies import handlers
from mopidy_goodies.tidal import TidalBackendMissing, TidalNotLoggedIn


class HealthTest(AsyncHTTPTestCase):
    session_error = None

    def get_app(self):
        config = {"local": {"enabled": False, "media_dir": "/nonexistent"}, "goodies": {"visualizer_fifo": None}}
        return Application([(r"/_health", handlers.HealthHandler, {"core": None, "config": config})])

    def health(self, monkeypatch_session):
        handlers.get_session = monkeypatch_session
        return json.loads(self.fetch("/_health").body)

    def setUp(self):
        super().setUp()
        self._real = handlers.get_session

    def tearDown(self):
        handlers.get_session = self._real
        super().tearDown()

    def test_reports_tidal_session_and_versions(self):
        body = self.health(lambda core: object())
        assert body["features"]["favorites_active"] is True
        assert body["tidal"]["session"] == "ok"
        assert set(body["tidal"]) == {"session", "mopidy_tidal", "tidalapi"}
        assert body["tidal"]["tidalapi"]  # installed in the test environment

    def test_logged_out_and_missing(self):
        def raises(exc):
            def f(core):
                raise exc("x")
            return f

        assert self.health(raises(TidalNotLoggedIn))["tidal"]["session"] == "logged_out"
        missing = self.health(raises(TidalBackendMissing))
        assert missing["tidal"]["session"] == "missing"
        assert missing["features"]["favorites_active"] is False

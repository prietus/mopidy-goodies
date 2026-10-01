import pytest

from mopidy_goodies.tidal import TidalNotLoggedIn, get_session


class _Core:
    def __init__(self, backend):
        self.backends = [backend]


class _Backend:
    """Stands in for a pykka ActorRef whose proxy() is the backend itself."""

    uri_schemes = ["tidal"]

    def __init__(self, logged_in, user=None):
        self.logged_in = logged_in
        self._active_session = type("S", (), {"user": user})()
        self.session_touched = False

    @property
    def session(self):
        # mopidy-tidal: the property starts an interactive login that sleeps
        # in the actor for minutes, blocking Mopidy.
        self.session_touched = True
        raise AssertionError("session property must not be touched")

    def proxy(self):
        return self


def test_not_logged_in_never_touches_session_property():
    backend = _Backend(logged_in=False)
    with pytest.raises(TidalNotLoggedIn):
        get_session(_Core(backend))
    assert backend.session_touched is False


def test_logged_in_returns_active_session():
    backend = _Backend(logged_in=True, user=object())
    assert get_session(_Core(backend)) is backend._active_session

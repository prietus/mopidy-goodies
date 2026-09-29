from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import playlists, radio


class FakeUserPlaylist:
    def __init__(self, pid, name, tracks=()):
        self.id, self.name, self._tracks = pid, name, list(tracks)
        self.edits, self.deleted = [], False

    @property
    def num_tracks(self):
        return len(self._tracks)

    def image(self, size):
        raise ValueError("no picture")

    def tracks(self):
        return list(self._tracks)

    def edit(self, title=None, description=None):
        self.name = title or self.name

    def delete(self):
        self.deleted = True

    def add(self, ids, allow_duplicates=False, position=-1, limit=100):
        self._tracks += [_t(int(i)) for i in ids]

    def remove_by_index(self, index):
        del self._tracks[index]

    def move_by_index(self, index, position):
        self._tracks.insert(position, self._tracks.pop(index))


class FakePlaylist(FakeUserPlaylist):
    """Followed, not owned — not a UserPlaylist."""


def _t(tid, name=None):
    return NS(id=tid, name=name or f"T{tid}", duration=200, artist=NS(id=1, name="Queen"),
              album=NS(id=10, name="A Night At The Opera"))


class Session:
    def __init__(self):
        self.own = FakeUserPlaylist("11111111-1111-1111-1111-111111111111", "Mine", [_t(1), _t(2), _t(3)])
        self.theirs = FakePlaylist("22222222-2222-2222-2222-222222222222", "Headphone Classics", [_t(9)])
        self.unfollowed = []
        self.user = NS(
            playlists=lambda: [self.own],
            create_playlist=self._create,
            favorites=NS(playlists=lambda: [self.own, self.theirs], remove_playlist=self.unfollowed.append),
        )

    def _create(self, name, description):
        self.own = FakeUserPlaylist("33333333-3333-3333-3333-333333333333", name)
        return self.own

    def playlist(self, pid):
        from tidalapi.exceptions import ObjectNotFound

        for p in (self.own, self.theirs):
            if p.id == pid:
                return p
        raise ObjectNotFound(pid)

    def track(self, tid):
        return _t(int(tid))


@pytest.fixture(autouse=True)
def _user_playlist_type(monkeypatch):
    # FakePlaylist subclasses FakeUserPlaylist for code reuse; treat only the exact type as owned.
    monkeypatch.setattr(playlists, "_editable", lambda session, p: type(p) is FakeUserPlaylist)


def test_list_own_then_followed():
    items = playlists.list_playlists(Session())
    assert [(p["name"], p["editable"]) for p in items] == [("Mine", True), ("Headphone Classics", False)]
    assert items[0]["uri"].startswith("tidal:playlist:") and items[0]["image"] is None


def test_get_playlist_tracks():
    s = Session()
    pl = playlists.get_playlist(s, s.own.id)
    assert [(t["index"], t["uri"]) for t in pl["tracks"]] == [
        (0, "tidal:track:1:10:1"), (1, "tidal:track:1:10:2"), (2, "tidal:track:1:10:3")]
    assert pl["tracks"][0]["duration_ms"] == 200_000


def test_create_rename_delete_and_unfollow():
    s = Session()
    created = playlists.create_playlist(s, "Road Trip")
    assert (created["name"], created["editable"]) == ("Road Trip", True)
    assert playlists.rename_playlist(s, created["id"], "Road Trip 2")["name"] == "Road Trip 2"
    playlists.delete_playlist(s, created["id"])
    assert s.own.deleted
    playlists.delete_playlist(s, s.theirs.id)
    assert s.unfollowed == [s.theirs.id]


def test_add_tidal_and_local_tracks(monkeypatch):
    s = Session()

    def fake_tidal_track(session, core, config, uri):
        if uri == "local:track:missing.flac":
            raise radio.NoSeed(uri)
        return _t(int(uri.split(":")[-1]) if uri.startswith("tidal:") else 42)

    monkeypatch.setattr(playlists, "tidal_track", fake_tidal_track)
    result = playlists.add_tracks(s, None, {}, s.own.id,
                                  ["tidal:track:1:10:7", "local:track:found.flac", "local:track:missing.flac"])
    assert result == {"added": 2, "skipped": ["local:track:missing.flac"]}
    assert [t.id for t in s.own.tracks()] == [1, 2, 3, 7, 42]


def test_remove_and_move():
    s = Session()
    playlists.remove_track(s, s.own.id, 0)
    assert [t.id for t in s.own.tracks()] == [2, 3]
    playlists.move_track(s, s.own.id, 1, 0)
    assert [t.id for t in s.own.tracks()] == [3, 2]
    with pytest.raises(playlists.NotFound):
        playlists.remove_track(s, s.own.id, 9)


def test_followed_playlists_are_read_only():
    s = Session()
    with pytest.raises(playlists.NotEditable):
        playlists.rename_playlist(s, s.theirs.id, "x")
    with pytest.raises(playlists.NotEditable):
        playlists.remove_track(s, s.theirs.id, 0)


def test_unknown_playlist():
    with pytest.raises(playlists.NotFound):
        playlists.get_playlist(Session(), "99999999-9999-9999-9999-999999999999")

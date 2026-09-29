"""Tidal playlists: list, create, rename, delete, add / remove / move tracks.

mopidy-tidal implements Mopidy's playlist API, but it caches playlists and
never sees its own renames, and removing a track right after a rename fails
with HTTP 412 (it reuses a playlist object with a stale ETag). Here every
operation fetches the playlist fresh from Tidal, so edits always apply and
reads always reflect them. Clients should play a playlist from the track URIs
returned here rather than from ``tidal:playlist:…`` through Mopidy, whose copy
may be stale.

Tracks can be added from local files too: each is matched to the same
recording on Tidal (ISRC tag, else artist + title), as for radio.
"""
from .radio import NoSeed, tidal_track


class NotFound(LookupError):
    pass


class NotEditable(PermissionError):
    """A playlist the user follows but doesn't own."""


def _user_playlist_type():
    import tidalapi

    return tidalapi.playlist.UserPlaylist


def list_playlists(session):
    """The user's own playlists, then the ones they follow (read-only)."""
    own = list(session.user.playlists())
    own_ids = {p.id for p in own}
    followed = [p for p in session.user.favorites.playlists() if p.id not in own_ids]
    return [_summary(p, editable=True) for p in own] + [_summary(p, editable=False) for p in followed]


def get_playlist(session, playlist_id):
    playlist = _fetch(session, playlist_id)
    tracks = [t for t in playlist.tracks() if getattr(t, "album", None) is not None]
    return {
        **_summary(playlist, editable=_editable(session, playlist)),
        "tracks": [_track(i, t) for i, t in enumerate(tracks)],
    }


def create_playlist(session, name, description=""):
    playlist = session.user.create_playlist(name, description or "")
    return _summary(playlist, editable=True)


def rename_playlist(session, playlist_id, name):
    playlist = _own(session, playlist_id)
    playlist.edit(title=name)
    return _summary(_fetch(session, playlist_id), editable=True)


def delete_playlist(session, playlist_id):
    """Deletes an owned playlist; unfollows a followed one."""
    playlist = _fetch(session, playlist_id)
    if _editable(session, playlist):
        playlist.delete()
    else:
        session.user.favorites.remove_playlist(playlist_id)


def add_tracks(session, core, config, playlist_id, uris):
    """Append tracks (Tidal or local URIs). Returns ``{"added", "skipped": [uri…]}``,
    skipping local tracks that can't be found on Tidal."""
    ids, skipped = [], []
    for uri in uris:
        if uri.startswith("tidal:track:"):
            # The id is the URI's last part: no need to ask Tidal (100 tracks = 100 calls).
            ids.append(uri.split(":")[-1])
            continue
        try:
            ids.append(str(tidal_track(session, core, config, uri).id))
        except NoSeed:
            skipped.append(uri)
        except Exception:  # unknown Tidal id, lookup failure
            skipped.append(uri)
    if ids:
        _own(session, playlist_id).add(ids, allow_duplicates=True)
    return {"added": len(ids), "skipped": skipped}


def remove_track(session, playlist_id, index):
    playlist = _own(session, playlist_id)
    if not 0 <= index < playlist.num_tracks:
        raise NotFound(f"no track {index} in {playlist_id}")
    playlist.remove_by_index(index)


def move_track(session, playlist_id, index, position):
    playlist = _own(session, playlist_id)
    if not (0 <= index < playlist.num_tracks and 0 <= position < playlist.num_tracks):
        raise NotFound(f"bad position in {playlist_id}")
    if index != position:
        playlist.move_by_index(index, position)


# ── helpers ────────────────────────────────────────────────────────────


def _fetch(session, playlist_id):
    try:
        from tidalapi.exceptions import ObjectNotFound
    except ImportError:  # pragma: no cover
        ObjectNotFound = ()
    try:
        return session.playlist(playlist_id)
    except ObjectNotFound as e:
        raise NotFound(playlist_id) from e


def _editable(session, playlist):
    return isinstance(playlist, _user_playlist_type())


def _own(session, playlist_id):
    playlist = _fetch(session, playlist_id)
    if not _editable(session, playlist):
        raise NotEditable(playlist_id)
    return playlist


def _summary(p, *, editable):
    try:
        image = p.image(320)
    except Exception:  # no picture yet (e.g. an empty new playlist)
        image = None
    return {
        "id": str(p.id),
        "uri": f"tidal:playlist:{p.id}",
        "name": p.name,
        "num_tracks": getattr(p, "num_tracks", None),
        "image": image,
        "editable": editable,
    }


def _track(index, t):
    artist_id = t.artist.id if t.artist is not None else 0
    return {
        "index": index,
        "uri": f"tidal:track:{artist_id}:{t.album.id}:{t.id}",
        "title": t.name,
        "artist": t.artist.name if t.artist is not None else None,
        "album": t.album.name,
        "album_uri": f"tidal:album:{t.album.id}",
        "duration_ms": int((getattr(t, "duration", 0) or 0) * 1000),
    }

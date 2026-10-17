"""Artwork of mopidy-local albums, and which image to show as the cover.

mopidy-local only knows the images embedded in the audio files, and rips often
embed something odd (a screenshot of the ripping tool, a tiny thumbnail)
while the album's folder holds proper scans: front, back, OBI, disc, booklet.
We list both — the embedded images as mopidy-local serves them and the image
files in the album's folders — serve the files, and remember which one the
user picked as the cover (shared by every client, stored in SQLite).

The album's folders are those of its tracks, plus their parent when the tracks
sit in per-disc folders ("CD1", "Disc 2"), each searched two levels down
("Covers", "Scans/Booklet"…). A track loose in the media dir itself doesn't
make the whole library its folder.
"""
import mimetypes
import os
import pathlib
import re
import sqlite3
import time
import urllib.parse

from .local import NotLocal, track_path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MAX_DEPTH = 2
_MAX_IMAGES = 300
_DISC_DIR = re.compile(r"^(cd|dis[ck]|side)[\s._-]*\w{0,3}$", re.I)
_FRONT = re.compile(r"(^|[\W_])(cover|front|folder|face)([\W_]|$)", re.I)
_NOT_FRONT = re.compile(r"back|inside|in_side|inlay|tray|page|book|cd|disc|obi", re.I)

FILE_ROUTE = "/goodies/local/artwork/file?path="


class ArtworkFileNotFound(LookupError):
    """Not an image inside the media dir."""


def album_artwork(core, media_dir, album_uri, db_path):
    """``{"uri", "selected", "images": [{"url", "name", "source", "width", "height"}]}``.

    URLs are server-relative (as mopidy-local's own image URLs). ``selected`` is the
    URL picked as the cover, or null for mopidy-local's default.
    """
    if not album_uri.startswith("local:album:"):
        raise NotLocal(album_uri)
    tracks = core.library.lookup(uris=[album_uri]).get().get(album_uri) or []
    if not tracks:
        raise NotLocal(album_uri)

    images = []
    embedded = core.library.get_images(uris=[album_uri]).get().get(album_uri) or []
    for i, image in enumerate(embedded, 1):
        images.append({
            "url": image.uri, "name": f"Embedded {i}", "source": "embedded",
            "width": image.width, "height": image.height,
        })
    for rel in folder_images(media_dir, [t.uri for t in tracks]):
        images.append({
            "url": file_url(rel), "name": rel.rsplit("/", 1)[-1], "source": "folder",
            "width": None, "height": None,
        })
    return {"uri": album_uri, "selected": choices(db_path).get(album_uri), "images": images}


def album_dirs(media_dir, track_uris):
    """The album's folders (absolute), from its tracks' paths."""
    root = pathlib.Path(media_dir).resolve()
    parents = set()
    for uri in track_uris:
        try:
            parents.add(track_path(media_dir, uri).parent)
        except NotLocal:
            continue
    dirs = {p for p in parents if p != root}
    # Per-disc folders: the album's own folder (with the scans) is one up.
    for p in list(dirs):
        if _DISC_DIR.match(p.name) and p.parent != root:
            dirs.add(p.parent)
    return sorted(dirs)


def folder_images(media_dir, track_uris):
    """Image files of the album's folders, relative to the media dir: likely front covers
    first, then shallowest first, in natural order ("Page-2" before "Page-10")."""
    root = pathlib.Path(media_dir).resolve()
    found = set()
    for top in album_dirs(media_dir, track_uris):
        top_depth = len(top.parts)
        for dirpath, dirnames, filenames in os.walk(top):
            depth = len(pathlib.Path(dirpath).parts) - top_depth
            if depth >= _MAX_DEPTH:
                dirnames[:] = []
            for name in filenames:
                if pathlib.Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                    found.add(pathlib.Path(dirpath, name).relative_to(root).as_posix())
            if len(found) >= _MAX_IMAGES:
                break
    return sorted(found, key=_image_order)[:_MAX_IMAGES]


def _image_order(rel):
    stem = pathlib.PurePosixPath(rel).stem
    front = bool(_FRONT.search(stem)) and not _NOT_FRONT.search(stem)
    return (not front, rel.count("/"), _natural(rel.lower()))


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def file_url(rel):
    return FILE_ROUTE + urllib.parse.quote(rel, safe="")


def resolve_file(media_dir, rel):
    """The absolute path of an image file named by a media-dir-relative path, or raise."""
    root = pathlib.Path(media_dir).resolve()
    path = (root / (rel or "")).resolve()
    if root not in path.parents or path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
        raise ArtworkFileNotFound(rel)
    return path


def read_file(media_dir, rel):
    """``(bytes, content type)`` of an album image file."""
    path = resolve_file(media_dir, rel)
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# ── chosen covers (SQLite) ─────────────────────────────────────────────


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS artwork_choices ("
        " album_uri TEXT PRIMARY KEY,"
        " url       TEXT NOT NULL,"       # one of album_artwork()'s image URLs
        " chosen_at INTEGER NOT NULL)"
    )
    return conn


def choose(db_path, album_uri, url):
    conn = _connect(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO artwork_choices VALUES (?, ?, ?)", (album_uri, url, int(time.time())))
        conn.commit()
    finally:
        conn.close()


def forget(db_path, album_uri):
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM artwork_choices WHERE album_uri = ?", (album_uri,))
        conn.commit()
    finally:
        conn.close()


def choices(db_path):
    """``{album_uri: url}`` of every album whose cover was picked."""
    conn = _connect(db_path)
    try:
        return dict(conn.execute("SELECT album_uri, url FROM artwork_choices").fetchall())
    finally:
        conn.close()

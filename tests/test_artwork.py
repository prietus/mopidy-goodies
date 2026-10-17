from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import artwork, local


def _touch(root, rel, data=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _uri(rel):
    return "local:track:" + rel.replace(" ", "%20")


@pytest.fixture
def library(tmp_path):
    album = "rips/Back In Black"
    for rel in [
        f"{album}/01 Hells Bells.flac", f"{album}/Covers/Cover_Face.jpg", f"{album}/Covers/Cover_Back.jpg",
        f"{album}/Covers/Japan_Book_Page-10.jpg", f"{album}/Covers/Japan_Book_Page-2.jpg", f"{album}/Covers/OBI.JPG",
        f"{album}/Covers/notes.txt", f"{album}/Covers/Deep/Deeper/too-deep.jpg",
        "rips/Other Album/folder.jpg", "loose.flac", "loose.jpg",
    ]:
        _touch(tmp_path, rel)
    return tmp_path


def test_folder_images_front_first_natural_order(library):
    images = artwork.folder_images(library, [_uri("rips/Back In Black/01 Hells Bells.flac")])
    assert images == [
        "rips/Back In Black/Covers/Cover_Face.jpg",          # a front cover leads
        "rips/Back In Black/Covers/Cover_Back.jpg",
        "rips/Back In Black/Covers/Japan_Book_Page-2.jpg",   # natural order: 2 before 10
        "rips/Back In Black/Covers/Japan_Book_Page-10.jpg",
        "rips/Back In Black/Covers/OBI.JPG",
    ]  # no text files, nothing three levels down, nothing from other albums


def test_per_disc_folders_reach_the_album_folder(tmp_path):
    _touch(tmp_path, "rips/Album/CD1/01.flac")
    _touch(tmp_path, "rips/Album/Disc 2/01.flac")
    _touch(tmp_path, "rips/Album/Scans/front.jpg")
    images = artwork.folder_images(tmp_path, [_uri("rips/Album/CD1/01.flac"), _uri("rips/Album/Disc 2/01.flac")])
    assert images == ["rips/Album/Scans/front.jpg"]


def test_loose_tracks_dont_claim_the_whole_library(library):
    assert artwork.folder_images(library, [_uri("loose.flac")]) == []


def test_resolve_file_stays_in_media_dir_and_images_only(library):
    assert artwork.resolve_file(library, "rips/Back In Black/Covers/OBI.JPG").name == "OBI.JPG"
    for bad in ["../etc/passwd", "rips/Back In Black/Covers/notes.txt", "rips/missing.jpg", "", "/etc/hosts.jpg"]:
        with pytest.raises(artwork.ArtworkFileNotFound):
            artwork.resolve_file(library, bad)


def test_read_file_content_type(library):
    data, content_type = artwork.read_file(library, "rips/Other Album/folder.jpg")
    assert (data, content_type) == (b"x", "image/jpeg")


def test_album_artwork_lists_embedded_then_folder_and_the_choice(library, tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "local.db"
    album = "local:album:md5:1"
    core = NS(library=NS(
        lookup=lambda uris: NS(get=lambda: {album: [NS(uri=_uri("rips/Back In Black/01 Hells Bells.flac"))]}),
        get_images=lambda uris: NS(get=lambda: {album: [NS(uri="/local/abc-419x413.jpeg", width=419, height=413)]}),
    ))
    info = artwork.album_artwork(core, library, album, db)
    assert info["selected"] is None
    assert info["images"][0] == {"url": "/local/abc-419x413.jpeg", "name": "Embedded 1", "source": "embedded",
                                 "width": 419, "height": 413}
    face = info["images"][1]
    assert (face["name"], face["source"]) == ("Cover_Face.jpg", "folder")
    assert face["url"] == "/goodies/local/artwork/file?path=rips%2FBack%20In%20Black%2FCovers%2FCover_Face.jpg"

    artwork.choose(db, album, face["url"])
    assert artwork.album_artwork(core, library, album, db)["selected"] == face["url"]
    assert artwork.choices(db) == {album: face["url"]}
    artwork.forget(db, album)
    assert artwork.choices(db) == {}

    with pytest.raises(local.NotLocal):
        artwork.album_artwork(core, library, "tidal:album:1", db)

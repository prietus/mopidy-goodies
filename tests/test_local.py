import json
import time
from types import SimpleNamespace as NS

import pytest

from mopidy_goodies import local

mutagen = pytest.importorskip("mutagen")


# ── tags ──────────────────────────────────────────────────────────────


def test_vorbis_picard_tags():
    t = local._from_vorbis({
        "ISRC": ["AUAP07800050"], "BARCODE": ["4547366035155"], "CATALOGNUMBER": ["SICP-1704"],
        "LABEL": ["Sony"], "RELEASECOUNTRY": ["JP"], "MUSICBRAINZ_ALBUMID": ["mb-1"],
        "PERFORMER": ["Angus Young (lead guitar)", "Bon Scott (vocals)", "Session Guy"],
        "PRODUCER": ["Harry Vanda", "George Young"], "MIXER": ["Someone"],
    })
    assert (t["isrc"], t["barcode"], t["catalog_number"], t["label"], t["country"]) == (
        "AUAP07800050", "4547366035155", "SICP-1704", "Sony", "JP")
    assert t["musicbrainz_album_id"] == "mb-1"
    roles = {c["role"]: [p["name"] for p in c["contributors"]] for c in t["credits"]}
    assert roles == {
        "Lead guitar": ["Angus Young"], "Vocals": ["Bon Scott"], "Performer": ["Session Guy"],
        "Producer": ["Harry Vanda", "George Young"], "Mixing Engineer": ["Someone"],
    }


def test_id3_tags_as_written_to_dsf():
    from mutagen.id3 import ID3, TIPL, TMCL, TPUB, TSRC, TXXX

    tags = ID3()
    tags.add(TSRC(encoding=3, text=["GBUM71029604"]))
    tags.add(TPUB(encoding=3, text=["EMI"]))
    tags.add(TXXX(encoding=3, desc="BARCODE", text=["0724383500527"]))
    tags.add(TXXX(encoding=3, desc="CATALOGNUMBER", text=["CDP 7 46001 2"]))
    tags.add(TMCL(encoding=3, people=[["drums", "Nick Mason"], ["bass", "Roger Waters"]]))
    tags.add(TIPL(encoding=3, people=[["producer", "Pink Floyd"]]))
    tags.add(TXXX(encoding=3, desc="PERFORMER", text=["David Gilmour (guitar)", "Queen"]))
    got = local._from_id3(tags)
    assert (got["isrc"], got["label"], got["barcode"], got["catalog_number"]) == (
        "GBUM71029604", "EMI", "0724383500527", "CDP 7 46001 2")
    roles = {c["role"]: [p["name"] for p in c["contributors"]] for c in got["credits"]}
    assert roles == {"Guitar": ["David Gilmour"], "Drums": ["Nick Mason"], "Bass": ["Roger Waters"],
                     "Producer": ["Pink Floyd"]}


def test_id3_label_from_txxx():
    from mutagen.id3 import ID3, TXXX

    tags = ID3()
    tags.add(TXXX(encoding=3, desc="LABEL", text=["EMI"]))
    assert local._from_id3(tags)["label"] == "EMI"


def test_first_skips_missing_values():
    assert local._first([None, "  ", "SICP-1704"]) == "SICP-1704"
    assert local._first([None, None]) is None
    assert local._first(None) is None


def test_track_path_stays_inside_media_dir(tmp_path):
    assert local.track_path(tmp_path, "local:track:rips/A%20B/01.flac") == (tmp_path / "rips/A B/01.flac").resolve()
    with pytest.raises(local.NotLocal):
        local.track_path(tmp_path, "local:track:..%2F..%2Fetc%2Fpasswd")
    with pytest.raises(local.NotLocal):
        local.track_path(tmp_path, "file:///etc/passwd")


# ── Tidal matching ────────────────────────────────────────────────────


def _album(id_, name, num_tracks=10, artist_id=7):
    return NS(id=id_, name=name, num_tracks=num_tracks, artist=NS(id=artist_id))


class _Tidal:
    def __init__(self, barcodes=None, isrcs=None):
        self.barcodes = barcodes or {}
        self.isrcs = isrcs or {}
        self.isrc_calls = 0

    def get_albums_by_barcode(self, bc):
        if bc not in self.barcodes:
            raise LookupError(bc)
        return [self.barcodes[bc]]

    def get_tracks_by_isrc(self, isrc):
        self.isrc_calls += 1
        return [NS(album=a) for a in self.isrcs.get(isrc, [])]


def test_barcode_wins():
    tidal = _Tidal(barcodes={"123": _album(1, "Edition")})
    m = local.match_tidal(tidal, album_name="X", barcode="123", isrcs=["A"], mbid=None, track_count=10)
    assert m == {"album_id": "1", "artist_id": "7", "title": "Edition", "method": "barcode",
                 "score": 1.0, "same_album": True}
    assert tidal.isrc_calls == 0


def test_isrc_vote_when_pressing_missing():
    live, best_of = _album(10, "If You Want Blood You've Got It (Live)"), _album(20, "Greatest Hits", 30)
    tidal = _Tidal(isrcs={"A": [live, best_of], "B": [live], "C": [live]})
    m = local.match_tidal(tidal, album_name="If You Want Blood", barcode="999",
                          isrcs=["A", "B", "C", "a"], mbid=None, track_count=10)
    assert (m["album_id"], m["method"], m["score"], m["same_album"]) == ("10", "isrc", 1.0, True)
    assert tidal.isrc_calls == 3  # duplicates (case-insensitive) looked up once


def test_isrc_tie_broken_by_title():
    a, b = _album(1, "Totally Different"), _album(2, "Bags' Groove")
    tidal = _Tidal(isrcs={"A": [a, b]})
    m = local.match_tidal(tidal, album_name="Bags Groove", barcode=None, isrcs=["A"], mbid=None, track_count=10)
    assert m["album_id"] == "2"


def test_same_record_beats_compilation_with_more_votes():
    original, box = _album(1, "Degüello"), _album(2, "The Complete Studio Albums (1970 - 1990)", 120)
    tidal = _Tidal(isrcs={"A": [original, box], "B": [box], "C": [box]})
    m = local.match_tidal(tidal, album_name="Degüello", barcode=None, isrcs=["A", "B", "C"], mbid=None, track_count=10)
    assert (m["album_id"], m["same_album"]) == ("1", True)


def test_search_finds_the_record_when_isrc_only_hits_a_compilation():
    import tidalapi

    box = _album(2, "The Complete Studio Albums (1970 - 1990)", 120)

    class _Both(_Tidal):
        def search(self, query, models=None, limit=None):
            return {"albums": [NS(id=1, name="Degüello", num_tracks=10, artist=NS(id=4, name="ZZ Top"))]}

    m = local.match_tidal(_Both(isrcs={"A": [box]}), album_name="Degüello", artist_name="ZZ Top",
                          barcode=None, isrcs=["A"], mbid=None, track_count=10)
    assert (m["album_id"], m["method"], m["same_album"]) == ("1", "search", True)


def test_only_compilation_is_flagged_not_same_album():
    soundtrack = _album(5, "Singles - Original Motion Picture Soundtrack")
    tidal = _Tidal(isrcs={"A": [soundtrack]})
    m = local.match_tidal(tidal, album_name="Are You Experienced", barcode=None, isrcs=["A"], mbid=None, track_count=17)
    assert (m["album_id"], m["same_album"]) == ("5", False)


def test_search_fallback_needs_artist_and_title():
    import tidalapi

    class _Search(_Tidal):
        def search(self, query, models=None, limit=None):
            assert models == [tidalapi.Album]
            return {"albums": [
                NS(id=1, name="Goodbye Yellow Brick Road", num_tracks=17, artist=NS(id=9, name="Tribute Band")),
                NS(id=2, name="Goodbye Yellow Brick Road (Remastered)", num_tracks=17, artist=NS(id=3, name="Elton John")),
            ]}

    m = local.match_tidal(_Search(), album_name="Goodbye Yellow Brick Road", artist_name="Elton John",
                          barcode=None, isrcs=["X"], mbid=None, track_count=17)
    assert (m["album_id"], m["method"], m["same_album"]) == ("2", "search", True)
    none = local.match_tidal(_Search(), album_name="Captain Fantastic", artist_name="Elton John",
                             barcode=None, isrcs=[], mbid=None, track_count=10)
    assert none is None


@pytest.mark.parametrize("raw,expected", [
    ("1984 Purple Rain", "purple rain"),
    ("1969 - Led Zeppelin II", "led zeppelin ii"),
    ("1984", "1984"),
    ("Brothers In Arms (40th Anniversary)", "brothers in arms"),
    ("Argus (Deluxe Edition)", "argus"),
    ("If You Want Blood You’ve Got It", "if you want blood you ve got it"),
])
def test_norm_title(raw, expected):
    assert local._norm_title(raw) == expected


def test_musicbrainz_barcode_fallback(monkeypatch):
    monkeypatch.setattr(local, "_musicbrainz_barcode", lambda mbid: "555" if mbid == "mb" else None)
    tidal = _Tidal(barcodes={"555": _album(3, "Via MB")})
    m = local.match_tidal(tidal, album_name="", barcode=None, isrcs=[], mbid="mb", track_count=1)
    assert (m["album_id"], m["method"]) == ("3", "musicbrainz")


def test_no_match():
    assert local.match_tidal(_Tidal(), album_name="", barcode=None, isrcs=[], mbid=None, track_count=1) is None


# ── cache ─────────────────────────────────────────────────────────────


def test_cached_match_hits_and_retries_misses(tmp_path, monkeypatch):
    db = tmp_path / "local.db"
    calls = []

    def compute(result):
        def f():
            calls.append(1)
            return result
        return f

    assert local._cached_match(db, "u1", compute({"album_id": "1"})) == {"album_id": "1"}
    assert local._cached_match(db, "u1", compute({"album_id": "X"})) == {"album_id": "1"}
    assert local._cached_match(db, "u2", compute(None)) is None
    assert local._cached_match(db, "u2", compute({"album_id": "2"})) is None  # miss remembered
    now = time.time()
    monkeypatch.setattr(local.time, "time", lambda: now + local._RETRY_MISS + 1)
    assert local._cached_match(db, "u2", compute({"album_id": "2"})) == {"album_id": "2"}  # retried
    local.forget_match(db, "u1")
    assert local._cached_match(db, "u1", compute({"album_id": "new"})) == {"album_id": "new"}
    assert len(calls) == 4


# ── credits merge ─────────────────────────────────────────────────────


def test_tag_credits_win_and_tidal_fills_by_isrc(monkeypatch):
    tidal_tracks = [
        {"isrc": "B", "credits": [{"role": "Producer", "contributors": [{"name": "Tidal P", "id": "1"}]}]},
    ]
    monkeypatch.setattr(local, "album_credits", lambda session, album_id: {"tracks": tidal_tracks})
    tag_credit = [{"role": "Drums", "contributors": [{"name": "Tag D", "id": None}]}]
    per_track = [
        (NS(uri="local:track:1", name="One", track_no=1, disc_no=1), {"isrc": "A", "credits": tag_credit}),
        (NS(uri="local:track:2", name="Two", track_no=2, disc_no=1), {"isrc": "b", "credits": []}),
        (NS(uri="local:track:3", name="Three", track_no=3, disc_no=1), {"isrc": None, "credits": []}),
    ]
    out, source = local._merge_credits(object(), per_track, {"album_id": "9"})
    assert source == "mixed"
    assert out[0]["credits"] == tag_credit
    assert out[1]["credits"][0]["contributors"][0]["name"] == "Tidal P"
    assert out[2]["credits"] == []
    assert json.dumps(out)  # serialisable


def test_same_album_fills_credits_by_title(monkeypatch):
    tidal_tracks = [{"isrc": "OTHER", "title": "Bennie and the Jets (Remastered)",
                     "credits": [{"role": "Piano", "contributors": [{"name": "Elton John", "id": "3"}]}]}]
    monkeypatch.setattr(local, "album_credits", lambda session, album_id: {"tracks": tidal_tracks})
    per_track = [(NS(uri="local:track:1", name="Bennie and the Jets", track_no=1, disc_no=1),
                  {"isrc": "GBFO80300790", "credits": []})]
    out, source = local._merge_credits(object(), per_track, {"album_id": "9", "same_album": True})
    assert source == "tidal" and out[0]["credits"][0]["role"] == "Piano"
    out, source = local._merge_credits(object(), per_track, {"album_id": "9", "same_album": False})
    assert source is None and out[0]["credits"] == []

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


# ── local artists ─────────────────────────────────────────────────────


def _artist(id_, name):
    return NS(id=id_, name=name)


class _ArtistTidal:
    def __init__(self, isrcs=None, search=None):
        self.isrcs = isrcs or {}
        self.results = search or []
        self.searches = []

    def get_tracks_by_isrc(self, isrc):
        return [NS(artists=artists) for artists in self.isrcs.get(isrc, [])]

    def search(self, query, models=None, limit=None):
        import tidalapi

        assert models == [tidalapi.Artist]
        self.searches.append(query)
        return {"artists": self.results}


def test_artist_by_isrc_votes_per_recording():
    miles, namesake = _artist(55, "Miles Davis"), _artist(9, "Miles Davis Tribute")
    # A recording on two editions credits Miles twice: still one vote.
    tidal = _ArtistTidal(isrcs={"A": [[miles], [miles]], "B": [[miles, _artist(1, "John Coltrane")]], "C": [[namesake]]})
    m = local.match_tidal_artist(tidal, name="Miles Davis", isrcs=["A", "B", "C"])
    assert m == {"artist_id": "55", "name": "Miles Davis", "method": "isrc"}
    assert tidal.searches == []


def test_artist_search_needs_the_exact_name():
    tidal = _ArtistTidal(search=[_artist(7, "Bill Evans Trio"), _artist(8, "Bill Evans")])
    assert local.match_tidal_artist(tidal, name="Bill Evans", isrcs=[])["artist_id"] == "8"
    assert local.match_tidal_artist(_ArtistTidal(search=[_artist(7, "Bill Evans Trio")]), name="Bill Evans", isrcs=[]) is None


def test_artist_role_suffix_is_dropped():
    tidal = _ArtistTidal(search=[_artist(3, "Abdel Rahman El Bacha")])
    m = local.match_tidal_artist(tidal, name="Abdel Rahman El Bacha(piano)", isrcs=[])
    assert (m["artist_id"], tidal.searches) == ("3", ["abdel rahman el bacha"])


def _core(tracks_by_uri):
    return NS(library=NS(lookup=lambda uris: NS(get=lambda: {u: tracks_by_uri.get(u, []) for u in uris})))


def _local_track(uri, album_uri, artist_uri, artist_name):
    a = NS(uri=artist_uri, name=artist_name)
    return NS(uri=uri, artists=[a], album=NS(uri=album_uri, artists=[a]))


def test_artist_info_matches_and_adds_bio(tmp_path, monkeypatch):
    uri = "local:artist:md5:1"
    tracks = [_local_track(f"local:track:a/{i}.flac", f"local:album:{i % 2}", uri, "Miles Davis") for i in range(4)]
    monkeypatch.setattr(local, "_artist_isrcs", lambda media_dir, ts: ["A"])
    monkeypatch.setattr(local, "artist_bio", lambda session, artist_id: {
        "image": f"https://img/{artist_id}.jpg", "text": "Trumpeter.", "source": "TiVo"})
    tidal = _ArtistTidal(isrcs={"A": [[_artist(55, "Miles Davis")]]})
    config = {"local": {"media_dir": str(tmp_path)}}
    info = local.artist_info(_core({uri: tracks}), config, tidal, uri, tmp_path / "local.db")
    assert info == {
        "uri": uri, "name": "Miles Davis",
        "tidal": {"artist_id": "55", "name": "Miles Davis", "method": "isrc"},
        "image": "https://img/55.jpg", "text": "Trumpeter.", "source": "TiVo",
    }
    # Cached: a second call doesn't match again.
    monkeypatch.setattr(local, "match_tidal_artist", lambda *a, **k: pytest.fail("not cached"))
    assert local.artist_info(_core({uri: tracks}), config, tidal, uri, tmp_path / "local.db")["tidal"]["artist_id"] == "55"


def test_artist_info_without_tidal_or_for_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "match_tidal_artist", lambda *a, **k: pytest.fail("must not match"))
    config = {"local": {"media_dir": str(tmp_path)}}
    va = "local:artist:md5:va"
    core = _core({va: [_local_track("local:track:x.flac", "local:album:1", va, "Various Artists")]})
    info = local.artist_info(core, config, object(), va, tmp_path / "local.db")
    assert (info["name"], info["tidal"], info["image"]) == ("Various Artists", None, None)
    info = local.artist_info(core, config, None, va, tmp_path / "local.db")  # Tidal not logged in
    assert info["tidal"] is None
    with pytest.raises(local.NotLocal):
        local.artist_info(core, config, None, "local:artist:md5:nobody", tmp_path / "local.db")
    with pytest.raises(local.NotLocal):
        local.artist_info(core, config, None, "tidal:artist:1", tmp_path / "local.db")


def test_artist_isrcs_one_per_album_first(tmp_path, monkeypatch):
    tags = {"1": "AAA", "2": "AAA", "3": "BBB", "4": "CCC", "5": "DDD", "6": "EEE"}
    monkeypatch.setattr(local, "read_tags", lambda path: {"isrc": tags[path.stem]})
    tracks = [NS(uri=f"local:track:{i}.flac", album=NS(uri=f"local:album:{'x' if i < 3 else i}")) for i in range(1, 7)]
    # Album x (tracks 1, 2) gives one ISRC up front; the cap of four is reached before track 6.
    assert local._artist_isrcs(tmp_path, tracks) == ["AAA", "BBB", "CCC", "DDD"]


def test_artist_tie_goes_to_the_closest_name():
    evans, hall = _artist(1158, "Bill Evans"), _artist(6013, "Jim Hall")
    tidal = _ArtistTidal(isrcs={"A": [[hall, evans]], "B": [[hall, evans]]})
    assert local.match_tidal_artist(tidal, name="Bill Evans & Jim Hall", isrcs=["A", "B"])["artist_id"] == "1158"


# ── artist groups ─────────────────────────────────────────────────────


@pytest.mark.parametrize("a,b", [
    ("AC/DC", "AC-DC"), ("Led Zeppelin", "LED ZEPPELIN"), ("Barón Rojo", "Baron Rojo"),
    ("Diana Krall , ", "Diana Krall"), ("Simon & Garfunkel", "Simon and Garfunkel"),
    ("The Black Crowes", "Black Crowes"),
])
def test_artist_key_ignores_form(a, b):
    assert local.artist_key(a) == local.artist_key(b)


def test_artist_key_keeps_different_artists_apart():
    assert local.artist_key("Bill Evans") != local.artist_key("Bill Evans Trio")
    assert local.artist_key("Berliner Philharmoniker") != local.artist_key("Wiener Philharmoniker")


def _groups_core(refs, tracks):
    return NS(library=NS(
        browse=lambda uri: NS(get=lambda: [NS(uri=u, name=n, type="artist") for u, n in refs]),
        lookup=lambda uris: NS(get=lambda: {u: tracks.get(u, []) for u in uris}),
    ))


def _tracks(uri, n, mbid=None):
    a = NS(uri=uri, name="x", musicbrainz_id=mbid)
    return [NS(artists=[a], album=NS(artists=[a])) for _ in range(n)]


def test_artist_groups_by_form_and_display_name(tmp_path):
    refs = [("u1", "AC-DC"), ("u2", "AC/DC"), ("u3", "AC/DC"), ("u4", "LED ZEPPELIN"),
            ("u5", "Led Zeppelin"), ("u6", "Diana Krall , "), ("u7", "Diana Krall"), ("u8", "Bill Evans")]
    tracks = {"u1": _tracks("u1", 57), "u2": _tracks("u2", 12), "u3": _tracks("u3", 33, mbid="66c662b6"),
              "u4": _tracks("u4", 40), "u5": _tracks("u5", 3), "u6": _tracks("u6", 1), "u7": _tracks("u7", 35)}
    groups = local.artist_groups(_groups_core(refs, tracks), tmp_path / "local.db")
    assert groups == [
        {"name": "AC/DC", "uris": ["u3", "u1", "u2"]},          # tagged first, then most tracks
        {"name": "Diana Krall", "uris": ["u7", "u6"]},
        {"name": "Led Zeppelin", "uris": ["u5", "u4"]},         # not shouting beats more tracks
    ]


def test_typos_group_only_when_tidal_agrees(tmp_path):
    db = tmp_path / "local.db"
    refs = [("b1", "David Bowie"), ("b2", "Davis Bowie"),
            ("p1", "Berliner Philharmoniker, John Williams"), ("p2", "Wiener Philharmoniker, John Williams"),
            ("e1", "Bill Evans"), ("e2", "Bill Evans & Jim Hall")]
    for uri, artist_id in {"b1": "1", "b2": "1", "p1": "7", "p2": "8", "e1": "1158", "e2": "1158"}.items():
        local._cached_match(db, f"local:artist:{uri}", lambda a=artist_id: {"artist_id": a, "name": "", "method": "isrc"})
    refs = [(f"local:artist:{u}", n) for u, n in refs]
    groups = local.artist_groups(_groups_core(refs, {}), db)
    # Bowie's typo joins; the orchestras (different Tidal artists) and the duo (not close) don't.
    assert groups == [{"name": "David Bowie", "uris": ["local:artist:b1", "local:artist:b2"]}]


def test_no_groups(tmp_path):
    assert local.artist_groups(_groups_core([("u1", "Cream")], {}), tmp_path / "local.db") == []

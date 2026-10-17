<h1 align="center">mopidy-goodies</h1>

<p align="center">
  <b>The extras a hi-fi Mopidy server is missing</b> — what really reaches your DAC,
  your listening stats, Tidal credits and reviews, covers and portraits for your own files.
</p>

<p align="center">
  <a href="https://github.com/prietus/mopidy-goodies/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/prietus/mopidy-goodies?sort=semver"></a>
  <a href="https://github.com/prietus/mopidy-goodies/actions/workflows/packages.yml"><img alt="Packages" src="https://github.com/prietus/mopidy-goodies/actions/workflows/packages.yml/badge.svg"></a>
  <img alt="Mopidy 3.4 and 4" src="https://img.shields.io/badge/Mopidy-3.4%20%7C%204-ff6c00">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue">
</p>

[Mopidy](https://mopidy.com/) plays your music files and Tidal beautifully, but
its API stops short of what a good remote needs. **mopidy-goodies** is a Mopidy
extension that adds it, as plain HTTP endpoints under `/goodies/` on the port
Mopidy already uses. Install it on the machine that runs Mopidy; your apps light
up the extra features on their own, and simply hide them on servers without it.

## What it adds

| | |
|---|---|
| 🎚️ **Signal path** | The real format reaching the DAC (from ALSA, not the file's tags) and whether the chain is **bit-perfect**. A warning when the DAC is unplugged or off. |
| 📊 **Listening stats** | Every play is recorded on the server: totals, top artists, albums, genres and labels, by day of the week and hour, most played and recent — for any week, month, year or all time. |
| 🎼 **Tidal, deeper** | Album **credits** (producers, engineers, every musician), editorial **reviews**, artist **biographies**, **favourites**, **radios** from any track or artist, and **playlists** you can create and edit. |
| 💿 **Your own files** | Pressing data from the tags (label, catalogue number, country), credits, and the same review and biography from the matching Tidal album. **Portraits** for local artists, artists spelled several ways shown **as one**, and every image in an album's folder (scans of the booklet, OBI, disc…) to **pick the cover** from. |
| 🔄 **Library scans** | Run `mopidy local scan` from the app, with progress — no SSH. |
| 🎵 **Visualizer feed** | Raw PCM over a WebSocket for spectrum displays (optional). |

Everything that needs Tidal uses the session [mopidy-tidal](https://github.com/EbbLabs/mopidy-tidal)
already has: no extra login. Stats, signal path and local features work with any backend.

## Apps that use it

<table>
<tr>
<td width="50%" valign="top">

### Pymote

A hi-fi remote for Mopidy for **iPhone and iPad**: your files and Tidal in one
library, credits, reviews, synced lyrics, stats, Identify Song and the signal
path, bit-perfect included.

<a href="https://apps.apple.com/app/pymote-mopidy-hifi-remote/id6817096653">App Store</a> ·
<a href="https://pymote.priet.us">pymote.priet.us</a>

</td>
<td width="50%" valign="top">

### mopytui

A feature-rich **terminal** client for Mopidy: cover art in the terminal (Kitty,
iTerm2, Sixel), Tidal, lyrics, credits, stats and a spectrum visualizer.

<a href="https://github.com/prietus/mopytui">github.com/prietus/mopytui</a>

</td>
</tr>
</table>

Building your own client? The [endpoints](#endpoints) below are plain JSON over HTTP;
start with [`/goodies/_health`](#discovery) to see what a server offers.

## Install

On the machine that runs Mopidy, one command installs Mopidy (if it's missing),
mopidy-goodies and, if you ask, mopidy-local and mopidy-tidal:

```sh
curl -fsSL https://raw.githubusercontent.com/prietus/mopidy-goodies/master/install.sh | sh -s -- --local --tidal
```

Leave out `--local` (your own music files) or `--tidal` (Tidal streaming) if you
don't use them. Works on **Debian 13, Ubuntu 24.04+, Fedora 44+ and Arch Linux**;
it takes care of what each distribution lacks (see [Notes per distribution](#notes-per-distribution)).

Then restart Mopidy: `sudo systemctl restart mopidy`, or
`systemctl --user restart mopidy` if it runs as your user.

<details>
<summary><b>Or install a package yourself</b></summary>

Every [release](https://github.com/prietus/mopidy-goodies/releases/latest) has
packages, built and tested on each distribution with its own Mopidy:

| Distribution | Mopidy it ships | Install |
|---|---|---|
| Debian 13 · Ubuntu 24.04+ | 3.4 | `sudo apt install ./mopidy-goodies_X.Y.Z_all.deb` |
| Fedora 44+ | 4 | `sudo dnf install ./mopidy-goodies-X.Y.Z-1.fc44.noarch.rpm` |
| Arch Linux | 4 | `sudo pacman -U mopidy-goodies-X.Y.Z-1-any.pkg.tar.zst`, or from the AUR: [`mopidy-goodies-git`](https://aur.archlinux.org/packages/mopidy-goodies-git) |

Anywhere else, with pip into the Python that runs Mopidy:

```sh
pip install "mopidy-goodies[tidal] @ git+https://github.com/prietus/mopidy-goodies.git"
```

</details>

### Check it's working

```sh
curl http://localhost:6680/goodies/_health
```

lists the version and the features available on this server.

## Configure

mopidy-goodies works without any configuration. It's on by default; to turn it
off, or to enable the optional visualizer feed:

```ini
[goodies]
enabled = true
# visualizer_udp_port = 5555   # see "Visualizer feed" below
```

For **Tidal**, mopidy-tidal needs its own section. With `login_method = AUTO`,
Pymote and mopytui show the link to authorize the server the first time:

```ini
[tidal]
enabled = true
quality = LOSSLESS
login_method = AUTO
```

For a **bit-perfect** path, send Mopidy straight to your DAC and leave volume to
the DAC:

```ini
[audio]
mixer = none
output = alsasink device=hw:CARD=YourDAC
```

(`aplay -l` lists your card names.)

### Updating Tidal

Tidal changes its API now and then, and an old `tidalapi` stops working: Tidal
browsing comes up empty. `/goodies/_health` reports the installed versions under
`tidal`. To update, run the install command again with `--tidal` (on Arch:
`yay -S python-mopidy-tidal-git`) and restart Mopidy.

### Notes per distribution

- **Debian 13** — its `mopidy-local` doesn't start on Python 3.13 (it needs
  `imghdr`, which 3.13 removed): `install.sh --local` adds the
  [`standard-imghdr`](https://pypi.org/project/standard-imghdr/) backport.
- **Debian, Ubuntu, Fedora** — none of them packages mopidy-tidal, so it comes
  from PyPI. Some of `tidalapi`'s dependencies don't build from source on recent
  Python; `install.sh --tidal` takes those from the distribution instead.
- **Fedora** — doesn't package mopidy-local either; `--local` installs it from PyPI.
- **Arch** — mopidy-local and mopidy-tidal ([`python-mopidy-tidal-git`](https://aur.archlinux.org/packages/python-mopidy-tidal-git))
  are in the AUR: the script uses `yay` or `paru` if you have one.

## Endpoints

For client developers: everything is JSON over HTTP, under `/goodies/` on Mopidy's HTTP port.

### Discovery

```
GET    /goodies/_health
```

Returns version + which features are active. Use this to decide which UI
features to show in your client.

```json
{
  "version": "0.7.0",
  "features": {
    "favorites": true,
    "favorites_active": true,
    "credits": true,
    "texts": true,
    "isrc": true,
    "radio": true,
    "playlists": true,
    "stats": true,
    "audio": true,
    "visualizer": false,
    "visualizer_transport": null,
    "library_scan": true,
    "local_metadata": true,
    "local_artists": true,
    "local_artist_groups": true,
    "local_artwork": true
  },
  "tidal": { "session": "ok", "mopidy_tidal": "0.3.13", "tidalapi": "0.8.11" }
}
```

`visualizer` is `true` when the visualizer feed is set up; `visualizer_transport`
says how (`udp`, or the older `fifo`). See [the visualizer section](#visualizer-feed-websocket).

`favorites_active` is `false` when `mopidy-tidal` isn't loaded *or* isn't
logged in. When clients hit the favorites endpoints in that state they get:

- `503` — `mopidy-tidal` backend not loaded at all (server-side config).
- `403` — backend loaded but no authenticated Tidal session; the operator
  needs to play any Tidal track in mopidy (e.g. via Iris or `mopidy-mpd`) to
  trigger mopidy-tidal's OAuth flow, then retry.

`tidal.session` is `ok`, `logged_out` or `missing` (mopidy-tidal not loaded), with
the installed `mopidy_tidal` and `tidalapi` versions (or `null`): when Tidal stops
working, clients can suggest updating rather than show an empty library.

`stats` and `audio` work for any backend (independent of Tidal).
`library_scan` is `true` when `mopidy-local` is enabled.

### Favorites

```
GET    /goodies/favorites/albums
POST   /goodies/favorites/albums          {"id": "<tidal album id>"}
DELETE /goodies/favorites/albums/<id>
```

Same shape for `tracks`, `artists`, `playlists`. The `id` is the Tidal numeric
id — for an album whose Mopidy URI is `tidal:album:12345`, send `"12345"`.

Responses:
- `GET` → `200` with JSON array of `{id, name, artist?}` summaries.
- `POST`/`DELETE` → `204` on success.
- `503` if `mopidy-tidal` isn't loaded.
- `403` if `mopidy-tidal` is loaded but the Tidal session isn't authenticated.
  The body carries an `error` field describing how to recover (play a Tidal
  track in mopidy to trigger its login flow).

### Stats

Listening history captured on every `track_playback_ended` event from any
Mopidy backend (Tidal, local, file, podcast, ...). Stored in SQLite under
`<mopidy data_dir>/goodies/history.db`.

```
GET /goodies/stats/recent?limit=50
GET /goodies/stats/most-played?limit=50&since=<unix>
GET /goodies/stats/top-artists?limit=10&since=<unix>
GET /goodies/stats/top-albums?limit=10&since=<unix>
GET /goodies/stats/by-genre?limit=20&since=<unix>
GET /goodies/stats/top-labels?limit=10&since=<unix>
GET /goodies/stats/by-day-of-week?since=<unix>
GET /goodies/stats/by-hour?since=<unix>
GET /goodies/stats/totals?since=<unix>
```

Every endpoint except `recent` takes an optional `since` (unix seconds) to
limit it to a period — e.g. the last week, month or year — so a client can
offer Week / Month / Year / All Time views.

`top-*` and `by-*` aggregations all rank by total played time. The
`by-day-of-week` and `by-hour` endpoints bucket in the **server's local
timezone** (so "Sunday peak" reflects the user's actual Sunday). Days are
0=Sunday..6=Saturday (sqlite `%w` convention).

A play is marked `completed` if it ran ≥50% of the track length OR ≥4 minutes
(Last.fm-style scrobble rule).

Genre and album cover URI are captured from Mopidy's Track model. The record
label is read from the file's own tags for `mopidy-local` tracks (same reader
as the local album metadata, so it needs mutagen); other backends don't expose
one, so `top-labels` only counts local plays. Plays recorded by an older
version of this plugin have NULL in those columns — they still count towards
totals / top artists / top albums, just not towards genres, labels or covers.

### Audio output

```
GET /goodies/audio/output
```

Returns the configured GStreamer sink and, when it's `alsasink`, resolves
the human-readable card name from `/proc/asound/cards`:

```json
{
  "sink": "alsasink",
  "device": "hw:1,0",
  "card": {
    "index": 1,
    "id": "D90III",
    "name": "Topping D90 III SABRE"
  }
}
```

For non-ALSA sinks (`pulsesink`, `pipewiresink`, `autoaudiosink`, …) or
when the card can't be identified (`device=default`, unknown index, non-
Linux host), `card` is `null` and clients should fall back to the raw
`device` string. Returns `null` (200 with body `null`) when no `audio.output`
is configured.

```
GET /goodies/audio/active
```

Combined runtime + static view of the audio chain. `format` is read live from
`/proc/asound/card<N>/pcm<DEV>p/sub0/hw_params` — what ALSA is actually
receiving right now. `chain` is a static analysis of the configured pipeline.

```json
{
  "output": {
    "sink": "alsasink",
    "device": "hw:CARD=SABRE,DEV=0",
    "card": { "index": 0, "id": "SABRE", "name": "D90 III SABRE" }
  },
  "active": true,
  "format": { "rate": 44100, "bits": 32, "channels": 2, "alsa_format": "S32_LE" },
  "chain": {
    "direct_hw": true,
    "no_mixer": true,
    "no_resample": true,
    "no_convert": true,
    "verdict": "bit-perfect"
  }
}
```

`chain.verdict` is one of:

- `"bit-perfect"` — `alsasink` bound directly to `hw:` (no `plughw:`, no
  `dmix`/`dsnoop`), `mixer = none`, no `audioresample`/`audioconvert` in the
  GStreamer bin spec.
- `"not-bit-perfect"` — at least one of the conditions above fails. It is also
  reported when the playing track is a DSD file (`.dsf`/`.dff`) and ALSA is
  receiving PCM: GStreamer decodes DSD to PCM (DSD64 → `S32_LE` @ 352.8 kHz),
  which is not a bit-perfect path. `chain.reason` is then
  `"dsd-decoded-to-pcm"`. If ALSA really receives DSD (`alsa_format` starting
  with `DSD_`) the verdict is not downgraded.
- `"unknown"` — non-ALSA sink (`pulsesink`, `pipewiresink`, `autoaudiosink`,
  …) where bit-perfect-ness depends on the sound server's own config, which
  we can't see from here.

When playback is paused/stopped, `active` is `false` and `format` is `null`,
but `chain` still reports.

`format.bits` is the **container** width that ALSA exposes (e.g. 24-bit PCM
streamed in an `S32_LE` container reports `32` here). The source bit depth
isn't recoverable from `/proc/asound`. `alsa_format` is the raw token, useful
for distinguishing DSD (`DSD_U32_BE`) from PCM (`S32_LE`).

### Visualizer feed (WebSocket)

```
WS /goodies/audio/visualizer
```

Streams the audio as raw PCM — **S16LE, 44.1 kHz, stereo**, the convention
clients assume — to every connected client, one binary message per chunk.
Clients run their own FFT for a spectrum/bar display. (mopytui, which can also
take the stream straight from Mopidy over UDP, uses the same format.)

**Setup** (opt-in). Split `[audio] output` with a `tee`: one branch goes to your
DAC untouched, the other is converted for the visualizer and sent to goodies
over UDP on the same machine:

```ini
[audio]
mixer = none
output = tee name=t  t. ! queue ! alsasink device=hw:CARD=SABRE,DEV=0  t. ! queue leaky=downstream max-size-buffers=200 ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=44100,channels=2 ! udpsink host=127.0.0.1 port=5555 sync=false async=false

[goodies]
visualizer_udp_port = 5555
```

(One line: `output` doesn't survive INI line continuations well. Put your own
`alsasink` device in the first branch.) Restart Mopidy; `/goodies/_health`
reports `"visualizer": true` and `"visualizer_transport": "udp"`.

**It doesn't touch what reaches your DAC.** Measured on a real server (FLAC
16/44.1 and 24/96, DSF), capturing the DAC branch in the DAC's own format: the
bytes are identical with and without the visualizer branch, and identical to the
decoded FLAC. The conversion only happens on the visualizer's branch, and
`/goodies/audio/active` still reports `bit-perfect` — it only looks at the
branch that feeds `alsasink`.

**Why UDP, not a FIFO.** Earlier versions wrote the visualizer branch to a named
pipe (`filesink location=…` + `visualizer_fifo`). That still works, but a pipe
can stall playback: with nothing reading it, Mopidy hangs as soon as you press
play; with a reader that stops reading, the pipe fills up and the track never
ends, so the queue doesn't advance. Sending over UDP never blocks and needs no
receiver: if goodies isn't listening the packets are simply dropped and the
music plays on. To move over, replace the FIFO branch's `filesink …` with the
`udpsink …` above, and `visualizer_fifo` with `visualizer_udp_port`.

### Tidal album credits

```
GET    /goodies/tidal/albums/<id>/credits
```

Per-track credits for a Tidal album — producers, composers, engineers and
every musician with their instrument. `tidalapi` has no wrapper for this, so
goodies calls the same endpoint the Tidal apps use through mopidy-tidal's
session. Same `503`/`403` rules as favorites; `404` if Tidal doesn't know the
album. Responses are cached in memory (credits don't change).

```json
{
  "album_id": "158152",
  "tracks": [
    {
      "id": "158153",
      "title": "That Old Feeling",
      "version": null,
      "track_num": 1,
      "volume_num": 1,
      "credits": [
        { "role": "Producer", "contributors": [{ "name": "Richard Bock", "id": "8021563" }] },
        { "role": "Composer", "contributors": [{ "name": "Lew Brown", "id": null }] }
      ]
    }
  ]
}
```

`role` is Tidal's label as-is (`Producer`, `Composer`, `Drum Kit`,
`Trumpet`, …). `id` is the contributor's Tidal artist id when they have one.

### Tidal album review & artist biography

```
GET    /goodies/tidal/albums/<id>/review
GET    /goodies/tidal/artists/<id>/bio
```

Editorial texts from Tidal, as plain text: Tidal-internal links
(`[wimpLink …]label[/wimpLink]`) keep their label and `<br/>` becomes a
newline. `source` (e.g. `TiVo`) is passed through so clients can credit it.
Same `503`/`403` rules as favorites; `404` when Tidal has no review for the
album or doesn't know the artist. Cached in memory.

```json
{ "album_id": "158152", "text": "…", "source": "TiVo", "last_updated": "2026-08-26T12:06:00.780+0000" }
```

The bio also carries the artist's name and a 750×750 picture URL; `text` is
`null` if the artist has a picture but no biography.

```json
{
  "artist_id": "1301",
  "name": "Chet Baker",
  "image": "https://resources.tidal.com/images/7f74…/750x750.jpg",
  "text": "Chet Baker was a primary exponent of the West Coast school of cool jazz…",
  "source": "TiVo",
  "last_updated": "2026-09-27T06:52:45.587+0000"
}
```

### Tidal tracks by ISRC

```
GET    /goodies/tidal/isrc/<ISRC>
```

Tidal tracks for a recording's ISRC — for example one identified with Shazam /
ShazamKit — so a client can play exactly that recording. The same ISRC is on
the original album, reissues, compilations and soundtracks; Tidal's lookup
doesn't say which is which, so results are ranked by album name: plain albums
first, then editions ("Deluxe", "Remaster", "Live"…), then soundtracks, then
compilations. `404` if Tidal has no available track for it. Cached in memory.

```json
{
  "isrc": "GBUM71029604",
  "tracks": [
    { "uri": "tidal:track:8992:534050382:534050397", "title": "Bohemian Rhapsody", "artist": "Queen",
      "artist_uri": "tidal:artist:8992", "album": "A Night At The Opera", "album_uri": "tidal:album:534050382" }
  ]
}
```

### Tidal radio

```
GET    /goodies/tidal/radio?uri=<track or artist URI>&limit=100
```

A Tidal "radio" — up to 100 similar tracks — seeded from **any** Mopidy track
or artist URI, so a client can offer *Start Radio* everywhere. mopidy-tidal
doesn't expose Tidal's radios; tidalapi does:

- `tidal:track:…` and `tidal:artist:<id>` seed it directly.
- `local:track:…` finds the same recording on Tidal by the file's `ISRC` tag,
  or by artist + title when there's none.
- `local:artist:…` searches Tidal for the artist's name (exact match only).

`404` when the seed can't be found on Tidal. Unavailable tracks are dropped;
results are cached in memory.

```json
{
  "seed": { "kind": "track", "title": "Bohemian Rhapsody", "artist": "Queen" },
  "tracks": [
    { "uri": "tidal:track:…", "title": "Stairway to Heaven (Remaster)", "artist": "Led Zeppelin",
      "album": "Led Zeppelin IV (Remaster)", "album_uri": "tidal:album:…" }
  ]
}
```

### Tidal playlists

```
GET    /goodies/tidal/playlists
POST   /goodies/tidal/playlists                 { "name": "Road Trip" }
GET    /goodies/tidal/playlists/<id>
PATCH  /goodies/tidal/playlists/<id>            { "name": "…" }
DELETE /goodies/tidal/playlists/<id>
POST   /goodies/tidal/playlists/<id>/tracks     { "uris": ["tidal:track:…", "local:track:…"] }
DELETE /goodies/tidal/playlists/<id>/tracks/<index>
POST   /goodies/tidal/playlists/<id>/move       { "from": 3, "to": 0 }
```

Create, rename, delete and edit your Tidal playlists. mopidy-tidal implements
Mopidy's playlist API too, but it caches playlists and never sees its own
renames, and removing a track right after a rename fails with HTTP 412 (a
stale ETag). Every call here works on a playlist fetched fresh from Tidal, so
edits apply and reads reflect them — play a playlist from the track URIs
returned here rather than from `tidal:playlist:…` through Mopidy.

- The list has your own playlists (`editable: true`) and then the ones you
  follow (`editable: false`, read-only; `DELETE` unfollows them).
- `…/tracks` appends Tidal *or local* tracks: local files are matched to the
  same recording on Tidal (ISRC tag, else artist + title), as for radio. It
  answers `{"added": n, "skipped": [uris not found on Tidal]}`.
- `404` for unknown playlists or positions, `403` when editing one you follow.

### Local album metadata

```
GET    /goodies/local/album?uri=local:album:…
DELETE /goodies/local/album/match?uri=local:album:…
```

For `mopidy-local` albums: reads the album's own files (Picard-style Vorbis
comments in FLAC, ID3 in DSF — as written by e.g. MusicBrainz Picard or
drtagger) with [mutagen](https://mutagen.readthedocs.io/), and matches the
music to Tidal so clients can show Tidal's review, artist bio and credits.

```json
{
  "uri": "local:album:md5:…",
  "edition": {
    "label": "Sony", "catalog_number": "SICP-1704", "barcode": "4547366035155",
    "country": "JP", "media": "CD", "musicbrainz_album_id": "…"
  },
  "tidal": { "album_id": "35986307", "artist_id": "8992", "title": "If You Want Blood You've Got It (Live)",
             "method": "isrc", "score": 1.0, "same_album": true },
  "credits_source": "tidal",
  "credits": { "album_id": "local:album:md5:…", "tracks": [ { "id": "local:track:…", "isrc": "…", "credits": [ … ] } ] }
}
```

- **Tidal match**, strongest first: `BARCODE` → Tidal album by UPC (exact
  edition, but Tidal often lacks regional pressings); then the tracks' `ISRC`s
  → vote for the Tidal album sharing the most recordings (`score` = share of
  looked-up ISRCs found on it; an album with the same title beats a compilation
  sharing more tracks); then `MUSICBRAINZ_ALBUMID` → barcode on MusicBrainz →
  UPC; finally a Tidal search accepting only a near-exact artist + title.
  `same_album` is `false` when the best Tidal album only shares recordings (a
  compilation, box set or soundtrack): show its review/bio only when `true`.
  Matches are stored in `data_dir/goodies/local.db`; misses
  are retried after a week. `DELETE …/match` forgets one (e.g. after retagging).
- **Credits** use the same shape as `/tidal/albums/<id>/credits`, keyed by local
  track URI. Tag credits (`PERFORMER` as "Name (instrument)", `COMPOSER`,
  `PRODUCER`, `ENGINEER`, `MIXER`…) win; tracks without them borrow the Tidal
  credits of the same recording (by ISRC) or, when `same_album`, of the track
  with the same title. `credits_source` is `tags`, `tidal`,
  `mixed` or `null`.
- Works without Tidal (`tidal` is then `null`). `503` if `mopidy-local`'s
  media dir or mutagen isn't available; `404` if the album has no local files.

### Local artists

```
GET    /goodies/local/artist?uri=local:artist:…
DELETE /goodies/local/artist/match?uri=local:artist:…
```

`mopidy-local` has no artist images. This matches a local artist to Tidal and
returns the Tidal artist's portrait and biography, so clients can show them as
they do for Tidal artists.

```json
{
  "uri": "local:artist:md5:…",
  "name": "Miles Davis",
  "tidal": { "artist_id": "55", "name": "Miles Davis", "method": "isrc" },
  "image": "https://resources.tidal.com/images/…/750x750.jpg",
  "text": "…",
  "source": "TiVo"
}
```

- **Match**: the `ISRC` tags of up to four of the artist's tracks (one per
  album first) → the Tidal artist credited on those recordings, by vote;
  otherwise a Tidal artist search accepting only the exact name (a trailing
  role such as "Name(piano)" is ignored). Placeholders like "Various Artists"
  are never matched. Stored with the album matches in `local.db`, misses
  retried after a week; `DELETE …/match` forgets one (e.g. it picked a namesake).
- `tidal`, `image`, `text` and `source` are `null` when there's no match, Tidal
  has no picture/bio, or Tidal isn't logged in. Same `503` / `404` as albums.

### Local artist groups

```
GET    /goodies/local/artists
```

`mopidy-local` makes one artist out of each spelling *and* MusicBrainz id it
finds, so a band whose albums are tagged differently shows up several times:
"AC/DC" with and without a MusicBrainz id, "AC-DC", "LED ZEPPELIN"… This lists
which local artists are the same one, so clients can show them once:

```json
{ "groups": [ { "name": "AC/DC", "uris": ["local:artist:md5:…", "local:artist:md5:…", "local:artist:md5:…"] } ] }
```

- Spellings that differ only in case, accents, punctuation, a leading "The",
  "&" vs "and" or stray trailing separators always group.
- Real typos ("Led Zepplin") group only when the names are at least 85 % alike
  **and** both were already matched to the same Tidal artist by
  `/local/artist` — closeness alone would merge "Berliner Philharmoniker" with
  "Wiener Philharmoniker". No matching happens here, so a typo joins its group
  once both spellings have been looked up.
- `name` is the spelling to show (MusicBrainz-tagged first, then not in all
  caps, then most tracks); its URI comes first. Only groups of two or more.
  Nothing is renamed: fix the tags and rescan for a permanent cure.

### Local album artwork

```
GET    /goodies/local/album/artwork?uri=local:album:…
PUT    /goodies/local/album/artwork?uri=local:album:…   { "url": "<one of its images>" }
DELETE /goodies/local/album/artwork?uri=local:album:…
GET    /goodies/local/artwork
GET    /goodies/local/artwork/file?path=<relative to media_dir>
```

`mopidy-local` only knows the images embedded in the files, and rips often embed
something odd while the folder has proper scans. This lists both and lets you
pick the cover — stored on the server, so every client shows the same one.

```json
{
  "uri": "local:album:md5:…",
  "selected": "/goodies/local/artwork/file?path=rips%2F…%2FCovers%2FCover_Face.jpg",
  "images": [
    { "url": "/local/cb0e…-419x413.jpeg", "name": "Embedded 1", "source": "embedded", "width": 419, "height": 413 },
    { "url": "/goodies/local/artwork/file?path=…", "name": "Cover_Face.jpg", "source": "folder", "width": null, "height": null }
  ]
}
```

- **Folder images** come from the folders of the album's tracks (plus their
  parent for per-disc folders like `CD1`), two levels down (`Covers/`,
  `Scans/Booklet/`…): likely front covers first, then in natural order. URLs are
  server-relative, like mopidy-local's own.
- `PUT` picks one of the album's image URLs (`400` otherwise), `DELETE` goes
  back to mopidy-local's. `GET /local/artwork` returns
  `{"covers": {album_uri: url}}` for every pick, so clients can apply them
  wherever covers show.
- `…/file` serves images only, never outside `media_dir`.

### Library scan

```
POST   /goodies/library/scan        { "force": false }
GET    /goodies/library/scan
```

Rescans `mopidy-local`'s media dir without shelling into the host. It runs
`mopidy local scan` as a child process, forwarding the `--config`/`--option`
arguments the running Mopidy was started with, so the scan uses the same
`[local]` settings. `mopidy-local` reads its SQLite library on every request,
so new albums show up to clients as soon as the scan finishes — no Mopidy
restart needed.

`POST` answers `202` with the status below, `409` if a scan is already
running, or `503` if `mopidy-local` isn't enabled. `{"force": true}`
rescans every file instead of only new/modified ones. Poll `GET` for
progress:

```json
{
  "running": false,
  "force": false,
  "started_at": 1759080000,
  "finished_at": 1759080005,
  "exit_code": 0,
  "to_scan": 91,
  "scanned": 91,
  "removed": 0,
  "error": null
}
```

`to_scan` is how many files need (re)scanning; it's `null` until the scan
has compared the media dir with the library. `error` holds the last lines
of the scan's output when it exits non-zero.

## History

- **0.13** — the visualizer feed over UDP: unlike a FIFO it can't stall playback.
  Measured: the visualizer branch leaves the DAC's bytes untouched.
- **0.12** — packages for Debian, Ubuntu, Fedora and Arch, built and tested on
  each one, and `install.sh`. Mopidy 3.4 supported; mopidy-tidal optional.
  `_health` reports the Tidal session and versions.
- **0.11** — local album artwork: every image in an album's files and folders,
  and a chosen cover shared by every client.
- **0.10** — local artists spelled several ways grouped as one.
- **0.9** — portraits and biographies for local artists.
- **0.8** — library scans over HTTP; Tidal credits, reviews, biographies, radio
  and playlists; local album metadata matched to Tidal.
- **0.7** — visualizer feed.
- **0.6** — renamed `mopidy-tidal-goodies` → `mopidy-goodies`.
- **0.5** — live ALSA parameters and the bit-perfect verdict.
- **0.1 – 0.4** — Tidal favourites, listening history and stats, audio output info.

## Migrating from `mopidy-tidal-goodies`

v0.6.0 is a rename. The HTTP API surface and JSON shapes are unchanged;
only paths, config section, and the on-disk db location moved. On your
Mopidy host:

```sh
# stop mopidy
sudo systemctl stop mopidy

# rename the config section in mopidy.conf
#   [tidal_goodies]  →  [goodies]

# move the stats DB so history is preserved
mv <data_dir>/tidal_goodies <data_dir>/goodies

# replace the old package
pip uninstall mopidy-tidal-goodies
# then install mopidy-goodies as described in "Install" above

sudo systemctl start mopidy
```

Clients hitting `/tidal_goodies/...` need to be updated to `/goodies/...`.

## License

Apache 2.0 — see [LICENSE](LICENSE).

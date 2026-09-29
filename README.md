# Mopidy-Goodies

HTTP companion endpoints for [Mopidy](https://mopidy.com/) that fill in gaps
the core and its extensions don't expose: Tidal favorites (via
[mopidy-tidal](https://github.com/EbbLabs/mopidy-tidal)), backend-agnostic
listening stats, and live audio chain info (configured sink, ALSA params,
bit-perfect verdict).

It's a companion package — it does **not** replace any other Mopidy
extension. Tidal-specific endpoints reuse the session that `mopidy-tidal`
has already authenticated, so clients don't need their own OAuth flow.
Stats and audio endpoints work with any backend.

> **Renamed from `mopidy-tidal-goodies` (v0.6.0).** Stats and audio endpoints
> are backend-agnostic, so the old name was misleading. See the migration
> note at the bottom of this README if you're upgrading.

## Why this exists

Adding everything upstream is slow (review cycles, maintainer scope), and a
fair amount of what's here is too client-specific or host-specific to belong
in any single extension. This package fills the gap on your own server, on
your own release cadence.

## Install

On your Mopidy host:

```sh
pip install git+https://github.com/prietus/mopidy-goodies.git
```

Then enable in `mopidy.conf`:

```ini
[goodies]
enabled = true
```

Restart Mopidy. Endpoints are mounted under `/goodies/` on whatever
port your `[http]` extension is bound to (typically `6680`).

## Endpoints

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
    "library_scan": true,
    "local_metadata": true
  }
}
```

`visualizer` is `true` only when `[goodies] visualizer_fifo` points at a
named pipe that exists. See [the visualizer section](#visualizer-feed-websocket).

`favorites_active` is `false` when `mopidy-tidal` isn't loaded *or* isn't
logged in. When clients hit the favorites endpoints in that state they get:

- `503` — `mopidy-tidal` backend not loaded at all (server-side config).
- `403` — backend loaded but no authenticated Tidal session; the operator
  needs to play any Tidal track in mopidy (e.g. via Iris or `mopidy-mpd`) to
  trigger mopidy-tidal's OAuth flow, then retry.

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
- `"not-bit-perfect"` — at least one of the conditions above fails.
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

Streams raw PCM chunks from a named pipe to all connected clients, one
binary WebSocket message per chunk (~4 KiB ≈ 23 ms at 44.1 kHz stereo).
Clients run their own FFT — typical use is a spectrum/bar visualizer in
mopytui (terminal) or mopyrust (Tauri).

**Operator setup** (three steps; visualizer is opt-in):

1. In `mopidy.conf`, branch `[audio] output` with a `tee` so one rama
   drives `alsasink` (keep this one bit-perfect) and the other writes
   PCM to a FIFO. Format on the FIFO branch is up to you, but `S16LE @
   44100, stereo` is the convention clients assume:

   ```ini
   [audio]
   output = tee name=t
     t. ! queue ! alsasink device=hw:CARD=SABRE,DEV=0 buffer-time=200000
     t. ! queue leaky=downstream max-size-buffers=200
        ! audioconvert ! audioresample
        ! audio/x-raw,format=S16LE,rate=44100,channels=2
        ! filesink location=/tmp/mopidy.fifo sync=false
   ```

   (Same line in INI — wrapping is for readability.)

2. Create the FIFO once:

   ```sh
   mkfifo /tmp/mopidy.fifo
   ```

3. Point goodies at it:

   ```ini
   [goodies]
   visualizer_fifo = /tmp/mopidy.fifo
   ```

Restart Mopidy. `/goodies/_health` now reports `"visualizer": true`. The
WS endpoint accepts connections immediately but only emits data while
Mopidy is actually playing (no writer on the FIFO → reader blocks
quietly, no error).

**Caveats**:

- The FIFO is **single-reader** at the kernel level; goodies opens it
  once and fans out to every WS client, so multiple subscribers are fine
  — but don't have another consumer (e.g. a local `cava`) opening the
  same FIFO at the same time, or kernel splits the bytes between them.
- The visualizer branch puts `audioresample`/`audioconvert` into the
  pipeline, so `/goodies/audio/active` will report `verdict =
  "not-bit-perfect"` even though the DAC's own branch is untouched. The
  static chain check is text-based and can't tell that those elements
  live on a side branch. Known limitation.

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

## Roadmap

- **v0.1** — favorites.
- **v0.2** — listening history (recent / most-played / totals).
- **v0.3** — aggregated stats (top artists/albums/genres, day-of-week, hour-of-day).
- **v0.4** — audio output device info.
- **v0.5** — live ALSA params + bit-perfect chain analysis. (0.5.1 splits 503/403 for not-loaded vs not-logged-in.)
- **v0.6** — package renamed `mopidy-tidal-goodies` → `mopidy-goodies`; ext_name `tidal_goodies` → `goodies`.
- **v0.7** — visualizer feed: WebSocket streaming raw PCM from a FIFO branch.
- **v0.8** — trigger `mopidy local scan` over HTTP, with progress; Tidal album credits, reviews and artist bios; local album metadata + Tidal matching. *(current)*
- **v0.9** — mutable Tidal playlists (create / add / remove / reorder).
- **v0.10** — discovery: Your Mixes, mood radios.
- **v0.11** — admin: force session refresh, cache stats.

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
pip install git+https://github.com/prietus/mopidy-goodies.git

sudo systemctl start mopidy
```

Clients hitting `/tidal_goodies/...` need to be updated to `/goodies/...`.

## License

Apache 2.0 — see [LICENSE](LICENSE).

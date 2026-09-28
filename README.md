# Mopidy-Goodies

[![PyPI](https://img.shields.io/pypi/v/mopidy-goodies)](https://pypi.org/project/mopidy-goodies/)

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
    "stats": true,
    "audio": true,
    "visualizer": false,
    "library_scan": true
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
GET /goodies/stats/by-day-of-week
GET /goodies/stats/by-hour
GET /goodies/stats/totals
```

`top-*` and `by-*` aggregations all rank by total played time. The
`by-day-of-week` and `by-hour` endpoints bucket in the **server's local
timezone** (so "Sunday peak" reflects the user's actual Sunday). Days are
0=Sunday..6=Saturday (sqlite `%w` convention).

A play is marked `completed` if it ran ≥50% of the track length OR ≥4 minutes
(Last.fm-style scrobble rule).

Genre and album cover URI are captured from Mopidy's Track model. Plays
recorded by an older version of this plugin will have NULL there — those rows
contribute to totals/top-artists/top-albums but not to top-genres or covers.

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
- **v0.8** — trigger `mopidy local scan` over HTTP, with progress; Tidal album credits. *(current)*
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

# Mopidy-Tidal-Goodies

[![PyPI](https://img.shields.io/pypi/v/mopidy-tidal-goodies)](https://pypi.org/project/mopidy-tidal-goodies/)

HTTP endpoints for things [mopidy-tidal](https://github.com/EbbLabs/mopidy-tidal)
doesn't (yet) expose: favoriting, mutable playlists, personalized mixes.

It's a companion package — it does **not** replace `mopidy-tidal`. It reuses
the Tidal session that `mopidy-tidal` has already authenticated, so clients
talking to it don't need their own OAuth flow or Tidal credentials.

## Why this exists

`mopidy-tidal` covers browse + search + playback well, but the Tidal API
surface is much larger. Adding everything upstream is slow (review cycles,
maintainer scope), and a fair amount of it is too client-specific to belong
there. This package fills the gap on your own server, on your own release
cadence.

## Install

On your Mopidy host:

```sh
pip install git+https://github.com/prietus/mopidy-tidal-goodies.git
```

Then enable in `mopidy.conf`:

```ini
[tidal_goodies]
enabled = true
```

Restart Mopidy. Endpoints are mounted under `/tidal_goodies/` on whatever
port your `[http]` extension is bound to (typically `6680`).

## Endpoints

### Discovery

```
GET    /tidal_goodies/_health
```

Returns version + which features are active. Use this to decide which UI
features to show in your client.

```json
{
  "version": "0.5.1",
  "features": {
    "favorites": true,
    "favorites_active": true,
    "stats": true,
    "audio": true
  }
}
```

`favorites_active` is `false` when `mopidy-tidal` isn't loaded *or* isn't
logged in. When clients hit the favorites endpoints in that state they get:

- `503` — `mopidy-tidal` backend not loaded at all (server-side config).
- `403` — backend loaded but no authenticated Tidal session; the operator
  needs to play any Tidal track in mopidy (e.g. via Iris or `mopidy-mpd`) to
  trigger mopidy-tidal's OAuth flow, then retry.

`stats` and `audio` work for any backend (independent of Tidal).

### Favorites

```
GET    /tidal_goodies/favorites/albums
POST   /tidal_goodies/favorites/albums          {"id": "<tidal album id>"}
DELETE /tidal_goodies/favorites/albums/<id>
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
`<mopidy data_dir>/tidal_goodies/history.db`.

```
GET /tidal_goodies/stats/recent?limit=50
GET /tidal_goodies/stats/most-played?limit=50&since=<unix>
GET /tidal_goodies/stats/top-artists?limit=10&since=<unix>
GET /tidal_goodies/stats/top-albums?limit=10&since=<unix>
GET /tidal_goodies/stats/by-genre?limit=20&since=<unix>
GET /tidal_goodies/stats/by-day-of-week
GET /tidal_goodies/stats/by-hour
GET /tidal_goodies/stats/totals
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
GET /tidal_goodies/audio/output
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
GET /tidal_goodies/audio/active
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

## Roadmap

- **v0.1** — favorites.
- **v0.2** — listening history (recent / most-played / totals).
- **v0.3** — aggregated stats (top artists/albums/genres, day-of-week, hour-of-day).
- **v0.4** — audio output device info.
- **v0.5** — live ALSA params + bit-perfect chain analysis. *(current — 0.5.1 splits 503/403 for not-loaded vs not-logged-in)*
- **v0.6** — mutable Tidal playlists (create / add / remove / reorder).
- **v0.7** — discovery: Your Mixes, mood radios.
- **v0.8** — admin: force session refresh, cache stats.

## License

Apache 2.0 — see [LICENSE](LICENSE).

# Developing Dapple

FastAPI + React, one container, one `/data` volume. No database.

```
app/pattern.py    weights → LED bytes (pure logic, the interesting part)
app/devices.py    xled wrapper, and DeviceGroup — where per-group offsets live
app/config.py     groups and strands: config.yaml, and the Strands tab's edits
app/presets.py    /data/presets.json, written atomically
app/state.py      /data/state.json — what each group was last set to
app/models.py     pydantic models shared by the API and the stores
app/main.py       FastAPI routes + static mount
frontend/src/     React + MUI, Vite build
scripts/smoke.py  one strand, one pattern, from the command line
```

## Running from source

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest                 # 260 tests, no network, no strands

npm --prefix frontend install
npm --prefix frontend test                 # the JS pattern port vs the Python fixtures
npm --prefix frontend run dev              # UI on :5173, proxying /api to :8080

DAPPLE_DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload --port 8080
```

The API serves the built UI from `static/` (where the Docker image puts it) or `frontend/dist/`,
whichever exists. With neither, `/` explains what to build and `/docs` still works.

`docker-compose.yml` pulls the published image, because that one file is all an end user
downloads. In a checkout, `docker-compose.override.yml` adds `build: .`, so
`docker compose up -d --build` runs your working tree instead.

## Configuration

| Setting | `config.yaml` | Env var | Default |
|---|---|---|---|
| Groups and strands | `groups` | — | none until you add one |
| Frames per upload | `movie_frames` | `DAPPLE_MOVIE_FRAMES` | `1` |
| Per-request timeout (s) | `timeout` | `DAPPLE_TIMEOUT` | `5.0` |
| sRGB → PWM gamma | `gamma` | `DAPPLE_GAMMA` | `2.2` |
| Data directory | — | `DAPPLE_DATA_DIR` | `/data` |
| Config file | — | `DAPPLE_CONFIG` | `<data>/config.yaml` |
| Log level | — | `DAPPLE_LOG_LEVEL` | `INFO` |
| Host port | — | `DAPPLE_PORT` (override only) | `8080` |
| Run as uid / gid | — | `PUID` / `PGID` | `1000` |
| Log timezone | — | `TZ` | `Etc/UTC` |

```yaml
groups:
  - id: tree
    name: "Christmas tree"
    strands:
      - name: TreeTop
        host: 192.168.40.21
      - name: TreeBottom
        host: 192.168.40.22
        # Read from the strand unless pinned here. The strand is the authority:
        # the frame we upload must be exactly as long as it expects, so a count
        # that disagrees breaks the upload rather than fixing anything.
        # number_of_led: 250
        # led_profile: RGBW
```

`config.yaml` is the only way to configure strands: the Strands tab writes it, and you can
hand-write it to seed a deployment. Starting with no file at all is normal — the first strand
you add creates one.

(There used to be a `TWINKLY_HOSTS` env var for a zero-config first run. It was dropped once the
Strands tab existed: it could only ever produce a single group, so on any multi-group setup it
silently put unrelated runs of lights into one stretched pattern.)

A file written before groups existed (a flat `devices:` list) loads as a single group,
`all-strands`, which is exactly how it behaved before. Loading is side-effect free; it's
rewritten in the new shape on the first edit.

---

## How a pattern becomes bytes

Weights are arbitrary numbers, reduced by their GCD first, so `80 / 20` becomes a 5-LED
repeating unit rather than a 100-LED one.

**Interleaved** hands each LED to whichever color is furthest behind its share
(largest-remainder apportionment). 7:3 comes out `A B A A A B A A B A`, never
`A A A A A A A B B B`.

**Blocked** gives each color `weight × block_size` consecutive LEDs, then repeats.

**Gamma.** Colors arrive from the browser as sRGB, which is gamma-encoded; an LED's PWM duty
cycle is linear in emitted light. Sent raw, `#FF5000` drives four times the green it should
relative to red and the orange on screen arrives as yellow on the strand. `pack()` decodes sRGB
to linear before packing. The preview is deliberately *not* corrected — it's already in display
space.

**RGBW byte order is `W R G B`**, not `R G B W`. A strand that disagrees shows every color as a
different color entirely (orange would come out green, not merely off-hue).

**Offsets** are a prefix sum *within a group*. Strand *k* is applied with an offset equal to the
LED count of every strand before it **in that group**, which is what makes a pattern continue
across a physical join. Between groups nothing is shared: every group starts at its own LED 0.

The browser draws its preview with a JS port of the same function
([frontend/src/pattern.js](frontend/src/pattern.js)). `tests/test_pattern_parity.py` generates
fixtures from the Python implementation and the JS suite asserts the port reproduces them
exactly, so the preview can't drift from what gets uploaded. Changing the algorithm means moving
`app/pattern.py`, `frontend/src/pattern.js` and `tests/fixtures/pattern_fixtures.json` together.

---

## REST API

Everything under `/api`, JSON in and out, errors as `{"detail": "…"}`. Interactive docs at
`/docs`. No auth — keep it on the LAN or put it behind a reverse proxy.

**Every call that changes lights names a group.** There is no whole-house apply or off.

### Status

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{ok, devices: [{name, host, ok, error}]}` — what the container HEALTHCHECK calls |
| GET | `/api/devices` | every strand: LED count, profile, firmware, mode, brightness, and its `group` |
| POST | `/api/devices/refresh` | re-read gestalt for every strand. Changes nothing on the strands — only our cache — which is why it takes no group |
| POST | `/api/preview` | `{pattern, num_leds, offset}` → the pattern as `[r,g,b,w]` per LED |

### Groups

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/groups` | | each group's strands, LED total, segments and what it's showing |
| GET | `/api/groups/{group}` | | 404 if unknown |
| POST | `/api/groups/{group}/apply` | `Pattern` | 503 if the group has no strands |
| POST | `/api/groups/{group}/preset` | `{"name": "Halloween"}` | what Home Assistant calls |
| POST | `/api/groups/{group}/brightness` | `{"value": 0-100}` | |
| POST | `/api/groups/{group}/on` | | back to `movie` mode |
| POST | `/api/groups/{group}/off` | | leaves the pattern on the strand; it just stops showing it |

The preset name travels in the body rather than the path because preset names contain spaces.

### Presets — a shared library, applied to whichever group you choose

| Method | Path | Body |
|---|---|---|
| GET | `/api/presets` | |
| GET \| PUT \| DELETE | `/api/presets/{name}` | `Pattern` on PUT |

### Configuration

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/config` | | groups, where the config came from, whether it can be saved |
| POST | `/api/config/groups` | `{"name": "Tree"}` | 201; the id is derived from the name |
| PUT | `/api/config/groups/order` | `{"ids": [...]}` | display order; must list every group exactly once |
| PUT | `/api/config/groups/{group}` | `{"name": "Big tree"}` | rename; the id is unchanged |
| DELETE | `/api/config/groups/{group}` | | 400 while it still holds strands |
| PUT | `/api/config/groups/{group}/strands/order` | `{"hosts": [...]}` | physical order — this sets the seam |
| POST | `/api/config/strands` | `{host, name?, group?}` | no `group` ⇒ a group of its own |
| PUT | `/api/config/strands/{host}` | `{host, name?}` | |
| PUT | `/api/config/strands/{host}/group` | `{"group": "tree", "index": 0}` | move between groups |
| DELETE | `/api/config/strands/{host}` | | |

A `Pattern`:

```json
{
  "slots": [
    {"rgbw": [255, 80, 0, 0], "weight": 80},
    {"rgbw": [128, 0, 255, 0], "weight": 20}
  ],
  "layout": "interleaved",
  "block_size": 1,
  "brightness": 60
}
```

`rgbw` is 0–255 per channel; on an RGBW strand a true white is `[0, 0, 0, 255]`, and saturated
colors keep `w` at 0. `weight: 0` parks a slot without using it. `brightness` is optional.

A group from `GET /api/groups`:

```json
{
  "id": "tree",
  "name": "Tree",
  "strands": [{"name": "TreeTop", "host": "192.168.40.21", "number_of_led": null, "led_profile": null}],
  "total_leds": 500,
  "segments": [{"name": "TreeTop", "host": "192.168.40.21", "offset": 0, "number_of_led": 250, "led_profile": "RGBW"}],
  "reachable": 2,
  "state": {"pattern": {}, "preset": "Halloween", "power": "on", "applied_at": "2026-09-23T18:04:11Z"}
}
```

`segments[].offset` is group-relative: the first strand in every group is at 0.

Apply calls return 200 with a per-device breakdown — one unplugged strand doesn't fail the
request:

```json
{"ok": false, "results": [
  {"name": "TreeTop", "host": "192.168.40.21", "ok": true, "error": null},
  {"name": "TreeBottom", "host": "192.168.40.22", "ok": false, "error": "DeviceError: ConnectTimeout…"}
]}
```

`state` is what was **last applied**, not a readback. The strands hold their movie themselves, so
a power cycle or someone opening the Twinkly app can make it optimistic.

---

## Talking to a strand directly

`scripts/smoke.py` is the first thing to reach for when hardware misbehaves — it bypasses the
web app entirely:

```bash
.venv/bin/python scripts/smoke.py 192.168.40.21
```

```
→ 192.168.40.21: reading gestalt…
  LEDs          250
  led_profile   RGBW  (4 bytes/LED)
  firmware      2.9.1  family G
  upload path   /movies/* (new)
  gamma         2.2
  asked for     ff500000 ff500000 8000ff00 …
  on the wire   00ff1400 00ff1400 00380 0ff …
```

Useful flags: `--gamma 2.8` to compare corrections on the real strand, `--frames 2` for firmware
that refuses a single-frame movie, `--white` to light the white channel only (which makes a byte
order problem obvious), `--off`, `--dry-run`. `--help` has the rest.

---

## Notes on the Twinkly protocol

- Auth is a challenge/response handshake that yields a short-lived token. Tokens expire and a
  power-cycled strand forgets ours, so every call retries once with a fresh login. `401` warnings
  in the log are normal and self-healing.
- Firmware family `D`, or version below 2.5.6, uses `POST /led/movie/full` +
  `/led/movie/config`. Everything newer uses `/movies/new` + `/movies/full` + `/movies/current`.
  `Device.uses_new_movie_api` picks between them.
- xled sets no request timeout, which means an unplugged strand would hold a request open for
  the OS TCP timeout. `Device._apply_timeout` patches the session's `send` — `Session.request`
  alone isn't enough, because the auth handshake goes around it.
- Verified hardware: Twinkly strands on firmware 2.9.1, family G, 250 LEDs, RGBW. Single-frame
  movies are accepted on that firmware.

## Troubleshooting

**`Cannot write /data/…`** — the data directory isn't writable by the container's user. The
entrypoint only claims root-owned files (what Docker creates for a missing bind-mount source), so
a folder owned by someone else needs `PUID`/`PGID` in `docker-compose.yml` to match its owner. Dapple keeps running: it falls back to
the built-in presets, and a failed *state* write never fails an apply (the lights did change —
failing would make Home Assistant retry a successful operation), while a failed *config* write
does 500 and rolls back.

**Wrong colors** — see *Gamma* and *RGBW byte order* above. Orange reading as yellow is gamma;
orange reading as green is byte order.

**Upload succeeds, strand stays dark** — try `movie_frames: 2`. Check `/api/devices` shows
`mode: movie` and brightness isn't 0.

**Only part of a run lights up** — the strand under-reports `number_of_led`. Pin the real count
in `config.yaml`.

**The pattern restarts at the second strand** — they're in different groups, in the wrong order
within their group, or one reports the wrong LED count. `GET /api/groups` shows the offsets in
use.

**Two groups with the same pattern look offset from each other** — they shouldn't. Every group
starts at its own LED 0. That's a bug.

---

## Releases

`.github/workflows/docker.yml` runs the Python and JS tests, then builds a `linux/amd64` +
`linux/arm64` image. Pull requests only build it. Pushes publish it to Docker Hub:

| Push | Tags |
|---|---|
| `main` | `latest`, `sha-<commit>` |
| tag `v1.2.3` | `1.2.3`, `1.2`, `sha-<commit>` |

Publishing needs a repository **variable** `DOCKERHUB_USERNAME` and a **secret** `DOCKERHUB_TOKEN`
(a Docker Hub access token with read/write scope). Without them the workflow still builds and
simply skips the push.

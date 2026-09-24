# Developing Dapple

FastAPI + React, one container, one `/data` volume. No database.

```
app/pattern.py    weights → LED bytes (pure logic, the interesting part)
app/devices.py    xled wrapper, and DeviceGroup — where per-group offsets live
app/config.py     groups and strands: config.yaml, and the Strands tab's edits
app/presets.py    /data/presets.json, written atomically
app/state.py      /data/state.json — what each group was last set to
app/actions.py    apply / brightness / on / off, shared by the routes and MQTT
app/mqtt.py       Home Assistant discovery: each group as an MQTT light
app/models.py     pydantic models shared by the API and the stores
app/main.py       FastAPI routes + static mount
frontend/src/     React + MUI, Vite build
scripts/smoke.py  one strand, one pattern, from the command line
```

## Running from source

```bash
uv sync --extra dev                        # .venv, pinned to uv.lock, with Dapple itself editable
.venv/bin/pre-commit install               # Black and Prettier on every commit
.venv/bin/python -m pytest                 # 329 tests, no network, no strands

npm --prefix frontend install
npm --prefix frontend test                 # the JS pattern port vs the Python fixtures
npm --prefix frontend run dev              # UI on :5173, proxying /api to :8080

DAPPLE_DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload --port 8080
```

`uv.lock` pins every Python dependency. CI and the Docker image both install from it, so what
the tests ran against is what ships. After changing dependencies in `pyproject.toml`, run
`uv lock`. To take newer versions, run `uv lock --upgrade`, then run the tests and commit the
lock. Run `uv sync --extra dev` after a version bump too, because the running app reads its
version from the installed package.

The API serves the built UI from `static/` (where the Docker image puts it) or `frontend/dist/`,
whichever exists. With neither, `/` explains what to build and `/docs` still works.

Python is formatted with Black and `frontend/` with Prettier, both at 100 columns. The
pre-commit hook formats staged files for you, and CI fails on anything it would have changed.
`.venv/bin/pre-commit run --all-files` formats the whole tree. The hook brings its own Node for
Prettier, so it works without npm installed.

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
| MQTT / Home Assistant | `mqtt` | — | off; the Home Assistant tab writes it |
| Data directory | — | `DAPPLE_DATA_DIR` | `/data` |
| Config file | — | `DAPPLE_CONFIG` | `<data>/config.yaml` |
| Log level | — | `DAPPLE_LOG_LEVEL` | `INFO` |
| Host port | — | `DAPPLE_PORT` (override only) | `8080` |
| Run as uid / gid | — | `PUID` / `PGID` | `1000` |
| Log timezone | — | `TZ` | `Etc/UTC` |
| Built UI to serve | — | `DAPPLE_STATIC_DIR` | `static/`, else `frontend/dist/` |
| API behind `npm run dev` | — | `DAPPLE_API` (Vite only) | `http://localhost:8080` |

Where a setting can come from both, `config.yaml` wins. The Strands and Home Assistant tabs
rewrite the file, but they only write `movie_frames`, `timeout` and `gamma` back if the file
already had them, so an env var keeps working until you put the key in the file yourself.

```yaml
groups:
  - id: tree
    name: "Christmas tree"
    strands:
      - name: TreeTop
        host: 192.168.40.21
      - name: TreeBottom
        host: 192.168.40.22
        # Read from the strand unless pinned here. Pin them only for a strand
        # that misreports, or to build patterns before the strands are on the
        # network: the frame we upload is exactly this long, and a strand may
        # refuse one that doesn't match what it expects.
        # number_of_led: 250
        # led_profile: RGBW
```

```yaml
mqtt:
  enabled: true             # false, or missing, means off
  host: 192.168.1.20
  port: 1883
  username: dapple
  password: secret          # plain text; data/ is the only place it lives
  discovery_prefix: homeassistant
  topic_prefix: dapple      # also this instance's identity in HA
```

`config.yaml` is the only way to configure strands: the Strands tab writes it, and you can
hand-write it to seed a deployment. Starting with no file at all is normal — the first strand
you add creates one. Loading never writes to it.

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
| GET | `/api/ping` | `{"ok": true}` — liveness only, never touches a strand; what the container HEALTHCHECK calls |
| GET | `/api/health` | `{ok, devices: [{name, host, ok, error}]}` — asks every strand; `ok` is false if any is down, still 200 |
| GET | `/api/devices` | every strand: LED count, profile, firmware, mode, brightness, and its `group` |
| POST | `/api/devices/refresh` | re-read gestalt for every strand. Changes nothing on the strands — only our cache — which is why it takes no group |
| POST | `/api/preview` | `{pattern, num_leds, offset}` → the pattern as `[r,g,b,w]` per LED |

### Groups

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/groups` | | each group's strands, LED total, segments and what it's showing |
| GET | `/api/groups/{group}` | | 404 if unknown |
| GET | `/api/groups/{group}/live` | | read from the strands: `power` (`null` if none answer), `brightness`, the `preset` still showing, `taken_over` |
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
| GET | `/api/config/mqtt` | | broker settings and connection `status`; never the password, only `password_set` |
| PUT | `/api/config/mqtt` | `{enabled, host, port?, username?, password?, …}` | saves and reconnects; `enabled` defaults to false; omit `password` to keep the saved one, `""` clears it |

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

Limits: up to 16 slots (the editor offers 8), `weight` 0–1000, `block_size` 1–500, `brightness`
0–100. The reduced repeating unit (the weights divided by their GCD, summed, times `block_size`
when blocked) must be at most 100,000 LEDs; anything the editor can build is well under that.

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

`state` is what was **last applied**, not a readback. The strands hold their movie themselves,
so a power cycle or someone opening the Twinkly app can make it optimistic. `/live` is the
readback, and it's what both the Pattern tab's power switch and Home Assistant show.

---

## MQTT

`app/mqtt.py` publishes [Home Assistant MQTT discovery][discovery] once it's switched on in the
Home Assistant tab. Each group is an HA light in
the JSON schema, with its presets as the effect list. With the default prefixes:

| Topic | Retained | What |
|---|---|---|
| `homeassistant/light/dapple/<group>/config` | yes | discovery; an empty payload removes the light |
| `dapple/status` | yes | `online`, or `offline` (the connection's last will, and sent on shutdown) |
| `dapple/<group>/state` | yes | `{"state": "ON", "brightness": 60, "effect": "Halloween", "color_mode": "brightness"}` |
| `dapple/<group>/availability` | yes | `online` while any strand in the group answers |
| `dapple/<group>/set` | | commands from HA, the same shape as state |

[discovery]: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery

- **Commands** go through `GroupActions`, the same code as the REST routes, so they record
  state exactly as a UI click does. `effect` applies the preset, `brightness` (0–100) follows,
  and a bare `ON` or `OFF` switches power.
- **State is read from the strands**, not from `state.json`. The native Twinkly integration and
  the Twinkly app change them without Dapple knowing. A poll every 60s publishes only what
  changed. The effect is cleared when a strand is in any mode other than `movie` or `off`,
  because then it isn't showing the preset.
- **Stale lights are cleaned up.** On connect Dapple subscribes to its own discovery topics and
  clears any retained config for a group that no longer exists, e.g. one deleted while the
  broker was down. `homeassistant/status` `online` (HA restarting) republishes everything.
- **Settings live in `config.yaml`**, written by the Home Assistant tab through
  `PUT /api/config/mqtt`. The password is write-only: responses carry `password_set`, never the
  value. A broken `mqtt:` section is logged and treated as off; it doesn't stop Dapple.
- **`topic_prefix` is the instance's identity.** It's in the topics, the discovery node id and
  every `unique_id` (`dapple_tree`), so a second Dapple on the same broker needs a different
  one.

To watch the traffic:

```bash
docker run --rm --network host eclipse-mosquitto:2 mosquitto_sub -h <broker> -v -t 'dapple/#' -t 'homeassistant/light/dapple/#'
```

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
a folder owned by someone else needs `PUID`/`PGID` in `docker-compose.yml` to match its owner.
The entrypoint says so in the log at startup, before any traceback. Dapple keeps running: it falls back to
the built-in presets, and a failed *state* write never fails an apply (the lights did change —
failing would make Home Assistant retry a successful operation), while a failed *config* write
does 500 and rolls back.

**Wrong colors** — see *Gamma* and *RGBW byte order* above. Orange reading as yellow is gamma;
orange reading as green is byte order.

**Upload succeeds, strand stays dark** — try `movie_frames: 2`. Check `/api/devices` shows
`mode: movie` and brightness isn't 0.

**Only part of a run lights up** — the strand under-reports `number_of_led`. Pin the real count
in `config.yaml`. If uploads start failing after that, the firmware won't take a frame of that
length, and the pin has to go.

**The pattern restarts at the second strand** — they're in different groups, in the wrong order
within their group, or one reports the wrong LED count. `GET /api/groups` shows the offsets in
use.

**Two groups with the same pattern look offset from each other** — they shouldn't. Every group
starts at its own LED 0. That's a bug.

---

## Releases

`.github/workflows/docker.yml` checks formatting and runs the Python and JS tests, then builds a `linux/amd64` +
`linux/arm64` image. Pull requests only build it. Pushes publish it to Docker Hub:

| Push | Tags |
|---|---|
| `main` | `latest`, `sha-<commit>`, and `1.2.3` + `1.2` when it's a release (below) |
| any other branch, e.g. `mqtt-discovery` | `mqtt-discovery`, `sha-<commit>` |
| tag `v1.2.3` | `1.2.3`, `1.2`, `sha-<commit>` |

A branch build never touches `latest` or a version tag, so it's safe to push work in progress.
Slashes in a branch name become dashes (`feature/x` → `feature-x`).

**Changes reach `main` only through pull requests, and a person merges them.** Push a branch, open
a PR, and wait for CI and a review.

**Releasing is a pull request that bumps the version.** In that PR:

1. Set the new version in `pyproject.toml` and `frontend/package.json`. Then run
   `npm --prefix frontend install --package-lock-only` and `uv lock` so both lock files follow.
   CI fails if the two versions differ or `uv.lock` is stale.
2. Add the release notes as `docs/releases/<version>.md`, written for people running Dapple, not
   developers. CI fails on a PR whose version has no tag and no notes file.

When the PR is merged, the `main` build sees a version with no `v<version>` tag. It pushes the
image as `latest`, `<version>` and `<major>.<minor>`, then tags the merge commit and creates the
GitHub release `Dapple <version>` from the notes file. A PR that doesn't bump the version just
moves `latest`.

**Testing a branch build.** On the machine that runs Dapple, point `docker-compose.yml` at the
branch tag and pull it:

```yaml
    image: afraley/dapple:mqtt-discovery
```

```bash
docker compose up -d
```

`pull_policy: always` fetches the newest build of the branch on every `up`. Put `latest` back
once the branch has merged. Branch tags stay on Docker Hub until you delete them there.

Publishing needs a repository **variable** `DOCKERHUB_USERNAME` and a **secret** `DOCKERHUB_TOKEN`
(a Docker Hub access token with read/write scope). Without them the workflow still builds and
simply skips the push.

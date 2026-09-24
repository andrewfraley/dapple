# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Dapple sets static multi-color patterns on Twinkly LED strands and exposes a REST API for Home
Assistant. FastAPI + React, one Docker container, one `/data` volume, no database.

The user runs it on a homelab against **real hardware**: two Twinkly strands, 250 LEDs each,
RGBW, firmware 2.9.1 family G, named TreeTop and TreeBottom (addresses in `data/config.yaml`),
wired as one physical run up a tree. Changes here turn real lights on and off — `docker compose up -d`
and an apply call are visible in the user's living room.

Docs by audience: [README.md](README.md) is for non-technical users,
[HOME_ASSISTANT.md](HOME_ASSISTANT.md) for HA integration, [DEVELOPING.md](DEVELOPING.md) for
the API reference and internals. Keep them in their lanes — don't put REST tables in the README.

## Commands

```bash
.venv/bin/python -m pytest -q                 # 260 tests, no network, no strands
.venv/bin/pre-commit run --all-files          # Black + Prettier; the git hook runs this on staged files
npm --prefix frontend test                    # JS pattern port vs Python fixtures
npm --prefix frontend run build               # required before the Docker build picks up UI changes
docker compose up -d --build                  # builds this tree via docker-compose.override.yml; :8081 here
docker compose logs --no-log-prefix | tail -40
.venv/bin/python scripts/smoke.py <ip>        # talk to one strand, bypassing the web app
```

No node/npm on this machine — run frontend tooling through Docker:

```bash
docker run --rm -v "$PWD:/work:z" -w /work/frontend -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e npm_config_cache=/tmp/.npm node:22-alpine sh -c "npm test"
```

The `:z` on the mount is required (SELinux), as is running as the host uid or npm fails on
permissions.

## Invariants — these have each cost a bug

**Per-group offsets.** `DeviceGroup.offsets()` is a prefix sum *within a group*. Every group
starts its pattern at its own LED 0; strand *k* in a group is offset by the LED count of the
strands before it *in that group*. There is deliberately no `offsets()` on `DeviceManager` —
putting one back invites the whole-estate prefix sum this replaced. Pinned by
`test_offsets_reset_for_every_group` and
`test_two_groups_with_one_pattern_both_start_at_the_beginning`.

*When writing offset tests, pick strand lengths that are not a whole number of pattern cycles.*
A 4:1 pattern has a 5-LED unit, so with 205-LED groups `offset=205` and `offset=0` produce
identical bytes and the test proves nothing. The existing tests use 103 + 100 for this reason.

**RGBW byte order is `W R G B`.** Confirmed against xled_plus and the real strands. Wrong order
shows every color as a *different* color, not merely off-hue.

**Gamma belongs on the wire only.** `pack()` decodes sRGB → linear PWM; `led_colors()` (which
feeds the preview) stays in display space. Correcting the preview too would darken it while the
strand stayed put and the two would still disagree.

**`app/pattern.py` ↔ `frontend/src/pattern.js` are duplicate implementations**, locked together
by `tests/fixtures/pattern_fixtures.json`, which `tests/test_pattern_parity.py` regenerates and
fails on drift. Change one, change all three.

**FastAPI matches routes in declaration order.** `/api/config/groups/order` must be declared
before `/api/config/groups/{group_id}`. `order`, `all` and `new` are also reserved group ids, so
a colliding group can't exist in the first place.

**`ConfigStore` never mutates in place.** Every method builds new `GroupConfig`/list objects
(`dataclasses.replace`). `_commit` restores the previous group list when the write fails, and an
in-place mutation anywhere would make that rollback silently useless. `test_a_failed_write_changes_nothing`
covers every mutation for this reason.

**`Device` knows nothing about its group.** `DeviceManager.sync()` reuses a `Device` when its
`DeviceConfig` is unchanged, keyed by host across all groups, so moving a strand between groups
doesn't cost an auth token and a re-login. Adding a group field to `Device` or `DeviceConfig`
would break that.

**A failed `state.json` write must not fail a request.** The lights already changed; a 500 would
make Home Assistant retry a successful operation. A failed *config* write does 500 and rolls
back, because there nothing happened and the user needs to know. This asymmetry is deliberate.

## API shape

Every mutating route names a group — there is no whole-house apply or off. The user chose this
explicitly. If asked to add a convenience route, `all` is already a reserved group id, and the
argument for it is power-only (`/api/groups/all/off`), never apply.

Preset names travel in request bodies, never path segments: `Warm white` is a built-in and path
nesting a user string is an encoding bug waiting to happen.

Group `id` is slugged from the name at creation and is permanent; rename changes `name` only, so
HA automations survive a rename.

## Conventions

- Black formats Python and Prettier formats `frontend/`, both at 100 columns, via the
  pre-commit hook. CI fails on unformatted files. Don't hand-format around them, and don't run
  Prettier with ad-hoc flags: `frontend/.prettierrc.json` holds the settings.
- Tests are named as sentences about behavior (`test_two_groups_with_one_pattern_both_start_at_the_beginning`),
  and docstrings say *why the case matters*, not what the code does.
- Fakes over mocks: `FakeControl` (records xled calls, `fail_once` for retry paths),
  `FakeManager`/`FakeDevice` (duck-types `DeviceManager` for route tests). Async is driven with
  plain `asyncio.run` — there's no pytest-asyncio dependency.
- Comments explain the non-obvious *why*. Don't narrate what the line does.
- Error messages tell the user what to do next ("Move or remove them first").
- `tests/test_api.py` injects `app.state.{config,manager,presets,group_state,strands}` before
  `TestClient(app)` starts; the lifespan's `hasattr` guards are what make that work. Adding a new
  store means adding a guard *and* setting it in `build_client`.

## Keeping it publishable

This repo is open source; `data/` and `.env` are the only places real details may live.

- Never copy anything out of `data/`, `.env` or live API responses into tracked files. That covers
  strand IPs, hostnames, MACs, serials, device names from the Twinkly app, and timestamps.
- Example addresses are `192.168.40.21`, `.22`, `.30` in docs and UI placeholders (matching
  `data.example/config.yaml`), `192.168.1.x` for the machine running Dapple (README,
  HOME_ASSISTANT.md), and `10.0.0.x` in tests. Don't invent new ranges.
- Screenshots in `docs/` come from a throwaway instance, never the live app or container:
  `scripts/screenshot.sh [page] [out.png] [width] [height]` starts one on a temp data dir with
  the example config, captures the page and strips metadata. Look at the image before committing.
- Pasted logs and `smoke.py` output carry real hosts too; swap in the example addresses.
- No names, emails, home paths (`/home/...`) or personal details in code, docs, comments or
  package metadata.
- Before finishing a change to docs or screenshots, sweep for leaks:
  `grep -rnIE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' --exclude-dir={node_modules,.venv,dist,data} .`
  Anything outside `127.0.0.1`, `0.0.0.0` and the example addresses above needs a reason.

## Gotchas

- `xled` sets no request timeout. `Device._apply_timeout` patches the session's `send`, not
  `request`, because the auth handshake bypasses `request`.
- `401` warnings from `xled.auth` in the logs are normal and self-healing.
- Startup refreshes devices in a background task so unreachable strands don't block boot.
- `data/` is bind-mounted and owned by the host user. The container starts as root so
  `scripts/entrypoint.sh` can chown anything root-owned in `/data` (Docker creates a missing
  bind source as root), then drops to `PUID`/`PGID` (default 1000) via `setpriv`. The compose file follows
  the LinuxServer.io shape — literal values users edit, no `.env` — because that's what
  homelab users already know; only the override reads `DAPPLE_PORT`.
- The repo is public on GitHub (`andrewfraley/dapple`); the image is `afraley/dapple` on Docker Hub,
  published by `.github/workflows/docker.yml`. Every branch push publishes
  `afraley/dapple:<branch>` for testing on the real strands; only main moves `latest`, and only
  `v*` tags make versions. `docker-compose.yml` must stay usable on its own (users download only
  that file), so anything that needs the source goes in the override.
  Check `git status` before committing:
  `data/`, `.env`, `frontend/dist/` and `*.egg-info/` must stay untracked.

"""Putting brightness back after a strand loses power.

The firmware keeps its pattern and its timer across a power cut, but comes back
at full brightness. Re-sending the brightness on a timer would fix that and also
undo every change made from the Twinkly app, so this only acts on a real reboot.

Every minute it reads each strand's uptime and brightness, and remembers the
brightness. That's a readback, not what Dapple sent, so a level set anywhere comes
back: Dapple's UI, Home Assistant, or the Twinkly app. state.json can't do this. A
built-in preset carries no brightness, so applying one forgets the level there
while the strand goes on showing it.

Both are written to /data/boots.json, so an outage during which Dapple was also
down is still noticed, and restarting Dapple alone doesn't count as one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable, NamedTuple

from app.storage import atomic_write

log = logging.getLogger(__name__)

CHECK_SECONDS = 60.0
#: How much later a boot has to be than the last one read, to count as another. Boot
#: time is worked out from two clocks, so it wobbles by about a request's length.
BOOT_TOLERANCE_SECONDS = 30.0
#: The same, against the saved boot time just after Dapple starts. A strand's clock
#: drifts while nobody's looking: at 50 ppm, this allows for about ten weeks stopped.
SAVED_BOOT_TOLERANCE_SECONDS = 300.0
#: What the firmware boots at. Any other value after a reboot means someone has
#: already set it, and that's theirs to keep.
FIRMWARE_BOOT_BRIGHTNESS = 100


class StrandBoot(NamedTuple):
    #: When it booted, in seconds since the epoch.
    boot: float
    #: Its brightness when last seen before any reboot.
    brightness: int | None


class BootStore:
    """What was last seen of each strand. Losing this only costs one missed restore,
    so like state.json, a failed write is logged and never raised."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.writable = True
        self._strands: dict[str, StrandBoot] = self._read() if self.path.is_file() else {}

    def _read(self) -> dict[str, StrandBoot]:
        try:
            raw = json.loads(self.path.read_text())
            return {
                str(host): StrandBoot(float(entry["boot"]), entry.get("brightness"))
                for host, entry in raw.items()
            }
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            log.error("Could not read %s (%s); starting with nothing remembered", self.path, exc)
            return {}

    def get(self, host: str) -> StrandBoot | None:
        return self._strands.get(host)

    def set(self, host: str, boot: float, brightness: int | None) -> None:
        self._strands[host] = StrandBoot(boot, brightness)
        payload = {
            host: {"boot": strand.boot, "brightness": strand.brightness}
            for host, strand in self._strands.items()
        }
        try:
            atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            if self.writable:  # log the first time, not on every change
                log.error("Cannot write %s (%s)", self.path, exc.strerror or exc)
            self.writable = False
            return
        self.writable = True


class RebootWatcher:
    def __init__(self, manager, actions, boots: BootStore, clock: Callable[[], float] = time.time):
        self.manager = manager
        self.actions = actions
        self.boots = boots
        self.clock = clock
        #: The boot last read, in memory only, because the saved one is only rewritten
        #: on a change. It's what catches a second reboot soon after the first; missing
        #: that would save the firmware's 100 as though someone had chosen it.
        self._seen: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def _rebooted(self, host: str, boot: float, known: StrandBoot) -> bool:
        seen = self._seen.get(host)
        if seen is not None:
            return boot > seen + BOOT_TOLERANCE_SECONDS
        return boot > known.boot + SAVED_BOOT_TOLERANCE_SECONDS

    async def check(self) -> None:
        for group in self.manager.groups:
            if group.devices:
                await self._check_group(group.id)

    async def _check_group(self, group_id: str) -> None:
        restores: dict[str, int] = {}
        boots: dict[str, float] = {}
        for reading in await self.manager.read_boots(group_id):
            host = reading.host
            if reading.uptime_ms is None:  # no answer, or firmware that doesn't report it
                continue
            boot = self.clock() - reading.uptime_ms / 1000
            known = self.boots.get(host)
            if known is None:
                # First sight says nothing about what it should be showing.
                self.boots.set(host, boot, reading.brightness)
            elif self._rebooted(host, boot, known):
                wanted = known.brightness
                if (
                    wanted is not None
                    and wanted != FIRMWARE_BOOT_BRIGHTNESS
                    and reading.brightness == FIRMWARE_BOOT_BRIGHTNESS
                ):
                    restores[host] = wanted
                    boots[host] = boot
                    continue  # stays unseen, so a refused restore is tried again
                self.boots.set(host, boot, reading.brightness)
            elif reading.brightness is not None and reading.brightness != known.brightness:
                self.boots.set(host, known.boot, reading.brightness)
            self._seen[host] = boot
        if not restores:
            return
        log.info("Group %s: %s rebooted", group_id, ", ".join(sorted(restores)))
        results = await self.actions.restore_brightness(group_id, restores)
        failed = {result.host for result in results if not result.ok}
        for host, value in restores.items():
            if host not in failed:
                self.boots.set(host, boots[host], value)
                self._seen[host] = boots[host]

    async def _run(self, after: asyncio.Task | None) -> None:
        if after is not None:
            await asyncio.wait([after])
        while True:
            try:
                await self.check()
            except Exception:  # one bad pass mustn't stop this until the next restart
                log.exception("Checking the strands for reboots failed; will try again")
            await asyncio.sleep(CHECK_SECONDS)

    def start(self, after: asyncio.Task | None = None) -> None:
        self._task = asyncio.create_task(self._run(after))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

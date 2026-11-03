"""Putting brightness back after a strand loses power.

The firmware keeps its pattern and its timer across a power cut, but comes back
at full brightness. Re-sending the brightness on a timer would fix that and also
undo every change made from the Twinkly app, so this only acts on a real reboot.
The strand reports its uptime, which gives the moment it booted; a later boot than
the last one seen means the power went.

Boot times are written to /data/boots.json so an outage during which Dapple was
also down is still noticed, and restarting Dapple alone doesn't count as one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable

from app.storage import atomic_write

log = logging.getLogger(__name__)

CHECK_SECONDS = 60.0
#: A boot has to be this much later than the last one to count. Boot time is
#: worked out from two clocks, so it wobbles, and a strand's drifts while nobody's
#: looking: at 50 ppm, this allows for about ten weeks of Dapple being stopped.
BOOT_TOLERANCE_SECONDS = 300.0
#: What the firmware boots at. Any other value means someone has already set it
#: since the reboot, and that's theirs to keep.
FIRMWARE_BOOT_BRIGHTNESS = 100


class BootStore:
    """When each strand last booted. Losing this only costs one missed restore, so
    like state.json, a failed write is logged and never raised."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.writable = True
        self._boots: dict[str, float] = self._read() if self.path.is_file() else {}

    def _read(self) -> dict[str, float]:
        try:
            raw = json.loads(self.path.read_text())
            return {str(host): float(boot) for host, boot in raw.items()}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.error("Could not read %s (%s); starting with no boot times", self.path, exc)
            return {}

    def get(self, host: str) -> float | None:
        return self._boots.get(host)

    def set(self, host: str, boot: float) -> None:
        self._boots[host] = boot
        try:
            atomic_write(self.path, json.dumps(self._boots, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            if self.writable:  # log the first time, not on every reboot
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
        self._task: asyncio.Task | None = None

    async def check(self) -> None:
        for group in self.manager.groups:
            if group.devices:
                await self._check_group(group.id)

    async def _check_group(self, group_id: str) -> None:
        rebooted: dict[str, float] = {}
        for reading in await self.manager.read_boots(group_id):
            if reading.uptime_ms is None:  # no answer, or firmware that doesn't report it
                continue
            boot = self.clock() - reading.uptime_ms / 1000
            known = self.boots.get(reading.host)
            if known is None:
                # First sight says nothing about what it should be showing.
                self.boots.set(reading.host, boot)
            elif boot > known + BOOT_TOLERANCE_SECONDS:
                if reading.brightness == FIRMWARE_BOOT_BRIGHTNESS:
                    rebooted[reading.host] = boot
                else:
                    self.boots.set(reading.host, boot)
        if not rebooted:
            return
        log.info("Group %s: %s rebooted", group_id, ", ".join(sorted(rebooted)))
        results = await self.actions.restore_brightness(group_id, set(rebooted))
        # A strand that refused stays unhandled, so the next check tries again.
        failed = {result.host for result in results if not result.ok}
        for host, boot in rebooted.items():
            if host not in failed:
                self.boots.set(host, boot)

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

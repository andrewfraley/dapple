"""Daily on and off times, kept on the strands' own timers.

The strand does the switching, so a schedule keeps running with Dapple stopped.
All Dapple does is make sure each strand holds the right times. It checks every
few minutes rather than only on save, because that one check covers a strand
that was unplugged, a strand moved into the group, a clock that has drifted and
someone changing the timer in the Twinkly app.

This stays out of :class:`~app.actions.GroupActions`: setting a timer doesn't
change what a group is showing, so there's no state to record. It never writes
state.json either. The MQTT poll and the UI read the live mode, so a scheduled
change shows up there on its own.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zoneinfo
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from app.config import ConfigStore, ScheduleConfig
from app.models import DeviceResult, ScheduleClock, StrandTimer

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
RECONCILE_SECONDS = 300.0
#: A strand's clock can be this far out before it gets corrected. It only needs
#: to be close, and a write per strand per pass would be pointless traffic.
CLOCK_TOLERANCE_SECONDS = 60
TIMER_UNSET = -1
FALLBACK_ZONE = "Etc/UTC"

DOCKER_HOW_TO = (
    "Set TZ in your docker-compose.yml, then run docker compose up -d. "
    "Restarting the container isn't enough to pick up a new value."
)
ADDON_HOW_TO = "Dapple uses Home Assistant's time zone: Settings → System → General."


def container_zone(env: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The zone schedules run in, and what's wrong with ``TZ`` if it isn't usable.

    One zone for the whole install, read from the same variable the logs follow.
    The Home Assistant Supervisor sets it on add-ons from Home Assistant's own
    setting, so nobody there has anything to configure.
    """
    env = os.environ if env is None else env
    wanted = env.get("TZ", "").strip().removeprefix(":")
    if not wanted:
        return FALLBACK_ZONE, "TZ isn't set, so Dapple is using UTC."
    try:
        ZoneInfo(wanted)
    except (KeyError, ValueError, OSError):  # not-found is a KeyError
        return FALLBACK_ZONE, f"TZ is {wanted!r}, which isn't a time zone, so Dapple is using UTC."
    return wanted, None


def how_to_change_zone(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return ADDON_HOW_TO if env.get("SUPERVISOR_TOKEN") else DOCKER_HOW_TO


def parse_time(value: str | None) -> int:
    """``17:30`` as seconds after midnight, or the firmware's "unset"."""
    if value is None:
        return TIMER_UNSET
    hours, minutes = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60


def seconds_after_midnight(zone: str, now: datetime | None = None) -> int:
    """The wall clock in ``zone``. Never the container's: its ``TZ`` defaults to UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone))
    return local.hour * 3600 + local.minute * 60 + local.second


def clock_error(a: int, b: int) -> int:
    """Seconds between two clock readings, the short way round midnight."""
    difference = abs(a - b) % SECONDS_PER_DAY
    return min(difference, SECONDS_PER_DAY - difference)


def posix_tz(zone: str) -> str:
    """``America/Chicago`` as the firmware wants it: ``CST6CDT,M3.2.0,M11.1.0``.

    That's the footer of the zone's TZif file. Empty if it can't be found, which
    costs nothing that matters: Dapple still corrects the clock itself.
    """
    for root in zoneinfo.TZPATH:
        try:
            data = (Path(root) / zone).read_bytes()
        except OSError:
            continue
        # Version 1 files have no footer.
        if data[:4] == b"TZif" and data[4:5] != b"\0":
            return data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("ascii", "replace")
    log.warning("No POSIX time zone found for %s; the strand's will be blank", zone)
    return ""


def timer_matches(
    schedule: ScheduleConfig, timer: StrandTimer, zone: str, now: datetime | None = None
) -> bool:
    """Whether a strand already holds what its group's schedule asks for.

    A switched-off schedule only cares that the timer is off. Its clock and time
    zone are no longer Dapple's business.
    """
    if not schedule.enabled:
        return timer.time_on == TIMER_UNSET and timer.time_off == TIMER_UNSET
    if (timer.time_on, timer.time_off) != (
        parse_time(schedule.time_on),
        parse_time(schedule.time_off),
    ):
        return False
    if timer.tz is not None and timer.tz != posix_tz(zone):
        return False
    expected = seconds_after_midnight(zone, now)
    return clock_error(timer.time_now or 0, expected) <= CLOCK_TOLERANCE_SECONDS


class ScheduleKeeper:
    def __init__(self, store: ConfigStore, manager, env: Mapping[str, str] | None = None):
        self.store = store
        self.manager = manager
        self.zone, self.zone_problem = container_zone(env)
        self.how_to_change_zone = how_to_change_zone(env)
        if self.zone_problem:
            log.warning("%s %s", self.zone_problem, self.how_to_change_zone)
        self._task: asyncio.Task | None = None

    def clock(self, now: datetime | None = None) -> ScheduleClock:
        """What the schedule dialog shows, so a wrong zone is obvious before it matters."""
        local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(self.zone))
        return ScheduleClock(
            zone=self.zone,
            time=local.strftime("%H:%M"),
            utc_offset_minutes=int(local.utcoffset().total_seconds() // 60),
            problem=self.zone_problem,
            how_to_change=self.how_to_change_zone,
        )

    async def _write(
        self, group_id: str, schedule: ScheduleConfig, hosts: set[str] | None = None
    ) -> list[DeviceResult]:
        if not schedule.enabled:
            return await self.manager.clear_timer(group_id, hosts=hosts)
        return await self.manager.set_timer(
            group_id,
            parse_time(schedule.time_on),
            parse_time(schedule.time_off),
            lambda: seconds_after_midnight(self.zone),
            posix_tz(self.zone),
            hosts=hosts,
        )

    async def push(self, group_id: str, hosts: set[str] | None = None) -> list[DeviceResult]:
        """Write a group's schedule to its strands now."""
        schedule = self.store.find_group(group_id).schedule
        if schedule is None:
            return []
        return await self._write(group_id, schedule, hosts)

    async def read(self, group_id: str) -> list[StrandTimer]:
        """Each strand's timer, marked with whether it matches the saved schedule."""
        schedule = self.store.find_group(group_id).schedule
        timers = await self.manager.read_timers(group_id)
        if schedule is None:
            return timers
        return [
            (
                timer.model_copy(update={"matches": timer_matches(schedule, timer, self.zone)})
                if timer.reachable
                else timer
            )
            for timer in timers
        ]

    async def strand_left(self, host: str, was_scheduled: bool) -> str | None:
        """Clear the timer on a strand no schedule looks after any more.

        Otherwise it would go on switching itself on and off for good. This is
        one attempt: nothing is left to retry it once the strand is out of the
        schedule, so a failure comes back as a message for the user.
        """
        if not was_scheduled:
            return None
        result = await self.manager.clear_strand_timer(host)
        if result.ok:
            return None
        return (
            f"{result.name} didn't answer, so it will keep following its old schedule. "
            "Clear its timer in the Twinkly app, or give its new group a schedule "
            "while it's plugged in."
        )

    async def reconcile(self) -> None:
        """Bring every strand of every scheduled group into line."""
        for group in self.store.groups:
            if group.schedule is None or not group.devices:
                continue
            timers = await self.manager.read_timers(group.id)
            stale = {
                timer.host
                for timer in timers
                if timer.reachable and not timer_matches(group.schedule, timer, self.zone)
            }
            if stale:
                log.info("Group %s: updating the timer on %s", group.id, ", ".join(sorted(stale)))
                await self._write(group.id, group.schedule, stale)

    async def _run(self, after: asyncio.Task | None) -> None:
        if after is not None:
            await asyncio.wait([after])
        while True:
            try:
                await self.reconcile()
            except Exception:  # one bad pass mustn't end the schedule upkeep for good
                log.exception("Checking the strand timers failed; will try again")
            await asyncio.sleep(RECONCILE_SECONDS)

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

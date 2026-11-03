"""Keeping the strands' timers in line with each group's schedule."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.config import AppConfig, ConfigStore, DeviceConfig, normalize_schedule
from app.models import DeviceResult, StrandTimer
from app.schedules import (
    clock_error,
    container_zone,
    how_to_change_zone,
    parse_time,
    posix_tz,
    ScheduleKeeper,
    seconds_after_midnight,
    timer_matches,
)

ZONE = "America/Chicago"
#: 12:00:00 Chicago time, in winter (UTC-6).
NOON = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
NOON_SECONDS = 12 * 3600


class TimerManager:
    """Duck-types the DeviceManager's timer calls, with strands that remember."""

    def __init__(self):
        self.timers = {}
        self.unreachable = set()
        self.writes = []

    def strand(self, host, **fields):
        self.timers[host] = StrandTimer(name=host, host=host, **fields)

    async def read_timers(self, group_id):
        return [
            (
                StrandTimer(name=host, host=host, reachable=False)
                if host in self.unreachable
                else timer
            )
            for host, timer in self.timers.items()
        ]

    async def set_timer(self, group_id, time_on, time_off, time_now, tz, hosts=None):
        self.writes.append(("set", group_id, hosts))
        results = []
        for host in hosts if hosts is not None else list(self.timers):
            if host in self.unreachable:
                results.append(DeviceResult(name=host, host=host, ok=False, error="timeout"))
                continue
            self.strand(host, time_on=time_on, time_off=time_off, time_now=time_now(), tz=tz)
            results.append(DeviceResult(name=host, host=host, ok=True))
        return results

    async def clear_timer(self, group_id, hosts=None):
        self.writes.append(("clear", group_id, hosts))
        return [DeviceResult(name=host, host=host, ok=True) for host in hosts or self.timers]

    async def clear_strand_timer(self, host):
        self.writes.append(("clear_strand", host))
        ok = host not in self.unreachable
        return DeviceResult(name=host, host=host, ok=ok, error=None if ok else "timeout")


@pytest.fixture
def store(tmp_path):
    store = ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))
    store.create_group("Tree")
    store.add(DeviceConfig(name="One", host="10.0.0.1"), "tree")
    return store


@pytest.fixture
def manager():
    return TimerManager()


@pytest.fixture
def keeper(store, manager):
    return ScheduleKeeper(store, manager, env={"TZ": ZONE})


def in_sync(schedule):
    """A strand holding exactly what ``schedule`` asks for, right now."""
    return {
        "time_on": parse_time(schedule.time_on),
        "time_off": parse_time(schedule.time_off),
        "time_now": seconds_after_midnight(ZONE),
        "tz": posix_tz(ZONE),
    }


# ---- helpers ---------------------------------------------------------------


def test_a_missing_time_is_the_firmwares_unset():
    assert parse_time(None) == -1


def test_parse_time_is_seconds_after_midnight():
    assert parse_time("17:30") == 63000


def test_the_clock_follows_daylight_saving():
    """The strand is told local time, so the same instant of the year moves by an hour."""
    winter = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)

    assert seconds_after_midnight(ZONE, winter) == 12 * 3600
    assert seconds_after_midnight(ZONE, summer) == 13 * 3600


def test_the_clock_changes_across_the_night_it_switches():
    """Chicago went back to standard time on 1 November 2026, repeating 01:30."""
    before = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # 01:30 CDT
    after = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)  # 01:30 CST, an hour later

    assert seconds_after_midnight(ZONE, before) == seconds_after_midnight(ZONE, after)


def test_clock_error_goes_the_short_way_round_midnight():
    """23:59:50 and 00:00:10 are 20 seconds apart, not most of a day."""
    assert clock_error(86390, 10) == 20
    assert clock_error(10, 86390) == 20
    assert clock_error(100, 160) == 60


def test_posix_tz_is_what_the_firmware_reports():
    """The form the firmware reports, so an in-sync strand isn't rewritten."""
    assert posix_tz("America/Chicago") == "CST6CDT,M3.2.0,M11.1.0"


def test_posix_tz_of_an_unknown_zone_is_blank():
    assert posix_tz("Mars/Olympus") == ""


# ---- the container's time zone ---------------------------------------------


def test_the_zone_comes_from_tz():
    assert container_zone({"TZ": "America/Chicago"}) == ("America/Chicago", None)


def test_no_tz_falls_back_to_utc_and_says_so():
    """Silently running hours out is the failure this exists to prevent."""
    zone, problem = container_zone({})

    assert zone == "Etc/UTC"
    assert "isn't set" in problem


@pytest.mark.parametrize("value", ["Mars/Olympus", "../../etc/passwd", "CST6CDT,M3.2.0,M11.1.0"])
def test_an_unusable_tz_falls_back_to_utc_and_names_it(value):
    zone, problem = container_zone({"TZ": value})

    assert zone == "Etc/UTC"
    assert value in problem


def test_utc_on_purpose_is_not_a_problem():
    """It's the default in docker-compose.yml, and may be exactly what someone wants."""
    assert container_zone({"TZ": "Etc/UTC"}) == ("Etc/UTC", None)


def test_home_assistant_users_are_not_told_to_set_an_env_var():
    """An add-on's TZ comes from Home Assistant, so there's no variable for them to edit."""
    assert "Settings" in how_to_change_zone({"SUPERVISOR_TOKEN": "x"})
    assert "docker-compose.yml" in how_to_change_zone({})


def test_the_clock_reports_the_local_time_and_offset(keeper):
    clock = keeper.clock(NOON)

    assert (clock.zone, clock.time, clock.utc_offset_minutes) == (ZONE, "12:00", -360)
    assert clock.problem is None


def test_the_clock_offset_handles_half_hour_zones(store, manager):
    """A browser comparison has to agree to the minute, not just the hour."""
    keeper = ScheduleKeeper(store, manager, env={"TZ": "America/St_Johns"})

    assert keeper.clock(NOON).utc_offset_minutes == -210


def test_changing_tz_gets_every_strand_corrected(store, manager):
    """No re-save: after a restart with a new TZ, the regular check sees it's all wrong."""
    schedule = normalize_schedule("17:00", "23:00")
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **in_sync(schedule))
    keeper = ScheduleKeeper(store, manager, env={"TZ": "Asia/Tokyo"})

    asyncio.run(keeper.reconcile())

    assert manager.writes == [("set", "tree", {"10.0.0.1"})]
    assert manager.timers["10.0.0.1"].tz == posix_tz("Asia/Tokyo")


# ---- what counts as in sync ------------------------------------------------


def timer(**fields):
    return StrandTimer(name="One", host="10.0.0.1", **fields)


@pytest.fixture
def schedule():
    return normalize_schedule("17:00", "23:00")


def test_a_strand_holding_the_schedule_matches(schedule):
    strand = timer(time_on=61200, time_off=82800, time_now=NOON_SECONDS, tz=posix_tz(ZONE))

    assert timer_matches(schedule, strand, ZONE, NOON)


def test_a_clock_a_little_out_still_matches(schedule):
    strand = timer(time_on=61200, time_off=82800, time_now=NOON_SECONDS + 60, tz=posix_tz(ZONE))

    assert timer_matches(schedule, strand, ZONE, NOON)


def test_a_clock_an_hour_out_does_not_match(schedule):
    """What a strand looks like when daylight saving changed and it didn't follow."""
    strand = timer(time_on=61200, time_off=82800, time_now=NOON_SECONDS + 3600, tz=posix_tz(ZONE))

    assert not timer_matches(schedule, strand, ZONE, NOON)


def test_different_times_do_not_match(schedule):
    strand = timer(time_on=60000, time_off=82800, time_now=NOON_SECONDS, tz=posix_tz(ZONE))

    assert not timer_matches(schedule, strand, ZONE, NOON)


def test_a_blanked_time_zone_does_not_match(schedule):
    strand = timer(time_on=61200, time_off=82800, time_now=NOON_SECONDS, tz="")

    assert not timer_matches(schedule, strand, ZONE, NOON)


def test_firmware_without_a_time_zone_can_still_match(schedule):
    """No tz reported means there's nothing to correct; it mustn't be rewritten forever."""
    strand = timer(time_on=61200, time_off=82800, time_now=NOON_SECONDS, tz=None)

    assert timer_matches(schedule, strand, ZONE, NOON)


def test_a_switched_off_schedule_only_cares_that_the_timer_is_off():
    schedule = normalize_schedule("17:00", "23:00", enabled=False)

    assert timer_matches(
        schedule, timer(time_on=-1, time_off=-1, time_now=5, tz="other"), ZONE, NOON
    )
    assert not timer_matches(schedule, timer(time_on=61200, time_off=-1, time_now=5), ZONE, NOON)


# ---- reconcile -------------------------------------------------------------


def test_a_strand_already_in_sync_gets_no_write(store, manager, keeper, schedule):
    """The check runs every few minutes, so it must only talk when there's a reason."""
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **in_sync(schedule))

    asyncio.run(keeper.reconcile())

    assert manager.writes == []


def test_a_strand_with_the_wrong_times_is_corrected(store, manager, keeper, schedule):
    """This is also what someone changing the timer in the Twinkly app looks like."""
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **{**in_sync(schedule), "time_on": 100})

    asyncio.run(keeper.reconcile())

    assert manager.writes == [("set", "tree", {"10.0.0.1"})]
    assert manager.timers["10.0.0.1"].time_on == 61200


def test_a_strand_with_a_wrong_clock_is_corrected(store, manager, keeper, schedule):
    store.set_schedule("tree", schedule)
    stale = (seconds_after_midnight(ZONE) + 3600) % 86400
    manager.strand("10.0.0.1", **{**in_sync(schedule), "time_now": stale})

    asyncio.run(keeper.reconcile())

    assert manager.writes == [("set", "tree", {"10.0.0.1"})]


def test_only_the_strand_that_is_out_gets_written(store, manager, keeper, schedule):
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **in_sync(schedule))
    manager.strand("10.0.0.2", **{**in_sync(schedule), "time_off": -1})

    asyncio.run(keeper.reconcile())

    assert manager.writes == [("set", "tree", {"10.0.0.2"})]


def test_an_unplugged_strand_is_skipped_until_it_returns(store, manager, keeper, schedule):
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **{**in_sync(schedule), "time_on": 100})
    manager.unreachable.add("10.0.0.1")

    asyncio.run(keeper.reconcile())
    assert manager.writes == []

    manager.unreachable.clear()
    asyncio.run(keeper.reconcile())
    assert manager.writes == [("set", "tree", {"10.0.0.1"})]


def test_a_group_with_no_schedule_is_left_alone(manager, keeper):
    """Its timer may be one the user set in the Twinkly app, and that's theirs."""
    manager.strand("10.0.0.1", time_on=100, time_off=200, time_now=5, tz="UTC0")

    asyncio.run(keeper.reconcile())

    assert manager.writes == []


def test_a_switched_off_schedule_clears_a_timer_that_is_still_set(store, manager, keeper):
    """Including on a strand that was unplugged when the schedule was switched off."""
    store.set_schedule("tree", normalize_schedule("17:00", "23:00", enabled=False))
    manager.strand("10.0.0.1", time_on=61200, time_off=82800, time_now=5, tz="UTC0")

    asyncio.run(keeper.reconcile())

    assert manager.writes == [("clear", "tree", {"10.0.0.1"})]


# ---- push and read ---------------------------------------------------------


def test_pushing_with_no_schedule_touches_nothing(manager, keeper):
    assert asyncio.run(keeper.push("tree")) == []
    assert manager.writes == []


def test_read_marks_each_strand_with_whether_it_matches(store, manager, keeper, schedule):
    store.set_schedule("tree", schedule)
    manager.strand("10.0.0.1", **in_sync(schedule))
    manager.strand("10.0.0.2", **{**in_sync(schedule), "time_on": 100})
    manager.strand("10.0.0.3", **in_sync(schedule))
    manager.unreachable.add("10.0.0.3")

    timers = {timer.host: timer for timer in asyncio.run(keeper.read("tree"))}

    assert timers["10.0.0.1"].matches is True
    assert timers["10.0.0.2"].matches is False
    assert timers["10.0.0.3"].matches is None


def test_read_with_no_schedule_makes_no_claim(manager, keeper):
    manager.strand("10.0.0.1", time_on=100, time_off=200, time_now=5)

    assert asyncio.run(keeper.read("tree"))[0].matches is None


# ---- strands leaving a schedule --------------------------------------------


def test_a_strand_leaving_a_schedule_is_cleared(manager, keeper):
    assert asyncio.run(keeper.strand_left("10.0.0.1", was_scheduled=True)) is None
    assert manager.writes == [("clear_strand", "10.0.0.1")]


def test_a_strand_that_was_never_scheduled_is_not_touched(manager, keeper):
    """Its timer isn't Dapple's: moving a strand mustn't wipe one set in the Twinkly app."""
    assert asyncio.run(keeper.strand_left("10.0.0.1", was_scheduled=False)) is None
    assert manager.writes == []


def test_a_strand_that_cannot_be_cleared_produces_a_warning(manager, keeper):
    """Nothing retries this one, so the user has to hear about it."""
    manager.unreachable.add("10.0.0.1")

    warning = asyncio.run(keeper.strand_left("10.0.0.1", was_scheduled=True))

    assert "old schedule" in warning


# ---- the loop --------------------------------------------------------------


def test_a_failed_pass_does_not_end_the_loop(store, keeper, monkeypatch):
    """A surprise on one pass mustn't silently stop schedule upkeep until a restart."""
    passes = []

    async def reconcile():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("surprise")

    monkeypatch.setattr(keeper, "reconcile", reconcile)
    monkeypatch.setattr("app.schedules.RECONCILE_SECONDS", 0)

    async def run():
        keeper.start()
        while len(passes) < 2:
            await asyncio.sleep(0)
        await keeper.stop()

    asyncio.run(asyncio.wait_for(run(), timeout=5))

    assert len(passes) >= 2

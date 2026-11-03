"""Putting brightness back after a power cut, and only after a power cut."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.actions import GroupActions
from app.models import DeviceResult, Pattern, Slot
from app.presets import PresetStore
from app.reboots import BootStore, RebootWatcher
from app.state import StateStore

NOW = 1_000_000.0
HOUR = 3600


class Strands:
    """Duck-types the manager: strands with an uptime, a brightness and a plug."""

    def __init__(self, *hosts):
        self.strands = {
            host: SimpleNamespace(uptime=HOUR, brightness=20, answering=True, refuses=False)
            for host in hosts
        }
        self.groups = [SimpleNamespace(id="tree", name="Tree", devices=list(hosts))]
        self.writes = []

    def group(self, group_id):
        return self.groups[0]

    async def read_boots(self, group_id):
        return [
            SimpleNamespace(
                host=host,
                uptime_ms=strand.uptime * 1000 if strand.answering else None,
                brightness=strand.brightness if strand.answering else None,
            )
            for host, strand in self.strands.items()
        ]

    async def set_brightness(self, group_id, value, hosts=None):
        results = []
        for host, strand in self.strands.items():
            if hosts is not None and host not in hosts:
                continue
            self.writes.append((host, value))
            if not strand.refuses:
                strand.brightness = value
            results.append(DeviceResult(name=host, host=host, ok=not strand.refuses))
        return results


class World:
    def __init__(self, tmp_path, *hosts):
        self.now = NOW
        self.manager = Strands(*hosts)
        self.state = StateStore(tmp_path / "state.json")
        self.changed = []
        self.actions = GroupActions(
            self.manager,
            PresetStore(tmp_path / "presets.json"),
            self.state,
            on_change=self.changed.append,
        )
        self.path = tmp_path / "boots.json"
        self.watcher = self.new_watcher()

    def new_watcher(self):
        return RebootWatcher(
            self.manager, self.actions, BootStore(self.path), clock=lambda: self.now
        )

    def applied(self, brightness=20):
        pattern = Pattern(slots=[Slot(rgbw=(255, 0, 0, 0))], brightness=brightness)
        self.state.record("tree", pattern)

    def power_cut(self, *hosts):
        """Time passes, the strands restart at full brightness."""
        self.now += 2 * HOUR
        for host, strand in self.manager.strands.items():
            if host in hosts:
                strand.uptime = 60
                strand.brightness = 100
            else:
                strand.uptime += 2 * HOUR

    def check(self):
        asyncio.run(self.watcher.check())


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, "10.0.0.1", "10.0.0.2")


def test_a_power_cut_gets_the_last_brightness_back(world):
    world.applied(brightness=20)
    world.check()

    world.power_cut("10.0.0.1", "10.0.0.2")
    world.check()

    assert sorted(world.manager.writes) == [("10.0.0.1", 20), ("10.0.0.2", 20)]
    assert world.changed == ["tree"]


def test_only_the_strand_that_lost_power_is_touched(world):
    world.applied()
    world.check()

    world.power_cut("10.0.0.2")
    world.check()

    assert world.manager.writes == [("10.0.0.2", 20)]


def test_it_restores_once_not_on_every_check(world):
    """Repeating it would undo a later change in the Twinkly app within a minute."""
    world.applied()
    world.check()
    world.power_cut("10.0.0.1")
    world.check()
    world.manager.writes.clear()

    world.manager.strands["10.0.0.1"].brightness = 100  # someone wants it bright
    world.manager.strands["10.0.0.1"].uptime += 60
    world.now += 60
    world.check()

    assert world.manager.writes == []


def test_no_reboot_means_no_write(world):
    """A brightness changed elsewhere stays changed, even at the value a reboot gives."""
    world.applied()
    world.check()
    world.now += HOUR
    for strand in world.manager.strands.values():
        strand.uptime += HOUR
        strand.brightness = 100

    world.check()

    assert world.manager.writes == []


def test_first_sight_of_a_strand_changes_nothing(world):
    """Upgrading to this, or adding a strand, says nothing about what it should show."""
    world.applied()
    for strand in world.manager.strands.values():
        strand.brightness = 100

    world.check()

    assert world.manager.writes == []


def test_a_brightness_already_changed_since_the_reboot_is_kept(world):
    """Someone got there first, and it's theirs."""
    world.applied(brightness=20)
    world.check()
    world.power_cut("10.0.0.1")
    world.manager.strands["10.0.0.1"].brightness = 40

    world.check()

    assert world.manager.writes == []


def test_an_outage_during_which_dapple_was_also_down_is_noticed(world):
    world.applied()
    world.check()

    world.power_cut("10.0.0.1")
    world.watcher = world.new_watcher()
    world.check()

    assert world.manager.writes == [("10.0.0.1", 20)]


def test_restarting_dapple_alone_is_not_a_reboot(world):
    world.applied()
    world.check()

    world.watcher = world.new_watcher()
    world.now += 30
    for strand in world.manager.strands.values():
        strand.uptime += 30
        strand.brightness = 100
    world.check()

    assert world.manager.writes == []


def test_a_little_clock_wobble_is_not_a_reboot(world):
    """Boot time comes from two clocks, so it never comes out quite the same."""
    world.applied()
    world.check()

    world.now += 200  # the strand's uptime has fallen behind by 200 seconds
    for strand in world.manager.strands.values():
        strand.brightness = 100
    world.check()

    assert world.manager.writes == []


def test_a_strand_that_comes_back_later_is_restored_when_it_answers(world):
    world.applied()
    world.check()
    world.power_cut("10.0.0.1")
    world.manager.strands["10.0.0.1"].answering = False

    world.check()
    assert world.manager.writes == []

    world.manager.strands["10.0.0.1"].answering = True
    world.check()
    assert world.manager.writes == [("10.0.0.1", 20)]


def test_a_refused_restore_is_tried_again(world):
    world.applied()
    world.check()
    world.power_cut("10.0.0.1")
    world.manager.strands["10.0.0.1"].refuses = True

    world.check()
    world.manager.strands["10.0.0.1"].refuses = False
    world.check()

    assert world.manager.writes == [("10.0.0.1", 20), ("10.0.0.1", 20)]
    assert world.manager.strands["10.0.0.1"].brightness == 20


def test_a_group_dapple_never_set_a_brightness_for_is_left_alone(world):
    world.applied(brightness=None)
    world.check()
    world.power_cut("10.0.0.1")

    world.check()

    assert world.manager.writes == []
    assert world.changed == []


def test_a_group_that_was_never_applied_to_is_left_alone(world):
    world.check()
    world.power_cut("10.0.0.1")

    world.check()

    assert world.manager.writes == []


def test_restoring_leaves_state_json_alone(world):
    """state.json is what Dapple was asked for, and nobody asked for this."""
    world.applied()
    before = world.state.get("tree").applied_at
    world.check()
    world.power_cut("10.0.0.1")

    world.check()

    assert world.state.get("tree").applied_at == before


def test_firmware_that_reports_no_uptime_is_ignored(world):
    world.applied()
    for strand in world.manager.strands.values():
        strand.answering = False

    world.check()

    assert world.manager.writes == []
    assert not world.path.exists()


# ---- the store -------------------------------------------------------------


def test_boot_times_survive_a_restart(tmp_path):
    BootStore(tmp_path / "boots.json").set("10.0.0.1", 123.5)

    assert BootStore(tmp_path / "boots.json").get("10.0.0.1") == 123.5


def test_a_damaged_file_starts_empty(tmp_path, caplog):
    path = tmp_path / "boots.json"
    path.write_text("{not json")

    assert BootStore(path).get("10.0.0.1") is None
    assert "Could not read" in caplog.text


def test_a_failed_write_is_not_raised(tmp_path):
    """Nothing here is worth failing over: at worst one restore gets repeated or missed."""
    (tmp_path / "not-a-folder").write_text("")
    store = BootStore(tmp_path / "not-a-folder" / "boots.json")

    store.set("10.0.0.1", 1.0)

    assert store.writable is False
    assert store.get("10.0.0.1") == 1.0


def test_the_file_is_plain_json(tmp_path):
    BootStore(tmp_path / "boots.json").set("10.0.0.1", 5.0)

    assert json.loads((tmp_path / "boots.json").read_text()) == {"10.0.0.1": 5.0}


# ---- the loop --------------------------------------------------------------


def test_a_failed_pass_does_not_end_the_loop(world, monkeypatch):
    passes = []

    async def check():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("surprise")

    monkeypatch.setattr(world.watcher, "check", check)
    monkeypatch.setattr("app.reboots.CHECK_SECONDS", 0)

    async def run():
        world.watcher.start()
        while len(passes) < 2:
            await asyncio.sleep(0)
        await world.watcher.stop()

    asyncio.run(asyncio.wait_for(run(), timeout=5))

    assert len(passes) >= 2

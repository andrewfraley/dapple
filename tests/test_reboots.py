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

    async def set_strand_brightness(self, group_id, values):
        results = []
        for host, value in values.items():
            strand = self.strands[host]
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

    def wait(self, seconds):
        self.now += seconds
        for strand in self.manager.strands.values():
            strand.uptime += seconds

    def power_cut(self, *hosts):
        """Time passes, and these strands restart at full brightness."""
        self.wait(2 * HOUR)
        for host in hosts:
            self.manager.strands[host].uptime = 60
            self.manager.strands[host].brightness = 100

    def set_level(self, value, *hosts):
        """Changed by anything at all: the strand is all Dapple asks."""
        for host in hosts or self.manager.strands:
            self.manager.strands[host].brightness = value

    def check(self):
        asyncio.run(self.watcher.check())


ONE, TWO = "10.0.0.1", "10.0.0.2"


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, ONE, TWO)


def test_a_power_cut_gets_the_last_brightness_back(world):
    world.check()

    world.power_cut(ONE, TWO)
    world.check()

    assert sorted(world.manager.writes) == [(ONE, 20), (TWO, 20)]
    assert world.changed == ["tree"]


def test_a_level_changed_outside_dapple_is_the_one_that_comes_back(world):
    """Home Assistant's own Twinkly integration or the Twinkly app: Dapple sent neither."""
    world.check()
    world.wait(60)
    world.set_level(40)
    world.check()

    world.power_cut(ONE)
    world.check()

    assert world.manager.writes == [(ONE, 40)]


def test_applying_a_preset_with_no_brightness_does_not_lose_the_level(world):
    """All the built-in presets are like this. state.json forgets the level, the strand
    doesn't, and a restore based on state.json left these lights at full brightness."""
    world.state.record("tree", Pattern(slots=[Slot(rgbw=(255, 0, 0, 0))], brightness=None))
    world.check()

    world.power_cut(ONE)
    world.check()

    assert world.manager.writes == [(ONE, 20)]


def test_a_group_that_was_never_applied_to_still_gets_its_level_back(world):
    """state.json has nowhere to keep this one at all."""
    assert world.state.get("tree") is None
    world.check()

    world.power_cut(ONE)
    world.check()

    assert world.manager.writes == [(ONE, 20)]


def test_strands_in_a_group_get_their_own_levels_back(world):
    world.check()
    world.wait(60)
    world.set_level(40, TWO)
    world.check()

    world.power_cut(ONE, TWO)
    world.check()

    assert sorted(world.manager.writes) == [(ONE, 20), (TWO, 40)]


def test_only_the_strand_that_lost_power_is_touched(world):
    world.check()

    world.power_cut(TWO)
    world.check()

    assert world.manager.writes == [(TWO, 20)]


def test_it_restores_once_not_on_every_check(world):
    """Repeating it would undo a later change in the Twinkly app within a minute."""
    world.check()
    world.power_cut(ONE)
    world.check()
    world.manager.writes.clear()

    world.wait(60)
    world.check()
    world.wait(60)
    world.set_level(100, ONE)  # someone wants it bright
    world.check()
    world.wait(60)
    world.check()

    assert world.manager.writes == []
    assert world.manager.strands[ONE].brightness == 100


def test_a_full_brightness_someone_chose_stays_full(world):
    """No reboot, so 100 is a choice, and the next power cut should keep it too."""
    world.check()
    world.wait(60)
    world.set_level(100)
    world.check()

    world.power_cut(ONE)
    world.check()

    assert world.manager.writes == []


def test_first_sight_of_a_strand_changes_nothing(world):
    """Upgrading to this, or adding a strand, says nothing about what it should show."""
    world.set_level(100)

    world.check()

    assert world.manager.writes == []


def test_a_brightness_already_changed_since_the_reboot_is_kept(world):
    """Someone got there first, and it's theirs."""
    world.check()
    world.power_cut(ONE)
    world.set_level(40, ONE)

    world.check()

    assert world.manager.writes == []

    world.power_cut(ONE)
    world.check()
    assert world.manager.writes == [(ONE, 40)]


def test_an_outage_during_which_dapple_was_also_down_is_noticed(world):
    world.check()

    world.power_cut(ONE)
    world.watcher = world.new_watcher()
    world.check()

    assert world.manager.writes == [(ONE, 20)]


def test_restarting_dapple_alone_is_not_a_reboot(world):
    world.check()

    world.watcher = world.new_watcher()
    world.wait(30)
    world.set_level(100)
    world.check()

    assert world.manager.writes == []


def test_a_little_clock_wobble_across_a_restart_is_not_a_reboot(world):
    """Boot time comes from two clocks, so it never comes out quite the same."""
    world.check()

    world.watcher = world.new_watcher()
    world.now += 200  # the strand's uptime has fallen behind by 200 seconds
    world.set_level(100)
    world.check()

    assert world.manager.writes == []


def test_a_second_reboot_soon_after_the_first_is_still_a_reboot(world):
    """Power often flickers. Missing this one would save the firmware's 100 as the level."""
    world.check()
    world.power_cut(ONE)
    world.check()
    assert world.manager.writes == [(ONE, 20)]
    world.check()  # settles on the new uptime

    world.wait(120)
    world.manager.strands[ONE].uptime = 30
    world.manager.strands[ONE].brightness = 100
    world.check()

    assert world.manager.writes == [(ONE, 20), (ONE, 20)]
    assert world.manager.strands[ONE].brightness == 20


def test_a_second_reboot_is_caught_even_when_uptime_has_not_gone_backwards(world):
    """Unreachable for a while in between, so its new uptime can be as high as the last."""
    world.check()
    world.power_cut(ONE)
    world.check()

    world.power_cut(ONE)  # back at the same uptime as the reading before
    world.check()

    assert world.manager.writes == [(ONE, 20), (ONE, 20)]


def test_a_little_wobble_while_running_is_not_a_reboot(world):
    world.check()

    world.now += 20  # a slow request: the clock moved and the uptime reading didn't
    world.set_level(100)
    world.check()

    assert world.manager.writes == []


def test_a_strand_that_comes_back_later_is_restored_when_it_answers(world):
    world.check()
    world.power_cut(ONE)
    world.manager.strands[ONE].answering = False

    world.check()
    assert world.manager.writes == []

    world.manager.strands[ONE].answering = True
    world.check()
    assert world.manager.writes == [(ONE, 20)]


def test_a_refused_restore_is_tried_again(world):
    world.check()
    world.power_cut(ONE)
    world.manager.strands[ONE].refuses = True

    world.check()
    world.manager.strands[ONE].refuses = False
    world.wait(60)
    world.check()

    assert world.manager.writes == [(ONE, 20), (ONE, 20)]
    assert world.manager.strands[ONE].brightness == 20


def test_restoring_leaves_state_json_alone(world):
    """state.json is what Dapple was asked for, and nobody asked for this."""
    world.state.record("tree", Pattern(slots=[Slot(rgbw=(255, 0, 0, 0))], brightness=60))
    before = world.state.get("tree")
    world.check()
    world.power_cut(ONE)

    world.check()

    assert world.manager.writes == [(ONE, 20)]
    assert world.state.get("tree") == before


def test_firmware_that_reports_no_uptime_is_ignored(world):
    for strand in world.manager.strands.values():
        strand.answering = False

    world.check()

    assert world.manager.writes == []
    assert not world.path.exists()


def test_an_unchanged_strand_writes_nothing_to_disk(world):
    """This runs every minute, and the data folder may be on an SD card."""
    world.check()
    world.path.unlink()

    world.wait(60)
    world.check()

    assert not world.path.exists()


# ---- the store -------------------------------------------------------------


def test_boot_times_and_levels_survive_a_restart(tmp_path):
    BootStore(tmp_path / "boots.json").set(ONE, 123.5, 40)

    assert BootStore(tmp_path / "boots.json").get(ONE) == (123.5, 40)


def test_a_damaged_file_starts_empty(tmp_path, caplog):
    path = tmp_path / "boots.json"
    path.write_text("{not json")

    assert BootStore(path).get(ONE) is None
    assert "Could not read" in caplog.text


def test_a_failed_write_is_not_raised(tmp_path):
    """Nothing here is worth failing over: at worst one restore gets missed."""
    (tmp_path / "not-a-folder").write_text("")
    store = BootStore(tmp_path / "not-a-folder" / "boots.json")

    store.set(ONE, 1.0, 20)

    assert store.writable is False
    assert store.get(ONE) == (1.0, 20)


def test_the_file_is_plain_json(tmp_path):
    BootStore(tmp_path / "boots.json").set(ONE, 5.0, 20)

    assert json.loads((tmp_path / "boots.json").read_text()) == {
        ONE: {"boot": 5.0, "brightness": 20}
    }


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

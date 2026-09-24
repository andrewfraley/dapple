"""What each group is showing: recording it, reading it back, and degrading well."""

import json

import pytest

from app.models import Pattern, Slot
from app.state import StateStore

ORANGE = (255, 80, 0, 0)
WHITE = (0, 0, 0, 255)


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


def a_pattern(brightness=60):
    return Pattern(
        slots=[Slot(rgbw=ORANGE, weight=4), Slot(rgbw=WHITE, weight=1)],
        brightness=brightness,
    )


# ---- recording -------------------------------------------------------------


def test_a_fresh_store_knows_nothing(store):
    assert store.all() == {}
    assert store.get("tree") is None


def test_record_and_read_back(store):
    store.record("tree", a_pattern(), preset="Halloween")

    state = store.get("tree")
    assert state.pattern == a_pattern()
    assert state.preset == "Halloween"
    assert state.power == "on"


def test_state_survives_a_restart(store):
    store.record("tree", a_pattern(), preset="Halloween")

    reopened = StateStore(store.path)

    assert reopened.get("tree").pattern == a_pattern()
    assert reopened.get("tree").preset == "Halloween"


def test_groups_are_independent(store):
    store.record("tree", a_pattern(brightness=10))
    store.record("porch", a_pattern(brightness=90))

    assert store.get("tree").pattern.brightness == 10
    assert store.get("porch").pattern.brightness == 90


def test_recording_again_replaces(store):
    store.record("tree", a_pattern(), preset="Halloween")
    store.record("tree", a_pattern(brightness=20))

    assert store.get("tree").preset is None
    assert store.get("tree").pattern.brightness == 20


def test_an_applied_pattern_with_no_preset(store):
    assert store.record("tree", a_pattern()).preset is None


# ---- brightness and power --------------------------------------------------


def test_brightness_updates_the_stored_pattern_and_keeps_the_preset(store):
    """A preset applied to two groups then dimmed on one must not leave both
    claiming the same thing."""
    store.record("tree", a_pattern(brightness=60), preset="Halloween")

    store.set_brightness("tree", 15)

    assert store.get("tree").pattern.brightness == 15
    assert store.get("tree").preset == "Halloween"


def test_power_off_keeps_the_pattern(store):
    """The strand still holds the movie — it's just dark."""
    store.record("tree", a_pattern(), preset="Halloween")

    store.set_power("tree", False)

    assert store.get("tree").power == "off"
    assert store.get("tree").pattern == a_pattern()


def test_power_on_again(store):
    store.record("tree", a_pattern())
    store.set_power("tree", False)

    store.set_power("tree", True)

    assert store.get("tree").power == "on"


def test_brightness_and_power_on_an_unrecorded_group_do_nothing(store):
    assert store.set_brightness("tree", 50) is None
    assert store.set_power("tree", False) is None
    assert store.get("tree") is None


def test_applied_at_moves_forward(store):
    first = store.record("tree", a_pattern()).applied_at

    assert store.set_power("tree", False).applied_at >= first


# ---- forgetting ------------------------------------------------------------


def test_forget(store):
    store.record("tree", a_pattern())

    store.forget("tree")

    assert store.get("tree") is None
    assert StateStore(store.path).get("tree") is None


def test_forgetting_an_unknown_group_is_quiet(store):
    store.forget("nope")


# ---- the file itself -------------------------------------------------------


def test_the_file_is_json_keyed_by_group(store):
    store.record("tree", a_pattern(), preset="Halloween")

    raw = json.loads(store.path.read_text())

    assert list(raw) == ["tree"]
    assert raw["tree"]["preset"] == "Halloween"
    assert raw["tree"]["pattern"]["slots"][0]["rgbw"] == [255, 80, 0, 0]


def test_write_leaves_no_temp_files(store):
    store.record("tree", a_pattern())

    assert [p.name for p in store.path.parent.iterdir()] == ["state.json"]


def test_an_unreadable_file_does_not_crash(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json")

    assert StateStore(path).all() == {}


def test_one_bad_entry_does_not_hide_the_others(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "tree": {
                    "pattern": a_pattern().model_dump(),
                    "power": "on",
                    "applied_at": "2026-09-23T18:04:11Z",
                },
                "porch": {"pattern": {"slots": "nonsense"}},
            }
        )
    )

    store = StateStore(path)

    assert list(store.all()) == ["tree"]


# ---- a read-only /data -----------------------------------------------------


def test_recording_to_a_read_only_dir_does_not_raise(tmp_path):
    """The lights already changed. Failing the request here would make Home
    Assistant retry an operation that worked."""
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    store = StateStore(read_only / "state.json")

    state = store.record("tree", a_pattern())

    assert state.pattern == a_pattern()
    assert store.writable is False


def test_state_is_still_served_from_memory_when_it_cannot_be_written(tmp_path):
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    store = StateStore(read_only / "state.json")

    store.record("tree", a_pattern(), preset="Halloween")

    assert store.get("tree").preset == "Halloween"

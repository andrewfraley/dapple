"""The preset store: seeding, round-tripping, atomic writes, bad input."""

import json

import pytest

from app.models import Pattern, Slot
from app.presets import (
    PresetError,
    PresetStorageError,
    PresetStore,
    default_presets,
    validate_name,
)


@pytest.fixture
def store(tmp_path):
    return PresetStore(tmp_path / "presets.json")


def a_pattern(weight=3):
    return Pattern(
        slots=[Slot(rgbw=(10, 20, 30, 0), weight=weight), Slot(rgbw=(0, 0, 0, 255), weight=1)],
        layout="blocked",
        block_size=2,
        brightness=55,
    )


def test_seeds_defaults_on_first_run(tmp_path):
    path = tmp_path / "presets.json"
    store = PresetStore(path)

    assert set(store.names()) == set(default_presets())
    assert path.is_file()
    assert set(json.loads(path.read_text())) == set(default_presets())


def test_does_not_reseed_an_existing_file(tmp_path):
    path = tmp_path / "presets.json"
    PresetStore(path).save("Mine", a_pattern())
    PresetStore(path).delete("Halloween")

    reopened = PresetStore(path)
    assert "Halloween" not in reopened.names()
    assert "Mine" in reopened.names()


def test_seeding_can_be_skipped(tmp_path):
    assert PresetStore(tmp_path / "presets.json", seed_defaults=False).names() == []


def test_save_and_get_round_trip(store):
    saved = a_pattern()
    store.save("Test", saved)

    assert store.get("Test") == saved
    assert PresetStore(store.path).get("Test") == saved


def test_save_replaces(store):
    store.save("Test", a_pattern(weight=3))
    store.save("Test", a_pattern(weight=9))

    assert store.get("Test").slots[0].weight == 9
    assert store.names().count("Test") == 1


def test_get_missing(store):
    with pytest.raises(PresetError):
        store.get("Nope")


def test_delete(store):
    store.save("Test", a_pattern())
    store.delete("Test")

    assert "Test" not in store.names()
    assert "Test" not in PresetStore(store.path).names()


def test_delete_missing(store):
    with pytest.raises(PresetError):
        store.delete("Nope")


def test_name_is_trimmed(store):
    store.save("  Spaced  ", a_pattern())
    assert "Spaced" in store.names()


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "x" * 65])
def test_rejected_names(store, name):
    with pytest.raises(PresetError):
        store.save(name, a_pattern())


def test_write_is_atomic_and_leaves_no_temp_files(store):
    store.save("Test", a_pattern())

    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != "presets.json"]
    assert leftovers == []


def test_creates_missing_data_dir(tmp_path):
    store = PresetStore(tmp_path / "nested" / "deep" / "presets.json")
    store.save("Test", a_pattern())

    assert store.path.is_file()


def test_unreadable_file_does_not_crash(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text("{ this is not json")

    assert PresetStore(path).names() == []


def test_one_bad_preset_does_not_hide_the_others(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps(
            {
                "Good": a_pattern().model_dump(),
                "Bad": {"slots": [{"rgbw": [999, 0, 0, 0], "weight": 1}]},
            }
        )
    )

    store = PresetStore(path)
    assert store.names() == ["Good"]


def test_validate_name_returns_the_trimmed_name():
    assert validate_name("  Halloween ") == "Halloween"


# ---- an unwritable data directory -----------------------------------------


def test_a_read_only_data_dir_does_not_stop_startup(tmp_path, monkeypatch):
    """A bind-mounted /data owned by another user is the common case here —
    the app must still come up on the built-in presets."""
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)

    store = PresetStore(read_only / "presets.json")

    assert set(store.names()) == set(default_presets())
    assert store.writable is False


def test_saving_to_a_read_only_data_dir_says_why(tmp_path):
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    store = PresetStore(read_only / "presets.json")

    with pytest.raises(PresetStorageError, match="Cannot write"):
        store.save("Test", a_pattern())

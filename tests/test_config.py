"""Config loading: the groups shape, the pre-groups migration, env fallback."""

import pytest

from app.config import MIGRATED_GROUP_ID, load_config


def write_config(tmp_path, text):
    (tmp_path / "config.yaml").write_text(text)
    return tmp_path


GROUPED = """
groups:
  - id: tree
    name: Christmas tree
    strands:
      - name: TreeTop
        host: 10.0.0.1
      - name: TreeBottom
        host: 10.0.0.2
  - id: porch
    name: Front porch
    strands:
      - name: Porch
        host: 10.0.0.3
"""


# ---- the groups shape ------------------------------------------------------


def test_groups_load_in_order_with_their_strands(tmp_path):
    write_config(tmp_path, GROUPED)

    config = load_config(tmp_path, env={})

    assert [(g.id, g.name) for g in config.groups] == [
        ("tree", "Christmas tree"),
        ("porch", "Front porch"),
    ]
    assert [d.host for d in config.groups[0].devices] == ["10.0.0.1", "10.0.0.2"]


def test_devices_flattens_every_group(tmp_path):
    write_config(tmp_path, GROUPED)

    assert [d.host for d in load_config(tmp_path, env={}).devices] == [
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
    ]


def test_strand_overrides_survive(tmp_path):
    write_config(
        tmp_path,
        "groups:\n  - id: tree\n    name: Tree\n    strands:\n"
        "      - host: 10.0.0.5\n        number_of_led: 105\n        led_profile: rgbw\n",
    )

    device = load_config(tmp_path, env={}).groups[0].devices[0]

    assert (device.number_of_led, device.led_profile) == (105, "RGBW")


# ---- migration from the pre-groups shape -----------------------------------


def test_a_devices_only_file_becomes_one_group(tmp_path):
    """The old shape meant one pattern across everything with continuity across
    the join — which is exactly one group."""
    write_config(
        tmp_path,
        "devices:\n  - name: TreeTop\n    host: 10.0.0.1\n"
        "  - name: TreeBottom\n    host: 10.0.0.2\n",
    )

    config = load_config(tmp_path, env={})

    assert len(config.groups) == 1
    assert config.groups[0].id == MIGRATED_GROUP_ID
    assert [d.name for d in config.groups[0].devices] == ["TreeTop", "TreeBottom"]


def test_migration_does_not_rewrite_the_file(tmp_path):
    """Loading stays side-effect free — /data may be read-only."""
    write_config(tmp_path, "devices:\n  - name: A\n    host: 10.0.0.1\n")
    before = (tmp_path / "config.yaml").read_text()

    load_config(tmp_path, env={})

    assert (tmp_path / "config.yaml").read_text() == before


def test_an_empty_group_list_is_not_fatal(tmp_path):
    write_config(tmp_path, "groups: []\n")

    assert load_config(tmp_path, env={}).groups == []


def test_no_config_at_all_is_not_fatal(tmp_path):
    """A first run has no config file — the Strands page writes one."""
    config = load_config(tmp_path, env={})

    assert config.groups == []
    assert config.devices == []
    assert config.presets_path == tmp_path / "presets.json"
    assert config.state_path == tmp_path / "state.json"


# ---- tolerating a hand-edited file ----------------------------------------


def test_a_group_without_an_id_gets_one_from_its_name(tmp_path):
    write_config(tmp_path, "groups:\n  - name: Front Porch!\n    strands:\n      - host: 10.0.0.1\n")

    assert load_config(tmp_path, env={}).groups[0].id == "front-porch"


def test_a_group_without_a_name_falls_back_to_its_id(tmp_path):
    write_config(tmp_path, "groups:\n  - id: tree\n    strands:\n      - host: 10.0.0.1\n")

    assert load_config(tmp_path, env={}).groups[0].name == "tree"


def test_a_duplicate_group_id_is_renamed_not_fatal(tmp_path):
    write_config(
        tmp_path,
        "groups:\n  - id: tree\n    strands:\n      - host: 10.0.0.1\n"
        "  - id: tree\n    strands:\n      - host: 10.0.0.2\n",
    )

    assert [g.id for g in load_config(tmp_path, env={}).groups] == ["tree", "tree-2"]


def test_a_host_in_two_groups_keeps_the_first(tmp_path):
    """Two entries for one strand would fight over it."""
    write_config(
        tmp_path,
        "groups:\n  - id: a\n    strands:\n      - host: 10.0.0.1\n"
        "  - id: b\n    strands:\n      - host: 10.0.0.1\n      - host: 10.0.0.2\n",
    )

    config = load_config(tmp_path, env={})

    assert [d.host for d in config.groups[0].devices] == ["10.0.0.1"]
    assert [d.host for d in config.groups[1].devices] == ["10.0.0.2"]


@pytest.mark.parametrize("reserved", ["order", "all"])
def test_a_reserved_group_id_in_the_file_is_reslugged(tmp_path, reserved):
    """A group called 'order' would be shadowed by /api/config/groups/order."""
    write_config(
        tmp_path, f"groups:\n  - id: {reserved}\n    strands:\n      - host: 10.0.0.1\n"
    )

    assert load_config(tmp_path, env={}).groups[0].id == f"{reserved}-group"


def test_strand_without_host_is_an_error(tmp_path):
    write_config(tmp_path, "groups:\n  - id: tree\n    strands:\n      - name: Tree\n")

    with pytest.raises(ValueError, match="missing 'host'"):
        load_config(tmp_path, env={})


# ---- scalars ---------------------------------------------------------------


def test_scalars_from_yaml(tmp_path):
    write_config(tmp_path, "movie_frames: 2\ntimeout: 12\ngamma: 1.0\n" + GROUPED)

    config = load_config(tmp_path, env={})

    assert (config.movie_frames, config.timeout, config.gamma) == (2, 12.0, 1.0)


def test_scalars_from_env(tmp_path):
    config = load_config(
        tmp_path,
        env={"DAPPLE_MOVIE_FRAMES": "2", "DAPPLE_TIMEOUT": "2.5", "DAPPLE_GAMMA": "2.8"},
    )

    assert (config.movie_frames, config.timeout, config.gamma) == (2, 2.5, 2.8)


def test_gamma_defaults_to_srgb(tmp_path):
    assert load_config(tmp_path, env={}).gamma == 2.2

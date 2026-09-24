"""Editing groups and strands at runtime: validation, ordering, writing it back."""

import pytest
import yaml

from app.config import (
    AppConfig,
    ConfigError,
    ConfigStorageError,
    ConfigStore,
    DeviceConfig,
    MqttConfig,
    UnknownGroupError,
    load_config,
    normalize_device,
    slugify,
    unique_group_id,
)


@pytest.fixture
def store(tmp_path):
    return ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))


def written(store) -> dict:
    return yaml.safe_load(store.path.read_text())


def strand(name, host, **kwargs):
    return normalize_device(name, host, **kwargs)


def populate(store, **groups):
    """populate(store, Tree=["10.0.0.1", "10.0.0.2"]) → named groups with strands."""
    for name, hosts in groups.items():
        group = store.create_group(name)
        for index, host in enumerate(hosts, 1):
            store.add(strand(f"{name}{index}", host), group.id)
    return store


# ---- slugs and ids ---------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Tree", "tree"),
        ("Christmas tree", "christmas-tree"),
        ("Front Porch!", "front-porch"),
        ("  spaced  ", "spaced"),
        ("⭐", "group"),
        ("", "group"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_unique_group_id_counts_up():
    assert unique_group_id("tree", set()) == "tree"
    assert unique_group_id("tree", {"tree"}) == "tree-2"
    assert unique_group_id("tree", {"tree", "tree-2"}) == "tree-3"


@pytest.mark.parametrize("reserved", ["order", "all", "new"])
def test_reserved_ids_are_never_handed_out(reserved):
    """A group called 'order' would be shadowed by /api/config/groups/order."""
    assert unique_group_id(reserved, set()) == f"{reserved}-group"


def test_a_group_cannot_be_called_order(store):
    assert store.create_group("Order").id == "order-group"


# ---- group CRUD ------------------------------------------------------------


def test_create_group(store):
    group = store.create_group("Christmas tree")

    assert (group.id, group.name, group.devices) == ("christmas-tree", "Christmas tree", [])
    assert written(store)["groups"] == [
        {"id": "christmas-tree", "name": "Christmas tree", "strands": []}
    ]


def test_two_groups_with_the_same_name_get_distinct_ids(store):
    assert [store.create_group("Tree").id, store.create_group("Tree").id] == [
        "tree",
        "tree-2",
    ]


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
def test_rejected_group_names(store, name):
    with pytest.raises(ConfigError):
        store.create_group(name)


def test_rename_keeps_the_id(store):
    """The id is what the API and Home Assistant address — renaming is safe."""
    store.create_group("Tree")

    renamed = store.rename_group("tree", "Big tree")

    assert (renamed.id, renamed.name) == ("tree", "Big tree")


def test_rename_an_unknown_group(store):
    with pytest.raises(UnknownGroupError):
        store.rename_group("nope", "Whatever")


def test_delete_an_empty_group(store):
    store.create_group("Tree")

    store.delete_group("tree")

    assert store.groups == []


def test_deleting_a_group_with_strands_is_refused(store):
    """A group is a label; its strands are hardware someone configured."""
    populate(store, Tree=["10.0.0.1", "10.0.0.2"])

    with pytest.raises(ConfigError, match="still has 2 strands"):
        store.delete_group("tree")

    assert len(store.find_group("tree").devices) == 2


def test_the_refusal_counts_one_strand_properly(store):
    populate(store, Tree=["10.0.0.1"])

    with pytest.raises(ConfigError, match="still has 1 strand\\."):
        store.delete_group("tree")


def test_reorder_groups(store):
    populate(store, A=[], B=[], C=[])

    store.reorder_groups(["c", "a", "b"])

    assert [g.id for g in store.groups] == ["c", "a", "b"]
    assert [g["id"] for g in written(store)["groups"]] == ["c", "a", "b"]


@pytest.mark.parametrize("order", [["a"], ["a", "b", "z"], ["a", "a", "b"]])
def test_a_partial_group_reorder_is_refused(store, order):
    populate(store, A=[], B=[])

    with pytest.raises(ConfigError, match="every group exactly once"):
        store.reorder_groups(order)


# ---- adding strands --------------------------------------------------------


def test_a_strand_with_no_group_gets_one_of_its_own(store):
    """'A lone strand is a group of one', literally."""
    group, device = store.add(strand("Tree", "10.0.0.1"))

    assert (group.id, group.name) == ("tree", "Tree")
    assert group.devices == [device]


def test_add_into_an_existing_group(store):
    store.create_group("Tree")

    store.add(strand("TreeTop", "10.0.0.1"), "tree")
    store.add(strand("TreeBottom", "10.0.0.2"), "tree")

    assert [d.name for d in store.find_group("tree").devices] == ["TreeTop", "TreeBottom"]


def test_add_into_an_unknown_group(store):
    with pytest.raises(UnknownGroupError):
        store.add(strand("Tree", "10.0.0.1"), "nope")


def test_a_duplicate_host_in_another_group_is_refused(store):
    populate(store, A=["10.0.0.1"])
    store.create_group("B")

    with pytest.raises(ConfigError, match="already configured"):
        store.add(strand("Copy", "10.0.0.1"), "b")


def test_auto_settings_stay_out_of_the_file(store):
    store.add(strand("Tree", "10.0.0.1"))

    assert written(store)["groups"][0]["strands"] == [{"name": "Tree", "host": "10.0.0.1"}]


def test_pinned_settings_are_written(store):
    store.add(strand("Tree", "10.0.0.1", number_of_led=105, led_profile="rgbw"))

    assert written(store)["groups"][0]["strands"] == [
        {"name": "Tree", "host": "10.0.0.1", "number_of_led": 105, "led_profile": "RGBW"}
    ]


# ---- editing and removing strands ------------------------------------------


def test_update_keeps_the_strand_in_its_group_and_position(store):
    populate(store, Tree=["10.0.0.1", "10.0.0.2", "10.0.0.3"])

    store.update("10.0.0.2", strand("Renamed", "10.0.0.2"))

    assert [d.name for d in store.find_group("tree").devices] == [
        "Tree1",
        "Renamed",
        "Tree3",
    ]


def test_update_can_change_the_host(store):
    populate(store, Tree=["10.0.0.1"])

    store.update("10.0.0.1", strand("Tree1", "10.0.0.9"))

    assert [d.host for d in store.devices] == ["10.0.0.9"]


def test_update_onto_another_strands_host_is_refused(store):
    populate(store, Tree=["10.0.0.1", "10.0.0.2"])

    with pytest.raises(ConfigError, match="already configured"):
        store.update("10.0.0.2", strand("Tree2", "10.0.0.1"))


def test_delete_a_strand(store):
    populate(store, Tree=["10.0.0.1", "10.0.0.2"])

    store.delete("10.0.0.1")

    assert [d.host for d in store.find_group("tree").devices] == ["10.0.0.2"]


def test_delete_an_unknown_strand(store):
    with pytest.raises(ConfigError, match="No strand configured"):
        store.delete("10.0.0.9")


def test_deleting_the_last_strand_leaves_an_empty_group(store):
    populate(store, Tree=["10.0.0.1"])

    store.delete("10.0.0.1")

    assert store.find_group("tree").devices == []


# ---- moving between groups -------------------------------------------------


def test_move_appends_by_default(store):
    populate(store, Tree=["10.0.0.1"], Porch=["10.0.0.2", "10.0.0.3"])

    store.move("10.0.0.1", "porch")

    assert store.find_group("tree").devices == []
    assert [d.host for d in store.find_group("porch").devices] == [
        "10.0.0.2",
        "10.0.0.3",
        "10.0.0.1",
    ]


def test_move_at_an_index(store):
    populate(store, Tree=["10.0.0.1"], Porch=["10.0.0.2", "10.0.0.3"])

    store.move("10.0.0.1", "porch", index=0)

    assert [d.host for d in store.find_group("porch").devices][0] == "10.0.0.1"


def test_move_within_the_same_group_repositions(store):
    populate(store, Tree=["10.0.0.1", "10.0.0.2", "10.0.0.3"])

    store.move("10.0.0.3", "tree", index=0)

    assert [d.host for d in store.find_group("tree").devices] == [
        "10.0.0.3",
        "10.0.0.1",
        "10.0.0.2",
    ]


def test_move_to_an_unknown_group(store):
    populate(store, Tree=["10.0.0.1"])

    with pytest.raises(UnknownGroupError):
        store.move("10.0.0.1", "nope")


def test_group_of(store):
    populate(store, Tree=["10.0.0.1"], Porch=["10.0.0.2"])

    assert store.group_of("10.0.0.2").id == "porch"


# ---- ordering within a group ----------------------------------------------


def test_reorder_is_scoped_to_one_group(store):
    populate(store, Tree=["10.0.0.1", "10.0.0.2"], Porch=["10.0.0.3", "10.0.0.4"])

    store.reorder("tree", ["10.0.0.2", "10.0.0.1"])

    assert [d.host for d in store.find_group("tree").devices] == ["10.0.0.2", "10.0.0.1"]
    assert [d.host for d in store.find_group("porch").devices] == ["10.0.0.3", "10.0.0.4"]


@pytest.mark.parametrize(
    "order",
    [["10.0.0.1"], ["10.0.0.1", "10.0.0.3"], ["10.0.0.1", "10.0.0.1"]],
)
def test_a_partial_reorder_within_a_group_is_refused(store, order):
    """Order within a group is the physical seam — a partial list would move it."""
    populate(store, Tree=["10.0.0.1", "10.0.0.2"], Porch=["10.0.0.3"])

    with pytest.raises(ConfigError, match="every strand in 'Tree' exactly once"):
        store.reorder("tree", order)


# ---- persistence -----------------------------------------------------------


def test_what_is_written_is_what_loads_back(tmp_path):
    store = ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))
    populate(store, Tree=["10.0.0.1", "10.0.0.2"], Porch=["10.0.0.3"])

    reloaded = load_config(tmp_path, env={})

    assert reloaded.groups == store.groups


def test_settings_written_in_the_file_survive_a_write(tmp_path):
    (tmp_path / "config.yaml").write_text("movie_frames: 2\ntimeout: 12.0\ngamma: 2.8\n")
    ConfigStore(load_config(tmp_path, env={})).create_group("Tree")

    reloaded = load_config(tmp_path, env={})

    assert (reloaded.movie_frames, reloaded.timeout, reloaded.gamma) == (2, 12.0, 2.8)


def test_env_settings_are_not_frozen_into_the_file_by_an_edit(tmp_path):
    """The file outranks the env. Writing env values into it on the first edit
    would make DAPPLE_GAMMA and friends stop working from then on."""
    env = {"DAPPLE_MOVIE_FRAMES": "2", "DAPPLE_TIMEOUT": "12", "DAPPLE_GAMMA": "2.8"}
    ConfigStore(load_config(tmp_path, env=env)).create_group("Tree")

    written = (tmp_path / "config.yaml").read_text()
    assert "gamma" not in written and "timeout" not in written and "movie_frames" not in written
    assert load_config(tmp_path, env={"DAPPLE_GAMMA": "1.0"}).gamma == 1.0


def test_saving_marks_the_file_as_the_config_source(tmp_path):
    """A first run starts with no file; the first edit creates one."""
    store = ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))
    assert store.config.source == "none"

    store.create_group("Tree")

    assert store.config.source == str(store.path)


def test_the_written_file_explains_itself(store):
    store.create_group("Tree")
    body = store.path.read_text()

    assert body.startswith("#")
    assert "physical order" in body
    assert "display order only" in body


def test_a_read_only_data_dir_says_why(tmp_path):
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    store = ConfigStore(AppConfig(data_dir=read_only, config_path=read_only / "config.yaml"))

    with pytest.raises(ConfigStorageError, match="Cannot write"):
        store.create_group("Tree")

    assert store.writable is False


def test_a_failed_write_leaves_no_temp_file(tmp_path):
    store = ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))
    store.create_group("Tree")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


def test_a_failed_write_changes_nothing(tmp_path):
    """Every mutation has to be all-or-nothing.

    The rollback restores the previous group list, so nothing may mutate a
    GroupConfig or its device list in place — if anything did, the restored
    list would already carry the change and this guarantee would be a lie.
    """
    writable = tmp_path / "rw"
    writable.mkdir()
    config = AppConfig(data_dir=writable, config_path=writable / "config.yaml")
    store = ConfigStore(config)
    populate(store, Tree=["10.0.0.1", "10.0.0.2"], Porch=["10.0.0.3"], Spare=[])
    snapshot = [(g.id, g.name, [d.host for d in g.devices]) for g in store.groups]
    writable.chmod(0o500)

    attempts = [
        lambda: store.create_group("New"),
        lambda: store.rename_group("tree", "Renamed"),
        lambda: store.delete_group("spare"),  # empty, so it gets as far as the write
        lambda: store.reorder_groups(["porch", "tree", "spare"]),
        lambda: store.add(strand("Extra", "10.0.0.9"), "tree"),
        lambda: store.update("10.0.0.1", strand("Renamed", "10.0.0.1")),
        lambda: store.delete("10.0.0.1"),
        lambda: store.move("10.0.0.1", "porch"),
        lambda: store.reorder("tree", ["10.0.0.2", "10.0.0.1"]),
        lambda: store.set_mqtt(MqttConfig(host="10.0.0.50", enabled=True)),
    ]
    for attempt in attempts:
        with pytest.raises(ConfigStorageError):
            attempt()

    assert [(g.id, g.name, [d.host for d in g.devices]) for g in store.groups] == snapshot
    assert store.config.mqtt is None


def test_next_names_count_up(store):
    assert store.next_name() == "Strand 1"
    store.add(strand("Tree", "10.0.0.1"))
    assert store.next_name() == "Strand 2"

"""The /api surface, against a fake DeviceManager — no strands involved."""

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, ConfigStore, DeviceConfig, MqttConfig, load_config
from app.main import app
from app.models import DeviceInfo, DeviceResult, StrandSegment
from app.presets import PresetStore
from app.state import StateStore

PATTERN = {
    "slots": [
        {"rgbw": [255, 80, 0, 0], "weight": 4},
        {"rgbw": [128, 0, 255, 0], "weight": 1},
    ],
    "layout": "interleaved",
    "block_size": 1,
    "brightness": 60,
}


class FakeDevice:
    """Stands in for devices.Device — enough of it that the routes can't tell."""

    def __init__(self, host, number_of_led=105, name=None):
        self.host = host
        self.name = name or host
        self.number_of_led = number_of_led
        self.led_profile = "RGBW"
        self.reachable = True
        self.config = DeviceConfig(name=self.name, host=host)


class FakeGroup:
    def __init__(self, id, name, devices):
        self.id = id
        self.name = name
        self.devices = [
            device if isinstance(device, FakeDevice) else FakeDevice(*device) for device in devices
        ]

    def total_leds(self):
        return sum(device.number_of_led for device in self.devices)

    def reachable(self):
        return sum(1 for device in self.devices if device.reachable)

    def offsets(self):
        offsets, running = [], 0
        for device in self.devices:
            offsets.append(running)
            running += device.number_of_led
        return offsets

    def segments(self):
        return [
            StrandSegment(
                name=device.name,
                host=device.host,
                offset=offset,
                number_of_led=device.number_of_led,
                led_profile="RGBW",
            )
            for device, offset in zip(self.devices, self.offsets())
        ]


class FakeManager:
    """Records what the routes asked for, so tests can assert on it.

    Mirrors DeviceManager: groups own their strands, and every write names one.
    """

    def __init__(self, groups=None, fail=False, store=None):
        self.store = store
        self.fail = fail
        self.applied = []
        self.brightness = []
        self.calls = []
        self.synced = 0
        self.probed = []
        self.groups = (
            groups
            if groups is not None
            else [FakeGroup("tree", "Tree", [("10.0.0.1", 105), ("10.0.0.2", 100)])]
        )

    # -- what the routes use --
    @property
    def devices(self):
        return [device for group in self.groups for device in group.devices]

    def group(self, group_id):
        from app.config import UnknownGroupError

        for group in self.groups:
            if group.id == group_id:
                return group
        raise UnknownGroupError(f"No group called {group_id!r}")

    def sync(self):
        self.synced += 1
        # Follow the real config store when one is wired in, so config routes
        # see their own edits reflected back.
        if self.store is not None:
            self.groups = [
                FakeGroup(
                    group.id,
                    group.name,
                    [FakeDevice(d.host, d.number_of_led or 105, d.name) for d in group.devices],
                )
                for group in self.store.groups
            ]
        return self.groups

    async def refresh_one(self, host):
        self.probed.append(host)
        return DeviceInfo(
            name="probed", host=host, reachable=True, number_of_led=105, led_profile="RGBW"
        )

    def _results(self, group):
        return [
            DeviceResult(
                name=device.name,
                host=device.host,
                ok=not self.fail,
                error="boom" if self.fail else None,
            )
            for device in group.devices
        ]

    async def health(self):
        self.calls.append("health")
        return [
            DeviceResult(
                name=device.name,
                host=device.host,
                ok=not self.fail,
                error="boom" if self.fail else None,
            )
            for device in self.devices
        ]

    async def info(self, with_live_state=False):
        self.calls.append("info")
        return [
            DeviceInfo(
                name=device.name,
                host=device.host,
                group=group.id,
                reachable=True,
                number_of_led=device.number_of_led,
                led_profile="RGBW",
            )
            for group in self.groups
            for device in group.devices
        ]

    async def refresh(self):
        self.calls.append("refresh")
        return await self.info()

    async def apply_pattern(self, group_id, pattern):
        self.applied.append((group_id, pattern))
        return self._results(self.group(group_id))

    async def set_brightness(self, group_id, value):
        self.brightness.append((group_id, value))
        return self._results(self.group(group_id))

    async def turn_on(self, group_id):
        self.calls.append(("on", group_id))
        return self._results(self.group(group_id))

    async def turn_off(self, group_id):
        self.calls.append(("off", group_id))
        return self._results(self.group(group_id))


class FakeBridge:
    """Stands in for mqtt.MqttBridge: records what the routes told it."""

    def __init__(self):
        self.status = "disabled"
        self.error = None
        self.changed = []
        self.config_changes = 0
        self.restarts = 0

    def group_changed(self, group_id):
        self.changed.append(group_id)

    def config_changed(self):
        self.config_changes += 1

    def start(self):
        pass

    async def stop(self):
        pass

    async def restart(self):
        self.restarts += 1
        self.status = "connecting"


@pytest.fixture
def config(tmp_path):
    return AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml")


@pytest.fixture
def manager():
    return FakeManager()


def build_client(config, manager, seed_presets=True):
    app.state.config = config
    app.state.manager = manager
    app.state.presets = PresetStore(config.presets_path, seed_defaults=seed_presets)
    app.state.group_state = StateStore(config.state_path)
    app.state.strands = ConfigStore(config)
    app.state.mqtt = FakeBridge()
    manager.store = app.state.strands
    return TestClient(app)


@pytest.fixture
def client(config, manager):
    with build_client(config, manager) as client:
        yield client


def add(client, host, **fields):
    return client.post("/api/config/strands", json={"host": host, **fields})


# ---- status ---------------------------------------------------------------


def test_health(client):
    body = client.get("/api/health").json()

    assert body["ok"] is True
    assert [d["host"] for d in body["devices"]] == ["10.0.0.1", "10.0.0.2"]


def test_health_reports_a_down_strand(config):
    with build_client(config, FakeManager(fail=True)) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is False
    assert body["devices"][0]["error"] == "boom"


def test_devices_are_tagged_with_their_group(client):
    body = client.get("/api/devices").json()

    assert body[0]["group"] == "tree"
    assert body[0]["number_of_led"] == 105


def test_devices_refresh(client, manager):
    assert client.post("/api/devices/refresh").status_code == 200
    assert "refresh" in manager.calls


# ---- groups ---------------------------------------------------------------


def test_list_groups(client):
    body = client.get("/api/groups").json()

    assert [g["id"] for g in body] == ["tree"]
    assert body[0]["total_leds"] == 205
    assert [s["offset"] for s in body[0]["segments"]] == [0, 105]
    assert body[0]["state"] is None


def test_get_one_group(client):
    assert client.get("/api/groups/tree").json()["name"] == "Tree"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/groups/nope", None),
        ("post", "/api/groups/nope/apply", PATTERN),
        ("post", "/api/groups/nope/preset", {"name": "Halloween"}),
        ("post", "/api/groups/nope/brightness", {"value": 50}),
        ("post", "/api/groups/nope/on", None),
        ("post", "/api/groups/nope/off", None),
    ],
)
def test_an_unknown_group_is_a_404(client, method, path, body):
    response = getattr(client, method)(path, **({"json": body} if body else {}))

    assert response.status_code == 404
    assert "No group called" in response.json()["detail"]


def test_segments_are_group_relative(config):
    """Every group starts at its own LED 0 — the point of the whole feature."""
    groups = [
        FakeGroup("tree", "Tree", [("10.0.0.1", 105), ("10.0.0.2", 100)]),
        FakeGroup("porch", "Porch", [("10.0.0.3", 80), ("10.0.0.4", 90)]),
    ]
    with build_client(config, FakeManager(groups=groups)) as client:
        body = client.get("/api/groups").json()

    assert [s["offset"] for s in body[0]["segments"]] == [0, 105]
    assert [s["offset"] for s in body[1]["segments"]] == [0, 80]


# ---- applying -------------------------------------------------------------


def test_apply_to_a_group(client, manager):
    body = client.post("/api/groups/tree/apply", json=PATTERN).json()

    assert body["ok"] is True
    assert manager.applied[0][0] == "tree"
    assert manager.applied[0][1].slots[0].weight == 4


def test_apply_records_what_the_group_is_showing(client):
    client.post("/api/groups/tree/apply", json=PATTERN)

    state = client.get("/api/groups/tree").json()["state"]
    assert state["pattern"]["slots"][0]["weight"] == 4
    assert state["preset"] is None
    assert state["power"] == "on"


def test_apply_to_an_empty_group(config):
    groups = [FakeGroup("empty", "Empty", [])]
    with build_client(config, FakeManager(groups=groups)) as client:
        response = client.post("/api/groups/empty/apply", json=PATTERN)

    assert response.status_code == 503
    assert "has no strands" in response.json()["detail"]


@pytest.mark.parametrize(
    "bad",
    [
        {"slots": [{"rgbw": [256, 0, 0, 0], "weight": 1}]},
        {"slots": [{"rgbw": [0, 0, 0], "weight": 1}]},
        {"slots": [{"rgbw": [0, 0, 0, 0], "weight": 1}], "layout": "spiral"},
        {"slots": [{"rgbw": [0, 0, 0, 0], "weight": 1}], "brightness": 101},
    ],
)
def test_apply_rejects_bad_patterns(client, bad):
    assert client.post("/api/groups/tree/apply", json=bad).status_code == 422


def test_groups_hold_different_patterns(config):
    groups = [
        FakeGroup("tree", "Tree", [("10.0.0.1", 105)]),
        FakeGroup("porch", "Porch", [("10.0.0.3", 80)]),
    ]
    other = {**PATTERN, "brightness": 10}
    with build_client(config, FakeManager(groups=groups)) as client:
        client.post("/api/groups/tree/apply", json=PATTERN)
        client.post("/api/groups/porch/apply", json=other)
        body = {g["id"]: g for g in client.get("/api/groups").json()}

    assert body["tree"]["state"]["pattern"]["brightness"] == 60
    assert body["porch"]["state"]["pattern"]["brightness"] == 10


# ---- presets --------------------------------------------------------------


def test_presets_are_seeded(client):
    assert set(client.get("/api/presets").json()) == {
        "Halloween",
        "Christmas",
        "July 4th",
        "Warm white",
    }


def test_put_get_delete_preset(client):
    assert client.put("/api/presets/Test", json=PATTERN).status_code == 200
    assert client.get("/api/presets/Test").json()["slots"][0]["weight"] == 4
    assert client.delete("/api/presets/Test").json() == {"ok": True}
    assert client.get("/api/presets/Test").status_code == 404


def test_apply_a_preset_to_a_group(client, manager):
    """What Home Assistant calls."""
    body = client.post("/api/groups/tree/preset", json={"name": "Halloween"}).json()

    assert body["ok"] is True
    assert manager.applied[0][0] == "tree"
    assert len(manager.applied[0][1].slots) == 2


def test_applying_a_preset_records_its_name(client):
    client.post("/api/groups/tree/preset", json={"name": "Halloween"})

    assert client.get("/api/groups/tree").json()["state"]["preset"] == "Halloween"


def test_a_preset_name_with_a_space(client):
    """Names travel in the body precisely so spaces aren't a path-encoding problem."""
    assert client.post("/api/groups/tree/preset", json={"name": "Warm white"}).status_code == 200


def test_applying_an_unknown_preset(client, manager):
    response = client.post("/api/groups/tree/preset", json={"name": "Nope"})

    assert response.status_code == 404
    assert manager.applied == []


def test_presets_are_shared_across_groups(config):
    groups = [
        FakeGroup("tree", "Tree", [("10.0.0.1", 105)]),
        FakeGroup("porch", "Porch", [("10.0.0.3", 80)]),
    ]
    with build_client(config, FakeManager(groups=groups)) as client:
        client.post("/api/groups/tree/preset", json={"name": "Halloween"})
        client.post("/api/groups/porch/preset", json={"name": "Halloween"})
        body = {g["id"]: g["state"]["preset"] for g in client.get("/api/groups").json()}

    assert body == {"tree": "Halloween", "porch": "Halloween"}


# ---- power ----------------------------------------------------------------


def test_brightness(client, manager):
    assert client.post("/api/groups/tree/brightness", json={"value": 40}).json()["ok"]
    assert manager.brightness == [("tree", 40)]


def test_brightness_updates_the_recorded_pattern(client):
    client.post("/api/groups/tree/apply", json=PATTERN)
    client.post("/api/groups/tree/brightness", json={"value": 15})

    state = client.get("/api/groups/tree").json()["state"]
    assert state["pattern"]["brightness"] == 15


@pytest.mark.parametrize("value", [-1, 101, "high"])
def test_brightness_rejects_out_of_range(client, value):
    assert client.post("/api/groups/tree/brightness", json={"value": value}).status_code == 422


def test_on_and_off(client, manager):
    assert client.post("/api/groups/tree/on").json()["ok"] is True
    assert client.post("/api/groups/tree/off").json()["ok"] is True
    assert manager.calls[-2:] == [("on", "tree"), ("off", "tree")]


def test_off_keeps_the_pattern_and_flips_the_power(client):
    client.post("/api/groups/tree/apply", json=PATTERN)
    client.post("/api/groups/tree/off")

    state = client.get("/api/groups/tree").json()["state"]
    assert state["power"] == "off"
    assert state["pattern"]["slots"][0]["weight"] == 4


# ---- preview --------------------------------------------------------------


def test_preview_matches_the_pattern(client):
    body = client.post("/api/preview", json={"pattern": PATTERN, "num_leds": 10}).json()

    assert len(body["leds"]) == 10
    assert body["leds"].count([128, 0, 255, 0]) == 2


def test_preview_honours_offset(client):
    full = client.post("/api/preview", json={"pattern": PATTERN, "num_leds": 10}).json()["leds"]
    shifted = client.post(
        "/api/preview", json={"pattern": PATTERN, "num_leds": 5, "offset": 3}
    ).json()["leds"]

    assert shifted == full[3:8]


# ---- group configuration --------------------------------------------------


def test_config_starts_empty(client):
    body = client.get("/api/config").json()

    assert body["groups"] == []
    assert body["writable"] is True
    assert body["state_writable"] is True


def test_create_a_group(client):
    response = client.post("/api/config/groups", json={"name": "Christmas tree"})

    assert response.status_code == 201
    assert response.json() == {"id": "christmas-tree", "name": "Christmas tree", "strands": []}


def test_rename_a_group_keeps_its_id(client):
    client.post("/api/config/groups", json={"name": "Tree"})

    body = client.put("/api/config/groups/tree", json={"name": "Big tree"}).json()

    assert (body["id"], body["name"]) == ("tree", "Big tree")


def test_rename_an_unknown_group_is_404(client):
    assert client.put("/api/config/groups/nope", json={"name": "X"}).status_code == 404


def test_delete_an_empty_group(client):
    client.post("/api/config/groups", json={"name": "Tree"})

    assert client.delete("/api/config/groups/tree").json() == {"ok": True}
    assert client.get("/api/config").json()["groups"] == []


def test_deleting_a_group_with_strands_is_refused(client):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", group="tree")

    response = client.delete("/api/config/groups/tree")

    assert response.status_code == 400
    assert "Move or remove" in response.json()["detail"]


def test_deleting_a_group_forgets_its_state(client):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", group="tree")
    client.post("/api/groups/tree/apply", json=PATTERN)
    client.delete("/api/config/strands/10.0.0.1")

    client.delete("/api/config/groups/tree")
    client.post("/api/config/groups", json={"name": "Tree"})

    assert client.get("/api/config").json()["groups"][0]["id"] == "tree"


def test_reorder_groups(client):
    for name in ("A", "B", "C"):
        client.post("/api/config/groups", json={"name": name})

    body = client.put("/api/config/groups/order", json={"ids": ["c", "a", "b"]}).json()

    assert [g["id"] for g in body] == ["c", "a", "b"]


def test_group_order_is_not_shadowed_by_the_rename_route(client):
    """PUT /api/config/groups/order must reorder, not rename a group called
    'order' — which is also why 'order' is a reserved id."""
    client.post("/api/config/groups", json={"name": "A"})

    response = client.put("/api/config/groups/order", json={"ids": ["a"]})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_a_group_cannot_be_named_into_the_order_route(client):
    assert client.post("/api/config/groups", json={"name": "Order"}).json()["id"] == "order-group"


def test_a_partial_group_reorder_is_refused(client):
    for name in ("A", "B"):
        client.post("/api/config/groups", json={"name": name})

    response = client.put("/api/config/groups/order", json={"ids": ["a"]})

    assert response.status_code == 400
    assert "every group exactly once" in response.json()["detail"]


# ---- strand configuration -------------------------------------------------


def test_a_strand_with_no_group_gets_one_of_its_own(client):
    body = add(client, "192.168.40.21", name="Tree").json()

    assert body["group"] == "tree"
    assert client.get("/api/config").json()["groups"][0]["strands"][0]["host"] == "192.168.40.21"


def test_add_into_an_existing_group(client):
    client.post("/api/config/groups", json={"name": "Tree"})

    assert add(client, "10.0.0.1", group="tree").json()["group"] == "tree"
    assert add(client, "10.0.0.2", group="tree").json()["group"] == "tree"
    assert len(client.get("/api/config").json()["groups"]) == 1


def test_adding_probes_the_strand(client, manager):
    body = add(client, "192.168.40.21").json()

    assert manager.probed == ["192.168.40.21"]
    assert body["info"]["reachable"] is True


def test_add_into_an_unknown_group_is_404(client):
    assert add(client, "10.0.0.1", group="nope").status_code == 404


def test_a_duplicate_host_is_refused(client):
    add(client, "10.0.0.1")

    response = add(client, "10.0.0.1")

    assert response.status_code == 400
    assert "already configured" in response.json()["detail"]


@pytest.mark.parametrize(
    "host,expected", [("http://10.0.0.1", "not a URL"), ("10.0.0.1/xled", "not a valid")]
)
def test_a_bad_host_explains_itself(client, host, expected):
    response = add(client, host)

    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_update_a_strand(client, manager):
    add(client, "10.0.0.1", name="Tree")

    response = client.put(
        "/api/config/strands/10.0.0.1",
        json={"name": "Big tree", "host": "10.0.0.1"},
    )

    assert response.status_code == 200
    assert response.json()["config"]["name"] == "Big tree"
    assert manager.probed[-1] == "10.0.0.1"


def test_move_a_strand_between_groups(client):
    add(client, "10.0.0.1", name="Tree")
    add(client, "10.0.0.2", name="Porch")

    body = client.put("/api/config/strands/10.0.0.2/group", json={"group": "tree"}).json()

    assert body["group"] == "tree"
    groups = {
        g["id"]: [s["host"] for s in g["strands"]]
        for g in client.get("/api/config").json()["groups"]
    }
    assert groups == {"tree": ["10.0.0.1", "10.0.0.2"], "porch": []}


def test_move_at_an_index(client):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", group="tree")
    add(client, "10.0.0.2", group="tree")

    client.put("/api/config/strands/10.0.0.2/group", json={"group": "tree", "index": 0})

    hosts = [s["host"] for s in client.get("/api/config").json()["groups"][0]["strands"]]
    assert hosts == ["10.0.0.2", "10.0.0.1"]


def test_move_to_an_unknown_group_is_404(client):
    add(client, "10.0.0.1")

    assert (
        client.put("/api/config/strands/10.0.0.1/group", json={"group": "nope"}).status_code == 404
    )


def test_reorder_within_a_group(client):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", group="tree")
    add(client, "10.0.0.2", group="tree")

    body = client.put(
        "/api/config/groups/tree/strands/order",
        json={"hosts": ["10.0.0.2", "10.0.0.1"]},
    ).json()

    assert [s["host"] for s in body] == ["10.0.0.2", "10.0.0.1"]


def test_a_partial_reorder_within_a_group_is_refused(client):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", group="tree")
    add(client, "10.0.0.2", group="tree")

    response = client.put("/api/config/groups/tree/strands/order", json={"hosts": ["10.0.0.1"]})

    assert response.status_code == 400
    assert "exactly once" in response.json()["detail"]


def test_delete_a_strand(client):
    add(client, "10.0.0.1")
    add(client, "10.0.0.2")

    assert client.delete("/api/config/strands/10.0.0.1").json() == {"ok": True}
    hosts = [s["host"] for g in client.get("/api/config").json()["groups"] for s in g["strands"]]
    assert hosts == ["10.0.0.2"]


def test_the_strand_list_survives_a_restart(client, config):
    client.post("/api/config/groups", json={"name": "Tree"})
    add(client, "10.0.0.1", name="TreeTop", group="tree")
    add(client, "10.0.0.2", name="TreeBottom", group="tree")

    reloaded = load_config(config.data_dir, env={}).groups

    assert [(g.id, [d.host for d in g.devices]) for g in reloaded] == [
        ("tree", ["10.0.0.1", "10.0.0.2"])
    ]


# ---- a read-only /data ----------------------------------------------------


def test_a_config_edit_reports_500_and_changes_nothing(tmp_path):
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    config = AppConfig(data_dir=read_only, config_path=read_only / "config.yaml")

    with build_client(config, FakeManager(), seed_presets=False) as client:
        response = client.post("/api/config/groups", json={"name": "Tree"})
        listed = client.get("/api/config").json()

    assert response.status_code == 500
    assert "Cannot write" in response.json()["detail"]
    assert listed["writable"] is False
    assert listed["groups"] == []


def test_an_apply_still_succeeds_when_state_cannot_be_written(tmp_path):
    """The lights changed. Failing here would make Home Assistant retry a
    successful operation."""
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    config = AppConfig(data_dir=read_only, config_path=read_only / "config.yaml")

    with build_client(config, FakeManager(), seed_presets=False) as client:
        response = client.post("/api/groups/tree/apply", json=PATTERN)
        listed = client.get("/api/config").json()

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert listed["state_writable"] is False


# ---- home assistant over mqtt ------------------------------------------------


def test_mqtt_starts_out_switched_off(client):
    assert client.get("/api/config/mqtt").json() == {
        "enabled": False,
        "host": None,
        "port": 1883,
        "username": None,
        "password_set": False,
        "discovery_prefix": "homeassistant",
        "topic_prefix": "dapple",
        "status": "disabled",
        "error": None,
    }


def test_saving_mqtt_settings_reconnects_and_writes_the_file(client, config):
    response = client.put(
        "/api/config/mqtt",
        json={"enabled": True, "host": "10.0.0.50", "username": "ha", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "connecting"
    assert app.state.mqtt.restarts == 1
    assert "secret" in config.config_path.read_text()


def test_mqtt_stays_off_unless_it_is_switched_on(client, config):
    """Filling in the address alone mustn't start publishing to someone's broker."""
    body = client.put("/api/config/mqtt", json={"host": "10.0.0.50"}).json()

    assert (body["enabled"], config.mqtt.enabled) == (False, False)


def test_the_mqtt_password_is_never_sent_back(client):
    """The settings page is on the LAN with no auth; the password stays in /data."""
    client.put("/api/config/mqtt", json={"host": "10.0.0.50", "password": "secret"})

    for body in (client.get("/api/config/mqtt").text, client.get("/api/config").text):
        assert "secret" not in body
    assert client.get("/api/config/mqtt").json()["password_set"] is True


def test_leaving_the_password_out_keeps_the_saved_one(client, config):
    """The form can't show the password, so saving it unchanged must not wipe it."""
    client.put("/api/config/mqtt", json={"host": "10.0.0.50", "password": "secret"})
    client.put("/api/config/mqtt", json={"host": "10.0.0.51"})
    assert config.mqtt.password == "secret"

    client.put("/api/config/mqtt", json={"host": "10.0.0.51", "password": ""})
    assert config.mqtt.password is None


def test_switching_mqtt_off_keeps_the_settings_for_next_time(client, config):
    client.put(
        "/api/config/mqtt", json={"enabled": True, "host": "10.0.0.50", "password": "secret"}
    )
    body = client.put("/api/config/mqtt", json={"enabled": False, "host": "10.0.0.50"}).json()

    assert (body["enabled"], body["host"], body["password_set"]) == (False, "10.0.0.50", True)


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"enabled": True, "host": ""}, "broker's address"),
        ({"host": "mqtt://10.0.0.50"}, "not a URL"),
        ({"host": "10.0.0.50", "topic_prefix": "Dapple/Dev"}, "topic prefix"),
        ({"host": "10.0.0.50", "discovery_prefix": "home#"}, "discovery prefix"),
    ],
)
def test_unusable_mqtt_settings_say_what_to_fix(client, payload, message):
    response = client.put("/api/config/mqtt", json=payload)

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert app.state.mqtt.restarts == 0


def test_applying_a_preset_tells_home_assistant(client):
    client.post("/api/groups/tree/preset", json={"name": "Halloween"})
    client.post("/api/groups/tree/off")

    assert app.state.mqtt.changed == ["tree", "tree"]


def test_preset_and_group_edits_update_home_assistant(client):
    """New effect lists, renamed and deleted lights."""
    client.put("/api/presets/Easter", json=PATTERN)
    client.delete("/api/presets/Easter")
    client.post("/api/config/groups", json={"name": "Porch"})

    assert app.state.mqtt.config_changes == 3

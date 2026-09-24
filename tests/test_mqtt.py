"""The Home Assistant bridge, against an in-memory broker — no network, no strands.

Async is driven with plain ``asyncio.run``, as in test_devices.py.
"""

import asyncio
import json
from types import SimpleNamespace

import aiomqtt
import pytest

from app.actions import GroupActions
from app.config import AppConfig, ConfigStore, MqttConfig, normalize_device
from app.models import DeviceInfo, DeviceResult, GroupState, Pattern, Slot
from app.mqtt import MqttBridge, Topics, availability, run_command, state_payload
from app.presets import PresetStore
from app.state import StateStore

SETTINGS = MqttConfig(host="10.0.0.50", enabled=True)


def matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard matching, enough of it for these tests."""
    want, got = pattern.split("/"), topic.split("/")
    if len(want) != len(got):
        return False
    return all(w in ("+", g) for w, g in zip(want, got))


class FakeBroker:
    """Retained messages, a subscription list and an inbox — one client at a time."""

    def __init__(self, refuse=False):
        self.refuse = refuse
        self.published = []
        self.retained = {}
        self.subscriptions = []
        self.connections = []
        self.inbox = None

    def client(self, **kwargs):
        self.connections.append(kwargs)
        return FakeClient(self)

    def send(self, topic, payload):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self.inbox.put_nowait(SimpleNamespace(topic=topic, payload=payload.encode()))

    def drop(self):
        self.inbox.put_nowait(None)

    def last(self, topic):
        for published_topic, payload, _retain in reversed(self.published):
            if published_topic == topic:
                return payload
        return None

    def last_json(self, topic):
        payload = self.last(topic)
        return None if payload is None else json.loads(payload)

    def count(self, topic):
        return sum(1 for published_topic, *_ in self.published if published_topic == topic)


class FakeClient:
    def __init__(self, broker):
        self.broker = broker

    async def __aenter__(self):
        if self.broker.refuse:
            raise aiomqtt.MqttError("Connection refused")
        self.broker.inbox = asyncio.Queue()
        return self

    async def __aexit__(self, *exc):
        return False

    async def publish(self, topic, payload, qos=0, retain=False):
        payload = payload.decode() if isinstance(payload, bytes) else payload
        self.broker.published.append((topic, payload, retain))
        if retain:
            if payload:
                self.broker.retained[topic] = payload
            else:
                self.broker.retained.pop(topic, None)

    async def subscribe(self, topic, qos=0):
        self.broker.subscriptions.append(topic)
        for retained_topic, payload in list(self.broker.retained.items()):
            if matches(topic, retained_topic):
                self.broker.send(retained_topic, payload)

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
            message = await self.broker.inbox.get()
            if message is None:
                raise aiomqtt.MqttError("Connection lost")
            yield message


class Strand:
    def __init__(self, host, name=None):
        self.host = host
        self.name = name or host
        self.mode = "off"
        self.brightness = 100
        self.answers = True


class Group:
    def __init__(self, id, name, strands):
        self.id = id
        self.name = name
        self.devices = strands


class StrandManager:
    """Duck-types DeviceManager with strands that remember their mode, so what
    the bridge reads back follows from what it (or anyone else) did."""

    def __init__(self, groups):
        self.groups = groups
        self.applied = []
        self.reads = []
        #: raised by the next live_state call, once
        self.fail_next_read = None

    def group(self, group_id):
        from app.config import UnknownGroupError

        for group in self.groups:
            if group.id == group_id:
                return group
        raise UnknownGroupError(f"No group called {group_id!r}")

    def _each(self, group_id, change):
        results = []
        for strand in self.group(group_id).devices:
            change(strand)
            results.append(DeviceResult(name=strand.name, host=strand.host, ok=True))
        return results

    async def apply_pattern(self, group_id, pattern):
        self.applied.append((group_id, pattern))

        def apply(strand):
            strand.mode = "movie"
            if pattern.brightness is not None:
                strand.brightness = pattern.brightness

        return self._each(group_id, apply)

    async def set_brightness(self, group_id, value):
        return self._each(group_id, lambda strand: setattr(strand, "brightness", value))

    async def turn_on(self, group_id):
        return self._each(group_id, lambda strand: setattr(strand, "mode", "movie"))

    async def turn_off(self, group_id):
        return self._each(group_id, lambda strand: setattr(strand, "mode", "off"))

    async def live_state(self, group_id=None):
        self.reads.append(group_id)
        error, self.fail_next_read = self.fail_next_read, None
        if error is not None:
            raise error
        return [
            DeviceInfo(
                name=strand.name,
                host=strand.host,
                group=group.id,
                reachable=strand.answers,
                mode=strand.mode if strand.answers else None,
                brightness=strand.brightness if strand.answers else None,
            )
            for group in self.groups
            if group_id in (None, group.id)
            for strand in group.devices
        ]


class Rig:
    """A bridge wired to real stores, a fake broker and strands that remember."""

    def __init__(self, tmp_path, settings=SETTINGS, refuse=False, poll_interval=3600):
        config = AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
        self.strands = ConfigStore(config)
        for name, hosts in {"Tree": ["10.0.0.1", "10.0.0.2"], "Porch": ["10.0.0.3"]}.items():
            group = self.strands.create_group(name)
            for host in hosts:
                self.strands.add(normalize_device(None, host), group.id)
        self.strands.create_group("Spare")
        config.mqtt = settings
        self.presets = PresetStore(config.presets_path)
        self.state = StateStore(config.state_path)
        self.manager = StrandManager(
            [
                Group("tree", "Tree", [Strand("10.0.0.1"), Strand("10.0.0.2")]),
                Group("porch", "Porch", [Strand("10.0.0.3")]),
                Group("spare", "Spare", []),
            ]
        )
        self.broker = FakeBroker(refuse=refuse)
        self.actions = GroupActions(self.manager, self.presets, self.state)
        self.bridge = MqttBridge(
            self.strands,
            self.actions,
            version="test",
            client_factory=self.broker.client,
            poll_interval=poll_interval,
        )
        self.actions.on_change = self.bridge.group_changed
        self.topics = Topics(settings or SETTINGS)

    def strand(self, host):
        return next(s for g in self.manager.groups for s in g.devices if s.host == host)

    async def until(self, check, what="condition"):
        for _ in range(500):
            if check():
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"timed out waiting for {what}")

    async def connected(self):
        self.bridge.start()
        await self.until(
            lambda: self.broker.last(self.topics.availability("spare")) is not None,
            "the initial publish",
        )

    async def settle(self):
        """Let queued messages and spawned publishes run."""
        for _ in range(20):
            await asyncio.sleep(0.005)

    def run(self, scenario):
        async def wrapped():
            try:
                await scenario()
            finally:
                await self.bridge.stop()

        asyncio.run(wrapped())


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---- discovery ---------------------------------------------------------------


def test_each_group_is_announced_as_a_light_with_the_presets_as_effects(rig):
    async def scenario():
        await rig.connected()
        config = json.loads(rig.broker.retained["homeassistant/light/dapple/tree/config"])
        assert config["unique_id"] == "dapple_tree"
        assert config["device"]["name"] == "Tree"
        assert config["effect_list"] == rig.presets.names()
        assert config["command_topic"] == "dapple/tree/set"
        assert rig.broker.retained["dapple/status"] == "online"
        assert "homeassistant/light/dapple/porch/config" in rig.broker.retained

    rig.run(scenario)


def test_the_connection_leaves_a_will_so_ha_sees_dapple_go_away(rig):
    async def scenario():
        await rig.connected()
        will = rig.broker.connections[0]["will"]
        assert (will.topic, will.payload, will.retain) == ("dapple/status", "offline", True)

    rig.run(scenario)


def test_stopping_says_offline_because_a_clean_disconnect_skips_the_will(rig):
    async def scenario():
        await rig.connected()

    rig.run(scenario)
    assert rig.broker.retained["dapple/status"] == "offline"


def test_renaming_a_group_renames_the_light_but_keeps_its_unique_id(rig):
    """HA ties automations, areas and history to the unique id."""

    async def scenario():
        await rig.connected()
        rig.strands.rename_group("tree", "Big tree")
        rig.manager.groups[0].name = "Big tree"
        rig.bridge.config_changed()
        await rig.settle()
        config = json.loads(rig.broker.retained["homeassistant/light/dapple/tree/config"])
        assert (config["device"]["name"], config["unique_id"]) == ("Big tree", "dapple_tree")

    rig.run(scenario)


def test_saving_a_preset_adds_it_to_the_effect_list(rig):
    async def scenario():
        await rig.connected()
        rig.presets.save("Easter", Pattern(slots=[Slot(rgbw=(255, 200, 255, 0))]))
        rig.bridge.config_changed()
        await rig.settle()
        config = json.loads(rig.broker.retained["homeassistant/light/dapple/porch/config"])
        assert "Easter" in config["effect_list"]

    rig.run(scenario)


def test_a_deleted_group_loses_its_light(rig):
    async def scenario():
        await rig.connected()
        rig.strands.delete_group("spare")
        rig.manager.groups.pop()
        rig.bridge.config_changed()
        await rig.settle()
        assert "homeassistant/light/dapple/spare/config" not in rig.broker.retained
        assert rig.broker.last("homeassistant/light/dapple/spare/config") == ""
        assert "dapple/spare/state" not in rig.broker.retained

    rig.run(scenario)


def test_a_group_deleted_while_disconnected_is_cleared_on_connect(rig):
    """The retained config outlives the connection; without this the light
    would sit in HA as unavailable forever."""
    rig.broker.retained["homeassistant/light/dapple/old/config"] = "{}"
    other = "homeassistant/light/dapple-dev/old/config"
    rig.broker.retained[other] = "{}"

    async def scenario():
        await rig.connected()
        await rig.settle()
        assert "homeassistant/light/dapple/old/config" not in rig.broker.retained
        assert other in rig.broker.retained  # another Dapple's light is left alone
        assert "homeassistant/light/dapple/tree/config" in rig.broker.retained

    rig.run(scenario)


def test_home_assistant_restarting_gets_everything_again(rig):
    async def scenario():
        await rig.connected()
        before = rig.broker.count("homeassistant/light/dapple/tree/config")
        rig.broker.send("homeassistant/status", "online")
        await rig.settle()
        assert rig.broker.count("homeassistant/light/dapple/tree/config") == before + 1
        assert rig.broker.count("dapple/tree/state") >= 2  # repeated despite no change

    rig.run(scenario)


# ---- commands ------------------------------------------------------------------


def test_an_effect_applies_the_preset_and_records_it_like_the_http_route(rig):
    async def scenario():
        await rig.connected()
        rig.broker.send("dapple/tree/set", {"state": "ON", "effect": "Halloween"})
        await rig.until(lambda: rig.manager.applied, "the apply")
        await rig.settle()
        assert rig.manager.applied == [("tree", rig.presets.get("Halloween"))]
        assert rig.state.get("tree").preset == "Halloween"
        state = rig.broker.last_json("dapple/tree/state")
        assert (state["state"], state["effect"]) == ("ON", "Halloween")

    rig.run(scenario)


def test_off_turns_the_group_off_and_ha_is_told(rig):
    async def scenario():
        await rig.connected()
        rig.broker.send("dapple/tree/set", {"state": "ON", "effect": "Christmas"})
        await rig.settle()
        rig.broker.send("dapple/tree/set", {"state": "OFF"})
        await rig.settle()
        assert rig.strand("10.0.0.1").mode == "off"
        assert rig.state.get("tree").power == "off"
        assert rig.broker.last_json("dapple/tree/state")["state"] == "OFF"
        assert rig.strand("10.0.0.3").mode == "off"  # the porch was never touched

    rig.run(scenario)


def test_a_command_only_reaches_its_own_group(rig):
    async def scenario():
        await rig.connected()
        rig.broker.send("dapple/porch/set", {"state": "ON", "effect": "Christmas"})
        await rig.settle()
        assert rig.manager.applied == [("porch", rig.presets.get("Christmas"))]
        assert rig.strand("10.0.0.1").mode == "off"

    rig.run(scenario)


def test_a_malformed_or_unknown_command_is_ignored_and_the_bridge_keeps_going(rig):
    async def scenario():
        await rig.connected()
        rig.broker.send("dapple/tree/set", {"state": "ON", "effect": "No such preset"})
        rig.broker.send("dapple/tree/set", "not json")
        rig.broker.send("dapple/nowhere/set", {"state": "ON"})
        rig.broker.send("dapple/spare/set", {"state": "ON"})  # a group with no strands
        rig.broker.send("dapple/tree/set", {"state": "ON", "effect": "Christmas"})
        await rig.until(lambda: rig.manager.applied, "the valid command")
        assert rig.manager.applied == [("tree", rig.presets.get("Christmas"))]
        assert rig.bridge.status == "connected"

    rig.run(scenario)


@pytest.mark.parametrize(
    "command,expected",
    [
        ({"state": "OFF"}, ["off"]),
        ({"state": "OFF", "brightness": 40}, ["off"]),
        ({"state": "ON"}, ["on"]),
        ({"state": "ON", "brightness": 40}, ["brightness 40", "on"]),
        ({"state": "ON", "effect": "Halloween"}, ["preset Halloween"]),
        (
            {"state": "ON", "effect": "Halloween", "brightness": 40},
            ["preset Halloween", "brightness 40"],
        ),
        ({"state": "ON", "brightness": 400}, ["brightness 100", "on"]),
    ],
)
def test_ha_commands_become_the_same_calls_the_rest_api_makes(command, expected):
    """Brightness after the preset, because a preset can carry its own brightness
    and what the user just asked for should win."""
    calls = []

    class Recorder:
        async def turn_off(self, group_id):
            calls.append("off")

        async def turn_on(self, group_id):
            calls.append("on")

        async def set_brightness(self, group_id, value):
            calls.append(f"brightness {value}")

        async def apply_preset(self, group_id, name):
            calls.append(f"preset {name}")

    asyncio.run(run_command(Recorder(), "tree", command))
    assert calls == expected


# ---- state read back from the strands ------------------------------------------


def test_a_strand_turned_off_outside_dapple_is_reported_off_on_the_next_poll(tmp_path):
    rig = Rig(tmp_path, poll_interval=0.01)

    async def scenario():
        await rig.connected()
        rig.broker.send("dapple/porch/set", {"state": "ON", "effect": "Christmas"})
        await rig.until(
            lambda: (rig.broker.last_json("dapple/porch/state") or {}).get("state") == "ON",
            "porch on",
        )
        rig.strand("10.0.0.3").mode = "off"  # the native integration's off switch
        await rig.until(
            lambda: rig.broker.last_json("dapple/porch/state")["state"] == "OFF",
            "the poll to notice",
        )
        assert rig.state.get("porch").power == "on"  # state.json is what Dapple sent

    rig.run(scenario)


def test_an_unchanged_poll_publishes_nothing(tmp_path):
    rig = Rig(tmp_path, poll_interval=0.01)

    async def scenario():
        await rig.connected()
        before = len(rig.broker.published)
        await asyncio.sleep(0.1)
        assert len(rig.broker.published) == before

    rig.run(scenario)


def test_a_group_whose_strands_all_stop_answering_goes_unavailable(tmp_path):
    rig = Rig(tmp_path, poll_interval=0.01)

    async def scenario():
        await rig.connected()
        assert rig.broker.last("dapple/porch/availability") == "online"
        rig.strand("10.0.0.3").answers = False
        await rig.until(lambda: rig.broker.last("dapple/porch/availability") == "offline")

    rig.run(scenario)


def recorded(preset="Halloween", power="on", brightness=None):
    return GroupState(
        pattern=Pattern(slots=[Slot(rgbw=(255, 0, 0, 0))], brightness=brightness),
        preset=preset,
        power=power,
        applied_at="2026-01-01T00:00:00Z",
    )


def live(*modes, brightness=70):
    return [
        DeviceInfo(name=f"s{i}", host=f"10.0.0.{i}", mode=mode, brightness=brightness)
        for i, mode in enumerate(modes, 1)
    ]


def test_a_native_color_change_clears_the_effect():
    """The strand is lit, but not with the preset — HA shouldn't claim it is."""
    state = state_payload(recorded(), live("movie", "color"), {"Halloween"})
    assert (state["state"], state["effect"]) == ("ON", None)


def test_the_effect_survives_being_switched_off_and_on():
    assert state_payload(recorded(), live("off", "off"), {"Halloween"})["effect"] == "Halloween"


def test_a_deleted_preset_is_not_reported_as_the_effect():
    assert state_payload(recorded(), live("movie"), {"Christmas"})["effect"] is None


def test_the_group_is_on_while_any_strand_is_lit():
    assert state_payload(recorded(), live("off", "movie"), {"Halloween"})["state"] == "ON"


def test_strands_that_dont_answer_fall_back_to_what_dapple_last_sent():
    state = state_payload(recorded(power="off", brightness=30), live(None), {"Halloween"})
    assert (state["state"], state["brightness"]) == ("OFF", 30)
    assert availability(live(None)) == "offline"


def test_a_group_never_applied_to_is_off_with_no_effect():
    state = state_payload(None, [], set())
    assert (state["state"], state["effect"]) == ("OFF", None)


# ---- connection lifecycle ------------------------------------------------------


def test_a_refused_connection_is_reported_with_what_to_check(tmp_path):
    rig = Rig(tmp_path, refuse=True)

    async def scenario():
        rig.bridge.start()
        await rig.until(lambda: rig.bridge.status == "error", "the failure")
        assert "10.0.0.50:1883" in rig.bridge.error
        assert "username and password" in rig.bridge.error

    rig.run(scenario)


def test_a_lost_connection_reconnects_and_republishes(rig, monkeypatch):
    monkeypatch.setattr("app.mqtt.RETRY_MIN_SECONDS", 0.01)

    async def scenario():
        await rig.connected()
        rig.broker.drop()
        await rig.until(lambda: len(rig.broker.connections) == 2, "the reconnect")
        await rig.until(lambda: rig.bridge.status == "connected")
        assert rig.broker.retained["dapple/status"] == "online"

    rig.run(scenario)


def test_an_unexpected_error_reconnects_instead_of_going_quiet(rig, monkeypatch):
    """Not an MqttError: before, the task just ended and the status stayed
    "connected" with nothing listening."""
    monkeypatch.setattr("app.mqtt.RETRY_MIN_SECONDS", 0.01)
    rig.manager.fail_next_read = RuntimeError("strand read blew up")

    async def scenario():
        rig.bridge.start()
        await rig.until(lambda: len(rig.broker.connections) == 2, "the reconnect")
        await rig.until(lambda: rig.bridge.status == "connected")
        assert rig.broker.last(rig.topics.availability("spare")) is not None

    rig.run(scenario)


def test_a_change_to_one_group_reads_only_that_groups_strands(rig):
    async def scenario():
        await rig.connected()
        rig.manager.reads.clear()
        rig.bridge.group_changed("porch")
        await rig.settle()
        assert rig.manager.reads == ["porch"]

    rig.run(scenario)


def test_without_a_broker_address_it_never_connects(tmp_path):
    rig = Rig(tmp_path, settings=None)

    async def scenario():
        rig.bridge.start()
        await rig.settle()
        assert rig.broker.connections == []
        assert rig.bridge.status == "disabled"

    rig.run(scenario)


def test_saved_but_switched_off_settings_never_connect(tmp_path):
    rig = Rig(tmp_path, settings=MqttConfig(host="10.0.0.50"))

    async def scenario():
        rig.bridge.start()
        await rig.settle()
        assert rig.broker.connections == []
        assert rig.bridge.status == "disabled"

    rig.run(scenario)


def test_notifications_while_disconnected_are_harmless(rig):
    rig.bridge.group_changed("tree")
    rig.bridge.config_changed()
    assert rig.broker.published == []


def test_another_dapple_on_the_same_broker_uses_its_own_topics(tmp_path):
    rig = Rig(
        tmp_path, settings=MqttConfig(host="10.0.0.50", enabled=True, topic_prefix="dapple-dev")
    )

    async def scenario():
        await rig.connected()
        config = json.loads(rig.broker.retained["homeassistant/light/dapple-dev/tree/config"])
        assert config["unique_id"] == "dapple-dev_tree"
        assert config["command_topic"] == "dapple-dev/tree/set"

    rig.run(scenario)


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("dapple/tree/set", "tree"),
        ("dapple/a/b/set", None),
        ("dapple//set", None),
        ("dapple/tree/state", None),
        ("other/tree/set", None),
    ],
)
def test_only_well_formed_command_topics_name_a_group(topic, expected):
    assert Topics(SETTINGS).command_group(topic) == expected

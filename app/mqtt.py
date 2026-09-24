"""Home Assistant over MQTT: each group is a light, and its presets are the effects.

Dapple publishes Home Assistant's MQTT discovery, so the lights appear in HA with
no YAML. Topics, under the topic prefix (``dapple`` unless changed):

    dapple/status                               online / offline (the last will)
    dapple/<group>/state                        {"state", "brightness", "effect", ...}
    dapple/<group>/availability                 online while any strand answers
    dapple/<group>/set                          commands from HA, the same JSON shape
    homeassistant/light/dapple/<group>/config   discovery, retained

Published state is read back from the strands, not taken from state.json: the
native Twinkly integration and the Twinkly app change them behind Dapple's back,
and HA should show what is actually lit. state.json stays the record of what
Dapple sent, and nothing here writes it except through :class:`GroupActions`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import suppress
from typing import Any, Callable, Sequence

import aiomqtt

from app.actions import GroupActions
from app.config import ConfigStore, MqttConfig
from app.models import DeviceInfo, GroupState
from app.state import live_state

log = logging.getLogger(__name__)

POLL_SECONDS = 60.0
RETRY_MIN_SECONDS = 5.0
RETRY_MAX_SECONDS = 60.0
SUPPORT_URL = "https://github.com/andrewfraley/dapple"


class Topics:
    """Every topic one Dapple instance uses, from its settings."""

    def __init__(self, settings: MqttConfig):
        self.prefix = settings.topic_prefix
        self.discovery_prefix = settings.discovery_prefix
        self.status = f"{self.prefix}/status"
        self.ha_status = f"{self.discovery_prefix}/status"
        self.commands = f"{self.prefix}/+/set"
        self.discovery_base = f"{self.discovery_prefix}/light/{self.prefix}"
        self.discoveries = f"{self.discovery_base}/+/config"

    def state(self, group_id: str) -> str:
        return f"{self.prefix}/{group_id}/state"

    def availability(self, group_id: str) -> str:
        return f"{self.prefix}/{group_id}/availability"

    def command(self, group_id: str) -> str:
        return f"{self.prefix}/{group_id}/set"

    def discovery(self, group_id: str) -> str:
        return f"{self.discovery_base}/{group_id}/config"

    def unique_id(self, group_id: str) -> str:
        return f"{self.prefix}_{group_id}"

    def _middle(self, topic: str, start: str, end: str) -> str | None:
        if topic.startswith(start + "/") and topic.endswith("/" + end):
            middle = topic[len(start) + 1 : -len(end) - 1]
            if middle and "/" not in middle:
                return middle
        return None

    def command_group(self, topic: str) -> str | None:
        return self._middle(topic, self.prefix, "set")

    def discovery_group(self, topic: str) -> str | None:
        return self._middle(topic, self.discovery_base, "config")


# ---- payloads --------------------------------------------------------------


def discovery_payload(
    topics: Topics, group_id: str, group_name: str, effects: list[str], version: str
) -> dict[str, Any]:
    """One group as an HA light, in the JSON schema.

    Each group is its own HA device, so it can be put in an area. The entity
    takes the device's name, which follows a rename; the unique id doesn't.
    """
    unique_id = topics.unique_id(group_id)
    return {
        "name": None,
        "unique_id": unique_id,
        "schema": "json",
        "command_topic": topics.command(group_id),
        "state_topic": topics.state(group_id),
        "availability": [{"topic": topics.status}, {"topic": topics.availability(group_id)}],
        "availability_mode": "all",
        "brightness": True,
        "brightness_scale": 100,
        "supported_color_modes": ["brightness"],
        "effect": True,
        "effect_list": effects,
        "device": {
            "identifiers": [unique_id],
            "name": group_name,
            "manufacturer": "Dapple",
            "model": "Twinkly group",
            "sw_version": version,
        },
        "origin": {"name": "Dapple", "sw_version": version, "support_url": SUPPORT_URL},
    }


def state_payload(
    recorded: GroupState | None, infos: Sequence[DeviceInfo], presets: set[str]
) -> dict[str, Any]:
    """What HA should show for a group: live where the strands answered.

    When none answer, fall back to what Dapple last sent — the light shows as
    unavailable then anyway.
    """
    live = live_state(recorded, infos, presets)
    if live.power is not None:
        on = live.power == "on"
    else:
        on = recorded is not None and recorded.power == "on"
    brightness = live.brightness
    if brightness is None and recorded is not None:
        brightness = recorded.pattern.brightness

    payload: dict[str, Any] = {
        "state": "ON" if on else "OFF",
        "color_mode": "brightness",
        "effect": live.preset,
    }
    if brightness is not None:
        payload["brightness"] = brightness
    return payload


def availability(infos: Sequence[DeviceInfo]) -> str:
    """Online while any strand in the group answers — the rest can still be driven."""
    return "online" if any(info.mode is not None for info in infos) else "offline"


async def run_command(actions: GroupActions, group_id: str, command: dict[str, Any]) -> None:
    """Carry out one HA command through the same code path as the HTTP routes.

    HA sends ``state`` on every call, plus whatever changed. An effect implies
    on, since applying a pattern switches the strands to it.
    """
    state = str(command.get("state", "")).upper()
    if state == "OFF":
        await actions.turn_off(group_id)
        return
    effect = command.get("effect")
    if effect is not None:
        await actions.apply_preset(group_id, str(effect))
    if command.get("brightness") is not None:
        await actions.set_brightness(group_id, max(0, min(100, int(command["brightness"]))))
    if effect is None and state == "ON":
        await actions.turn_on(group_id)


# ---- the connection --------------------------------------------------------


class MqttBridge:
    """Keeps one broker connection up and HA in step with the groups.

    Configuration and preset changes call :meth:`config_changed`; anything that
    changes the lights calls :meth:`group_changed` via :class:`GroupActions`.
    Both are no-ops while disconnected — connecting republishes everything.

    Commands from HA go through ``actions``, the same instance the HTTP routes
    use, so there is one place that records state and notifies.
    """

    def __init__(
        self,
        strands: ConfigStore,
        actions: GroupActions,
        *,
        version: str = "",
        client_factory: Callable[..., Any] | None = None,
        poll_interval: float = POLL_SECONDS,
    ):
        self.strands = strands
        self.actions = actions
        self.presets = actions.presets
        self.group_state = actions.group_state
        self.manager = actions.manager
        self.version = version
        self.client_factory = client_factory or aiomqtt.Client
        self.poll_interval = poll_interval
        self.status = "disabled"
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._client = None
        self._topics: Topics | None = None
        #: Group ids with a discovery config on the broker, so deletes can clear them.
        self._discovered: set[str] = set()
        #: Last payload per state/availability topic; the poll publishes changes only.
        self._last: dict[str, str] = {}
        self._pending: set[asyncio.Task] = set()
        self._retry_delay = RETRY_MIN_SECONDS

    @property
    def settings(self) -> MqttConfig | None:
        return self.strands.config.mqtt

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        settings = self.settings
        if settings is None or not settings.enabled:
            self.status, self.error = "disabled", None
            return
        self.status, self.error = "connecting", None
        self._task = asyncio.create_task(self._run(settings))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        pending = list(self._pending)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.status, self.error = "disabled", None

    async def restart(self) -> None:
        await self.stop()
        self.start()

    async def _run(self, settings: MqttConfig) -> None:
        topics = Topics(settings)
        self._retry_delay = RETRY_MIN_SECONDS
        while True:
            try:
                client = self.client_factory(
                    hostname=settings.host,
                    port=settings.port,
                    username=settings.username,
                    password=settings.password,
                    will=aiomqtt.Will(topics.status, "offline", qos=1, retain=True),
                )
                async with client:
                    await self._serve(client, topics, settings)
            except Exception as exc:
                # Anything, not just MqttError: a task that died here would sit
                # at "connected" with nothing listening, and never come back.
                if isinstance(exc, aiomqtt.MqttError):
                    error = (
                        f"Could not talk to the MQTT broker at {settings.host}:{settings.port} "
                        f"({exc}). Check the address and port, and the username and password "
                        "if your broker needs them."
                    )
                else:
                    error = f"The Home Assistant connection failed ({type(exc).__name__}: {exc})."
                if error != self.error:  # once per distinct failure, not every retry
                    log.warning(
                        "%s Retrying, backing off to once every %ds.",
                        error,
                        int(RETRY_MAX_SECONDS),
                        exc_info=not isinstance(exc, aiomqtt.MqttError),
                    )
                self.status, self.error = "error", error
            else:
                self.status = "connecting"
            await asyncio.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, RETRY_MAX_SECONDS)

    async def _serve(self, client, topics: Topics, settings: MqttConfig) -> None:
        self._client, self._topics = client, topics
        self.status, self.error = "connected", None
        self._retry_delay = RETRY_MIN_SECONDS
        log.info("Connected to MQTT broker at %s:%s", settings.host, settings.port)
        poller = None
        try:
            await client.publish(topics.status, "online", qos=1, retain=True)
            await client.subscribe(topics.commands, qos=1)
            await client.subscribe(topics.ha_status)
            # Retained configs left from before — a group deleted while we were
            # disconnected — come back on this subscription and get cleared.
            await client.subscribe(topics.discoveries)
            self._last.clear()
            await self._publish_discovery(client, topics)
            await self._publish_states(client, topics)
            poller = asyncio.create_task(self._poll(client, topics))
            async for message in client.messages:
                await self._handle(client, topics, message)
        finally:
            if poller is not None:
                poller.cancel()
            self._client = None
            # A clean disconnect doesn't fire the will, so say it ourselves.
            # This fails harmlessly when the connection is what went away.
            with suppress(Exception):
                await asyncio.wait_for(
                    client.publish(topics.status, "offline", qos=1, retain=True), 2
                )

    # ---- incoming ----------------------------------------------------------

    async def _handle(self, client, topics: Topics, message) -> None:
        topic = str(message.topic)
        payload = message.payload
        if isinstance(payload, str):
            payload = payload.encode()
        elif not isinstance(payload, (bytes, bytearray)):
            payload = b"" if payload is None else str(payload).encode()
        payload = bytes(payload)

        if topic == topics.ha_status:
            if payload == b"online":  # HA restarted and forgot everything
                self._last.clear()
                await self._publish_discovery(client, topics)
                await self._publish_states(client, topics)
            return

        group_id = topics.discovery_group(topic)
        if group_id is not None:
            if payload and group_id not in self._group_ids():
                log.info("Removing the Home Assistant light for deleted group %r", group_id)
                await self._forget(client, topics, group_id)
            return

        group_id = topics.command_group(topic)
        if group_id is None:
            return
        try:
            command = json.loads(payload)
            if not isinstance(command, dict):
                raise ValueError("expected a JSON object")
            await run_command(self.actions, group_id, command)
        except Exception as exc:  # one bad command mustn't drop the connection
            log.warning("Ignoring MQTT command for %r (%s): %s", group_id, payload[:200], exc)
            # Put HA's optimistic view back to what's true.
            await self._publish_states(client, topics, force=True)

    # ---- outgoing ----------------------------------------------------------

    def _group_ids(self) -> set[str]:
        return {group.id for group in self.manager.groups}

    async def _publish_discovery(self, client, topics: Topics) -> None:
        effects = self.presets.names()
        current = set()
        for group in self.manager.groups:
            current.add(group.id)
            payload = discovery_payload(topics, group.id, group.name, effects, self.version)
            await client.publish(topics.discovery(group.id), json.dumps(payload), retain=True)
        for gone in self._discovered - current:
            await self._forget(client, topics, gone)
        self._discovered = current

    async def _forget(self, client, topics: Topics, group_id: str) -> None:
        """An empty retained config is how HA is told an entity is gone."""
        for topic in (
            topics.discovery(group_id),
            topics.state(group_id),
            topics.availability(group_id),
        ):
            await client.publish(topic, b"", retain=True)
            self._last.pop(topic, None)
        self._discovered.discard(group_id)

    async def _publish_states(
        self, client, topics: Topics, force: bool = False, group_id: str | None = None
    ) -> None:
        """Every group's state, or just ``group_id``'s — reading a group means
        asking each of its strands, so a change to one shouldn't ask them all."""
        infos = await self.manager.live_state(group_id)
        by_group: dict[str, list[DeviceInfo]] = defaultdict(list)
        for info in infos:
            by_group[info.group].append(info)
        presets = set(self.presets.names())
        groups = [self.manager.group(group_id)] if group_id else self.manager.groups
        for group in groups:
            group_infos = by_group.get(group.id, [])
            state = state_payload(self.group_state.get(group.id), group_infos, presets)
            await self._publish_changed(
                client, topics.state(group.id), json.dumps(state), force=force
            )
            await self._publish_changed(
                client, topics.availability(group.id), availability(group_infos), force=force
            )

    async def _publish_changed(self, client, topic: str, payload: str, force: bool) -> None:
        if not force and self._last.get(topic) == payload:
            return
        await client.publish(topic, payload, retain=True)
        self._last[topic] = payload

    async def _poll(self, client, topics: Topics) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                await self._publish_states(client, topics)
            except Exception as exc:  # the message loop notices a dead connection
                log.debug("MQTT state poll failed: %s", exc)

    # ---- notifications -----------------------------------------------------

    def group_changed(self, group_id: str) -> None:
        """A group's lights changed; tell HA once the strands have settled."""
        self._spawn(lambda client, topics: self._publish_states(client, topics, group_id=group_id))

    def config_changed(self) -> None:
        """Groups or presets changed: new names, new effect lists, deleted lights."""

        async def republish(client, topics):
            await self._publish_discovery(client, topics)
            await self._publish_states(client, topics)

        self._spawn(republish)

    def _spawn(self, work) -> None:
        client, topics = self._client, self._topics
        if client is None or topics is None:
            return

        async def guarded():
            try:
                await work(client, topics)
            except Exception as exc:
                log.warning("Could not update Home Assistant over MQTT: %s", exc)

        task = asyncio.create_task(guarded())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

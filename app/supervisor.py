"""Home Assistant's Supervisor, for when Dapple runs as a Home Assistant add-on.

The Supervisor hands an add-on that declares ``services: [mqtt:want]`` the
Mosquitto add-on's address and a login made for it. Dapple writes those into
the Home Assistant tab so there's nothing to type, but leaves the bridge
switched off: MQTT only ever starts because someone turned it on.

Reinstalling Mosquitto issues a new password the user never sees, so the login
is refreshed on every start for as long as it's still the one Mosquitto made.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
import urllib.request
from collections.abc import Callable, Mapping

from app.config import ConfigError, ConfigStorageError, ConfigStore, MqttConfig, normalize_mqtt

log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
TIMEOUT_SECONDS = 5.0

Fetch = Callable[[str, str], dict]


def fetch_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def mqtt_service(
    env: Mapping[str, str] | None = None, fetch: Fetch = fetch_json
) -> MqttConfig | None:
    """The broker the Supervisor offers, switched off, or None outside an add-on."""
    env = os.environ if env is None else env
    token = env.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        # A 400 here means no add-on provides MQTT, i.e. Mosquitto isn't installed.
        service = fetch(f"{SUPERVISOR_URL}/services/mqtt", token).get("data") or {}
    except Exception as exc:  # any failure just means "type it in yourself"
        log.info("Home Assistant has no MQTT broker to offer (%s)", exc)
        return None
    if service.get("ssl"):
        log.info("Home Assistant's MQTT broker requires TLS, which Dapple doesn't support yet")
        return None
    try:
        return normalize_mqtt(
            host=service.get("host"),
            port=service.get("port") or 1883,
            username=service.get("username"),
            password=service.get("password"),
            enabled=False,
        )
    except (ConfigError, TypeError, ValueError) as exc:
        log.warning("Ignoring the MQTT broker Home Assistant offered: %s", exc)
        return None


def _same_login(saved: MqttConfig, offered: MqttConfig) -> bool:
    """Whether the saved broker is the one the Supervisor offers, password aside."""
    return (saved.host, saved.port, saved.username) == (
        offered.host,
        offered.port,
        offered.username,
    )


def sync_mqtt(
    store: ConfigStore, env: Mapping[str, str] | None = None, fetch: Fetch = fetch_json
) -> bool:
    """Save or refresh the Supervisor's broker, never a user's own. True if saved."""
    offered = mqtt_service(env, fetch)
    if offered is None:
        return False
    saved = store.config.mqtt
    if saved is None:
        settings = offered
        message = (
            "Filled in Home Assistant's MQTT broker (%s:%d). Turn on Connect to Home "
            "Assistant on the Home Assistant tab to start using it."
        )
    elif _same_login(saved, offered) and saved.password != offered.password:
        # Only the password: switched on or off, and the prefixes, are the user's.
        settings = replace(saved, password=offered.password)
        message = "Mosquitto issued Dapple a new password (%s:%d); saved it."
    else:
        return False  # unchanged, or a broker or login the user chose
    try:
        store.set_mqtt(settings)
    except ConfigStorageError as exc:
        log.warning("Couldn't save Home Assistant's MQTT broker: %s", exc)
        return False
    log.info(message, settings.host, settings.port)
    return True

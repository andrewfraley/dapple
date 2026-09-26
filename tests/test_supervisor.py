"""Filling in the MQTT broker from Home Assistant's Supervisor, add-on installs only."""

import urllib.error

import pytest

from app.config import AppConfig, ConfigStore, MqttConfig, load_config
from app.supervisor import SUPERVISOR_URL, mqtt_service, prefill_mqtt

ADDON = {"SUPERVISOR_TOKEN": "token"}


class FakeSupervisor:
    """Answers /services/mqtt like the Supervisor does, and records who asked."""

    def __init__(self, service=None, error=None):
        self.service = service
        self.error = error
        self.requests = []

    def __call__(self, url, token):
        self.requests.append((url, token))
        if self.error:
            raise self.error
        return {"result": "ok", "data": self.service}


MOSQUITTO = {
    "host": "core-mosquitto",
    "port": 1883,
    "ssl": False,
    "protocol": "3.1.1",
    "username": "addons",
    "password": "secret",
    "addon": "core_mosquitto",
}


@pytest.fixture
def store(tmp_path):
    return ConfigStore(AppConfig(data_dir=tmp_path, config_path=tmp_path / "config.yaml"))


def test_an_add_on_gets_the_mosquitto_login_filled_in(store):
    """The whole point: no broker address or login to type on the Home Assistant tab."""
    supervisor = FakeSupervisor(MOSQUITTO)

    assert prefill_mqtt(store, ADDON, supervisor)

    assert supervisor.requests == [(f"{SUPERVISOR_URL}/services/mqtt", "token")]
    mqtt = store.config.mqtt
    assert (mqtt.host, mqtt.port, mqtt.username, mqtt.password) == (
        "core-mosquitto",
        1883,
        "addons",
        "secret",
    )


def test_the_filled_in_broker_starts_switched_off(store):
    """MQTT is off until someone turns it on, even when Dapple found the broker itself."""
    prefill_mqtt(store, ADDON, FakeSupervisor(MOSQUITTO))

    assert store.config.mqtt.enabled is False


def test_the_filled_in_broker_survives_a_restart(store, tmp_path):
    """It's written to config.yaml like one typed in, so the password stays write-only."""
    prefill_mqtt(store, ADDON, FakeSupervisor(MOSQUITTO))

    assert load_config(tmp_path, env={}).mqtt == store.config.mqtt


def test_a_broker_the_user_set_up_is_left_alone(store):
    """Their own broker, or one they switched off on purpose, must never be overwritten."""
    theirs = MqttConfig(host="10.0.0.50", enabled=True)
    store.set_mqtt(theirs)
    supervisor = FakeSupervisor(MOSQUITTO)

    assert not prefill_mqtt(store, ADDON, supervisor)

    assert store.config.mqtt == theirs
    assert supervisor.requests == []


def test_outside_an_add_on_the_supervisor_is_never_asked(store):
    """Docker installs have no Supervisor; asking would just stall startup on DNS."""
    supervisor = FakeSupervisor(MOSQUITTO)

    assert not prefill_mqtt(store, {}, supervisor)

    assert supervisor.requests == []
    assert store.config.mqtt is None


def test_without_mosquitto_nothing_is_filled_in(store):
    """The Supervisor answers 400 when no add-on provides MQTT."""
    missing = urllib.error.HTTPError(f"{SUPERVISOR_URL}/services/mqtt", 400, "", {}, None)

    assert not prefill_mqtt(store, ADDON, FakeSupervisor(error=missing))

    assert store.config.mqtt is None


def test_a_broker_that_needs_tls_is_not_filled_in(store):
    """Dapple connects without TLS, so these settings could only ever fail to connect."""
    assert mqtt_service(ADDON, FakeSupervisor({**MOSQUITTO, "ssl": True})) is None


def test_a_nonsense_answer_is_ignored(store):
    """A Supervisor bug shouldn't stop Dapple from starting."""
    assert not prefill_mqtt(store, ADDON, FakeSupervisor({"host": "not a host!"}))

    assert store.config.mqtt is None

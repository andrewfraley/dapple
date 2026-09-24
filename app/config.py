"""Groups and the strands in them: loaded from /data/config.yaml, and edited
from the Strands page through :class:`ConfigStore`.

A group is the unit that holds a pattern. Strand order *within* a group is
physical order — it sets the offsets that make a pattern continue across the
join. Group order is display order only; every group starts its pattern at LED 0.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

import yaml

from app.pattern import DEFAULT_GAMMA

log = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("/data")
CONFIG_FILENAME = "config.yaml"
PRESETS_FILENAME = "presets.json"
STATE_FILENAME = "state.json"


#: A hostname or IPv4/IPv6 literal — no scheme, no port, no path. Strands speak
#: plain HTTP on port 80, so anything else is a typo rather than a setting.
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")

#: Group ids go in URL paths, so they stay to one boring slug-shaped segment.
GROUP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: ``order`` is a static segment in ``/api/config/groups/order``; a group with
#: that id would be permanently unreachable. ``all`` is held back so it stays
#: available as a whole-house target without ever colliding with a real group.
RESERVED_GROUP_IDS = frozenset({"order", "all", "new"})

MAX_LEDS = 20000
MAX_NAME = 64

#: What the migration calls the group it wraps a pre-groups config into.
MIGRATED_GROUP_ID = "all-strands"
MIGRATED_GROUP_NAME = "All strands"


class ConfigError(Exception):
    """A strand or group entry that can't be used as given."""


class UnknownGroupError(ConfigError):
    """No group by that id — a 404 rather than a 400."""


class ConfigStorageError(Exception):
    """config.yaml could not be written — almost always /data permissions."""


@dataclass
class DeviceConfig:
    """One strand.

    ``number_of_led`` and ``led_profile`` are read from the strand itself when
    left as ``None``; set them to pin a strand that reports them wrong, or to
    configure one that isn't plugged in yet.
    """

    name: str
    host: str
    number_of_led: int | None = None
    led_profile: str | None = None


@dataclass
class GroupConfig:
    """One group and the strands in it, in physical order.

    ``id`` is what the API and Home Assistant address, and never changes —
    renaming a group is safe.
    """

    id: str
    name: str
    devices: list[DeviceConfig] = field(default_factory=list)


@dataclass
class AppConfig:
    groups: list[GroupConfig] = field(default_factory=list)
    data_dir: Path = DEFAULT_DATA_DIR
    #: Some firmware refuses a 1-frame movie; 2 uploads the frame twice.
    movie_frames: int = 1
    #: Seconds to wait on a strand before giving up. A strand that is simply
    #: unplugged must not hold a request open for the OS's TCP timeout.
    timeout: float = 5.0
    #: sRGB → PWM gamma. 2.2 makes the strand match the on-screen preview;
    #: raise it for deeper colors, or set 1.0 to send values through untouched.
    gamma: float = DEFAULT_GAMMA
    source: str = "none"
    config_path: Path | None = None

    @property
    def devices(self) -> list[DeviceConfig]:
        """Every strand, in group order then member order."""
        return [device for group in self.groups for device in group.devices]

    @property
    def presets_path(self) -> Path:
        return self.data_dir / PRESETS_FILENAME

    @property
    def state_path(self) -> Path:
        return self.data_dir / STATE_FILENAME


# ---- names and ids ---------------------------------------------------------


def slugify(name: str) -> str:
    """A group name reduced to one URL-safe path segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:32].strip("-")
    return slug or "group"


def normalize_group_name(name: str | None, fallback: str = "Group") -> str:
    name = (name or "").strip()
    if not name:
        raise ConfigError("A group name is required")
    if len(name) > MAX_NAME:
        raise ConfigError(f"Group name must be {MAX_NAME} characters or fewer")
    return name


def unique_group_id(preferred: str, taken: set[str]) -> str:
    """``tree``, then ``tree-2``, ``tree-3``… avoiding reserved ids."""
    base = preferred if GROUP_ID_PATTERN.match(preferred) else slugify(preferred)
    if base in RESERVED_GROUP_IDS:
        base = f"{base}-group"
    candidate, suffix = base, 1
    while candidate in taken:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


# ---- loading ---------------------------------------------------------------


def _device_from_yaml(entry: object, where: str, index: int) -> DeviceConfig:
    if not isinstance(entry, dict):
        raise ValueError(f"config.yaml: {where} strand #{index} must be a mapping")
    host = entry.get("host")
    if not host:
        raise ValueError(f"config.yaml: {where} strand #{index} is missing 'host'")
    profile = entry.get("led_profile")
    return DeviceConfig(
        name=str(entry.get("name") or f"Strand {index}"),
        host=str(host),
        number_of_led=entry.get("number_of_led"),
        led_profile=str(profile).upper() if profile else None,
    )


def _one_group(devices: list[DeviceConfig]) -> list[GroupConfig]:
    """Wrap a flat strand list in a single group.

    This is what a pre-groups config migrates to, and it is exactly the old
    behavior: one pattern across the lot, continuity preserved across the join.
    """
    if not devices:
        return []
    return [GroupConfig(id=MIGRATED_GROUP_ID, name=MIGRATED_GROUP_NAME, devices=devices)]


def _groups_from_yaml(document: object) -> list[GroupConfig]:
    """Parse ``groups:``, or migrate a ``devices:`` file into one group.

    A hand-edited file shouldn't take the app down, so duplicate ids and
    repeated hosts are warned about and repaired rather than raised. A strand
    with no ``host`` is still fatal — there is nothing sensible to assume.
    """
    if not isinstance(document, dict):
        raise ValueError("config.yaml must contain a mapping at the top level")

    if document.get("groups") is None:
        return _one_group(_flat_devices_from_yaml(document))

    entries = document.get("groups") or []
    if not isinstance(entries, list):
        raise ValueError("config.yaml: 'groups' must be a list")

    groups: list[GroupConfig] = []
    seen_ids: set[str] = set()
    seen_hosts: set[str] = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"config.yaml: group #{index} must be a mapping")
        name = str(entry.get("name") or entry.get("id") or f"Group {index}")
        wanted = str(entry.get("id") or slugify(name))
        group_id = unique_group_id(wanted, seen_ids)
        if group_id != wanted:
            log.warning(
                "config.yaml: group id %r is unusable or taken; using %r",
                wanted,
                group_id,
            )
        seen_ids.add(group_id)

        strands = entry.get("strands") or entry.get("devices") or []
        if not isinstance(strands, list):
            raise ValueError(f"config.yaml: group {group_id!r} 'strands' must be a list")
        devices = []
        for position, raw in enumerate(strands, 1):
            device = _device_from_yaml(raw, f"group {group_id!r}", position)
            if device.host in seen_hosts:
                log.warning(
                    "config.yaml: %s appears more than once; keeping the first",
                    device.host,
                )
                continue
            seen_hosts.add(device.host)
            devices.append(device)
        groups.append(GroupConfig(id=group_id, name=name, devices=devices))
    return groups


def _flat_devices_from_yaml(document: dict) -> list[DeviceConfig]:
    """The pre-groups ``devices:`` list, for migration."""
    entries = document.get("devices") or []
    if not isinstance(entries, list):
        raise ValueError("config.yaml: 'devices' must be a list")
    return [
        _device_from_yaml(entry, "top-level", index)
        for index, entry in enumerate(entries, 1)
    ]


def load_config(
    data_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Read config.yaml, if there is one yet.

    Starting with no strands at all is normal: the Strands page writes this
    file the first time you add one.

    A file written before groups existed loads as one group, so nothing about
    the running setup changes. It is rewritten in the new shape on the first
    edit — loading stays side-effect free, which matters when /data is
    read-only.
    """
    env = os.environ if env is None else env
    data_dir = Path(data_dir or env.get("DAPPLE_DATA_DIR") or DEFAULT_DATA_DIR)
    config_path = Path(env.get("DAPPLE_CONFIG") or data_dir / CONFIG_FILENAME)

    config = AppConfig(data_dir=data_dir, config_path=config_path)
    config.movie_frames = int(env.get("DAPPLE_MOVIE_FRAMES") or 1)
    config.timeout = float(env.get("DAPPLE_TIMEOUT") or 5.0)
    config.gamma = float(env.get("DAPPLE_GAMMA") or DEFAULT_GAMMA)

    if config_path.is_file():
        document = yaml.safe_load(config_path.read_text()) or {}
        groups = _groups_from_yaml(document)
        if isinstance(document, dict) and document.get("movie_frames"):
            config.movie_frames = int(document["movie_frames"])
        if isinstance(document, dict) and document.get("timeout"):
            config.timeout = float(document["timeout"])
        if isinstance(document, dict) and document.get("gamma") is not None:
            config.gamma = float(document["gamma"])
        if groups:
            config.groups = groups
            config.source = str(config_path)
            return config

    log.info(
        "No strands configured yet — add one on the Strands page, or write %s",
        config_path,
    )
    return config


# ---- editing ---------------------------------------------------------------


def normalize_device(
    name: str | None,
    host: str,
    number_of_led: int | None = None,
    led_profile: str | None = None,
    fallback_name: str = "Strand",
) -> DeviceConfig:
    """Validate one strand entry as it comes in from the Strands page."""
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ConfigError("A hostname or IP address is required")
    # A pasted URL is the obvious mistake; say so rather than failing the regex.
    if "://" in host:
        raise ConfigError(f"Enter just the hostname or IP, not a URL: {host!r}")
    if not HOST_PATTERN.match(host):
        raise ConfigError(f"{host!r} is not a valid hostname or IP address")

    if number_of_led is not None and not 1 <= number_of_led <= MAX_LEDS:
        raise ConfigError(f"LED count must be between 1 and {MAX_LEDS}")

    if led_profile is not None:
        led_profile = str(led_profile).upper()
        if led_profile not in ("RGB", "RGBW"):
            raise ConfigError(f"LED profile must be RGB or RGBW, not {led_profile!r}")

    return DeviceConfig(
        name=(name or "").strip() or fallback_name,
        host=host,
        number_of_led=number_of_led,
        led_profile=led_profile,
    )


def _device_to_yaml(device: DeviceConfig) -> dict:
    entry = {"name": device.name, "host": device.host}
    if device.number_of_led is not None:
        entry["number_of_led"] = device.number_of_led
    if device.led_profile is not None:
        entry["led_profile"] = device.led_profile
    return entry


def _group_to_yaml(group: GroupConfig) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "strands": [_device_to_yaml(device) for device in group.devices],
    }


HEADER = """\
# Dapple configuration. Written by the Strands page; hand edits are kept as long
# as the keys below survive — comments elsewhere in this file are not.
#
# Each strand belongs to exactly one group, and a group gets one pattern.
#
# Strand order WITHIN a group is physical order: it sets the offsets that make an
# interleaved pattern continue across the join instead of restarting. Group order
# is display order only — every group starts its pattern at its own first LED.
#
# 'id' is what the API and Home Assistant address. It never changes, so renaming
# a group is safe.
"""


class ConfigStore:
    """Groups and their strands, editable at runtime and written to config.yaml.

    Hosts are the identity for strands and are unique across every group — two
    entries for one strand would fight over it. Group ids are unique and
    permanent.

    Every method here builds new lists rather than mutating the ones in
    :class:`AppConfig`; :meth:`_commit` depends on that to put the previous
    state back when a write fails.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.writable = True

    @property
    def path(self) -> Path:
        return self.config.config_path or (self.config.data_dir / CONFIG_FILENAME)

    @property
    def groups(self) -> list[GroupConfig]:
        return list(self.config.groups)

    @property
    def devices(self) -> list[DeviceConfig]:
        return self.config.devices

    # ---- lookups -----------------------------------------------------------

    def find_group(self, group_id: str) -> GroupConfig:
        for group in self.config.groups:
            if group.id == group_id:
                return group
        raise UnknownGroupError(f"No group called {group_id!r}")

    def group_of(self, host: str) -> GroupConfig:
        for group in self.config.groups:
            if any(device.host == host for device in group.devices):
                return group
        raise ConfigError(f"No strand configured at {host!r}")

    def find(self, host: str) -> DeviceConfig:
        for group in self.config.groups:
            for device in group.devices:
                if device.host == host:
                    return device
        raise ConfigError(f"No strand configured at {host!r}")

    def _check_free(self, host: str, ignoring: str | None = None) -> None:
        for device in self.devices:
            if device.host == host and device.host != ignoring:
                raise ConfigError(f"{host} is already configured as {device.name!r}")

    def next_name(self) -> str:
        return f"Strand {len(self.devices) + 1}"

    def next_group_name(self) -> str:
        return f"Group {len(self.config.groups) + 1}"

    # ---- groups ------------------------------------------------------------

    def create_group(self, name: str) -> GroupConfig:
        name = normalize_group_name(name, self.next_group_name())
        group = GroupConfig(
            id=unique_group_id(slugify(name), {g.id for g in self.config.groups}),
            name=name,
            devices=[],
        )
        self._commit([*self.groups, group])
        return group

    def rename_group(self, group_id: str, name: str) -> GroupConfig:
        """Changes the display name only — the id is what everything addresses."""
        self.find_group(group_id)
        name = normalize_group_name(name)
        groups = [
            replace(group, name=name) if group.id == group_id else group
            for group in self.config.groups
        ]
        self._commit(groups)
        return self.find_group(group_id)

    def delete_group(self, group_id: str) -> None:
        """Refused while it still holds strands.

        A strand is hardware with an address someone typed in; a group is a
        label. Deleting the label shouldn't quietly delete the hardware config,
        and there is no undo.
        """
        group = self.find_group(group_id)
        if group.devices:
            count = len(group.devices)
            raise ConfigError(
                f"{group.name!r} still has {count} strand{'' if count == 1 else 's'}. "
                "Move or remove them first."
            )
        self._commit([g for g in self.config.groups if g.id != group_id])

    def reorder_groups(self, ids: list[str]) -> list[GroupConfig]:
        configured = [group.id for group in self.config.groups]
        if sorted(ids) != sorted(configured):
            raise ConfigError("The new order must list every group exactly once")
        by_id = {group.id: group for group in self.config.groups}
        self._commit([by_id[group_id] for group_id in ids])
        return self.groups

    # ---- strands -----------------------------------------------------------

    def add(
        self, device: DeviceConfig, group_id: str | None = None
    ) -> tuple[GroupConfig, DeviceConfig]:
        """Add a strand. With no group, it gets one of its own.

        That keeps adding a strand a single step, and is the literal reading of
        "a lone strand is a group of one".
        """
        self._check_free(device.host)
        if group_id is None:
            groups = [
                *self.groups,
                GroupConfig(
                    id=unique_group_id(
                        slugify(device.name), {g.id for g in self.config.groups}
                    ),
                    name=device.name,
                    devices=[device],
                ),
            ]
            self._commit(groups)
            return self.group_of(device.host), device

        self.find_group(group_id)
        self._commit(
            [
                replace(group, devices=[*group.devices, device])
                if group.id == group_id
                else group
                for group in self.config.groups
            ]
        )
        return self.find_group(group_id), device

    def update(self, host: str, device: DeviceConfig) -> DeviceConfig:
        """Edit a strand in place — same group, same position."""
        self.find(host)
        self._check_free(device.host, ignoring=host)
        self._commit(
            [
                replace(
                    group,
                    devices=[
                        device if existing.host == host else existing
                        for existing in group.devices
                    ],
                )
                if any(existing.host == host for existing in group.devices)
                else group
                for group in self.config.groups
            ]
        )
        return device

    def delete(self, host: str) -> None:
        self.find(host)
        self._commit(
            [
                replace(
                    group, devices=[d for d in group.devices if d.host != host]
                )
                for group in self.config.groups
            ]
        )

    def move(self, host: str, group_id: str, index: int | None = None) -> DeviceConfig:
        """Move a strand into another group (or reposition it within its own)."""
        device = self.find(host)
        self.find_group(group_id)
        groups = []
        for group in self.config.groups:
            devices = [d for d in group.devices if d.host != host]
            if group.id == group_id:
                position = len(devices) if index is None else max(0, min(index, len(devices)))
                devices = [*devices[:position], device, *devices[position:]]
            groups.append(replace(group, devices=devices))
        self._commit(groups)
        return device

    def reorder(self, group_id: str, hosts: list[str]) -> list[DeviceConfig]:
        """Reorder within one group. That order is the physical seam order, so a
        partial list would silently move the join."""
        group = self.find_group(group_id)
        configured = [device.host for device in group.devices]
        if sorted(hosts) != sorted(configured):
            raise ConfigError(
                f"The new order must list every strand in {group.name!r} exactly once"
            )
        by_host = {device.host: device for device in group.devices}
        self._commit(
            [
                replace(existing, devices=[by_host[host] for host in hosts])
                if existing.id == group_id
                else existing
                for existing in self.config.groups
            ]
        )
        return self.find_group(group_id).devices

    # ---- persistence -------------------------------------------------------

    def _commit(self, groups: list[GroupConfig]) -> None:
        """Swap in a new group list, keeping the old one if the write fails.

        Otherwise a read-only /data would leave the running config holding
        something that isn't in the file — the next restart would lose it, and
        the error message would be a lie.
        """
        previous = self.config.groups
        self.config.groups = groups
        try:
            self.save()
        except ConfigStorageError:
            self.config.groups = previous
            raise

    def save(self) -> None:
        document = {
            "groups": [_group_to_yaml(group) for group in self.config.groups],
            "movie_frames": self.config.movie_frames,
            "timeout": self.config.timeout,
            "gamma": self.config.gamma,
        }
        body = HEADER + yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
        try:
            self._write_atomically(body)
        except OSError as exc:
            self.writable = False
            raise ConfigStorageError(
                f"Cannot write {self.path} ({exc.strerror or exc}). "
                "Make the data directory writable by the user the container runs as "
                "— see 'Permissions on ./data' in the README."
            ) from exc
        self.writable = True
        self.config.source = str(self.path)

    def _write_atomically(self, body: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".config-", suffix=".yaml"
        )
        try:
            with os.fdopen(handle, "w") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

"""Pydantic models shared by the API, the preset store and the pattern builder."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Annotated[int, Field(ge=0, le=255)]
Rgbw = tuple[Channel, Channel, Channel, Channel]

Layout = Literal["interleaved", "blocked"]
LedProfile = Literal["RGB", "RGBW"]

MAX_SLOTS = 16


class Slot(BaseModel):
    """One color in a pattern, with its share of the strand."""

    model_config = ConfigDict(extra="forbid")

    rgbw: Rgbw
    weight: Annotated[int, Field(ge=0, le=1000)] = 1


class Pattern(BaseModel):
    """A static pattern: colors, their shares, and how they are laid out."""

    model_config = ConfigDict(extra="forbid")

    slots: Annotated[list[Slot], Field(max_length=MAX_SLOTS)]
    layout: Layout = "interleaved"
    block_size: Annotated[int, Field(ge=1, le=500)] = 1
    brightness: Annotated[int, Field(ge=0, le=100)] | None = None


class DeviceInfo(BaseModel):
    """What we know about one configured strand."""

    name: str
    host: str
    group: str | None = None
    reachable: bool = False
    number_of_led: int | None = None
    led_profile: LedProfile | None = None
    fw_version: str | None = None
    fw_family: str | None = None
    mode: str | None = None
    brightness: int | None = None
    error: str | None = None


class DeviceResult(BaseModel):
    """Outcome of one operation against one strand."""

    name: str
    host: str
    ok: bool
    error: str | None = None


class ApplyResponse(BaseModel):
    ok: bool
    results: list[DeviceResult]


class HealthResponse(BaseModel):
    ok: bool
    devices: list[DeviceResult]


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Pattern
    num_leds: Annotated[int, Field(ge=0, le=20000)]
    offset: Annotated[int, Field(ge=0)] = 0


class PreviewResponse(BaseModel):
    leds: list[Rgbw]


class BrightnessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[int, Field(ge=0, le=100)]


class StrandConfig(BaseModel):
    """One strand as configured — what the Strands page edits.

    ``number_of_led`` and ``led_profile`` are null for "read it from the
    strand", which is the right answer for a strand that's plugged in.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=64)] = ""
    host: Annotated[str, Field(min_length=1, max_length=255)]
    number_of_led: Annotated[int, Field(ge=1, le=20000)] | None = None
    led_profile: LedProfile | None = None


class StrandCreate(StrandConfig):
    """Adding a strand. With no group it gets one of its own."""

    group: str | None = None


class StrandConfigResult(BaseModel):
    """A configured strand plus what the strand itself says, after a probe."""

    config: StrandConfig
    group: str
    info: DeviceInfo | None = None


class StrandSegment(BaseModel):
    """Where one strand sits inside its group's LED run, for the UI preview.

    ``offset`` is group-relative: every group starts its pattern at its own
    first LED, so the first strand in each group is at 0.
    """

    name: str
    host: str
    offset: int
    number_of_led: int
    led_profile: LedProfile


class GroupState(BaseModel):
    """What was last applied to a group.

    A claim, not a readback: the strands hold their movie themselves, so this
    is what we sent, not what is necessarily lit right now.
    """

    pattern: Pattern
    #: The preset it came from, when it came from one.
    preset: str | None = None
    power: Literal["on", "off"] = "on"
    applied_at: datetime


class GroupModel(BaseModel):
    """A group as configured."""

    id: str
    name: str
    strands: list[StrandConfig]


class GroupStatus(BaseModel):
    """A group as it is right now — what both UI pages render from."""

    id: str
    name: str
    strands: list[StrandConfig]
    total_leds: int
    #: Offsets here are group-relative: every group starts at its own LED 0.
    segments: list[StrandSegment]
    reachable: int
    state: GroupState | None = None


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=64)]


class GroupRename(GroupCreate):
    pass


class GroupOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Every configured group id, in the new display order.
    ids: list[str]


class MoveStrandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The group to move it into; may be the one it is already in.
    group: str
    index: int | None = None


class ApplyPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Preset names contain spaces, so they travel in the body, not the path.
    name: Annotated[str, Field(min_length=1, max_length=64)]


class ConfigResponse(BaseModel):
    groups: list[GroupModel]
    #: Where the running config came from, and whether edits can be saved.
    source: str
    writable: bool
    #: False when /data is read-only: applies still work, but each group's
    #: last-applied pattern won't survive a restart.
    state_writable: bool
    movie_frames: int
    timeout: float


class ReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Every host in the group, in the new physical order.
    hosts: list[str]


class MqttSettings(BaseModel):
    """The Home Assistant tab. The password itself is never sent back."""

    enabled: bool
    host: str | None = None
    port: int = 1883
    username: str | None = None
    password_set: bool = False
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "dapple"
    status: Literal["disabled", "connecting", "connected", "error"]
    error: str | None = None


class MqttUpdate(BaseModel):
    """Leave ``password`` out to keep the saved one; send ``""`` to clear it."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str | None = None
    port: Annotated[int, Field(ge=1, le=65535)] = 1883
    username: str | None = None
    password: str | None = None
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "dapple"

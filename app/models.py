"""Pydantic models shared by the API, the preset store and the pattern builder."""

from __future__ import annotations

from datetime import datetime
from functools import reduce
from math import gcd
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Annotated[int, Field(ge=0, le=255)]
Rgbw = tuple[Channel, Channel, Channel, Channel]

Layout = Literal["interleaved", "blocked"]
LedProfile = Literal["RGB", "RGBW"]

MAX_SLOTS = 16
#: Longest strand, group or preset name.
MAX_NAME = 64
#: Most LEDs on one strand — far past any Twinkly, but it bounds a frame's size.
MAX_LEDS = 20000
#: Longest repeating unit a pattern may have. The builders materialise one unit
#: as a list; 16 coprime weights of 1000 in 500-LED blocks would be 8M entries.
#: The editor's own limits (8 colors, shares to 100, blocks to 500) stay under it.
MAX_UNIT = 100_000


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

    @model_validator(mode="after")
    def _unit_fits(self) -> "Pattern":
        weights = [slot.weight for slot in self.slots if slot.weight > 0]
        divisor = reduce(gcd, weights, 0) or 1
        unit = sum(weights) // divisor
        if self.layout == "blocked":
            unit *= self.block_size
        if unit > MAX_UNIT:
            raise ValueError(
                f"This pattern repeats only every {unit} LEDs (the limit is {MAX_UNIT}). "
                "Use a smaller block size, or shares with a common factor, like 80/20 "
                "rather than 79/21."
            )
        return self


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
    num_leds: Annotated[int, Field(ge=0, le=MAX_LEDS)]
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

    name: Annotated[str, Field(max_length=MAX_NAME)] = ""
    host: Annotated[str, Field(min_length=1, max_length=255)]
    number_of_led: Annotated[int, Field(ge=1, le=MAX_LEDS)] | None = None
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


class GroupLive(BaseModel):
    """What a group's strands are doing right now, read from them.

    Unlike :class:`GroupState`, this is a readback: it notices the Twinkly app
    or Home Assistant's Twinkly integration switching a strand off, or putting
    something else on it.
    """

    #: None when no strand answered — unknown, not off.
    power: Literal["on", "off"] | None = None
    brightness: int | None = None
    #: The preset showing, when the strands still show what Dapple last applied.
    preset: str | None = None
    #: Something other than Dapple's pattern is lit (a Twinkly color or effect).
    taken_over: bool = False
    answering: int = 0
    strands: int = 0


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

    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]


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
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]


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

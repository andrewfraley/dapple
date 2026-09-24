"""Talking to the strands: xled wrapper, cached gestalt, one retry on a stale token.

Everything in here is blocking (``requests`` under the hood). The manager wraps
each call in :func:`asyncio.to_thread` so FastAPI's event loop stays free, and
fans out across a group's strands concurrently.

Offsets live on :class:`DeviceGroup`, never on the manager: they are a prefix
sum *within* a group, so every group starts its pattern at its own first LED.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from typing import Any, Callable, Sequence, TypeVar

from xled.control import ControlInterface
from xled.exceptions import (
    ApplicationError,
    AuthenticationError,
    TokenExpiredError,
)

from app.config import AppConfig, DeviceConfig, UnknownGroupError
from app.models import DeviceInfo, DeviceResult, Pattern, StrandSegment
from app.pattern import DEFAULT_GAMMA, build_frame

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Firmware at or above this uses /movies/*; older firmware uses /led/movie/*.
NEW_MOVIE_API_VERSION = (2, 5, 6)
MOVIE_NAME = "dapple"
#: A static pattern never advances, so the frame delay only matters for firmware
#: that insists on more than one frame.
FRAME_DELAY_MS = 1000


def _get(response: Any, key: str, default: Any = None) -> Any:
    """Read a key out of an xled ApplicationResponse without assuming it exists."""
    try:
        return response[key]
    except (KeyError, TypeError):
        return default


class DeviceError(Exception):
    """Anything that stopped us from talking to a strand."""


class Device:
    """One strand: cached device info, and the operations we actually use."""

    def __init__(
        self,
        config: DeviceConfig,
        movie_frames: int = 1,
        timeout: float = 5.0,
        gamma: float = DEFAULT_GAMMA,
    ):
        self.config = config
        self.gamma = gamma
        self.name = config.name
        self.host = config.host
        self.movie_frames = max(1, movie_frames)
        self.timeout = timeout
        self._control: ControlInterface | None = None
        self.number_of_led: int | None = config.number_of_led
        self.led_profile: str | None = config.led_profile
        self.fw_version: str | None = None
        self.fw_family: str | None = None
        self.reachable = False
        self.error: str | None = None

    # ---- plumbing ----------------------------------------------------------

    @property
    def control(self) -> ControlInterface:
        if self._control is None:
            self._control = ControlInterface(self.host)
        self._apply_timeout(self._control)
        return self._control

    def _apply_timeout(self, control: ControlInterface) -> None:
        """Give every request a deadline, including the login handshake.

        xled leaves timeouts to requests, which means no timeout at all: an
        unplugged strand would otherwise hold a request open for the OS's TCP
        timeout. Patching ``send`` catches the auth handshake too, which goes
        around ``Session.request``.
        """
        session = getattr(control, "session", None)
        if session is None or getattr(session, "_dapple_timeout", None) == self.timeout:
            return
        original = getattr(session, "_dapple_original_send", None) or session.send

        def send(request, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = self.timeout
            return original(request, **kwargs)

        session._dapple_original_send = original
        session.send = send
        session._dapple_timeout = self.timeout

    def _reset_session(self) -> None:
        """Drop the cached token so the next call logs in again."""
        if self._control is not None:
            self._control._session = None

    def _call(self, operation: Callable[[], T]) -> T:
        """Run a device call, retrying once with a fresh token.

        Tokens expire, and a strand that was power-cycled forgets ours; both
        surface as a 401 on the next call rather than at login.
        """
        try:
            return operation()
        except (TokenExpiredError, AuthenticationError) as exc:
            log.info("%s: token rejected (%s); logging in again", self.host, exc)
        except ApplicationError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status != 401:
                raise DeviceError(str(exc)) from exc
            log.info("%s: 401 from device; logging in again", self.host)
        self._reset_session()
        try:
            return operation()
        except Exception as exc:
            raise DeviceError(str(exc)) from exc

    # ---- state -------------------------------------------------------------

    def refresh(self) -> DeviceInfo:
        """Re-read gestalt and firmware version, and cache them."""
        try:
            info = self._call(self.control.get_device_info)
            version = self._call(self.control.firmware_version)
        except DeviceError as exc:
            self.reachable = False
            self.error = str(exc)
            return self.info()
        except Exception as exc:  # connection refused, DNS, timeout
            self.reachable = False
            self.error = f"{type(exc).__name__}: {exc}"
            return self.info()

        # Config overrides win: they exist for strands that report nonsense.
        self.number_of_led = self.config.number_of_led or _get(info, "number_of_led")
        profile = self.config.led_profile or _get(info, "led_profile", "RGB")
        self.led_profile = str(profile).upper()
        self.fw_family = _get(info, "fw_family", "D")
        self.fw_version = _get(version, "version")
        self.reachable = True
        self.error = None
        return self.info()

    def ensure_ready(self) -> None:
        if not self.reachable or not self.number_of_led:
            self.refresh()
        if not self.reachable:
            raise DeviceError(self.error or "device unreachable")
        if not self.number_of_led:
            raise DeviceError("device did not report number_of_led")

    def info(self, with_live_state: bool = False) -> DeviceInfo:
        profile = self.led_profile if self.led_profile in ("RGB", "RGBW") else None
        mode = brightness = None
        if with_live_state and self.reachable:
            try:
                mode = _get(self._call(self.control.get_mode), "mode")
                brightness = _get(self._call(self.control.get_brightness), "value")
            except Exception as exc:  # live state is a nicety, not a failure
                log.debug("%s: could not read live state: %s", self.host, exc)
        return DeviceInfo(
            name=self.name,
            host=self.host,
            reachable=self.reachable,
            number_of_led=self.number_of_led,
            led_profile=profile,
            fw_version=self.fw_version,
            fw_family=self.fw_family,
            mode=mode,
            brightness=brightness,
            error=self.error,
        )

    @property
    def uses_new_movie_api(self) -> bool:
        """Whether this firmware wants /movies/* instead of /led/movie/*."""
        if (self.fw_family or "D") == "D":
            return False
        if not self.fw_version:
            return False
        try:
            version = tuple(int(part) for part in self.fw_version.split("."))
        except ValueError:
            return False
        return version >= NEW_MOVIE_API_VERSION

    # ---- operations --------------------------------------------------------

    def apply_frame(self, frame: bytes) -> None:
        """Upload ``frame`` as a movie and switch the strand to it.

        Stored on the device, so the strand holds the pattern with no further
        traffic from us and keeps it across a power cycle.
        """
        self.ensure_ready()
        payload = frame * self.movie_frames
        if self.uses_new_movie_api:
            self._upload_movies_api(payload, self.movie_frames)
        else:
            self._upload_legacy_api(payload, self.movie_frames)
        self._call(lambda: self.control.set_mode("movie"))

    def _upload_legacy_api(self, payload: bytes, frames: int) -> None:
        self._call(lambda: self.control.led_reset())
        self._call(
            lambda: self.control.set_led_movie_config(FRAME_DELAY_MS, frames, self.number_of_led)
        )
        self._call(lambda: self.control.set_led_movie_full(io.BytesIO(payload)))

    def _upload_movies_api(self, payload: bytes, frames: int) -> None:
        # We only ever keep one movie, so clear the list rather than juggling
        # the device's frame capacity. Deleting while it plays is refused.
        mode = _get(self._call(self.control.get_mode), "mode")
        if mode in ("movie", "playlist"):
            self._call(lambda: self.control.set_mode("off"))
        self._call(lambda: self.control.delete_movies())
        response = self._call(
            lambda: self.control.set_movies_new(
                MOVIE_NAME,
                str(uuid.uuid4()),
                f"{(self.led_profile or 'RGB').lower()}_raw",
                self.number_of_led,
                frames,
                1,
            )
        )
        self._call(lambda: self.control.set_movies_full(io.BytesIO(payload)))
        movie_id = _get(response, "id")
        if movie_id is not None:
            self._call(lambda: self.control.set_movies_current(movie_id))

    def set_brightness(self, value: int) -> None:
        self._call(lambda: self.control.set_brightness(value))

    def turn_on(self) -> None:
        self._call(lambda: self.control.set_mode("movie"))

    def turn_off(self) -> None:
        self._call(lambda: self.control.set_mode("off"))


class DeviceGroup:
    """One group's strands, and where each sits in that group's run.

    This is where the offsets live. The prefix sum restarts for every group, so
    two groups showing the same pattern both begin it at their own first LED
    rather than one continuing the other.
    """

    def __init__(self, id: str, name: str, devices: list[Device]):
        self.id = id
        self.name = name
        self.devices = devices

    def offsets(self) -> list[int]:
        """LEDs before each strand, counting only within this group.

        A strand that hasn't answered yet counts as 0 rather than being skipped,
        so an unreachable strand doesn't shift the ones after it once it comes
        back.
        """
        offsets, running = [], 0
        for device in self.devices:
            offsets.append(running)
            running += device.number_of_led or 0
        return offsets

    def total_leds(self) -> int:
        return sum(device.number_of_led or 0 for device in self.devices)

    def reachable(self) -> int:
        return sum(1 for device in self.devices if device.reachable)

    def segments(self) -> list[StrandSegment]:
        """Where each strand sits in this group's run — what the UI draws."""
        return [
            StrandSegment(
                name=device.name,
                host=device.host,
                offset=offset,
                number_of_led=device.number_of_led or 0,
                led_profile=device.led_profile if device.led_profile in ("RGB", "RGBW") else "RGB",
            )
            for device, offset in zip(self.devices, self.offsets())
            if device.number_of_led
        ]


class DeviceManager:
    """Every configured strand, arranged into groups.

    A group is the unit that gets a pattern. Within a group, config order is
    physical order: strand *k* is applied with an offset equal to the LED count
    of every strand before it *in that group*, so the pattern continues across
    the join. Between groups nothing is shared.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.groups: list[DeviceGroup] = []
        self.sync()

    @property
    def devices(self) -> list[Device]:
        """Every strand, flattened — for whole-estate reads like health."""
        return [device for group in self.groups for device in group.devices]

    def sync(self) -> list[DeviceGroup]:
        """Rebuild the groups after the strand config changed.

        Strands whose settings didn't change keep their existing object, and
        with it their auth token and cached gestalt. The lookup spans every
        group, so moving a strand between groups doesn't make it log in again —
        a :class:`Device` knows nothing about which group it's in, deliberately.
        """
        existing = {device.config.host: device for group in self.groups for device in group.devices}
        groups = []
        for group_config in self.config.groups:
            devices = []
            for device_config in group_config.devices:
                current = existing.get(device_config.host)
                if current is not None and current.config == device_config:
                    devices.append(current)
                else:
                    devices.append(
                        Device(
                            device_config,
                            movie_frames=self.config.movie_frames,
                            timeout=self.config.timeout,
                            gamma=self.config.gamma,
                        )
                    )
            groups.append(DeviceGroup(group_config.id, group_config.name, devices))
        self.groups = groups
        return groups

    def group(self, group_id: str) -> DeviceGroup:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise UnknownGroupError(f"No group called {group_id!r}")

    def group_of(self, host: str) -> DeviceGroup | None:
        for group in self.groups:
            if any(device.host == host for device in group.devices):
                return group
        return None

    def get(self, host: str) -> Device:
        for device in self.devices:
            if device.host == host:
                return device
        raise DeviceError(f"No strand configured at {host!r}")

    async def refresh_one(self, host: str) -> DeviceInfo:
        device = self.get(host)
        return await asyncio.to_thread(device.refresh)

    # ---- fan-out -----------------------------------------------------------

    async def _fan_out(
        self, devices: Sequence[Device], operation: Callable[[Device], None]
    ) -> list[DeviceResult]:
        """Run ``operation`` against an explicit set of strands, concurrently.

        The caller picks the strands, which is what keeps every write scoped to
        one group. Failures come back as result rows rather than raising, so one
        unplugged strand doesn't fail the whole request.
        """

        async def run(device: Device) -> DeviceResult:
            try:
                await asyncio.to_thread(operation, device)
                return DeviceResult(name=device.name, host=device.host, ok=True)
            except Exception as exc:
                log.warning("%s (%s): %s", device.name, device.host, exc)
                return DeviceResult(
                    name=device.name,
                    host=device.host,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )

        return list(await asyncio.gather(*(run(device) for device in devices)))

    # ---- whole-estate reads ------------------------------------------------

    async def refresh(self) -> list[DeviceInfo]:
        infos = await asyncio.gather(
            *(asyncio.to_thread(device.refresh) for device in self.devices)
        )
        return self._with_groups(infos)

    async def info(self, with_live_state: bool = False) -> list[DeviceInfo]:
        infos = await asyncio.gather(
            *(asyncio.to_thread(device.info, with_live_state) for device in self.devices)
        )
        return self._with_groups(infos)

    async def live_state(self) -> list[DeviceInfo]:
        """Every strand's mode and brightness as it is right now.

        Strands that were unreachable are probed again first; otherwise one
        that was unplugged at boot would read as gone until the next restart.
        """

        def read(device: Device) -> DeviceInfo:
            if not device.reachable:
                device.refresh()
            return device.info(with_live_state=True)

        infos = await asyncio.gather(*(asyncio.to_thread(read, device) for device in self.devices))
        return self._with_groups(infos)

    def _with_groups(self, infos: Sequence[DeviceInfo]) -> list[DeviceInfo]:
        """Tag each strand with the group it belongs to, for the Strands page."""
        group_of = {device.host: group.id for group in self.groups for device in group.devices}
        return [info.model_copy(update={"group": group_of.get(info.host)}) for info in infos]

    async def health(self) -> list[DeviceResult]:
        def check(device: Device) -> None:
            device.refresh()
            if not device.reachable:
                raise DeviceError(device.error or "unreachable")

        return await self._fan_out(self.devices, check)

    # ---- group-scoped writes -----------------------------------------------

    async def apply_pattern(self, group_id: str, pattern: Pattern) -> list[DeviceResult]:
        group = self.group(group_id)
        # Offsets are read here rather than inside the operation so every strand
        # in the group is placed against the same snapshot of LED counts.
        offsets = dict(zip((d.host for d in group.devices), group.offsets()))

        def apply(device: Device) -> None:
            device.ensure_ready()
            frame = build_frame(
                pattern,
                device.number_of_led or 0,
                device.led_profile or "RGB",
                offset=offsets[device.host],
                gamma=device.gamma,
            )
            device.apply_frame(frame)
            if pattern.brightness is not None:
                device.set_brightness(pattern.brightness)

        return await self._fan_out(group.devices, apply)

    async def set_brightness(self, group_id: str, value: int) -> list[DeviceResult]:
        group = self.group(group_id)
        return await self._fan_out(group.devices, lambda device: device.set_brightness(value))

    async def turn_on(self, group_id: str) -> list[DeviceResult]:
        group = self.group(group_id)
        return await self._fan_out(group.devices, lambda device: device.turn_on())

    async def turn_off(self, group_id: str) -> list[DeviceResult]:
        group = self.group(group_id)
        return await self._fan_out(group.devices, lambda device: device.turn_off())

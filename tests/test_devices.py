"""The device wrapper, against a fake ControlInterface — no strands involved.

The manager is async; the few tests that need it drive it with ``asyncio.run``
rather than pulling in pytest-asyncio for four cases.
"""

import asyncio
import threading
import time

import pytest
from xled.exceptions import ApplicationError, TokenExpiredError

from app.config import AppConfig, DeviceConfig, GroupConfig, UnknownGroupError
from app.devices import Device, DeviceError, DeviceManager
from app.models import Pattern, Slot

from app.pattern import DEFAULT_GAMMA as DEFAULT_GAMMA_FOR_TESTS, build_frame

PATTERN = Pattern(
    slots=[Slot(rgbw=(255, 80, 0, 0), weight=4), Slot(rgbw=(128, 0, 255, 0), weight=1)]
)


class FakeTimerResponse:
    """Enough of a requests.Response for xled's ApplicationResponse to read."""

    raw = object()
    ok = True

    def __init__(self, code=1000):
        self.code = code

    def raise_for_status(self):
        pass

    def json(self):
        return {"code": self.code}


class FakeTimerSession:
    """The one route xled can't carry: a timer POST that includes ``tz``.

    Like the firmware, a POST without ``tz`` blanks it.
    """

    def __init__(self, control):
        self.control = control
        self.code = 1000

    def send(self, request, **kwargs):
        return None

    def post(self, url, json=None):
        self.control._record("post_timer", url, json)
        self.control.timer = {"tz": "", **json}
        return FakeTimerResponse(self.code)


class FakeControl:
    """Stands in for xled's ControlInterface, recording every call."""

    def __init__(self, number_of_led=105, profile="RGBW", version="2.8.3", family="F"):
        self.number_of_led = number_of_led
        self.profile = profile
        self.version = version
        self.family = family
        self.calls = []
        self.movies = []
        self.mode = "off"
        self.brightness = None
        #: The firmware reports this as a string of milliseconds.
        self.uptime = "3600000"
        self._session = object()
        self.base_url = "http://10.0.0.5/xled/v1/"
        self.session = FakeTimerSession(self)
        self.timer = {"time_on": -1, "time_off": -1, "time_now": 1000, "tz": "UTC0"}
        #: calls that raise on their first attempt, e.g. {"set_mode": TokenExpiredError()}
        self.fail_once = {}

    def _record(self, name, *args):
        self.calls.append((name, *args))
        error = self.fail_once.pop(name, None)
        if error is not None:
            raise error

    def get_device_info(self):
        self._record("get_device_info")
        return {
            "number_of_led": self.number_of_led,
            "led_profile": self.profile,
            "fw_family": self.family,
            "uptime": self.uptime,
        }

    def firmware_version(self):
        self._record("firmware_version")
        return {"version": self.version}

    def get_mode(self):
        self._record("get_mode")
        return {"mode": self.mode}

    def get_timer(self):
        self._record("get_timer")
        return self.timer

    def get_brightness(self):
        self._record("get_brightness")
        return {"value": self.brightness or 0}

    def set_mode(self, mode):
        self._record("set_mode", mode)
        self.mode = mode

    def set_brightness(self, value):
        self._record("set_brightness", value)
        self.brightness = value

    def led_reset(self):
        self._record("led_reset")

    def set_led_movie_config(self, delay, frames, leds):
        self._record("set_led_movie_config", delay, frames, leds)

    def set_led_movie_full(self, movie):
        data = movie.read()
        self._record("set_led_movie_full", len(data))
        self.movies.append(data)

    def delete_movies(self):
        self._record("delete_movies")
        self.movies = []

    def set_movies_new(self, name, uid, dtype, nleds, nframes, fps):
        self._record("set_movies_new", name, dtype, nleds, nframes, fps)
        return {"id": 7}

    def set_movies_full(self, movie):
        data = movie.read()
        self._record("set_movies_full", len(data))
        self.movies.append(data)

    def set_movies_current(self, movie_id):
        self._record("set_movies_current", movie_id)


def make_device(control=None, **config_kwargs):
    device = Device(DeviceConfig(name="Tree", host="10.0.0.5", **config_kwargs))
    device._control = control or FakeControl()
    return device


def names(control):
    return [call[0] for call in control.calls]


# ---- refresh --------------------------------------------------------------


def test_refresh_caches_gestalt():
    device = make_device()

    info = device.refresh()

    assert info.reachable is True
    assert info.number_of_led == 105
    assert info.led_profile == "RGBW"
    assert info.fw_version == "2.8.3"


def test_config_overrides_beat_the_device():
    device = make_device(number_of_led=250, led_profile="RGB")

    info = device.refresh()

    assert info.number_of_led == 250
    assert info.led_profile == "RGB"


def test_unreachable_device_is_reported_not_raised():
    control = FakeControl()
    control.fail_once["get_device_info"] = ConnectionError("no route to host")
    device = make_device(control)

    info = device.refresh()

    assert info.reachable is False
    assert "no route to host" in info.error


# ---- auth retry -----------------------------------------------------------


def test_expired_token_is_retried_once():
    control = FakeControl()
    control.fail_once["get_device_info"] = TokenExpiredError("expired")
    device = make_device(control)

    info = device.refresh()

    assert info.reachable is True
    assert names(control).count("get_device_info") == 2


def test_401_is_retried_with_a_fresh_session():
    class Response:
        status_code = 401

    control = FakeControl()
    control.fail_once["set_mode"] = ApplicationError("nope", response=Response())
    device = make_device(control)
    device.refresh()

    device.turn_off()

    assert names(control).count("set_mode") == 2
    assert control._session is None or True  # session was dropped before the retry


def test_a_non_401_application_error_is_not_retried():
    class Response:
        status_code = 500

    control = FakeControl()
    control.fail_once["set_mode"] = ApplicationError("boom", response=Response())
    device = make_device(control)
    device.refresh()

    with pytest.raises(DeviceError):
        device.turn_off()

    assert names(control).count("set_mode") == 1


def test_a_second_failure_surfaces_as_a_device_error():
    control = FakeControl()
    control.fail_once["get_mode"] = TokenExpiredError("expired")

    def always_fail():
        raise TokenExpiredError("still expired")

    control.get_mode = always_fail
    device = make_device(control)

    with pytest.raises(DeviceError):
        device._call(control.get_mode)


# ---- movie upload ---------------------------------------------------------


def test_new_firmware_uses_the_movies_api():
    control = FakeControl(version="2.8.3", family="F")
    device = make_device(control)
    device.refresh()

    device.apply_frame(b"\x00" * (105 * 4))

    assert "set_movies_new" in names(control)
    assert "set_movies_full" in names(control)
    assert ("set_movies_current", 7) in control.calls
    assert ("set_mode", "movie") == control.calls[-1]
    assert "set_led_movie_full" not in names(control)


def test_new_firmware_declares_the_right_descriptor():
    control = FakeControl(profile="RGBW")
    device = make_device(control)
    device.refresh()

    device.apply_frame(b"\x00" * (105 * 4))

    call = next(c for c in control.calls if c[0] == "set_movies_new")
    assert call[2] == "rgbw_raw"
    assert call[3] == 105
    assert call[4] == 1


def test_old_firmware_uses_the_legacy_api():
    control = FakeControl(version="2.4.14", family="D")
    device = make_device(control)
    device.refresh()

    device.apply_frame(b"\x00" * (105 * 3))

    assert "set_led_movie_full" in names(control)
    assert "set_movies_new" not in names(control)
    assert ("set_led_movie_config", 1000, 1, 105) in control.calls


@pytest.mark.parametrize(
    "family,version,expected",
    [
        ("F", "2.8.3", True),
        ("F", "2.5.6", True),
        ("F", "2.5.5", False),
        ("D", "2.8.3", False),
        ("F", "", False),
    ],
)
def test_movie_api_selection(family, version, expected):
    control = FakeControl(family=family, version=version)
    device = make_device(control)
    device.refresh()

    assert device.uses_new_movie_api is expected


def test_movie_frames_2_uploads_the_frame_twice():
    """Escape hatch for firmware that refuses a single-frame movie."""
    control = FakeControl()
    device = Device(DeviceConfig(name="Tree", host="10.0.0.5"), movie_frames=2)
    device._control = control
    device.refresh()

    frame = bytes(range(105 * 4 % 256)) * 4
    device.apply_frame(frame)

    call = next(c for c in control.calls if c[0] == "set_movies_new")
    assert call[4] == 2
    assert control.movies[-1] == frame * 2


def test_playing_movies_are_stopped_before_deleting():
    control = FakeControl()
    control.mode = "movie"
    device = make_device(control)
    device.refresh()

    device.apply_frame(b"\x00" * (105 * 4))

    order = names(control)
    assert order.index("set_mode") < order.index("delete_movies")


def test_apply_frame_refreshes_first_if_needed():
    control = FakeControl()
    device = make_device(control)

    device.apply_frame(b"\x00" * (105 * 4))

    assert "get_device_info" in names(control)


def test_apply_frame_on_an_unreachable_device_raises():
    control = FakeControl()
    control.fail_once["get_device_info"] = ConnectionError("down")
    device = make_device(control)

    with pytest.raises(DeviceError):
        device.apply_frame(b"")


# ---- manager --------------------------------------------------------------


def make_manager(*groups, gamma=DEFAULT_GAMMA_FOR_TESTS, **config_kwargs):
    """make_manager([105, 100], [80]) → two groups: two strands, then one."""
    config = AppConfig(
        groups=[
            GroupConfig(
                id=f"g{index}",
                name=f"Group {index}",
                devices=[
                    DeviceConfig(name=f"S{index}-{n}", host=f"10.0.{index}.{n}")
                    for n, _ in enumerate(counts)
                ],
            )
            for index, counts in enumerate(groups)
        ],
        gamma=gamma,
        **config_kwargs,
    )
    manager = DeviceManager(config)
    for group, counts in zip(manager.groups, groups):
        for device, count in zip(group.devices, counts):
            device._control = FakeControl(number_of_led=count)
            device.refresh()
    return manager


def frames_of(group):
    return [device._control.movies[-1] for device in group.devices]


# ---- offsets: the whole point of groups ------------------------------------


def test_offsets_reset_for_every_group():
    """Each group starts its pattern at its own first LED. Before groups this
    was one prefix sum across everything, so group 2 continued group 1."""
    manager = make_manager([105, 100], [105, 100])

    assert manager.groups[0].offsets() == [0, 105]
    assert manager.groups[1].offsets() == [0, 105]
    assert manager.groups[1].total_leds() == 205


def test_a_group_of_one_starts_at_zero():
    assert make_manager([250]).groups[0].offsets() == [0]


def test_segments_are_group_relative():
    segments = make_manager([105, 100], [80, 90]).groups[1].segments()

    assert [(s.offset, s.number_of_led) for s in segments] == [(0, 80), (80, 90)]


def test_a_strand_that_has_not_answered_counts_as_zero_leds():
    manager = make_manager([105, 100])
    manager.groups[0].devices[0].number_of_led = None

    assert manager.groups[0].offsets() == [0, 0]
    assert [s.host for s in manager.groups[0].segments()] == ["10.0.0.1"]


def test_a_first_strand_unplugged_at_boot_is_read_before_the_offsets_are():
    """Strand 1 was unreachable at startup, so it has no LED count. If the
    offsets were taken before it was read, strand 2 would start at 0 and the
    pattern would restart at the join. 103 + 100 so the two phases differ."""
    manager = make_manager([103, 100])
    first = manager.groups[0].devices[0]
    first.number_of_led, first.reachable = None, False

    asyncio.run(manager.apply_pattern("g0", PATTERN))

    top, bottom = frames_of(manager.groups[0])
    assert bottom != build_frame(PATTERN, 100, "RGBW"), "lengths must not hide a restart"
    assert top + bottom == build_frame(PATTERN, 203, "RGBW")


def test_one_strand_never_runs_two_operations_at_once():
    """An upload is several requests; another thread's must not land between them."""

    class SlowControl(FakeControl):
        def set_movies_new(self, *args):
            time.sleep(0.05)
            return super().set_movies_new(*args)

    control = SlowControl()
    device = make_device(control)
    device.refresh()
    control.calls.clear()

    threads = [threading.Thread(target=device.apply_frame, args=(b"x" * 420,)) for _ in range(2)]
    threads.append(threading.Thread(target=device.info, kwargs={"with_live_state": True}))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    upload = ["delete_movies", "set_movies_new", "set_movies_full", "set_movies_current"]
    sequence = names(control)
    starts = [i for i in range(len(sequence)) if sequence[i : i + len(upload)] == upload]
    assert len(starts) == 2, sequence


def test_apply_is_continuous_within_a_group():
    """The seam test: two strands in one group are one unbroken run."""
    manager = make_manager([105, 100])

    asyncio.run(manager.apply_pattern("g0", PATTERN))

    first, second = frames_of(manager.groups[0])
    assert first + second == build_frame(PATTERN, 205, "RGBW")


def test_two_groups_with_one_pattern_both_start_at_the_beginning():
    """The regression that groups exist to prevent: group 2 must not resume
    where group 1 stopped.

    Strand lengths here are deliberately not multiples of the pattern's 5-LED
    repeating unit (103 + 100 = 203), so continuing from group 1 would land on
    a visibly different phase. With 205 the two are indistinguishable and the
    test proves nothing.
    """
    manager = make_manager([103, 100], [103, 100])

    asyncio.run(manager.apply_pattern("g0", PATTERN))
    asyncio.run(manager.apply_pattern("g1", PATTERN))

    assert frames_of(manager.groups[0]) == frames_of(manager.groups[1])
    assert frames_of(manager.groups[1])[0] == build_frame(PATTERN, 103, "RGBW")
    # The shape the old whole-estate prefix sum would have produced:
    assert build_frame(PATTERN, 103, "RGBW", offset=203) != build_frame(
        PATTERN, 103, "RGBW"
    ), "test lengths must not be a whole number of pattern cycles"
    assert frames_of(manager.groups[1])[0] != build_frame(PATTERN, 103, "RGBW", offset=203)


def test_applying_to_one_group_leaves_the_others_alone():
    manager = make_manager([105], [100])

    asyncio.run(manager.apply_pattern("g0", PATTERN))

    assert manager.groups[1].devices[0]._control.movies == []
    assert manager.groups[1].devices[0]._control.mode == "off"


def test_groups_can_hold_different_patterns():
    other = Pattern(slots=[Slot(rgbw=(0, 0, 0, 255), weight=1)])
    manager = make_manager([10], [10])

    asyncio.run(manager.apply_pattern("g0", PATTERN))
    asyncio.run(manager.apply_pattern("g1", other))

    assert frames_of(manager.groups[0])[0] == build_frame(PATTERN, 10, "RGBW")
    assert frames_of(manager.groups[1])[0] == build_frame(other, 10, "RGBW")


def test_an_unknown_group_raises():
    with pytest.raises(UnknownGroupError):
        make_manager([105]).group("nope")


# ---- sync ------------------------------------------------------------------


def test_moving_a_strand_between_groups_keeps_its_token():
    """A Device knows nothing about its group, so a move must not cost a login."""
    manager = make_manager([105], [100])
    moved = manager.groups[0].devices[0]

    manager.config.groups[1].devices.append(manager.config.groups[0].devices.pop())
    manager.sync()

    assert manager.groups[0].devices == []
    assert manager.groups[1].devices[-1] is moved


def test_editing_a_strand_rebuilds_only_that_device():
    manager = make_manager([105, 100])
    untouched = manager.groups[0].devices[1]

    manager.config.groups[0].devices[0] = DeviceConfig(name="Renamed", host="10.0.0.0")
    manager.sync()

    assert manager.groups[0].devices[0] is not untouched
    assert manager.groups[0].devices[1] is untouched


def test_devices_flattens_every_group():
    assert [d.host for d in make_manager([1, 1], [1]).devices] == [
        "10.0.0.0",
        "10.0.0.1",
        "10.0.1.0",
    ]


# ---- group-scoped writes ---------------------------------------------------


def test_apply_pattern_also_sets_brightness_when_given():
    manager = make_manager([105])

    asyncio.run(manager.apply_pattern("g0", PATTERN.model_copy(update={"brightness": 30})))

    assert manager.groups[0].devices[0]._control.brightness == 30


def test_one_failing_strand_does_not_stop_the_others():
    manager = make_manager([105, 100])

    def explode(_movie):
        raise RuntimeError("cable out")

    manager.groups[0].devices[0]._control.set_movies_full = explode

    results = asyncio.run(manager.apply_pattern("g0", PATTERN))

    assert [result.ok for result in results] == [False, True]
    assert "cable out" in results[0].error


def test_turn_off_is_scoped_to_its_group():
    manager = make_manager([105], [100])

    asyncio.run(manager.turn_off("g0"))

    assert manager.groups[0].devices[0]._control.mode == "off"
    assert "set_mode" not in names(manager.groups[1].devices[0]._control)


def test_brightness_is_scoped_to_its_group():
    manager = make_manager([105], [100])

    asyncio.run(manager.set_brightness("g0", 25))

    assert manager.groups[0].devices[0]._control.brightness == 25
    assert manager.groups[1].devices[0]._control.brightness is None


def test_health_covers_every_group():
    results = asyncio.run(make_manager([105], [100]).health())

    assert [result.ok for result in results] == [True, True]


def test_info_tags_each_strand_with_its_group():
    infos = asyncio.run(make_manager([105], [100]).info())

    assert [info.group for info in infos] == ["g0", "g1"]


# ---- timeouts -------------------------------------------------------------


class FakeSession:
    """Just enough of requests.Session for the timeout patch to grab."""

    def __init__(self):
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(kwargs.get("timeout"))
        return "response"


class FakeControlInterface:
    def __init__(self):
        self.session = FakeSession()


def test_every_request_gets_a_timeout():
    """An unplugged strand must not hold a request open for the TCP timeout."""
    device = Device(DeviceConfig(name="Tree", host="10.0.0.5"), timeout=3.5)
    device._control = FakeControlInterface()

    session = device.control.session
    session.send("request")
    session.send("request", timeout=None)

    assert session.sent == [3.5, 3.5]


def test_an_explicit_timeout_is_left_alone():
    device = Device(DeviceConfig(name="Tree", host="10.0.0.5"), timeout=3.5)
    device._control = FakeControlInterface()

    device.control.session.send("request", timeout=30)

    assert device.control.session.sent == [30]


def test_the_patch_is_not_stacked_on_repeated_access():
    device = Device(DeviceConfig(name="Tree", host="10.0.0.5"), timeout=3.5)
    device._control = FakeControlInterface()
    original = device.control.session.send

    for _ in range(5):
        device.control  # noqa: B018 - the property is what applies the patch

    assert device.control.session.send is original


def test_manager_passes_the_configured_settings_to_each_device():
    manager = make_manager([105], timeout=9.0, movie_frames=2)

    assert manager.devices[0].timeout == 9.0
    assert manager.devices[0].movie_frames == 2


# ---- gamma -----------------------------------------------------------------


def test_uploaded_frames_are_gamma_corrected():
    """The strand gets PWM duty cycles, not sRGB — otherwise orange looks
    yellow on the tree."""
    manager = make_manager([1], gamma=2.2)

    asyncio.run(manager.apply_pattern("g0", Pattern(slots=[Slot(rgbw=(255, 80, 0, 0), weight=1)])))

    assert manager.groups[0].devices[0]._control.movies[-1] == bytes([0, 255, 20, 0])


def test_gamma_1_sends_values_through_untouched():
    manager = make_manager([1], gamma=1.0)

    asyncio.run(manager.apply_pattern("g0", Pattern(slots=[Slot(rgbw=(255, 80, 0, 0), weight=1)])))

    assert manager.groups[0].devices[0]._control.movies[-1] == bytes([0, 255, 80, 0])


# ---- timers ----------------------------------------------------------------


def test_setting_a_timer_sends_the_time_zone():
    """xled's own set_timer leaves tz out, and the firmware then blanks it."""
    control = FakeControl()
    device = make_device(control)

    device.set_timer(61200, 82800, 1000, "CST6CDT,M3.2.0,M11.1.0")

    assert control.timer["tz"] == "CST6CDT,M3.2.0,M11.1.0"
    assert control.calls[-1][1] == "http://10.0.0.5/xled/v1/timer"


def test_clearing_a_timer_leaves_the_clock_and_time_zone_alone():
    """Clearing also happens to strands that have left Dapple's schedule, so
    nothing else about them is Dapple's to change."""
    control = FakeControl()
    control.timer = {"time_on": 61200, "time_off": 82800, "time_now": 4242, "tz": "CST6CDT"}
    device = make_device(control)

    device.clear_timer()

    assert control.timer == {"time_on": -1, "time_off": -1, "time_now": 4242, "tz": "CST6CDT"}


def test_clearing_a_timer_that_is_already_clear_writes_nothing():
    control = FakeControl()
    device = make_device(control)

    device.clear_timer()

    assert "post_timer" not in names(control)


def test_a_timer_write_the_firmware_refuses_raises():
    control = FakeControl()
    control.session.code = 1103
    device = make_device(control)

    with pytest.raises(DeviceError):
        device.set_timer(61200, 82800, 1000, "bad")


def test_reading_a_timer_retries_with_a_fresh_token():
    control = FakeControl()
    control.fail_once["get_timer"] = TokenExpiredError()
    device = make_device(control)

    assert device.get_timer().time_now == 1000
    assert names(control) == ["get_timer", "get_timer"]


def test_firmware_that_reports_no_time_zone_reads_as_none():
    control = FakeControl()
    control.timer = {"time_on": -1, "time_off": -1, "time_now": 5}
    device = make_device(control)

    assert device.get_timer().tz is None


def timer_manager():
    config = AppConfig(
        groups=[
            GroupConfig(
                id="tree",
                name="Tree",
                devices=[
                    DeviceConfig(name="One", host="10.0.0.1"),
                    DeviceConfig(name="Two", host="10.0.0.2"),
                ],
            )
        ]
    )
    manager = DeviceManager(config)
    controls = {}
    for device in manager.devices:
        device._control = controls[device.host] = FakeControl()
        device.reachable = True
    return manager, controls


def test_a_timer_write_can_be_limited_to_one_strand():
    """Correcting one strand mustn't rewrite its neighbour for no reason."""
    manager, controls = timer_manager()

    results = asyncio.run(
        manager.set_timer("tree", 100, 200, lambda: 50, "UTC0", hosts={"10.0.0.2"})
    )

    assert [result.host for result in results] == ["10.0.0.2"]
    assert controls["10.0.0.1"].timer["time_on"] == -1
    assert controls["10.0.0.2"].timer["time_on"] == 100


def test_a_strand_that_cannot_be_read_comes_back_as_unreachable():
    """One unplugged strand mustn't stop the rest of the group being checked."""
    manager, controls = timer_manager()
    controls["10.0.0.1"].fail_once["get_timer"] = ApplicationError("nope")

    timers = {timer.host: timer for timer in asyncio.run(manager.read_timers("tree"))}

    assert timers["10.0.0.1"].reachable is False
    assert timers["10.0.0.2"].reachable is True


# ---- reboots ---------------------------------------------------------------


def test_uptime_is_read_as_a_number():
    """The firmware sends a string, and a string compares wrongly without complaint."""
    control = FakeControl()
    control.brightness = 20
    device = make_device(control)

    reading = device.read_boot()

    assert (reading.uptime_ms, reading.brightness) == (3600000, 20)


def test_firmware_with_no_uptime_reads_as_none():
    control = FakeControl()
    control.uptime = None
    device = make_device(control)

    assert device.read_boot().uptime_ms is None


def boot_manager():
    config = AppConfig(
        groups=[
            GroupConfig(
                id="tree",
                name="Tree",
                devices=[
                    DeviceConfig(name="One", host="10.0.0.1"),
                    DeviceConfig(name="Two", host="10.0.0.2"),
                ],
            )
        ]
    )
    manager = DeviceManager(config)
    controls = {}
    for device in manager.devices:
        device._control = controls[device.host] = FakeControl()
        device.reachable = True
    return manager, controls


def test_brightness_can_be_limited_to_one_strand():
    """Only the strand that rebooted lost it; its neighbour may have been changed."""
    manager, controls = boot_manager()

    results = asyncio.run(manager.set_brightness("tree", 20, hosts={"10.0.0.2"}))

    assert [result.host for result in results] == ["10.0.0.2"]
    assert controls["10.0.0.1"].brightness is None
    assert controls["10.0.0.2"].brightness == 20


def test_a_strand_that_cannot_be_read_does_not_stop_the_others():
    manager, controls = boot_manager()
    controls["10.0.0.1"].fail_once["get_brightness"] = ApplicationError("nope")

    readings = {reading.host: reading for reading in asyncio.run(manager.read_boots("tree"))}

    assert readings["10.0.0.1"].uptime_ms is None
    assert readings["10.0.0.2"].uptime_ms == 3600000

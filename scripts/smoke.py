#!/usr/bin/env python3
"""Apply a pattern to one strand from the command line — the first real-hardware test.

    python scripts/smoke.py 192.168.40.21

Prints what the strand reports about itself (LED count, profile, firmware, which
movie-upload path that implies), uploads a 4:1 orange/purple pattern, and leaves
it there. Then:

  * check the colors look right. Orange reading as yellow is a gamma problem,
    not a byte-order one — compare `--gamma 1.0`, `--gamma 2.2` (the default)
    and `--gamma 2.8` on the real strand and put the winner in config.yaml. A
    genuinely wrong byte order looks nothing like the color you asked for, and
    `--white` makes that obvious;
  * exit the script and confirm the strand keeps the pattern;
  * power-cycle the strand and confirm it comes back with the pattern;
  * if the upload succeeds but the strand stays dark, retry with `--frames 2` —
    some firmware refuses a single-frame movie. If that fixes it, set
    `movie_frames: 2` in config.yaml.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DeviceConfig  # noqa: E402
from app.devices import Device  # noqa: E402
from app.models import Pattern, Slot  # noqa: E402
from app.pattern import DEFAULT_GAMMA, build_frame, led_colors  # noqa: E402

ORANGE = (255, 80, 0, 0)
PURPLE = (128, 0, 255, 0)
WHITE = (0, 0, 0, 255)


def parse_color(text: str) -> tuple[int, int, int, int]:
    """``ff5000`` or ``255,80,0`` or ``255,80,0,120`` (the 4th value is white)."""
    text = text.strip().lstrip("#")
    if "," in text:
        parts = [int(part) for part in text.split(",")]
        if len(parts) == 3:
            parts.append(0)
        if len(parts) != 4:
            raise argparse.ArgumentTypeError(f"expected 3 or 4 values, got {text!r}")
    else:
        if len(text) != 6:
            raise argparse.ArgumentTypeError(f"expected rrggbb, got {text!r}")
        parts = [int(text[i : i + 2], 16) for i in (0, 2, 4)] + [0]
    if any(part < 0 or part > 255 for part in parts):
        raise argparse.ArgumentTypeError(f"values must be 0-255: {text!r}")
    return tuple(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP address of the strand")
    parser.add_argument(
        "--color",
        action="append",
        type=parse_color,
        metavar="COLOR",
        help="repeatable; hex rrggbb or r,g,b[,w]. Default: orange and purple",
    )
    parser.add_argument(
        "--weight",
        action="append",
        type=int,
        metavar="N",
        help="repeatable, paired with --color in order. Default: 4 then 1",
    )
    parser.add_argument("--layout", choices=["interleaved", "blocked"], default="interleaved")
    parser.add_argument("--block-size", type=int, default=1)
    parser.add_argument("--brightness", type=int, help="0-100")
    parser.add_argument("--frames", type=int, default=1, help="upload the frame N times (try 2)")
    parser.add_argument(
        "--gamma",
        type=float,
        default=DEFAULT_GAMMA,
        help=f"sRGB → PWM correction (default {DEFAULT_GAMMA}; 1.0 sends values untouched)",
    )
    parser.add_argument("--white", action="store_true", help="ignore colors, light the W channel only")
    parser.add_argument("--off", action="store_true", help="just turn the strand off and exit")
    parser.add_argument("--dry-run", action="store_true", help="read the strand, build the frame, upload nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s"
    )

    device = Device(
        DeviceConfig(name="smoke", host=args.host),
        movie_frames=max(1, args.frames),
        gamma=args.gamma,
    )

    print(f"→ {args.host}: reading gestalt…")
    info = device.refresh()
    if not info.reachable:
        print(f"✗ unreachable: {info.error}")
        print("  Check the IP, and that this host can reach the strand's VLAN on port 80.")
        return 1

    print(f"  name          {info.name}")
    print(f"  LEDs          {info.number_of_led}")
    print(f"  led_profile   {info.led_profile}  ({'4' if info.led_profile == 'RGBW' else '3'} bytes/LED)")
    print(f"  firmware      {info.fw_version}  family {info.fw_family}")
    print(f"  upload path   {'/movies/* (new)' if device.uses_new_movie_api else '/led/movie/* (legacy)'}")

    if args.off:
        device.turn_off()
        print("✓ off")
        return 0

    if args.white:
        slots = [Slot(rgbw=WHITE, weight=1)]
    else:
        colors = args.color or [ORANGE, PURPLE]
        weights = args.weight or ([4, 1] if not args.color else [1] * len(colors))
        if len(weights) < len(colors):
            weights = weights + [1] * (len(colors) - len(weights))
        slots = [Slot(rgbw=color, weight=weight) for color, weight in zip(colors, weights)]

    pattern = Pattern(
        slots=slots,
        layout=args.layout,
        block_size=max(1, args.block_size),
        brightness=args.brightness,
    )

    num_leds = info.number_of_led or 0
    profile = info.led_profile or "RGB"
    frame = build_frame(pattern, num_leds, profile, gamma=args.gamma)
    preview = led_colors(pattern, min(num_leds, 20))
    bytes_per_led = 4 if profile == "RGBW" else 3
    print(f"  frame         {len(frame)} bytes for {num_leds} LEDs × {args.frames} frame(s)")
    print(f"  gamma         {args.gamma}")
    print("  asked for     " + " ".join("".join(f"{c:02x}" for c in led) for led in preview[:8]))
    print(
        "  on the wire   "
        + " ".join(
            frame[i : i + bytes_per_led].hex()
            for i in range(0, min(len(frame), 8 * bytes_per_led), bytes_per_led)
        )
    )

    if args.dry_run:
        print("✓ dry run, nothing uploaded")
        return 0

    print("→ uploading…")
    device.apply_frame(frame)
    if args.brightness is not None:
        device.set_brightness(args.brightness)
    print("✓ applied. Leave it, power-cycle the strand, and check the pattern survives.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

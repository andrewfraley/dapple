"""Turn a :class:`~app.models.Pattern` into the raw LED bytes a strand expects.

Pure logic, no device I/O, no imports beyond the models — everything here is
exercised by ``tests/test_pattern.py``.

The same algorithm is ported to ``frontend/src/pattern.js`` so the browser can
draw the preview without a round trip; ``tests/test_pattern_parity.py`` writes
the fixtures that keep the two implementations honest.
"""

from __future__ import annotations

from functools import lru_cache
from math import gcd
from typing import Sequence

from app.models import Layout, LedProfile, Pattern, Slot

BLACK: tuple[int, int, int, int] = (0, 0, 0, 0)

#: Bytes on the wire per LED, keyed by the strand's ``led_profile``.
BYTES_PER_LED: dict[str, int] = {"RGB": 3, "RGBW": 4}

#: sRGB's approximate gamma. Colors arrive from the browser gamma-encoded, but
#: an LED's PWM duty cycle is linear in emitted light, so they have to be
#: decoded on the way to the strand. 1.0 disables the correction.
DEFAULT_GAMMA = 2.2


@lru_cache(maxsize=8)
def gamma_table(gamma: float) -> tuple[int, ...]:
    """sRGB channel value → PWM duty cycle, for all 256 values.

    Without this, ``#FF5000`` reaches the strand as R=255 G=80, which emits
    four times the green of the orange on screen — it looks yellow. Cached
    because it's the same 256 numbers on every frame.
    """
    if gamma == 1.0:
        return tuple(range(256))
    return tuple(round(255 * (value / 255) ** gamma) for value in range(256))


def active_slots(pattern: Pattern) -> list[Slot]:
    """Slots that actually get LEDs: weight 0 means "unused slot"."""
    return [slot for slot in pattern.slots if slot.weight > 0]


def reduce_weights(weights: Sequence[int]) -> list[int]:
    """Divide weights by their GCD, so ``[80, 20]`` becomes ``[4, 1]``.

    Keeps the repeating unit short, which is what makes an 80/20 split show up
    as a 5-LED cycle instead of a 100-LED one.
    """
    if not weights:
        return []
    divisor = 0
    for weight in weights:
        divisor = gcd(divisor, weight)
    if divisor <= 1:
        return list(weights)
    return [weight // divisor for weight in weights]


def interleave(weights: Sequence[int]) -> list[int]:
    """Spread weighted colors as evenly as the counts allow.

    Returns one repeating unit as slot indices, of length ``sum(weights)``.
    Each step hands the LED to whichever color is furthest behind its share
    (largest-remainder apportionment, the same idea as Bresenham's line
    algorithm); ties go to the earlier slot. So 7:3 comes out as
    ``A B A A A B A A B A`` — never ``A A A A A A A B B B``.
    """
    total = sum(weights)
    if total <= 0:
        return []
    counts = [0] * len(weights)
    sequence: list[int] = []
    for step in range(1, total + 1):
        best = 0
        best_credit = weights[0] * step - counts[0] * total
        for index in range(1, len(weights)):
            credit = weights[index] * step - counts[index] * total
            if credit > best_credit:
                best, best_credit = index, credit
        sequence.append(best)
        counts[best] += 1
    return sequence


def block(weights: Sequence[int], block_size: int) -> list[int]:
    """One repeating unit where each color owns ``weight * block_size`` LEDs in a row."""
    sequence: list[int] = []
    for index, weight in enumerate(weights):
        sequence.extend([index] * (weight * block_size))
    return sequence


def build_sequence(
    weights: Sequence[int],
    layout: Layout = "interleaved",
    block_size: int = 1,
) -> list[int]:
    """The repeating unit of slot indices for the given weights and layout."""
    reduced = reduce_weights(weights)
    if layout == "blocked":
        return block(reduced, max(1, block_size))
    return interleave(reduced)


def led_colors(
    pattern: Pattern,
    num_leds: int,
    offset: int = 0,
) -> list[tuple[int, int, int, int]]:
    """The RGBW value of every LED, in strand order.

    ``offset`` shifts the pattern's phase. Applying it to a strand as the total
    LED count of the strands physically before it makes the pattern continue
    across the join instead of restarting.
    """
    if num_leds <= 0:
        return []
    slots = active_slots(pattern)
    if not slots:
        return [BLACK] * num_leds
    sequence = build_sequence(
        [slot.weight for slot in slots], pattern.layout, pattern.block_size
    )
    if not sequence:
        return [BLACK] * num_leds
    colors = [tuple(slot.rgbw) for slot in slots]
    length = len(sequence)
    return [colors[sequence[(i + offset) % length]] for i in range(num_leds)]


def pack(
    colors: Sequence[Sequence[int]],
    led_profile: LedProfile,
    gamma: float = DEFAULT_GAMMA,
) -> bytes:
    """Pack RGBW tuples into the strand's wire format, gamma-corrected.

    RGB strands take ``R G B``. RGBW strands take ``W R G B`` — the white
    channel comes first, which is what xled_plus does and what the strands
    actually show; a swapped order here looks like wrong colors, not an error.

    Colors come in as sRGB, the way the browser and the preview use them, and
    leave as PWM duty cycles — see :func:`gamma_table`.
    """
    table = gamma_table(gamma)
    if led_profile == "RGBW":
        return bytes(
            table[byte] for r, g, b, w in colors for byte in (w, r, g, b)
        )
    if led_profile == "RGB":
        return bytes(table[byte] for r, g, b, _w in colors for byte in (r, g, b))
    raise ValueError(f"unknown led_profile: {led_profile!r}")


def build_frame(
    pattern: Pattern,
    num_leds: int,
    led_profile: LedProfile,
    offset: int = 0,
    gamma: float = DEFAULT_GAMMA,
) -> bytes:
    """One movie frame: ``num_leds`` LEDs of ``pattern``, ready to upload."""
    return pack(led_colors(pattern, num_leds, offset), led_profile, gamma)

"""Frame building: weights, distribution, layouts, packing, multi-strand offsets."""

import pytest

from app.models import Pattern, Slot
from app.pattern import (
    BLACK,
    DEFAULT_GAMMA,
    build_frame,
    build_sequence,
    gamma_table,
    interleave,
    led_colors,
    pack,
    reduce_weights,
)

ORANGE = (255, 80, 0, 0)
PURPLE = (128, 0, 255, 0)
WHITE = (0, 0, 0, 255)
RED = (255, 0, 0, 0)
GREEN = (0, 255, 0, 0)


def pattern(*slots, **kwargs):
    return Pattern(slots=[Slot(rgbw=rgbw, weight=weight) for rgbw, weight in slots], **kwargs)


# ---- weights --------------------------------------------------------------


@pytest.mark.parametrize(
    "weights,expected",
    [
        ([80, 20], [4, 1]),
        ([50, 50], [1, 1]),
        ([1, 1], [1, 1]),
        ([70, 30], [7, 3]),
        ([2, 4, 6], [1, 2, 3]),
        ([3], [1]),
        ([7, 3], [7, 3]),
        ([], []),
    ],
)
def test_reduce_weights(weights, expected):
    assert reduce_weights(weights) == expected


# ---- interleaving ---------------------------------------------------------


def test_interleave_alternates_when_equal():
    assert interleave([1, 1]) == [0, 1]
    assert interleave([2, 2]) == [0, 1, 0, 1]


def test_interleave_4_to_1():
    sequence = interleave([4, 1])
    assert len(sequence) == 5
    assert sequence.count(0) == 4
    assert sequence.count(1) == 1


def test_interleave_7_to_3_spreads_the_minority():
    """The whole point: 7:3 must not clump into O*7 then P*3."""
    sequence = interleave([7, 3])

    assert len(sequence) == 10
    assert sequence.count(0) == 7
    assert sequence.count(1) == 3
    # No two minority LEDs adjacent, and no run of majority longer than 3.
    minority = [i for i, slot in enumerate(sequence) if slot == 1]
    gaps = [b - a for a, b in zip(minority, minority[1:])]
    assert all(gap >= 2 for gap in gaps), sequence
    assert "000000" not in "".join(str(s) for s in sequence), sequence


def test_interleave_three_colors():
    sequence = interleave([3, 2, 1])
    assert len(sequence) == 6
    assert [sequence.count(i) for i in range(3)] == [3, 2, 1]


def test_interleave_is_deterministic():
    assert interleave([5, 3, 2]) == interleave([5, 3, 2])


def test_interleave_empty():
    assert interleave([]) == []
    assert interleave([0, 0]) == []


def test_build_sequence_reduces_first():
    assert build_sequence([80, 20]) == build_sequence([4, 1])
    assert len(build_sequence([80, 20])) == 5


# ---- blocked --------------------------------------------------------------


def test_blocked_layout_groups_colors():
    assert build_sequence([2, 1], layout="blocked") == [0, 0, 1]


def test_blocked_layout_block_size():
    assert build_sequence([2, 1], layout="blocked", block_size=3) == [0] * 6 + [1] * 3


def test_blocked_layout_reduces_weights():
    assert build_sequence([80, 20], layout="blocked") == [0, 0, 0, 0, 1]


# ---- led_colors -----------------------------------------------------------


def test_led_colors_cycles_the_unit():
    colors = led_colors(pattern((ORANGE, 4), (PURPLE, 1)), 10)
    assert len(colors) == 10
    assert colors.count(PURPLE) == 2
    assert colors[:5] == colors[5:]


def test_led_colors_drops_zero_weight_slots():
    colors = led_colors(pattern((ORANGE, 1), (PURPLE, 0)), 4)
    assert colors == [ORANGE] * 4


def test_led_colors_with_no_usable_slots_is_black():
    assert led_colors(pattern(), 3) == [BLACK] * 3
    assert led_colors(pattern((ORANGE, 0)), 3) == [BLACK] * 3


def test_led_colors_zero_leds():
    assert led_colors(pattern((ORANGE, 1)), 0) == []


def test_led_colors_partial_cycle():
    """A strand that doesn't divide evenly by the unit just truncates."""
    colors = led_colors(pattern((ORANGE, 4), (PURPLE, 1)), 7)
    assert len(colors) == 7


# ---- packing --------------------------------------------------------------


def test_pack_rgb_drops_white():
    assert pack([(1, 2, 3, 4)], "RGB", gamma=1.0) == bytes([1, 2, 3])


def test_pack_rgbw_puts_white_first():
    """Twinkly's rgbw_raw frames are W R G B, not R G B W."""
    assert pack([(1, 2, 3, 4)], "RGBW", gamma=1.0) == bytes([4, 1, 2, 3])


def test_pack_unknown_profile():
    with pytest.raises(ValueError):
        pack([(1, 2, 3, 4)], "CMYK")


# ---- gamma ----------------------------------------------------------------


def test_gamma_table_keeps_the_ends_fixed():
    table = gamma_table(DEFAULT_GAMMA)

    assert table[0] == 0
    assert table[255] == 255


def test_gamma_table_only_ever_darkens():
    table = gamma_table(DEFAULT_GAMMA)

    assert all(out <= value for value, out in enumerate(table))
    assert all(a <= b for a, b in zip(table, table[1:])), "must stay monotonic"


def test_gamma_1_is_a_no_op():
    assert gamma_table(1.0) == tuple(range(256))
    assert pack([(1, 2, 3, 4)], "RGBW", gamma=1.0) == bytes([4, 1, 2, 3])


def test_orange_does_not_reach_the_strand_as_yellow():
    """#FF5000 is sRGB; sent raw it drives 4x the green it should and reads as
    yellow on the strand. Decoding to linear PWM is what fixes it."""
    _w, red, green, _blue = pack([(255, 80, 0, 0)], "RGBW")

    assert red == 255
    assert green == 20


def test_build_frame_applies_gamma():
    assert build_frame(pattern((ORANGE, 1)), 1, "RGB") == bytes([255, 20, 0])


def test_build_frame_gamma_is_overridable():
    assert build_frame(pattern((ORANGE, 1)), 1, "RGB", gamma=1.0) == bytes([255, 80, 0])


def test_the_preview_is_left_in_display_space():
    """led_colors feeds the canvas, which draws sRGB — gamma belongs only on
    the wire, or the preview would darken while the strand stayed the same."""
    assert led_colors(pattern((ORANGE, 1)), 1) == [ORANGE]


def test_build_frame_lengths():
    single = pattern((ORANGE, 1))
    assert len(build_frame(single, 50, "RGB")) == 150
    assert len(build_frame(single, 50, "RGBW")) == 200


def test_build_frame_true_white_on_rgbw():
    """Full-scale channels are unchanged by gamma, so white stays white."""
    frame = build_frame(pattern((WHITE, 1)), 2, "RGBW")
    assert frame == bytes([255, 0, 0, 0] * 2)


# ---- offsets / multi-strand continuity ------------------------------------


def test_offset_shifts_the_phase():
    single = pattern((ORANGE, 4), (PURPLE, 1))
    full = led_colors(single, 10)
    assert led_colors(single, 5, offset=3) == full[3:8]


def test_two_strands_join_seamlessly():
    """Strand A (105 LEDs) + strand B (100 LEDs, offset 105) must equal one
    205-LED run — that's what keeps the pattern unbroken across the join."""
    combined_pattern = pattern((ORANGE, 7), (PURPLE, 3))

    whole = build_frame(combined_pattern, 205, "RGBW")
    strand_a = build_frame(combined_pattern, 105, "RGBW", offset=0)
    strand_b = build_frame(combined_pattern, 100, "RGBW", offset=105)

    assert strand_a + strand_b == whole


def test_two_strands_join_seamlessly_blocked():
    blocked = pattern((RED, 2), (GREEN, 1), layout="blocked", block_size=4)
    whole = build_frame(blocked, 205, "RGB")
    assert build_frame(blocked, 105, "RGB") + build_frame(blocked, 100, "RGB", offset=105) == whole


def test_offset_larger_than_the_unit_wraps():
    single = pattern((ORANGE, 1), (PURPLE, 1))
    assert led_colors(single, 4, offset=2) == led_colors(single, 4, offset=0)


def test_a_pattern_whose_repeating_unit_is_absurdly_long_is_refused():
    """Blocked with coprime weights would build one unit of millions of LEDs."""
    primes = [997, 991, 983, 977, 971, 967, 953, 947, 941, 937, 929, 919, 911, 907, 887, 883]
    slots = [Slot(rgbw=(255, 0, 0, 0), weight=w) for w in primes]

    with pytest.raises(ValueError, match="repeats only every"):
        Pattern(slots=slots, layout="blocked", block_size=500)
    Pattern(slots=slots)  # interleaved, the same shares: 15,000 LEDs is fine


def test_the_editors_largest_pattern_is_accepted():
    """8 colors, shares up to 100 in steps of 5, blocks up to 500."""
    shares = [100, 95, 90, 85, 80, 75, 70, 65]
    Pattern(
        slots=[Slot(rgbw=(255, 0, 0, 0), weight=w) for w in shares],
        layout="blocked",
        block_size=500,
    )

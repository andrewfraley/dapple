"""Fixtures that keep frontend/src/pattern.js honest against app/pattern.py.

The UI draws its preview in JS so it stays responsive while sliders move; that
means the algorithm exists twice. This test regenerates the shared fixtures and
fails if the checked-in file has drifted, and ``frontend/src/pattern.test.js``
asserts the JS port reproduces them exactly.
"""

import json
from pathlib import Path

from app.models import Pattern, Slot
from app.pattern import led_colors

FIXTURES = Path(__file__).parent / "fixtures" / "pattern_fixtures.json"

CASES = [
    ("80/20 interleaved", {"slots": [[[255, 80, 0, 0], 80], [[128, 0, 255, 0], 20]]}, 40, 0),
    ("7:3 interleaved", {"slots": [[[255, 0, 0, 0], 7], [[0, 0, 255, 0], 3]]}, 30, 0),
    (
        "three colors",
        {"slots": [[[255, 0, 0, 0], 3], [[0, 255, 0, 0], 2], [[0, 0, 0, 255], 1]]},
        25,
        0,
    ),
    (
        "blocked",
        {"slots": [[[255, 0, 0, 0], 2], [[0, 255, 0, 0], 1]], "layout": "blocked", "block_size": 4},
        40,
        0,
    ),
    ("second strand offset", {"slots": [[[255, 80, 0, 0], 7], [[128, 0, 255, 0], 3]]}, 100, 105),
    ("zero-weight slot dropped", {"slots": [[[255, 0, 0, 0], 1], [[0, 255, 0, 0], 0]]}, 8, 0),
    ("no usable slots", {"slots": [[[255, 0, 0, 0], 0]]}, 5, 0),
    ("single color", {"slots": [[[0, 0, 0, 255], 1]]}, 6, 0),
]


def build(spec: dict) -> Pattern:
    return Pattern(
        slots=[Slot(rgbw=tuple(rgbw), weight=weight) for rgbw, weight in spec["slots"]],
        layout=spec.get("layout", "interleaved"),
        block_size=spec.get("block_size", 1),
    )


def generate() -> list[dict]:
    fixtures = []
    for label, spec, num_leds, offset in CASES:
        pattern = build(spec)
        fixtures.append(
            {
                "label": label,
                "pattern": pattern.model_dump(),
                "num_leds": num_leds,
                "offset": offset,
                "leds": [list(color) for color in led_colors(pattern, num_leds, offset)],
            }
        )
    return fixtures


def test_fixtures_are_up_to_date():
    """Regenerates the file; a diff here means the JS port needs re-checking."""
    fixtures = generate()
    serialized = json.dumps(fixtures, indent=2) + "\n"

    stale = not FIXTURES.is_file() or FIXTURES.read_text() != serialized
    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES.write_text(serialized)

    assert not stale, (
        f"{FIXTURES} was out of date and has been rewritten. "
        "Re-run `npm --prefix frontend test` to check the JS port still matches."
    )


def test_every_case_has_the_led_count_it_asked_for():
    for fixture in generate():
        assert len(fixture["leds"]) == fixture["num_leds"], fixture["label"]

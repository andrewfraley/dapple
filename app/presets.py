"""Named patterns on disk, in /data/presets.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.models import MAX_NAME, Pattern, Slot
from app.storage import atomic_write

log = logging.getLogger(__name__)

ORANGE = (255, 152, 0, 0)
PURPLE = (128, 0, 255, 0)
RED = (255, 0, 0, 0)
GREEN = (0, 255, 0, 0)
WHITE = (0, 0, 0, 255)
BLUE = (0, 0, 255, 0)
YELLOW = (255, 236, 0, 0)


def default_presets() -> dict[str, Pattern]:
    """Seeded on first run so the UI is useful before anything is saved.

    Weights are on the editor's 0–100 Share slider, which moves in steps of 5.
    80/20 and 4/1 light the strand identically once reduced, but 4 and 1 would
    load with both sliders at the far left.
    """
    return {
        "Halloween": Pattern(
            slots=[
                Slot(rgbw=ORANGE, weight=70),
                Slot(rgbw=PURPLE, weight=20),
                Slot(rgbw=YELLOW, weight=10),
            ]
        ),
        "Christmas": Pattern(
            slots=[
                Slot(rgbw=RED, weight=40),
                Slot(rgbw=GREEN, weight=40),
                Slot(rgbw=WHITE, weight=20),
            ]
        ),
        "July 4th": Pattern(
            slots=[
                Slot(rgbw=RED, weight=30),
                Slot(rgbw=WHITE, weight=30),
                Slot(rgbw=BLUE, weight=30),
            ]
        ),
        "Warm white": Pattern(slots=[Slot(rgbw=WHITE, weight=100)]),
    }


class PresetError(Exception):
    """A preset name that is missing, or not usable as a key."""


class PresetStorageError(Exception):
    """presets.json could not be written — almost always /data permissions."""


def validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise PresetError("preset name must not be empty")
    if len(name) > MAX_NAME:
        raise PresetError(f"preset name must be {MAX_NAME} characters or fewer")
    if "/" in name or "\\" in name:
        raise PresetError("preset name must not contain slashes")
    return name


class PresetStore:
    """In-memory presets, written through to JSON on every change.

    Writes go to a temp file in the same directory and are renamed into place,
    so a crash mid-write cannot leave a truncated presets.json behind. A change
    only replaces the in-memory set once it is on disk, so a failed save isn't
    left showing in the UI and in Home Assistant's effect list.
    """

    def __init__(self, path: Path | str, seed_defaults: bool = True):
        self.path = Path(path)
        self._presets: dict[str, Pattern] = {}
        self.writable = True
        if self.path.is_file():
            self._presets = self._read()
        elif seed_defaults:
            self._presets = default_presets()
            try:
                self._write(self._presets)
            except PresetStorageError as exc:
                # A read-only /data shouldn't stop the app: applying patterns
                # and the built-in presets still work, saving doesn't.
                log.error("%s Presets are in memory only for this run.", exc)

    def _read(self) -> dict[str, Pattern]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read %s (%s); starting with no presets", self.path, exc)
            return {}
        presets = {}
        for name, payload in (raw or {}).items():
            try:
                presets[name] = Pattern.model_validate(payload)
            except Exception as exc:  # a hand-edited file shouldn't take the app down
                log.error("Skipping preset %r: %s", name, exc)
        return presets

    def _write(self, presets: dict[str, Pattern]) -> None:
        payload = {name: pattern.model_dump() for name, pattern in sorted(presets.items())}
        try:
            atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            self.writable = False
            raise PresetStorageError(
                f"Cannot write {self.path} ({exc.strerror or exc}). "
                "Make the data directory writable by the user the container runs as "
                "— see 'Permissions on ./data' in the README."
            ) from exc
        self.writable = True

    def all(self) -> dict[str, Pattern]:
        return dict(self._presets)

    def names(self) -> list[str]:
        return sorted(self._presets)

    def get(self, name: str) -> Pattern:
        try:
            return self._presets[name]
        except KeyError:
            raise PresetError(f"no such preset: {name!r}") from None

    def save(self, name: str, pattern: Pattern) -> Pattern:
        name = validate_name(name)
        presets = {**self._presets, name: pattern}
        self._write(presets)
        self._presets = presets
        return pattern

    def delete(self, name: str) -> None:
        if name not in self._presets:
            raise PresetError(f"no such preset: {name!r}")
        presets = {key: value for key, value in self._presets.items() if key != name}
        self._write(presets)
        self._presets = presets

"""What each group is showing, in /data/state.json.

Shaped like :class:`~app.presets.PresetStore`, with one deliberate difference:
a failed write here must not fail the request. A preset save *is* the thing the
user asked for, so it has to 500 when it doesn't happen. This is a side record
of an apply that already reached the lights — failing the route would make Home
Assistant retry an operation that worked.

It records what was *sent*. The strands hold their movie themselves, so a power
cycle or someone opening the Twinkly app can make this optimistic; the UI calls
it "last applied" for that reason.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.models import DeviceInfo, GroupLive, GroupState, Pattern
from app.storage import atomic_write

log = logging.getLogger(__name__)

#: Modes in which a strand is showing Dapple's movie, or nothing at all. Any
#: other mode (color, effect, playlist…) means something else took over.
OWN_MODES = frozenset({"movie", "off"})


def live_state(
    recorded: GroupState | None, infos: Sequence[DeviceInfo], presets: set[str]
) -> GroupLive:
    """A group as its strands report it, for the UI and for Home Assistant alike.

    The group counts as on while any strand is lit. The preset is the one Dapple
    last applied, but only while the strands are still showing it.
    """
    answered = [info for info in infos if info.mode is not None]
    power = None
    taken_over = False
    if answered:
        power = "on" if any(info.mode != "off" for info in answered) else "off"
        taken_over = any(info.mode not in OWN_MODES for info in answered)
    preset = None
    if recorded is not None and recorded.preset in presets and not taken_over:
        preset = recorded.preset
    return GroupLive(
        power=power,
        brightness=next(
            (info.brightness for info in answered if info.brightness is not None), None
        ),
        preset=preset,
        taken_over=taken_over,
        answering=len(answered),
        strands=len(infos),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    """Per-group last-applied pattern, written through to JSON."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.writable = True
        self._state: dict[str, GroupState] = self._read() if self.path.is_file() else {}

    # ---- reading -----------------------------------------------------------

    def _read(self) -> dict[str, GroupState]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read %s (%s); starting with no state", self.path, exc)
            return {}
        state = {}
        for group_id, payload in (raw or {}).items():
            try:
                state[group_id] = GroupState.model_validate(payload)
            except Exception as exc:  # a hand-edited file shouldn't take the app down
                log.error("Skipping state for group %r: %s", group_id, exc)
        return state

    def get(self, group_id: str) -> GroupState | None:
        """None when a group has never been applied to — not an error."""
        return self._state.get(group_id)

    # ---- writing -----------------------------------------------------------

    def record(self, group_id: str, pattern: Pattern, preset: str | None = None) -> GroupState:
        """Remember the pattern just applied to a group.

        Recorded even when some strands failed: some of them did change, and the
        per-device results in the response are the truth about which.
        """
        state = GroupState(pattern=pattern, preset=preset, power="on", applied_at=_now())
        self._state[group_id] = state
        self._save()
        return state

    def set_brightness(self, group_id: str, value: int) -> GroupState | None:
        """Keep the stored pattern, update its brightness.

        Without this, a preset applied to two groups and then dimmed on one
        would have both claiming the same thing.
        """
        current = self._state.get(group_id)
        if current is None:
            return None
        state = current.model_copy(
            update={
                "pattern": current.pattern.model_copy(update={"brightness": value}),
                "applied_at": _now(),
            }
        )
        self._state[group_id] = state
        self._save()
        return state

    def set_power(self, group_id: str, on: bool) -> GroupState | None:
        """Off doesn't clear the pattern — the strand still holds it, it's dark."""
        current = self._state.get(group_id)
        if current is None:
            return None
        state = current.model_copy(update={"power": "on" if on else "off", "applied_at": _now()})
        self._state[group_id] = state
        self._save()
        return state

    def forget(self, group_id: str) -> None:
        if self._state.pop(group_id, None) is not None:
            self._save()

    def _save(self) -> None:
        """Write, or log and carry on — never raise into a request."""
        payload = {
            group_id: json.loads(state.model_dump_json())
            for group_id, state in sorted(self._state.items())
        }
        try:
            atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            if self.writable:  # log the first time, not on every apply
                log.error(
                    "Cannot write %s (%s). Each group's last-applied pattern will be "
                    "forgotten on restart; see 'Permissions on ./data' in the README.",
                    self.path,
                    exc.strerror or exc,
                )
            self.writable = False
            return
        self.writable = True

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
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.models import GroupState, Pattern

log = logging.getLogger(__name__)


class StateStorageError(Exception):
    """state.json could not be written — almost always /data permissions."""


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

    def all(self) -> dict[str, GroupState]:
        return dict(self._state)

    def get(self, group_id: str) -> GroupState | None:
        """None when a group has never been applied to — not an error."""
        return self._state.get(group_id)

    # ---- writing -----------------------------------------------------------

    def record(
        self, group_id: str, pattern: Pattern, preset: str | None = None
    ) -> GroupState:
        """Remember the pattern just applied to a group.

        Recorded even when some strands failed: some of them did change, and the
        per-device results in the response are the truth about which.
        """
        state = GroupState(
            pattern=pattern, preset=preset, power="on", applied_at=_now()
        )
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
        state = current.model_copy(
            update={"power": "on" if on else "off", "applied_at": _now()}
        )
        self._state[group_id] = state
        self._save()
        return state

    def forget(self, group_id: str) -> None:
        if self._state.pop(group_id, None) is not None:
            self._save()

    def _save(self) -> None:
        """Write, or log and carry on — never raise into a request."""
        try:
            self._write_atomically()
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

    def _write_atomically(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            group_id: json.loads(state.model_dump_json())
            for group_id, state in sorted(self._state.items())
        }
        handle, temp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".state-", suffix=".json"
        )
        try:
            with os.fdopen(handle, "w") as file:
                json.dump(payload, file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

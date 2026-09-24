"""Everything that changes what a group is showing.

The HTTP routes and the MQTT bridge both come through here. A command from Home
Assistant has to record state and tell the bridge exactly as a click in the UI
does; two copies of these few lines is how one of them would quietly stop doing so.
"""

from __future__ import annotations

from typing import Callable

from app.models import DeviceResult, Pattern
from app.presets import PresetStore
from app.state import StateStore


class EmptyGroupError(Exception):
    """A group with no strands — there is nothing to apply to. A 503."""


class GroupActions:
    def __init__(
        self,
        manager,
        presets: PresetStore,
        group_state: StateStore,
        on_change: Callable[[str], None] | None = None,
    ):
        self.manager = manager
        self.presets = presets
        self.group_state = group_state
        self.on_change = on_change

    def _group(self, group_id: str):
        group = self.manager.group(group_id)
        if not group.devices:
            raise EmptyGroupError(f"Group {group.name!r} has no strands")
        return group

    def _changed(self, group_id: str) -> None:
        if self.on_change is not None:
            self.on_change(group_id)

    async def apply_pattern(
        self, group_id: str, pattern: Pattern, preset: str | None = None
    ) -> list[DeviceResult]:
        group = self._group(group_id)
        results = await self.manager.apply_pattern(group.id, pattern)
        self.group_state.record(group.id, pattern, preset=preset)
        self._changed(group.id)
        return results

    async def apply_preset(self, group_id: str, name: str) -> list[DeviceResult]:
        # Looked up first, so an unknown preset is a 404 even on an empty group.
        pattern = self.presets.get(name)
        return await self.apply_pattern(group_id, pattern, preset=name)

    async def set_brightness(self, group_id: str, value: int) -> list[DeviceResult]:
        group = self._group(group_id)
        results = await self.manager.set_brightness(group.id, value)
        self.group_state.set_brightness(group.id, value)
        self._changed(group.id)
        return results

    async def turn_on(self, group_id: str) -> list[DeviceResult]:
        group = self._group(group_id)
        results = await self.manager.turn_on(group.id)
        self.group_state.set_power(group.id, True)
        self._changed(group.id)
        return results

    async def turn_off(self, group_id: str) -> list[DeviceResult]:
        """Off leaves the pattern on the strand — it just stops showing it."""
        group = self._group(group_id)
        results = await self.manager.turn_off(group.id)
        self.group_state.set_power(group.id, False)
        self._changed(group.id)
        return results

"""Doors (access control)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOOR_MODE_LOCKED, DOOR_STATUS
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcItemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SpcDoor(coordinator, door_id) for door_id in coordinator.data.doors)


class SpcDoor(SpcItemEntity, LockEntity):
    """Locked = door mode "locked"; unlock = back to normal mode; open = momentary open."""

    _collection = "doors"
    _attr_translation_key = "door"

    def __init__(self, coordinator: SpcFlexcCoordinator, door_id: str) -> None:
        super().__init__(coordinator, door_id, "lock")
        door = self.item
        self._attr_translation_placeholders = {"door": door.get("zone_name") or f"{door_id}"}
        if door.get("momentary_allowed") == "1":
            self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        mode = self.item.get("mode")
        return None if mode is None else mode == DOOR_MODE_LOCKED

    @property
    def is_open(self) -> bool | None:
        dps = self.item.get("dps_input")
        return None if dps is None else dps == "1"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.item
        return {
            "door_id": self._item_id,
            "zone_id": d.get("zone_id"),
            "area_name": d.get("area_name"),
            "status": DOOR_STATUS.get(d.get("status", ""), d.get("status")),
            "mode": {"0": "normal", "1": "locked", "2": "blocked"}.get(
                d.get("mode", ""), d.get("mode")
            ),
        }

    async def async_lock(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.door_command(self._item_id, "lock"))

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.door_command(self._item_id, "set_normal_mode"))

    async def async_open(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.door_command(self._item_id, "open_momentarily"))

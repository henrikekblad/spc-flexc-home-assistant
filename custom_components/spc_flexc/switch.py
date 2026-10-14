"""Outputs (mapping gates) and zone inhibit/isolate."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ZONE_STATUS_INHIBIT, ZONE_STATUS_ISOLATE
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcItemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        SpcOutput(coordinator, mg_id) for mg_id in coordinator.data.outputs
    ]
    for zone_id, zone in coordinator.data.zones.items():
        if zone.get("inhibit_allowed") == "1":
            entities.append(SpcZoneControl(coordinator, zone_id, "inhibit"))
        if zone.get("isolate_allowed") == "1":
            entities.append(SpcZoneControl(coordinator, zone_id, "isolate"))
    async_add_entities(entities)


class SpcOutput(SpcItemEntity, SwitchEntity):
    """An output (mapping gate)."""

    _collection = "outputs"

    def __init__(self, coordinator: SpcFlexcCoordinator, mg_id: str) -> None:
        super().__init__(coordinator, mg_id, "switch")
        self._attr_name = self.item.get("mg_name") or f"Output {mg_id}"

    @property
    def is_on(self) -> bool | None:
        state = self.item.get("state")
        return None if state is None else state == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.output_command(self._item_id, "set"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.output_command(self._item_id, "reset"))


class SpcZoneControl(SpcItemEntity, SwitchEntity):
    """Inhibit (one set period) or isolate (until de-isolated) a zone."""

    _collection = "zones"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SpcFlexcCoordinator, zone_id: str, action: str) -> None:
        super().__init__(coordinator, zone_id, action)
        self._action = action
        self._status = ZONE_STATUS_INHIBIT if action == "inhibit" else ZONE_STATUS_ISOLATE
        self._attr_translation_key = f"zone_{action}"
        self._attr_translation_placeholders = {
            "zone": self.item.get("zone_name") or f"Zone {zone_id}"
        }

    @property
    def is_on(self) -> bool | None:
        status = self.item.get("status")
        return None if status is None else status == self._status

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.coordinator.client.zone_command(self._item_id, self._action))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(
            self.coordinator.client.zone_command(self._item_id, f"de{self._action}")
        )

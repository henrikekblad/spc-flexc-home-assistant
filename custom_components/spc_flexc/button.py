"""Clear all alerts."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SpcClearAlerts(entry.runtime_data)])


class SpcClearAlerts(SpcEntity, ButtonEntity):
    _attr_translation_key = "clear_alerts"

    def __init__(self, coordinator: SpcFlexcCoordinator) -> None:
        super().__init__(coordinator, "clear_alerts")

    async def async_press(self) -> None:
        await self._command(self.coordinator.client.clear_alerts())

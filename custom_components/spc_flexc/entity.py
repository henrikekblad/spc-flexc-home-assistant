"""Base entity for SPC FlexC."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import SpcError
from .const import DOMAIN, MANUFACTURER
from .coordinator import SpcFlexcCoordinator


class SpcEntity(CoordinatorEntity[SpcFlexcCoordinator]):
    """An entity on the panel's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SpcFlexcCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial}_{key}"
        panel = coordinator.data.panel if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.serial)},
            manufacturer=MANUFACTURER,
            model=panel.get("spc_type"),
            name=panel.get("installation_name") or panel.get("spc_type") or "SPC",
            serial_number=panel.get("spc_serial_no"),
            sw_version=panel.get("spc_fw_version"),
            hw_version=panel.get("spc_hw_version"),
        )

    async def _command(self, call: Awaitable[Any]) -> None:
        """Run a panel command and refresh."""
        try:
            await call
        except SpcError as err:
            raise HomeAssistantError(f"SPC panel refused the command: {err}") from err
        await self.coordinator.async_request_refresh()


class SpcItemEntity(SpcEntity):
    """An entity for one area, zone, door or output."""

    _collection: str  # attribute name on SpcData

    def __init__(self, coordinator: SpcFlexcCoordinator, item_id: str, key: str) -> None:
        super().__init__(coordinator, f"{self._collection}_{item_id}_{key}")
        self._item_id = item_id

    @property
    def item(self) -> dict[str, Any]:
        return getattr(self.coordinator.data, self._collection).get(self._item_id, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.item)

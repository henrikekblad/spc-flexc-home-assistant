"""Zones and connection state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ZONE_INPUT, ZONE_STATUS, ZONE_TYPE
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcEntity, SpcItemEntity

# Zone types that say what the zone is, whatever it is called.
_TYPE_CLASS = {
    "1": BinarySensorDeviceClass.DOOR,  # entry/exit
    "3": BinarySensorDeviceClass.SMOKE,  # fire
    "4": BinarySensorDeviceClass.DOOR,  # fire exit
    "8": BinarySensorDeviceClass.TAMPER,
    "10": BinarySensorDeviceClass.SAFETY,  # medical
    "15": BinarySensorDeviceClass.PROBLEM,  # fault
    "17": BinarySensorDeviceClass.VIBRATION,  # seismic
}

# For general alarm/technical zones the name is the only hint (English words; anything else can
# be set per entity with "Show as" in Home Assistant, see docs/zones.md).
_NAME_CLASS = (
    (("motion", "pir"), BinarySensorDeviceClass.MOTION),
    (("glass",), BinarySensorDeviceClass.VIBRATION),
    (("window",), BinarySensorDeviceClass.WINDOW),
    (("garage door",), BinarySensorDeviceClass.GARAGE_DOOR),
    (("door", "gate"), BinarySensorDeviceClass.DOOR),
    (("smoke",), BinarySensorDeviceClass.SMOKE),
    (("fire", "heat"), BinarySensorDeviceClass.HEAT),
    (("water", "leak"), BinarySensorDeviceClass.MOISTURE),
    (("tamper",), BinarySensorDeviceClass.TAMPER),
)


def zone_device_class(zone: dict[str, Any]) -> BinarySensorDeviceClass | None:
    if (cls := _TYPE_CLASS.get(zone.get("type", ""))) is not None:
        return cls
    name = (zone.get("zone_name") or "").lower()
    for words, cls in _NAME_CLASS:
        if any(w in name for w in words):
            return cls
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        SpcPanelConnected(coordinator),
    ]
    entities.extend(SpcZone(coordinator, zone_id) for zone_id in coordinator.data.zones)
    entities.extend(SpcZoneAlarm(coordinator, zone_id) for zone_id in coordinator.data.zones)
    async_add_entities(entities)


class SpcZone(SpcItemEntity, BinarySensorEntity):
    """A zone: on when its input is not closed."""

    _collection = "zones"

    def __init__(self, coordinator: SpcFlexcCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "input")
        zone = self.item
        self._attr_name = zone.get("zone_name") or f"Zone {zone_id}"
        self._attr_device_class = zone_device_class(zone)

    @property
    def is_on(self) -> bool | None:
        value = self.item.get("input")
        return None if value is None else value != "0"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.item
        return {
            "zone_id": self._item_id,
            "area_id": z.get("area_id"),
            "area_name": z.get("area_name"),
            "zone_type": ZONE_TYPE.get(z.get("type", ""), z.get("type")),
            "input": ZONE_INPUT.get(z.get("input", ""), z.get("input")),
            "status": ZONE_STATUS.get(z.get("status", ""), z.get("status")),
            "actuated": z.get("proc_state") == "1",
        }


class SpcZoneAlarm(SpcItemEntity, BinarySensorEntity):
    """On when the zone is not in a normal state (alarm, tamper, trouble, masked ...)."""

    _collection = "zones"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "zone_problem"

    def __init__(self, coordinator: SpcFlexcCoordinator, zone_id: str) -> None:
        super().__init__(coordinator, zone_id, "problem")
        self._attr_translation_placeholders = {
            "zone": self.item.get("zone_name") or f"Zone {zone_id}"
        }

    @property
    def is_on(self) -> bool | None:
        status = self.item.get("status")
        if status is None:
            return None
        # ok, inhibit, isolate and "ok but not used recently" are not problems.
        return status not in ("0", "1", "2", "6")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.item.get("status", "")
        return {"status": ZONE_STATUS.get(status, status)}


class SpcPanelConnected(SpcEntity, BinarySensorEntity):
    """Whether the panel is connected to Home Assistant (its FlexC link is up)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "panel_connected"

    def __init__(self, coordinator: SpcFlexcCoordinator) -> None:
        super().__init__(coordinator, "panel_connected")

    @property
    def available(self) -> bool:
        # The link state is known even while polling fails.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.client.connected

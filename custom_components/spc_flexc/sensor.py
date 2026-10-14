"""Panel measurements and the last event."""

from __future__ import annotations

from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOOR_STATUS
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcEntity, SpcItemEntity

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _number(value: str | None) -> float | None:
    """'12.9V' -> 12.9, '400mA' -> 400.0."""
    if not value or not (m := _NUMBER.search(value)):
        return None
    return float(m.group())


@dataclass(frozen=True, kw_only=True)
class SpcPanelSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


PANEL_SENSORS: tuple[SpcPanelSensorDescription, ...] = (
    SpcPanelSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: _number(p.get("spc_batt_volt")),
    ),
    SpcPanelSensorDescription(
        key="aux_voltage",
        translation_key="aux_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: _number(p.get("spc_aux_volt")),
    ),
    SpcPanelSensorDescription(
        key="aux_current",
        translation_key="aux_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: _number(p.get("spc_aux_curr")),
    ),
    SpcPanelSensorDescription(
        key="ac_frequency",
        translation_key="ac_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: _number(p.get("spc_ac_freq")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SpcPanelSensor(coordinator, description) for description in PANEL_SENSORS
    ]
    entities.append(SpcLastEvent(coordinator))
    entities.extend(SpcDoorStatus(coordinator, door_id) for door_id in coordinator.data.doors)
    async_add_entities(entities)


class SpcPanelSensor(SpcEntity, SensorEntity):
    entity_description: SpcPanelSensorDescription

    def __init__(
        self, coordinator: SpcFlexcCoordinator, description: SpcPanelSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data.panel)


class SpcLastEvent(SpcEntity, SensorEntity):
    """The description of the latest event pushed by the panel."""

    _attr_translation_key = "last_event"

    def __init__(self, coordinator: SpcFlexcCoordinator) -> None:
        super().__init__(coordinator, "last_event")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        event = self.coordinator.last_event
        if not event:
            return None
        return (event.get("ev_desc") or event.get("sia_code") or "")[:255] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        event = dict(self.coordinator.last_event or {})
        if ts := event.get("timestamp"):
            with contextlib.suppress(TypeError, ValueError, OverflowError):
                event["time"] = datetime.fromtimestamp(int(ts), UTC).isoformat()
        return event


class SpcDoorStatus(SpcItemEntity, SensorEntity):
    """Door status (ok, left open, forced ...)."""

    _collection = "doors"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(DOOR_STATUS.values())
    _attr_translation_key = "door_status"

    def __init__(self, coordinator: SpcFlexcCoordinator, door_id: str) -> None:
        super().__init__(coordinator, door_id, "status")
        self._attr_translation_placeholders = {"door": self.item.get("zone_name") or f"{door_id}"}

    @property
    def native_value(self) -> str | None:
        return DOOR_STATUS.get(self.item.get("status", ""))

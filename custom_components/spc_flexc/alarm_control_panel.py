"""One alarm panel per SPC area."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    AREA_MODE_FULLSET,
    AREA_MODE_PARTSET_A,
    AREA_MODE_PARTSET_B,
    AREA_MODE_UNSET,
    CONF_CODE,
    CONF_CODE_ARM_REQUIRED,
    CONF_FORCE_ARM,
    DOMAIN,
    ZONE_STATUS_ALARM,
)
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator
from .entity import SpcItemEntity

_MODE_STATE = {
    AREA_MODE_UNSET: AlarmControlPanelState.DISARMED,
    AREA_MODE_PARTSET_A: AlarmControlPanelState.ARMED_HOME,
    AREA_MODE_PARTSET_B: AlarmControlPanelState.ARMED_NIGHT,
    AREA_MODE_FULLSET: AlarmControlPanelState.ARMED_AWAY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SpcArea(coordinator, area_id) for area_id in coordinator.data.areas)


def _epoch(value: str | None) -> str | None:
    if not value or value == "0":
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class SpcArea(SpcItemEntity, AlarmControlPanelEntity):
    """An SPC area."""

    _collection = "areas"

    def __init__(self, coordinator: SpcFlexcCoordinator, area_id: str) -> None:
        super().__init__(coordinator, area_id, "alarm")
        self._attr_name = self.item.get("area_name") or f"Area {area_id}"
        options = coordinator.config_entry.options
        self._code: str | None = options.get(CONF_CODE) or None
        self._force = bool(options.get(CONF_FORCE_ARM, False))
        self._attr_code_arm_required = bool(self._code) and bool(
            options.get(CONF_CODE_ARM_REQUIRED, False)
        )
        if self._code:
            self._attr_code_format = CodeFormat.NUMBER if self._code.isdigit() else CodeFormat.TEXT

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        area = self.item
        features = AlarmControlPanelEntityFeature.ARM_AWAY
        if area.get("partseta_enable") == "1":
            features |= AlarmControlPanelEntityFeature.ARM_HOME
        if area.get("partsetb_enable") == "1":
            features |= AlarmControlPanelEntityFeature.ARM_NIGHT
        return features

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        mode = self.item.get("mode")
        state = _MODE_STATE.get(mode)
        if state is None:
            return None
        if mode != AREA_MODE_UNSET and any(
            z.get("area_id") == self._item_id and z.get("status") == ZONE_STATUS_ALARM
            for z in self.coordinator.data.zones.values()
        ):
            return AlarmControlPanelState.TRIGGERED
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        a = self.item
        attrs = {
            "area_id": self._item_id,
            "partset_a_name": a.get("partseta_name"),
            "partset_b_name": a.get("partsetb_name"),
            "last_set_time": _epoch(a.get("last_set_time")),
            "last_set_user": a.get("last_set_user_name"),
            "last_unset_time": _epoch(a.get("last_unset_time")),
            "last_unset_user": a.get("last_unset_user_name"),
            "last_alarm": _epoch(a.get("last_alarm")),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    def _check_code(self, code: str | None, arming: bool) -> None:
        if not self._code or (arming and not self._attr_code_arm_required):
            return
        if code != self._code:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_code")

    async def _arm(self, action: str, code: str | None) -> None:
        self._check_code(code, arming=True)
        if self._force:
            action = f"{action}_forced"
        await self._command(self.coordinator.client.area_command(self._item_id, action))

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._check_code(code, arming=False)
        await self._command(self.coordinator.client.area_command(self._item_id, "unset"))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._arm("set_a", code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._arm("set_b", code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._arm("set", code)

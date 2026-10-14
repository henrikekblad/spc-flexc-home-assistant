"""Diagnostics for SPC FlexC."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_CODE, CONF_KEY
from .coordinator import SpcConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_KEY,
    CONF_CODE,
    "license_key",
    "spc_serial_no",
    "serial_no",
    "panel_serial",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SpcConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "link": {
            "connected": coordinator.client.connected,
            "connected_since": coordinator.client.connected_since,
            "last_frame": coordinator.client.last_frame,
        },
        "last_event": coordinator.last_event,
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT)
        if coordinator.data
        else None,
    }

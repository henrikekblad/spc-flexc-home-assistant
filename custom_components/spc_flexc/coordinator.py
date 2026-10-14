"""State for one panel: polling plus the events the panel pushes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SpcAuthError, SpcError, SpcFlexcClient, SpcPanelNotConnected
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_REFRESH_DELAY,
    EVENT_SPC,
)

_LOGGER = logging.getLogger(__name__)

# SIA codes that tell a zone's input directly, so the entity can move before the refresh.
_SIA_ZONE_INPUT = {"ZO": "1", "ZC": "0"}


@dataclass
class SpcData:
    """Everything read from the panel, keyed by id."""

    panel: dict[str, Any] = field(default_factory=dict)
    areas: dict[str, dict[str, Any]] = field(default_factory=dict)
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    doors: dict[str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)


type SpcConfigEntry = ConfigEntry[SpcFlexcCoordinator]


class SpcFlexcCoordinator(DataUpdateCoordinator[SpcData]):
    """Polls the panel and applies pushed events."""

    config_entry: SpcConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SpcConfigEntry, client: SpcFlexcClient) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=EVENT_REFRESH_DELAY, immediate=False
            ),
        )
        self.client = client
        self.last_event: dict[str, Any] | None = None
        client.set_callbacks(self._on_event, self._on_link)

    @property
    def serial(self) -> str:
        return self.config_entry.unique_id or self.config_entry.entry_id

    async def _async_update_data(self) -> SpcData:
        try:
            # One at a time: the panel answers one command at a time.
            panel = await self.client.get_panel()
            areas = await self.client.get_areas()
            zones = await self.client.get_zones()
            doors = await self.client.get_doors()
            outputs = await self.client.get_outputs()
        except SpcAuthError as err:
            raise ConfigEntryAuthFailed(
                f"The panel refused the user: {err}. Check the username and web password."
            ) from err
        except SpcPanelNotConnected as err:
            raise UpdateFailed("The panel is not connected to Home Assistant") from err
        except SpcError as err:
            raise UpdateFailed(str(err)) from err
        return SpcData(
            panel=panel,
            areas={a["area_id"]: a for a in areas if "area_id" in a},
            zones={z["zone_id"]: z for z in zones if "zone_id" in z},
            doors={d["door_id"]: d for d in doors if "door_id" in d},
            outputs={o["mg_id"]: o for o in outputs if "mg_id" in o},
        )

    # --- pushed by the panel -------------------------------------------------------------------

    @callback
    def _on_link(self, up: bool) -> None:
        _LOGGER.debug("Panel link %s", "up" if up else "down")
        if up:
            # Events may have been missed while the link was down.
            self.hass.async_create_task(self.async_request_refresh())
        elif self.data is not None:
            self.async_set_update_error(UpdateFailed("The panel disconnected from Home Assistant"))
        self.async_update_listeners()

    @callback
    def _on_event(self, data: dict[str, Any]) -> None:
        event = data.get("event")
        if not isinstance(event, dict):
            _LOGGER.debug("Ignoring panel message %s", data)
            return
        _LOGGER.debug("Panel event: %s", event)
        self.last_event = event
        self.hass.bus.async_fire(EVENT_SPC, {"entry_id": self.config_entry.entry_id, **event})
        zone = self.data.zones.get(str(event.get("zone_id"))) if self.data else None
        if zone is not None and (value := _SIA_ZONE_INPUT.get(event.get("sia_code", ""))):
            zone["input"] = value
        if self.data is not None:
            self.async_update_listeners()
        self.hass.async_create_task(self.async_request_refresh())

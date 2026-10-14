"""SPC FlexC: Vanderbilt SPC alarm panels connected straight to Home Assistant.

The panel connects to Home Assistant over its own FlexC protocol (Home Assistant is the IP
receiver configured on the panel); no gateway hardware is involved.

The receiver for a port is kept in `hass.data` between the config flow, failed setup attempts
and reloads, so that the panel's connection is not dropped each time. It is closed when the
entry is removed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import SpcFlexcClient, create_client
from .const import DOMAIN, PLATFORMS
from .coordinator import SpcConfigEntry, SpcFlexcCoordinator

# How long setup waits for the panel before trying again later.
SETUP_WAIT = 30


def _clients(hass: HomeAssistant) -> dict[int, SpcFlexcClient]:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

        async def _stop_all(_: Event) -> None:
            for client in list(hass.data.get(DOMAIN, {}).values()):
                await client.stop()
            hass.data.pop(DOMAIN, None)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_all)
    return hass.data[DOMAIN]


async def async_get_client(hass: HomeAssistant, data: dict[str, Any]) -> SpcFlexcClient:
    """The running receiver for this configuration, started if needed. Raises OSError when
    the port cannot be opened, ValueError for an invalid key."""
    clients = _clients(hass)
    port = data[CONF_PORT]
    client = clients.get(port)
    if client is not None and not client.same_link(data):
        await client.stop()
        clients.pop(port, None)
        client = None
    if client is not None:
        client.set_credentials(data[CONF_USERNAME], data[CONF_PASSWORD])
    else:
        client = create_client(hass, data)
        await client.start()
        clients[port] = client
    return client


async def async_release_client(hass: HomeAssistant, port: int) -> None:
    if (client := _clients(hass).pop(port, None)) is not None:
        await client.stop()


async def async_setup_entry(hass: HomeAssistant, entry: SpcConfigEntry) -> bool:
    try:
        client = await async_get_client(hass, dict(entry.data))
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Cannot open port {entry.data[CONF_PORT]} for the panel: {err}"
        ) from err
    if not await client.wait_connected(SETUP_WAIT):
        raise ConfigEntryNotReady(
            f"The panel has not connected to port {entry.data[CONF_PORT]} yet"
        )
    coordinator = SpcFlexcCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpcConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.client.set_callbacks(None, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SpcConfigEntry) -> None:
    await async_release_client(hass, entry.data[CONF_PORT])

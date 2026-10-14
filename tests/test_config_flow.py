"""Config, reauth and options flow."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.spc_flexc.const import DOMAIN

from .conftest import SimPanel


async def _until_done(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Follow the waiting step until the panel has answered."""
    for _ in range(200):
        if result["type"] is not FlowResultType.SHOW_PROGRESS:
            return result
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def test_user_flow(hass: HomeAssistant, panel: SimPanel, entry_data) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], entry_data)
    result = await _until_done(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Testvägen"
    assert result["result"].unique_id == "123456789"
    await hass.async_block_till_done()
    # The receiver opened by the flow is the one the entry uses: no new connection needed.
    assert hass.states.get("binary_sensor.testvagen_panel_connected").state == "on"


async def test_form_errors(hass: HomeAssistant, entry_data) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = {**entry_data, "encryption_key": "abc", "username": "", "password": 'a"bcdef'}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"] == {
        "encryption_key": "invalid_key",
        "username": "invalid_username",
        "password": "invalid_password",
    }


async def test_panel_does_not_connect(hass: HomeAssistant, entry_data) -> None:
    with patch("custom_components.spc_flexc.config_flow.CONNECT_WAIT", 0.2):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], entry_data)
        result = await _until_done(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "panel_not_connected"}


async def test_wrong_password(hass: HomeAssistant, panel: SimPanel, entry_data) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**entry_data, "password": "wrong!"}
    )
    result = await _until_done(hass, result)
    assert result["errors"] == {"base": "invalid_auth"}

    # Fixing the password keeps the same receiver (the panel stays connected).
    result = await hass.config_entries.flow.async_configure(result["flow_id"], entry_data)
    result = await _until_done(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth(hass: HomeAssistant, setup_entry, panel: SimPanel, entry_data) -> None:
    entry = await setup_entry()
    panel.password = "changed!"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**entry_data, "password": "changed!"}
    )
    result = await _until_done(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "changed!"


async def test_options_flow(hass: HomeAssistant, setup_entry) -> None:
    entry = await setup_entry()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"code": "", "code_arm_required": False, "force_arm": True, "scan_interval": 60},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"code_arm_required": False, "force_arm": True, "scan_interval": 60}

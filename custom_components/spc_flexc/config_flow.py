"""Config flow for SPC FlexC.

The user enters the same values as on the panel (port, encryption key, account code, RCT ID) and
the panel user. The flow then opens the port and waits for the panel to connect and answer, which
checks the key, the ids and the user in one go.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import async_get_client, async_release_client
from .client import (
    SpcAuthError,
    SpcError,
    SpcFlexcClient,
    SpcPanelNotConnected,
    parse_key,
)
from .const import (
    CONF_ACCOUNT_CODE,
    CONF_CODE,
    CONF_CODE_ARM_REQUIRED,
    CONF_FORCE_ARM,
    CONF_KEY,
    CONF_RCT_ID,
    CONF_SCAN_INTERVAL,
    CONNECT_WAIT,
    DEFAULT_ACCOUNT_CODE,
    DEFAULT_PORT,
    DEFAULT_RCT_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import SpcConfigEntry

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_KEY, default=defaults.get(CONF_KEY, "")): _PASSWORD,
            vol.Required(
                CONF_ACCOUNT_CODE,
                default=defaults.get(CONF_ACCOUNT_CODE, DEFAULT_ACCOUNT_CODE),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Required(CONF_RCT_ID, default=defaults.get(CONF_RCT_ID, DEFAULT_RCT_ID)): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): _PASSWORD,
        }
    )


def _check(data: dict[str, Any]) -> dict[str, str]:
    """Form-level checks before the port is opened."""
    errors: dict[str, str] = {}
    try:
        parse_key(data[CONF_KEY])
    except ValueError:
        errors[CONF_KEY] = "invalid_key"
    # The panel decides what it accepts; a double quote would break the request itself.
    if not data[CONF_USERNAME] or '"' in data[CONF_USERNAME]:
        errors[CONF_USERNAME] = "invalid_username"
    if not data[CONF_PASSWORD] or '"' in data[CONF_PASSWORD]:
        errors[CONF_PASSWORD] = "invalid_password"
    return errors


async def _wait_for_panel(client: SpcFlexcClient) -> tuple[str | None, dict[str, Any]]:
    """Wait for the panel and read its summary: (error key or None, panel summary)."""
    if not await client.wait_connected(CONNECT_WAIT):
        return "panel_not_connected", {}
    try:
        return None, await client.get_panel()
    except SpcAuthError:
        return "invalid_auth", {}
    except SpcPanelNotConnected:
        return "panel_not_connected", {}
    except SpcError:
        _LOGGER.exception("Unexpected answer from the panel")
        return "unknown", {}


class SpcFlexcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a panel."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._panel: dict[str, Any] = {}
        self._wait_task: asyncio.Task[tuple[str | None, dict[str, Any]]] | None = None
        self._entry: SpcConfigEntry | None = None

    async def _start(self, user_input: dict[str, Any], step_id: str) -> ConfigFlowResult:
        """Check the form, open the port and go to the waiting step."""
        self._errors = _check(user_input)
        if not self._errors:
            for entry in self._async_current_entries():
                if entry is not self._entry and entry.data.get(CONF_PORT) == user_input[CONF_PORT]:
                    self._errors = {CONF_PORT: "port_used_by_entry"}
        if not self._errors:
            try:
                client = await async_get_client(self.hass, user_input)
            except OSError:
                self._errors = {CONF_PORT: "port_in_use"}
            else:
                self._data = user_input
                self._wait_task = self.hass.async_create_task(_wait_for_panel(client))
                return await self.async_step_wait()
        return self.async_show_form(
            step_id=step_id, data_schema=_schema(user_input), errors=self._errors
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._start(user_input, "user")
        return self.async_show_form(
            step_id="user", data_schema=_schema(self._data), errors=self._errors
        )

    async def async_step_wait(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        task = self._wait_task
        if task is not None and not task.done():
            return self.async_show_progress(
                step_id="wait",
                progress_action="wait_for_panel",
                progress_task=task,
                description_placeholders={
                    "port": str(self._data[CONF_PORT]),
                    "seconds": str(CONNECT_WAIT),
                },
            )
        error, self._panel = task.result() if task is not None else ("unknown", {})
        self._wait_task = None
        if error:
            self._errors = {"base": error}
            return self.async_show_progress_done(next_step_id="retry")
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_retry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        step = "reconfigure" if self._entry is not None else "user"
        return self.async_show_form(
            step_id=step, data_schema=_schema(self._data), errors=self._errors
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        serial = self._panel.get("spc_serial_no") or f"port-{self._data[CONF_PORT]}"
        await self.async_set_unique_id(serial)
        if self._entry is not None:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(self._entry, data=self._data)
        self._abort_if_unique_id_configured(updates=self._data)
        title = self._panel.get("installation_name") or self._panel.get("spc_type") or "SPC"
        return self.async_create_entry(title=title, data=self._data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reconfigure()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._entry = (
            self._get_reauth_entry() if self.source == "reauth" else self._get_reconfigure_entry()
        )
        if user_input is not None:
            if user_input[CONF_PORT] != self._entry.data[CONF_PORT]:
                await async_release_client(self.hass, self._entry.data[CONF_PORT])
            return await self._start(user_input, "reconfigure")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(self._data or self._entry.data),
            errors=self._errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SpcConfigEntry) -> SpcFlexcOptionsFlow:
        return SpcFlexcOptionsFlow()


class SpcFlexcOptionsFlow(OptionsFlowWithReload):
    """Alarm code and polling."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if not user_input.get(CONF_CODE):
                user_input.pop(CONF_CODE, None)
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CODE,
                    description={"suggested_value": options.get(CONF_CODE)},
                ): _PASSWORD,
                vol.Required(
                    CONF_CODE_ARM_REQUIRED,
                    default=options.get(CONF_CODE_ARM_REQUIRED, False),
                ): bool,
                vol.Required(CONF_FORCE_ARM, default=options.get(CONF_FORCE_ARM, False)): bool,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

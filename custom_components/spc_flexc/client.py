"""The panel as the entities see it: reads and commands over a FlexC link.

Same interface as before the integration spoke FlexC itself: `get_panel/areas/zones/doors/
outputs` return the panel's items as dicts of strings, and the `*_command` methods take the
action names (`set`, `unset`, `inhibit`, `lock`, ...). Events arrive through `on_event` as
`{"event": {...}}` dicts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import tzinfo
import logging
from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_ACCOUNT_CODE, CONF_KEY, CONF_RCT_ID
from .flexc import commands
from .flexc.commands import Command
from .flexc.session import FlexcNotConnected, FlexcServer, FlexcTimeout
from .flexc.xmlconv import PanelError, event_to_dict, parse, reply_to_dict

_LOGGER = logging.getLogger(__name__)


class SpcError(Exception):
    """Base error."""


class SpcConnectionError(SpcError):
    """No answer from the panel."""


class SpcPanelNotConnected(SpcConnectionError):
    """The panel has not connected to Home Assistant."""


class SpcAuthError(SpcError):
    """The panel refused the user (username, web password or rights)."""


class SpcApiError(SpcError):
    """The panel answered a command with an error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _as_list(value: Any) -> list[dict[str, Any]]:
    """One item comes as a dict, several as a list."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def parse_key(text: str) -> bytes:
    """The link key from 64 hex characters (spaces and colons are ignored)."""
    clean = "".join(c for c in text if c not in " :-\t\n")
    if len(clean) != 64:
        raise ValueError("the key must be 64 hexadecimal characters")
    return bytes.fromhex(clean)


class SpcFlexcClient:
    """One panel, connected to our FlexC receiver."""

    def __init__(
        self,
        *,
        port: int,
        key: bytes,
        account_code: int,
        gateway_id: int,
        username: str,
        password: str,
        tz: tzinfo,
        host: str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._link_settings = (port, key, account_code, gateway_id)
        self._tz = tz
        self._on_event: Callable[[dict[str, Any]], None] | None = None
        self._on_link: Callable[[bool], None] | None = None
        self._linked = asyncio.Event()
        self._server = FlexcServer(
            key=key,
            account_code=account_code,
            gateway_id=gateway_id,
            port=port,
            host=host,
            on_event=self._event,
            on_link=self._link,
        )

    # --- lifecycle -----------------------------------------------------------------------------

    def same_link(self, data: dict[str, Any]) -> bool:
        """Whether this receiver runs with the link settings in `data` (port, key, account code,
        RCT id); the panel user can change without dropping the link."""
        try:
            return self._link_settings == _settings(data)[:4]
        except (KeyError, ValueError):
            return False

    def set_credentials(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    async def start(self) -> None:
        """Open the port. Raises OSError when it is in use."""
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()

    def set_callbacks(
        self,
        on_event: Callable[[dict[str, Any]], None] | None,
        on_link: Callable[[bool], None] | None,
    ) -> None:
        self._on_event = on_event
        self._on_link = on_link

    @property
    def connected(self) -> bool:
        return self._server.connected

    @property
    def connected_since(self) -> float | None:
        return self._server.connected_since

    @property
    def last_frame(self) -> float | None:
        return self._server.last_frame

    @property
    def port(self) -> int:
        return self._server.port

    async def wait_connected(self, timeout: float) -> bool:
        """Wait for the panel to connect; True when it has."""
        if self.connected:
            return True
        try:
            await asyncio.wait_for(self._linked.wait(), timeout)
        except TimeoutError:
            return False
        return self.connected

    @property
    def connect_details(self) -> dict[str, str]:
        """The panel's Connect request (serial number, product, firmware), if seen."""
        root = parse(self._server.connect_info) if self._server.connect_info else None
        if root is None or not root.children:
            return {}
        return {k.lower(): v for k, v in root.children[0].attrs}

    def _link(self, up: bool) -> None:
        if up:
            self._linked.set()
        else:
            self._linked.clear()
        if self._on_link is not None:
            self._on_link(up)

    def _event(self, xml: bytes) -> None:
        if self._on_event is None:
            return
        data = event_to_dict(xml, self._tz)
        if data:
            self._on_event(data)

    # --- requests ------------------------------------------------------------------------------

    async def _run(self, cmd: Command) -> dict[str, Any]:
        payload = commands.request_payload(cmd, self._username, self._password)
        try:
            reply = await self._server.transact(payload)
        except FlexcNotConnected as err:
            raise SpcPanelNotConnected("the panel is not connected") from err
        except FlexcTimeout as err:
            raise SpcConnectionError("the panel did not answer") from err
        try:
            return reply_to_dict(commands.reply_xml(reply), cmd.name, cmd.reply_element, self._tz)
        except PanelError as err:
            if err.is_auth_error:
                raise SpcAuthError(str(err)) from err
            raise SpcApiError(str(err.code), err.message) from err

    async def get_panel(self) -> dict[str, Any]:
        data = await self._run(commands.PANEL_SUMMARY)
        panel = data.get("panel_summary")
        return panel if isinstance(panel, dict) else {}

    async def get_areas(self) -> list[dict[str, Any]]:
        return _as_list((await self._run(commands.AREA_STATUS)).get("area_status"))

    async def get_zones(self) -> list[dict[str, Any]]:
        return _as_list((await self._run(commands.ZONE_STATUS)).get("zone_status"))

    async def get_doors(self) -> list[dict[str, Any]]:
        try:
            return _as_list((await self._run(commands.DOOR_STATUS)).get("door_status"))
        except SpcApiError:
            return []  # panels without door control answer with an error

    async def get_outputs(self) -> list[dict[str, Any]]:
        return _as_list((await self._run(commands.OUTPUT_STATUS)).get("mg_status"))

    async def area_command(self, area_id: str, action: str) -> None:
        """action: unset, set_a, set_b, set, set_delayed, and the *_forced variants."""
        await self._run(commands.area_command(area_id, action))

    async def zone_command(self, zone_id: str, action: str) -> None:
        """action: inhibit, deinhibit, isolate, deisolate."""
        await self._run(commands.zone_command(zone_id, action))

    async def door_command(self, door_id: str, action: str) -> None:
        """action: open_momentarily, open_permanently, set_normal_mode, lock."""
        await self._run(commands.door_command(door_id, action))

    async def output_command(self, output_id: str, action: str) -> None:
        """action: set, reset."""
        await self._run(commands.output_command(output_id, action))

    async def clear_alerts(self) -> None:
        await self._run(commands.CLEAR_ALERTS)


def _settings(data: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(data[CONF_PORT]),
        parse_key(data[CONF_KEY]),
        int(data[CONF_ACCOUNT_CODE]),
        int(data[CONF_RCT_ID]),
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )


def create_client(hass: HomeAssistant, data: dict[str, Any]) -> SpcFlexcClient:
    """A receiver for the settings in `data` (entry data); not started."""
    port, key, account_code, gateway_id, username, password = _settings(data)
    return SpcFlexcClient(
        port=port,
        key=key,
        account_code=account_code,
        gateway_id=gateway_id,
        username=username,
        password=password,
        tz=dt_util.get_default_time_zone(),
    )

"""Fixtures: a simulated panel that connects to the integration's FlexC receiver over the
loopback interface and answers like an SPC5000 (same XML shapes as a real panel)."""

from __future__ import annotations

import asyncio
import contextlib
import copy
from dataclasses import replace
import re
import socket
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spc_flexc.const import DOMAIN
from custom_components.spc_flexc.flexc import frame as fr
from custom_components.spc_flexc.flexc.frame import Frame, Header, MsgType

pytest_plugins = "pytest_homeassistant_custom_component"

KEY_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
KEY = bytes.fromhex(KEY_HEX)
ACCOUNT, RCT_ID = 999, 1
USER, PASSWORD = "homeassistant", "secret!"

PANEL = {
    "spc_type": "SPC5000",
    "spc_serial_no": "123456789",
    "spc_fw_version": "3.13.7 - R.41043",
    "spc_hw_version": "1.4",
    "installation_name": "Testvägen",
    "spc_batt_volt": "12.9V",
    "spc_aux_volt": "13.5V",
    "spc_aux_curr": "420mA",
    "spc_ac_freq": "50Hz",
    "license_key": "SECRET",
}
AREAS = [
    {
        "area_id": "1",
        "area_name": "Hem",
        "mode": "0",
        "partseta_enable": "1",
        "partsetb_enable": "0",
        "partseta_name": "Skalskydd",
        # Panel time HHMMSSDDMMYYYY (local).
        "last_set_time": "07103012102026",
        "last_set_user_name": "alma",
    }
]
ZONES = [
    {
        "zone_id": "1",
        "type": "3",
        "zone_name": "Rök hall",
        "area_id": "1",
        "area_name": "Hem",
        "input": "0",
        "status": "0",
        "proc_state": "0",
        "inhibit_allowed": "1",
        "isolate_allowed": "1",
    },
    {
        "zone_id": "11",
        "type": "0",
        "zone_name": "Rörelse v-rum",
        "area_id": "1",
        "area_name": "Hem",
        "input": "1",
        "status": "0",
        "proc_state": "1",
        "inhibit_allowed": "1",
        "isolate_allowed": "1",
    },
    {
        "zone_id": "17",
        "type": "1",
        "zone_name": "Ytterdörr",
        "area_id": "1",
        "area_name": "Hem",
        "input": "0",
        "status": "0",
        "proc_state": "0",
        "inhibit_allowed": "1",
        "isolate_allowed": "1",
    },
]
DOORS = [
    {
        "door_id": "1",
        "dps_input": "0",
        "status": "0",
        "mode": "0",
        "zone_id": "17",
        "zone_name": "Ytterdörr",
        "area_id": "1",
        "area_name": "Hem",
        "momentary_allowed": "1",
    }
]
OUTPUTS = [{"mg_id": "1", "mg_name": "Ringklocka", "state": "0"}]

_LISTS = {
    "GET_AREA_STATUS": ("AREA_STATUS", "areas"),
    "GET_ZONE_STATUS": ("ZONE_STATUS", "zones"),
    "GET_DOOR_STATUS": ("DOOR_STATUS", "doors"),
    "GET_MG_STATUS": ("MG_STATUS", "outputs"),
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _attrs(item: dict[str, str]) -> str:
    return "".join(f" {k}={quoteattr(v)}" for k, v in item.items())


class SimPanel:
    """The panel: connects to Home Assistant and answers FLEXML commands."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.panel = copy.deepcopy(PANEL)
        self.areas = copy.deepcopy(AREAS)
        self.zones = copy.deepcopy(ZONES)
        self.doors = copy.deepcopy(DOORS)
        self.outputs = copy.deepcopy(OUTPUTS)
        self.commands: list[tuple[str, dict[str, str]]] = []
        self.logins: list[tuple[str, str]] = []
        self.password = PASSWORD
        self.silent = False
        self.seq = 1000
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self.connected = asyncio.Event()

    # --- connection ----------------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._writer:
            self._writer.close()
        self.connected.clear()

    async def drop(self) -> None:
        """Close the link (like a cable pulled); `start` reconnects."""
        await self.stop()

    def _header(self, msg_type: int, **kw: Any) -> Header:
        self.seq += 1
        return Header(
            msg_type=msg_type, seq=self.seq, account_code=ACCOUNT, gateway_id=RCT_ID, **kw
        )

    async def _send(self, frame: Frame) -> None:
        assert self._writer is not None
        self._writer.write(fr.encode(frame, KEY))
        await self._writer.drain()

    async def _run(self) -> None:
        while True:
            try:
                reader, self._writer = await asyncio.open_connection("127.0.0.1", self.port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        await self._send(
            Frame(
                self._header(MsgType.CONNECT, conn_id=1000062661),
                b'<CONNECTION_REQUEST ATS_ID="2" SERIAL_NUMBER="123456789"/>\x00',
            )
        )
        while True:
            head = await reader.readexactly(64)
            total = fr.wire_len(head)
            rest = await reader.readexactly(total - 64) if total > 64 else b""
            frame = fr.decode(head + rest, KEY)
            if frame.msg_type == MsgType.CONNECT_ACK:
                # Like the real panel: poll right after the Connect acknowledgement.
                await self._send(Frame(self._header(MsgType.POLL, field_28=fr.FIELD_28_DATA)))
            elif frame.msg_type == MsgType.POLL_ACK:
                self.connected.set()
            elif frame.msg_type == MsgType.DATA:
                await self._answer(frame)

    async def push_event(self, **attrs: str) -> None:
        await self._send(
            Frame(self._header(MsgType.EVENT), f"<EVENT{_attrs(attrs)}/>\x00".encode())
        )

    # --- commands ------------------------------------------------------------------------------

    async def _answer(self, req: Frame) -> None:
        await self._send(Frame(replace(req.header, msg_type=MsgType.DATA_ACK)))
        if self.silent:
            return
        body = req.payload.split(b"\x00")[1].decode()
        login = re.search(r'PANEL_USERNAME="([^"]*)" PANEL_PASSWORD="([^"]*)"', body)
        assert login is not None
        self.logins.append((login.group(1), login.group(2)))
        m = re.search(r"<CMD_([A-Z_]+)([^/]*)/>", body)
        assert m is not None
        cmd = m.group(1)
        args = dict(re.findall(r'([A-Z_]+)="([^"]*)"', m.group(2)))
        if login.groups() != (USER, self.password):
            xml = (
                f'<FLEXML_REPLY VER="1.0"><REPLY_{cmd} RESULT="54" CMD_RESULT="ERROR"/>'
                "</FLEXML_REPLY>"
            )
        elif cmd == "GET_PANEL_SUMMARY":
            xml = (
                f'<FLEXML_REPLY VER="1.0"><REPLY_{cmd} CMD_RESULT="OK">'
                f"<PANEL_SUMMARY{_attrs(self.panel)}/></REPLY_{cmd}></FLEXML_REPLY>"
            )
        elif cmd in _LISTS:
            element, attr = _LISTS[cmd]
            items = "".join(f"<{element}{_attrs(i)}/>" for i in getattr(self, attr))
            xml = (
                f'<FLEXML_REPLY VER="1.0"><REPLY_{cmd} CMD_RESULT="OK">{items}</REPLY_{cmd}>'
                "</FLEXML_REPLY>"
            )
        else:
            self.commands.append((cmd, args))
            xml = f'<FLEXML_REPLY VER="1.0"><REPLY_{cmd} CMD_RESULT="OK"/></FLEXML_REPLY>'
        reply = f'<FLEXML XML_LEN="{len(xml)}"/>'.encode() + b"\x00" + xml.encode() + b"\x00"
        await self._send(
            Frame(replace(req.header, msg_type=MsgType.DATA, seq=req.header.seq + 1), reply)
        )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def port(socket_enabled) -> int:  # real sockets, on the loopback interface only
    return free_port()


@pytest.fixture
def entry_data(port: int) -> dict[str, Any]:
    return {
        "port": port,
        "encryption_key": KEY_HEX,
        "account_code": ACCOUNT,
        "rct_id": RCT_ID,
        "username": USER,
        "password": PASSWORD,
    }


@pytest.fixture
async def panel(port: int):
    sim = SimPanel(port)
    sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def setup_entry(hass, panel, entry_data):
    async def _setup(options: dict[str, Any] | None = None) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN, data=entry_data, options=options or {}, unique_id="123456789"
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup

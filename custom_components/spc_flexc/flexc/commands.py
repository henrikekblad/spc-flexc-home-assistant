"""FLEXML commands.

Every request is one Data frame whose payload is two NUL-terminated strings:

    <FLEXML XML_COMPRESS="0" XML_CLEN="n" XML_LEN="n" BIN_LEN="0" XML_CRC="crc" BIN_CRC="0" />\\0
    <FLEXML_CMD VER="1.0" PANEL_USERNAME="..." PANEL_PASSWORD="..."><CMD_x .../></FLEXML_CMD>\\0

where n is the length of the second string and crc its CRC-32. The panel checks the user on
every command; the link itself is protected by the AES key.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import quoteattr
import zlib


@dataclass(frozen=True)
class Command:
    """One panel command: `CMD_<name>` with attributes, and the element its reply lists."""

    name: str
    attrs: tuple[tuple[str, str], ...] = ()
    reply_element: str = ""

    def xml(self) -> str:
        args = "".join(f" {k}={quoteattr(v)}" for k, v in self.attrs)
        return f"<CMD_{self.name}{args} />"


def request_payload(cmd: Command, username: str, password: str) -> bytes:
    """The Data-frame payload for `cmd`, logged in as `username`."""
    # Username and password go in as written, like the original gateway (the panel accepted
    # that); a double quote would end the attribute, so it is refused.
    if '"' in username or '"' in password:
        raise ValueError("the panel username and password cannot contain a double quote")
    body = (
        f'<FLEXML_CMD VER="1.0" PANEL_USERNAME="{username}" '
        f'PANEL_PASSWORD="{password}">{cmd.xml()}</FLEXML_CMD>'
    ).encode()
    head = (
        f'<FLEXML XML_COMPRESS="0" XML_CLEN="{len(body)}" XML_LEN="{len(body)}" '
        f'BIN_LEN="0" XML_CRC="{zlib.crc32(body)}" BIN_CRC="0" />'
    ).encode()
    return head + b"\x00" + body + b"\x00"


def reply_xml(payload: bytes) -> bytes:
    """The reply XML: the second NUL-terminated string of a Data-frame payload."""
    if not payload or payload[0] == 0:
        return b""
    first = payload.find(b"\x00")
    if first < 0:
        return b""
    rest = payload[first + 1 :]
    end = rest.find(b"\x00")
    return rest if end < 0 else rest[:end]


# --- reads ---------------------------------------------------------------------------------

PANEL_SUMMARY = Command("GET_PANEL_SUMMARY", (), "PANEL_SUMMARY")
AREA_STATUS = Command("GET_AREA_STATUS", (("AREA_ID", "0"),), "AREA_STATUS")
ZONE_STATUS = Command("GET_ZONE_STATUS", (("ZONE_ID", "0"),), "ZONE_STATUS")
DOOR_STATUS = Command("GET_DOOR_STATUS", (("DOOR_ID", "0"),), "DOOR_STATUS")
OUTPUT_STATUS = Command("GET_MG_STATUS", (("MG_ID", "0"),), "MG_STATUS")
CLEAR_ALERTS = Command("CLEAR_ALL_ALERTS", (), "REPLY_CLEAR_ALL_ALERTS")

# --- commands ------------------------------------------------------------------------------

AREA_ACTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "unset": (("MODE", "0"),),
    "set_a": (("MODE", "1"),),
    "set_b": (("MODE", "2"),),
    "set": (("MODE", "3"),),
    "set_delayed": (("MODE", "3"), ("DELAYED", "1")),
    "set_a_forced": (("MODE", "1"), ("FORCE", "1")),
    "set_b_forced": (("MODE", "2"), ("FORCE", "1")),
    "set_forced": (("MODE", "3"), ("FORCE", "1")),
    "set_delayed_forced": (("MODE", "3"), ("DELAYED", "1"), ("FORCE", "1")),
}
ZONE_ACTIONS = {"inhibit": "0", "deinhibit": "1", "isolate": "2", "deisolate": "3"}
DOOR_ACTIONS = {
    "open_momentarily": "5",
    "open_permanently": "6",
    "set_normal_mode": "7",
    "lock": "8",
}
OUTPUT_ACTIONS = {"set": "1", "reset": "0"}


def _id(value: str) -> str:
    if not value.isdigit():
        raise ValueError(f"invalid id {value!r}")
    return value


def area_command(area_id: str, action: str) -> Command:
    if action not in AREA_ACTIONS:
        raise ValueError(f"unknown area action {action!r}")
    return Command(
        "AREA_CHANGE_MODE",
        (("AREA_ID", _id(area_id)), *AREA_ACTIONS[action]),
        "REPLY_AREA_CHANGE_MODE",
    )


def zone_command(zone_id: str, action: str) -> Command:
    return Command(
        "ZONE_CONTROL",
        (("ZONE_ID", _id(zone_id)), ("ACTION", ZONE_ACTIONS[action])),
        "REPLY_ZONE_CONTROL",
    )


def door_command(door_id: str, action: str) -> Command:
    return Command(
        "DOOR_CONTROL",
        (("DOOR_ID", _id(door_id)), ("ACTION", DOOR_ACTIONS[action])),
        "REPLY_DOOR_CONTROL",
    )


def output_command(output_id: str, action: str) -> Command:
    return Command(
        "MG_CONTROL",
        (("MG_ID", _id(output_id)), ("ACTION", OUTPUT_ACTIONS[action])),
        "REPLY_MG_CONTROL",
    )

"""FLEXML replies and events to dicts (shapes as seen from a real SPC5300)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.spc_flexc.flexc import commands
from custom_components.spc_flexc.flexc.xmlconv import (
    PanelError,
    event_to_dict,
    reply_to_dict,
    spc_time_to_epoch,
)

TZ = ZoneInfo("Europe/Stockholm")


def test_list_and_single_item() -> None:
    xml = (
        b'<FLEXML_REPLY VER="1.0"><REPLY_GET_ZONE_STATUS CMD_RESULT="OK">'
        b'<ZONE_STATUS zone_id="1" zone_name="R\xc3\xb6k hall" input="0"/>'
        b'<ZONE_STATUS zone_id="2" zone_name="Ytterd\xc3\xb6rr" input="1"/>'
        b"</REPLY_GET_ZONE_STATUS></FLEXML_REPLY>"
    )
    out = reply_to_dict(xml, "GET_ZONE_STATUS", "ZONE_STATUS", TZ)
    assert out == {
        "zone_status": [
            {"zone_id": "1", "zone_name": "Rök hall", "input": "0"},
            {"zone_id": "2", "zone_name": "Ytterdörr", "input": "1"},
        ]
    }
    one = reply_to_dict(
        b'<FLEXML_REPLY><REPLY_GET_MG_STATUS CMD_RESULT="OK"><MG_STATUS MG_ID="1" mg_name="Klocka" '
        b'state="0"/></REPLY_GET_MG_STATUS></FLEXML_REPLY>',
        "GET_MG_STATUS",
        "MG_STATUS",
        TZ,
    )
    assert one == {"mg_status": {"mg_id": "1", "mg_name": "Klocka", "state": "0"}}


def test_time_attributes_become_unix_time_in_the_panels_zone() -> None:
    xml = (
        b'<FLEXML_REPLY><REPLY_GET_AREA_STATUS CMD_RESULT="OK"><AREA_STATUS area_id="1" '
        b'last_set_time="07103012102026"/></REPLY_GET_AREA_STATUS></FLEXML_REPLY>'
    )
    area = reply_to_dict(xml, "GET_AREA_STATUS", "AREA_STATUS", TZ)["area_status"]
    expected = datetime(2026, 10, 12, 5, 10, 30, tzinfo=UTC).timestamp()  # 07:10:30 CEST
    assert area == {
        "area_id": "1",
        "last_set_time": str(int(expected)),
        "last_set_time_spc": "07103012102026",
    }
    assert spc_time_to_epoch("x", TZ) == 0
    assert spc_time_to_epoch("99999999999999", TZ) == 0


@pytest.mark.parametrize(("code", "auth"), [(54, True), (25, True), (73, True), (13, False)])
def test_error_reply(code: int, auth: bool) -> None:
    xml = (
        f'<FLEXML_REPLY VER="1.0"><REPLY_GET_PANEL_SUMMARY RESULT="{code}" CMD_RESULT="ERROR"/>'
        "</FLEXML_REPLY>"
    ).encode()
    with pytest.raises(PanelError) as err:
        reply_to_dict(xml, "GET_PANEL_SUMMARY", "PANEL_SUMMARY", TZ)
    assert err.value.code == code
    assert err.value.is_auth_error is auth


def test_successful_reply_without_elements_is_empty() -> None:
    # A panel without mapping gates (or doors) answers OK with nothing in it.
    xml = b'<FLEXML_REPLY VER="1.0"><REPLY_GET_MG_STATUS CMD_RESULT="OK"/></FLEXML_REPLY>'
    assert reply_to_dict(xml, "GET_MG_STATUS", "MG_STATUS", TZ) == {}
    xml = (
        b'<FLEXML_REPLY VER="1.0"><REPLY_GET_DOOR_STATUS CMD_RESULT="OK">'
        b"</REPLY_GET_DOOR_STATUS></FLEXML_REPLY>"
    )
    assert reply_to_dict(xml, "GET_DOOR_STATUS", "DOOR_STATUS", TZ) == {}


def test_missing_element_and_garbage() -> None:
    with pytest.raises(PanelError, match="102"):
        reply_to_dict(b"<FLEXML_REPLY/>", "GET_ZONE_STATUS", "ZONE_STATUS", TZ)
    with pytest.raises(PanelError, match="102"):
        reply_to_dict(b"", "GET_ZONE_STATUS", "ZONE_STATUS", TZ)


def test_command_reply_ok() -> None:
    xml = b'<FLEXML_REPLY VER="1.0"><REPLY_AREA_CHANGE_MODE CMD_RESULT="OK"/></FLEXML_REPLY>'
    out = reply_to_dict(xml, "AREA_CHANGE_MODE", "REPLY_AREA_CHANGE_MODE", TZ)
    assert out == {"reply_area_change_mode": {"cmd_result": "OK"}}


def test_event_lenient_parsing() -> None:
    xml = (
        b'<EVENT ev_desc="Sektionsing\xc3\xa5ng \xc3\xb6ppen" sia_code="ZO" zone_id="20" '
        b"zone_name='Hall &amp; trappa' timestamp=\"14451413102026\"><![CDATA[ignored]]></EVENT>"
    )
    event = event_to_dict(xml, TZ)["event"]
    assert event["ev_desc"] == "Sektionsingång öppen"
    assert event["zone_name"] == "Hall & trappa"
    assert event["timestamp_spc"] == "14451413102026"
    assert int(event["timestamp"]) == int(
        datetime(2026, 10, 13, 12, 45, 14, tzinfo=UTC).timestamp()
    )


def test_request_payload() -> None:
    cmd = commands.area_command("1", "set_forced")
    payload = commands.request_payload(cmd, "homeassistant", "pw!")
    head, body, end = payload.split(b"\x00")
    assert end == b""
    assert body == (
        b'<FLEXML_CMD VER="1.0" PANEL_USERNAME="homeassistant" PANEL_PASSWORD="pw!">'
        b'<CMD_AREA_CHANGE_MODE AREA_ID="1" MODE="3" FORCE="1" /></FLEXML_CMD>'
    )
    assert f'XML_LEN="{len(body)}"'.encode() in head
    assert commands.reply_xml(b"<FLEXML/>\x00<R/>\x00") == b"<R/>"
    with pytest.raises(ValueError):
        commands.area_command("1; x", "set")
    with pytest.raises(ValueError):
        commands.request_payload(cmd, 'a"b', "pw")

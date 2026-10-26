"""Setup, entities, events and commands, against the simulated panel."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.spc_flexc.const import EVENT_SPC

from .conftest import SimPanel

AREA = "alarm_control_panel.testvagen_hem"
ZONE_PIR = "binary_sensor.testvagen_rorelse_v_rum"
ZONE_SMOKE = "binary_sensor.testvagen_rok_hall"
ZONE_DOOR = "binary_sensor.testvagen_ytterdorr"
CONNECTED = "binary_sensor.testvagen_panel_connected"


async def refresh(hass: HomeAssistant) -> None:
    entry = hass.config_entries.async_entries("spc_flexc")[0]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_creates_entities(hass: HomeAssistant, setup_entry, panel: SimPanel) -> None:
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.LOADED

    area = hass.states.get(AREA)
    assert area.state == "disarmed"
    assert area.attributes["last_set_user"] == "alma"

    pir = hass.states.get(ZONE_PIR)
    assert pir.state == STATE_ON
    # Named in Swedish and of type "alarm": no device class guessed (see docs/zones.md).
    assert "device_class" not in pir.attributes
    assert hass.states.get(ZONE_SMOKE).attributes["device_class"] == "smoke"
    assert hass.states.get(ZONE_DOOR).state == STATE_OFF
    assert hass.states.get(ZONE_DOOR).attributes["device_class"] == "door"

    assert hass.states.get("sensor.testvagen_battery_voltage").state == "12.9"
    assert hass.states.get("sensor.testvagen_aux_current").state == "420.0"
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get("switch.testvagen_ringklocka").state == STATE_OFF
    assert hass.states.get("lock.testvagen_ytterdorr_door").state == "unlocked"

    # Every command logged in as the configured panel user.
    assert set(panel.logins) == {("homeassistant", "secret!")}

    reg = er.async_get(hass)
    inhibit = reg.async_get_entity_id("switch", "spc_flexc", "123456789_zones_11_inhibit")
    assert inhibit is not None
    assert reg.async_get(inhibit).disabled


async def test_event_updates_zone_and_fires_bus_event(
    hass: HomeAssistant, setup_entry, panel: SimPanel
) -> None:
    await setup_entry()
    events = async_capture_events(hass, EVENT_SPC)
    await panel.push_event(
        ev_desc="Sektionsingång sluten",
        sia_code="ZC",
        zone_id="11",
        zone_name="Rörelse v-rum",
        timestamp="12000010102026",
    )
    for _ in range(50):
        await hass.async_block_till_done()
        if events:
            break
        await asyncio.sleep(0.02)

    # Applied at once from the event, before the refresh.
    assert hass.states.get(ZONE_PIR).state == STATE_OFF
    assert len(events) == 1
    assert events[0].data["sia_code"] == "ZC"
    assert events[0].data["timestamp_spc"] == "12000010102026"
    last = hass.states.get("sensor.testvagen_last_event")
    assert last.state == "Sektionsingång sluten"
    assert last.attributes["zone_id"] == "11"


async def test_link_down_and_back(hass: HomeAssistant, setup_entry, panel: SimPanel) -> None:
    await setup_entry()
    await panel.drop()
    for _ in range(50):
        await hass.async_block_till_done()
        if hass.states.get(AREA).state == STATE_UNAVAILABLE:
            break
        await asyncio.sleep(0.02)
    assert hass.states.get(AREA).state == STATE_UNAVAILABLE
    assert hass.states.get(CONNECTED).state == STATE_OFF

    panel.start()
    await asyncio.wait_for(panel.connected.wait(), 2)
    await refresh(hass)
    assert hass.states.get(AREA).state == "disarmed"
    assert hass.states.get(CONNECTED).state == STATE_ON


async def test_alarm_commands(hass: HomeAssistant, setup_entry, panel: SimPanel) -> None:
    await setup_entry()
    for service in ("alarm_arm_away", "alarm_arm_home", "alarm_disarm"):
        await hass.services.async_call(
            "alarm_control_panel", service, {"entity_id": AREA}, blocking=True
        )
    assert panel.commands == [
        ("AREA_CHANGE_MODE", {"AREA_ID": "1", "MODE": "3"}),
        ("AREA_CHANGE_MODE", {"AREA_ID": "1", "MODE": "1"}),
        ("AREA_CHANGE_MODE", {"AREA_ID": "1", "MODE": "0"}),
    ]

    panel.areas[0]["mode"] = "3"
    await refresh(hass)
    assert hass.states.get(AREA).state == "armed_away"

    panel.zones[1]["status"] = "5"
    await refresh(hass)
    assert hass.states.get(AREA).state == "triggered"


async def test_alarm_code_and_force(hass: HomeAssistant, setup_entry, panel: SimPanel) -> None:
    await setup_entry({"code": "1234", "code_arm_required": False, "force_arm": True})
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": AREA}, blocking=True
    )
    assert panel.commands[-1] == ("AREA_CHANGE_MODE", {"AREA_ID": "1", "MODE": "3", "FORCE": "1"})

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": AREA, "code": "0000"},
            blocking=True,
        )
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": AREA, "code": "1234"}, blocking=True
    )
    assert panel.commands[-1] == ("AREA_CHANGE_MODE", {"AREA_ID": "1", "MODE": "0"})


async def test_output_door_zone_and_button(
    hass: HomeAssistant, setup_entry, panel: SimPanel
) -> None:
    await setup_entry()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.testvagen_ringklocka"}, blocking=True
    )
    await hass.services.async_call(
        "lock", "open", {"entity_id": "lock.testvagen_ytterdorr_door"}, blocking=True
    )
    await hass.services.async_call(
        "lock", "lock", {"entity_id": "lock.testvagen_ytterdorr_door"}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.testvagen_clear_alerts"}, blocking=True
    )
    assert panel.commands == [
        ("MG_CONTROL", {"MG_ID": "1", "ACTION": "1"}),
        ("DOOR_CONTROL", {"DOOR_ID": "1", "ACTION": "5"}),
        ("DOOR_CONTROL", {"DOOR_ID": "1", "ACTION": "8"}),
        ("CLEAR_ALL_ALERTS", {}),
    ]


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, setup_entry, panel: SimPanel
) -> None:
    entry = await setup_entry()
    panel.password = "changed!"  # the user's web password was changed on the panel
    await refresh(hass)
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)
    assert entry.state is ConfigEntryState.LOADED


async def test_unload_keeps_the_link(hass: HomeAssistant, setup_entry, panel: SimPanel) -> None:
    entry = await setup_entry()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # A reload does not drop the panel's connection.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(CONNECTED).state == STATE_ON


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        ({"type": "0", "zone_name": "Hall PIR"}, "motion"),
        ({"type": "0", "zone_name": "Garage door"}, "garage_door"),
        ({"type": "0", "zone_name": "Back door"}, "door"),
        ({"type": "1", "zone_name": "Anything"}, "door"),  # entry/exit, by type
        ({"type": "1", "zone_name": "Hallway PIR"}, "motion"),  # the name wins over entry/exit
        ({"type": "4", "zone_name": "Fire exit"}, "door"),  # fire exit, by type (not "fire" from the name)
        ({"type": "3", "zone_name": "Kitchen"}, "smoke"),  # fire, by type
        ({"type": "0", "zone_name": "Rörelse hall"}, None),
    ],
)
def test_zone_device_class(zone: dict[str, str], expected: str | None) -> None:
    from custom_components.spc_flexc.binary_sensor import zone_device_class

    cls = zone_device_class(zone)
    assert (cls.value if cls else None) == expected

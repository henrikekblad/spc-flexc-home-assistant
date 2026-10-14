"""Constants for the SPC FlexC integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "spc_flexc"
MANUFACTURER = "Vanderbilt"

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Connection (entry data). Username/password are the panel user's (CONF_USERNAME/CONF_PASSWORD).
CONF_KEY = "encryption_key"
CONF_ACCOUNT_CODE = "account_code"
CONF_RCT_ID = "rct_id"

# Options
CONF_CODE = "code"
CONF_CODE_ARM_REQUIRED = "code_arm_required"
CONF_FORCE_ARM = "force_arm"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 52000
DEFAULT_ACCOUNT_CODE = 999
DEFAULT_RCT_ID = 1
DEFAULT_USERNAME = "homeassistant"
DEFAULT_SCAN_INTERVAL = 30  # seconds; pushed events give the fast path
MIN_SCAN_INTERVAL = 10

# How long the config flow waits for the panel to connect.
CONNECT_WAIT = 90
# Coalesce a burst of panel events into one refresh.
EVENT_REFRESH_DELAY = 1.0

# Fired on the HA bus for every event the panel pushes.
EVENT_SPC = f"{DOMAIN}_event"

# Area modes (GET /spc/area -> mode).
AREA_MODE_UNSET = "0"
AREA_MODE_PARTSET_A = "1"
AREA_MODE_PARTSET_B = "2"
AREA_MODE_FULLSET = "3"

# Zone status (GET /spc/zone -> status).
ZONE_STATUS = {
    "0": "ok",
    "1": "inhibit",
    "2": "isolate",
    "3": "soak",
    "4": "tamper",
    "5": "alarm",
    "6": "ok_not_used",
    "7": "trouble",
    "8": "masked",
    "9": "post_alarm",
}
ZONE_STATUS_INHIBIT = "1"
ZONE_STATUS_ISOLATE = "2"
ZONE_STATUS_ALARM = "5"

# Zone input (GET /spc/zone -> input). Anything but 0 is "not closed".
ZONE_INPUT = {
    "0": "closed",
    "1": "open",
    "2": "short",
    "3": "disconnected",
    "4": "pir_masked",
    "5": "dc_substitution",
    "6": "sensor_missing",
    "7": "offline",
}

# Zone type (GET /spc/zone -> type).
ZONE_TYPE = {
    "0": "alarm",
    "1": "entry_exit",
    "2": "exit_terminator",
    "3": "fire",
    "4": "fire_exit",
    "5": "line",
    "6": "panic",
    "7": "hold_up",
    "8": "tamper",
    "9": "technical",
    "10": "medical",
    "11": "keyarm",
    "12": "unused",
    "13": "shunt",
    "14": "x_shunt",
    "15": "fault",
    "16": "lock_supervision",
    "17": "seismic",
    "18": "all_okay",
}

# Door status / mode (GET /spc/door).
DOOR_STATUS = {
    "0": "ok",
    "1": "open_too_long",
    "2": "left_open",
    "3": "forced",
    "4": "tamper",
    "5": "offline",
}
DOOR_MODE_NORMAL = "0"
DOOR_MODE_LOCKED = "1"
DOOR_MODE_BLOCKED = "2"

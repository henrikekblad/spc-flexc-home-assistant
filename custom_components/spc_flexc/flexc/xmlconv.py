"""FLEXML replies and events to plain dicts.

The panel answers in XML like

    <FLEXML_REPLY><REPLY_GET_ZONE_STATUS CMD_RESULT="OK">
      <ZONE_STATUS zone_id="1" zone_name="Ytterdörr" input="0" .../>
      <ZONE_STATUS zone_id="2" .../>
    </REPLY_GET_ZONE_STATUS></FLEXML_REPLY>

and pushes events as `<EVENT ev_desc="..." sia_code="ZO" zone_id="20" .../>`.

Conversion (the same shapes the original gateway gives as JSON, which the entities expect):
element and attribute names become lower case, every value is a string, and the children of an
element are grouped by name: one child gives a dict, several give a list. Text content is
ignored. Time attributes (`HHMMSSDDMMYYYY` in the panel's local time) become Unix time, with the
raw value kept under `<name>_spc`.

The parser is deliberately lenient (the panel's XML is not always well formed): it reads
elements and attributes and skips everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, tzinfo
import re
from typing import Any

# Attributes holding a panel time.
TIME_ATTRS = frozenset(
    {
        "timestamp",
        "last_set_time",
        "last_unset_time",
        "last_alarm",
        "atp_last_up_timestamp",
        "atp_last_down_timestamp",
        "atp_last_tx_ok_timestamp",
        "installation_date",
        "spc_time",
        "time_syscfg",
        "time_users",
        "alert_time",
        "last_call",
        "vzone_time",
        "alerttime",
        "time",
    }
)

# The panel's result codes (RESULT on a reply with CMD_RESULT="ERROR").
ERRORS: dict[int, str] = {
    10: "Generic error",
    11: "Unknown error",
    12: "Missing ID",
    13: "Invalid ID",
    14: "Unknown tag",
    15: "Memory full",
    16: "Invalid data",
    17: "Missing data",
    18: "Invalid CRC",
    19: "Invalid length",
    20: "Not ready",
    21: "Invalid sequence number",
    22: "Invalid decryption",
    23: "Invalid connection details",
    24: "Invalid username",
    25: "Invalid password",
    40: "Check failed",
    50: "Active",
    51: "Inactive",
    52: "Invalid user",
    53: "Invalid number",
    54: "Authentication failed",
    55: "Engineer not authorized",
    56: "Invalid name",
    57: "Invalid profile",
    59: "Invalid PIN",
    65: "No rights",
    66: "System set",
    71: "Area",
    72: "Door",
    73: "Web password not enabled",
    74: "Null data",
    75: "Bad command",
    76: "PIN expired",
    77: "Blocked",
    78: "Not allowed in engineer mode",
    100: "XML buffer failure",
    101: "XML bad format",
    102: "XML bad data",
    103: "XML unknown tag",
    104: "XML compulsory parameter not found",
}

# Codes that mean the panel user (username/password/rights) is wrong.
AUTH_ERRORS = frozenset({24, 25, 52, 54, 55, 59, 65, 73, 76, 77})


class PanelError(Exception):
    """The panel answered a command with an error."""

    def __init__(self, code: int) -> None:
        self.code = code
        self.message = ERRORS.get(code, "Unknown error")
        super().__init__(f"{code}: {self.message}")

    @property
    def is_auth_error(self) -> bool:
        return self.code in AUTH_ERRORS


@dataclass
class Element:
    """A parsed XML element (attributes in document order, keys as written)."""

    name: str
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list[Element] = field(default_factory=list)

    def attr(self, name: str) -> str | None:
        for key, value in self.attrs:
            if key == name:
                return value
        return None


_TOKEN = re.compile(
    r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|<!.*?>|</\s*([^\s>]+)\s*>|<([^\s/>!?]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>",
    re.S,
)
_ATTR = re.compile(r"""([^\s=/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
_ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#[0-9]+|lt|gt|amp|quot|apos);")
_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def _unescape(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        ref = m.group(1)
        try:
            if ref.startswith("#x"):
                return chr(int(ref[2:], 16))
            if ref.startswith("#"):
                return chr(int(ref[1:]))
        except (ValueError, OverflowError):
            return m.group(0)
        return _NAMED[ref]

    return _ENTITY.sub(repl, value)


def parse(xml: bytes | str) -> Element | None:
    """Parse into a tree under a synthetic root; None when there is no element at all."""
    text = xml.decode("utf-8", errors="replace") if isinstance(xml, bytes) else xml
    root = Element("")
    stack = [root]
    for m in _TOKEN.finditer(text):
        close, name, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if close:
            # Pop to the matching element; an unmatched close tag is ignored.
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].name == close:
                    del stack[i:]
                    break
            continue
        if not name:
            continue  # comment, CDATA, declaration
        el = Element(name)
        for am in _ATTR.finditer(attrs or ""):
            value = am.group(2) if am.group(2) is not None else am.group(3)
            if value is None:
                value = am.group(4) or ""
            el.attrs.append((am.group(1), _unescape(value)))
        stack[-1].children.append(el)
        if not selfclose:
            stack.append(el)
    return root if root.children else None


def _lower(name: str) -> str:
    return name.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def spc_time_to_epoch(value: str, tz: tzinfo) -> int:
    """`HHMMSSDDMMYYYY` in the panel's local time to Unix time; 0 when it is not a time."""
    if len(value) < 14 or not value[:14].isdigit():
        return 0
    try:
        when = datetime(
            int(value[10:14]),
            int(value[8:10]),
            int(value[6:8]),
            int(value[0:2]),
            int(value[2:4]),
            min(int(value[4:6]), 59),
            tzinfo=tz,
        )
    except ValueError:
        return 0
    return int(when.timestamp())


def to_dict(el: Element, tz: tzinfo) -> dict[str, Any]:
    """An element's attributes and children (grouped by name) as a dict."""
    out: dict[str, Any] = {}
    for key, value in el.attrs:
        name = _lower(key)
        if name in TIME_ATTRS:
            out[name] = str(spc_time_to_epoch(value, tz))
            out[f"{name}_spc"] = value
        else:
            out[name] = value
    out.update(_group(el.children, tz))
    return out


def _group(children: list[Element], tz: tzinfo) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for child in children:
        name = _lower(child.name)
        value = to_dict(child, tz)
        if name not in grouped:
            grouped[name] = value
        elif isinstance(grouped[name], list):
            grouped[name].append(value)
        else:
            grouped[name] = [grouped[name], value]
    return grouped


def _find(el: Element, name: str) -> tuple[Element, Element] | None:
    """Depth-first search for `name`; returns (parent, element)."""
    for child in el.children:
        if child.name == name:
            return el, child
        if (found := _find(child, name)) is not None:
            return found
    return None


def reply_to_dict(xml: bytes, cmd: str, reply_element: str, tz: tzinfo) -> dict[str, Any]:
    """The reply to `CMD_<cmd>`: the elements named `reply_element` grouped as a dict, e.g.
    `{"zone_status": [...]}`. Raises PanelError for an error reply."""
    root = parse(xml)
    if root is None:
        raise PanelError(102)
    if (hit := _find(root, f"REPLY_{cmd}")) is not None:
        reply = hit[1]
        if reply.attr("CMD_RESULT") == "ERROR":
            try:
                code = int((reply.attr("RESULT") or "10").strip())
            except ValueError:
                code = 10
            if code != 0:
                raise PanelError(code)
    if (hit := _find(root, reply_element)) is None:
        raise PanelError(102)
    parent = hit[0]
    return _group([c for c in parent.children if c.name == reply_element], tz)


def event_to_dict(xml: bytes, tz: tzinfo) -> dict[str, Any] | None:
    """A pushed event: `{"event": {...}}` (or whatever the top element is called)."""
    root = parse(xml)
    if root is None:
        return None
    return _group(root.children, tz)

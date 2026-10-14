"""Frame encoding against vectors made independently (hashlib SHA-1 + `openssl enc
-aes-256-cbc -nopad`), with the test key 00 01 .. 1f."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.spc_flexc.flexc import frame as fr
from custom_components.spc_flexc.flexc.frame import FrameError, Header, MsgType

KEY = bytes(range(32))

DATA = bytes.fromhex(
    "4601000571c0ffee0000000000000000dd3052b9bda3f18e82bbac89a9bd4791"
    "727026363a967822e50312902bdce733d115ba45a436f58e9809cdc27c957585"
    "eea1c0e7d12d8a945baa2a19745e2c26db1e4d5f8a248a6b78324bdeae55afdb"
    "077d6dec31777944db6109aba828604c423c5dd9b15453b8e25a100540b6b17f"
    "8e760790db51d2663b2a7c8df77a2ff4"
)
CONNECT_ACK = bytes.fromhex(
    "4601000077a1b2c3000000000000000098a9cfc0c2c0c5997a09e3bca3dd400c"
    "3cae5c4de10adcbba370a9310ca631216a0b514904402950281befa56e967b8a"
)
POLL_ACK = bytes.fromhex(
    "4601000077a1b2c3000000000000000072c657642903137d1c31b8b34784c576"
    "ff0231128ba4af0ce340bf092126a173a1cdb2f583a0242bd44b2c70fb28eedf"
)
DATA_2BLOCK = bytes.fromhex(
    "4601000271c0ffee00000000000000006f9dc3cd8df3ee133c2339afe030c3f5"
    "435b9ea92ee70e50336afd75f51e9c03eac41c91b9d5cb292e6b2607b0f47cbd"
    "97f30d8599b6f016be32503740bcddac8e568055783f73e3541a8755c77f0fdf"
)


def _header(msg_type: int, **kw) -> Header:
    return Header(msg_type=msg_type, account_code=999, gateway_id=1, **kw)


@pytest.mark.parametrize(
    ("name", "frame", "wire"),
    [
        (
            "data",
            fr.Frame(
                _header(
                    MsgType.DATA,
                    conn_id=0x71C0FFEE,
                    seq=0x2A,
                    trans_id=5,
                    flags=b"\x00\x01",
                    field_28=fr.FIELD_28_DATA,
                ),
                b'<FLEXML_CMD VER="1.0" PANEL_USERNAME="admin" PANEL_PASSWORD="x">a</FLEXML_CMD>',
            ),
            DATA,
        ),
        (
            "connect ack",
            fr.Frame(
                _header(
                    MsgType.CONNECT_ACK, conn_id=0x77A1B2C3, seq=0x00010002, trans_id=0xDEADBEEF
                )
            ),
            CONNECT_ACK,
        ),
        ("poll ack", fr.Frame(_header(MsgType.POLL_ACK, conn_id=0x77A1B2C3, seq=0x2A)), POLL_ACK),
        (
            "data over two blocks",
            fr.Frame(
                _header(
                    MsgType.DATA,
                    conn_id=0x71C0FFEE,
                    seq=0x00C0FFEE,
                    trans_id=6,
                    field_28=fr.FIELD_28_DATA,
                ),
                b'<FLEXML XML_LEN="0" />',
            ),
            DATA_2BLOCK,
        ),
    ],
)
def test_vectors(name: str, frame: fr.Frame, wire: bytes) -> None:
    payload = frame.payload
    assert fr.encode(frame, KEY) == wire, name
    back = fr.decode(wire, KEY)
    assert back.payload == payload
    assert back.header.account_code == 999 and back.header.gateway_id == 1
    assert back.header == frame.header


def test_header_only_keeps_payload_length() -> None:
    incoming = fr.Frame(_header(MsgType.EVENT, conn_id=7, seq=3, payload_len=78))
    ack = fr.ack_for(incoming, MsgType.EVENT_ACK)
    wire = fr.encode(ack, KEY, header_only=True)
    assert len(wire) == 64
    back = fr.decode(wire, KEY)
    assert back.header.payload_len == 78 and back.payload == b""
    assert back.header.msg_type == MsgType.EVENT_ACK


def test_wrong_key_and_damage_are_refused() -> None:
    with pytest.raises(FrameError, match="SHA-1"):
        fr.decode(DATA, bytes(32))
    damaged = bytearray(DATA)
    damaged[-1] ^= 1
    with pytest.raises(FrameError):
        fr.decode(bytes(damaged), KEY)
    with pytest.raises(FrameError, match="magic"):
        fr.decode(b"G" + DATA[1:], KEY)
    with pytest.raises(FrameError, match="should be"):
        fr.decode(DATA[:-16], KEY)


def test_ack_poll_zeroes_field_28_and_echoes_seq() -> None:
    poll = fr.Frame(_header(MsgType.POLL, seq=41, field_28=b"\x05\x06\x07\x08"))
    ack = fr.ack_for(poll, MsgType.POLL_ACK, zero_field_28=True)
    assert ack.header.field_28 == b"\x00\x06\x07\x08"
    counter = fr.SeqCounter(100)
    assert counter.stamp(ack.header).seq == 41  # acks echo
    counter.after_send(ack.header)
    assert counter.value == 41
    data = replace(ack.header, msg_type=MsgType.DATA)
    assert counter.stamp(data).seq == 41

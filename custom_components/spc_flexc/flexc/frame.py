"""FlexC frames.

A frame is a 64-byte header followed by N blocks of 16 bytes of payload. Bytes 0x00-0x0F are
clear text; everything from 0x10 on is AES-256-CBC encrypted without padding, with a fixed IV and
the link key that is also entered on the panel. Bytes 0x2C-0x3F hold a SHA-1 over the whole
frame (clear text, before encryption) with that field set to zero.

Header layout (all integers big-endian):

    0x00  'F'            magic
    0x01  0x01           version
    0x02  u16            N, number of 16-byte payload blocks
    0x04  u32            connection id
    0x08  8 bytes        unused (kept as received)
    0x10  u32            sequence number           -- encrypted from here --
    0x14  u32            transaction id
    0x18  u32            account code
    0x1C  u32            gateway id
    0x20  2 bytes        flags (kept as received)
    0x22  u8             message type
    0x23  u8             reserved
    0x24  u32            payload length (without the zero padding); for a reply split over
                         several Data frames, the length of the whole reply
    0x28  4 bytes        field 0x28 (first byte 1 in requests, and in the first frame of a reply)
    0x2C  20 bytes       SHA-1
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = 0x46
VERSION = 0x01
HEADER_LEN = 0x40
BLOCK = 16
SHA1_LEN = 20
FRAME_MAX = 0x2840
MAX_BLOCKS = (FRAME_MAX - HEADER_LEN) // BLOCK  # 640
IV = b"mSCeSsxPClenFei\x00"

OFF_BLOCKS = 0x02
OFF_CONN_ID = 0x04
OFF_CLEAR = 0x08
OFF_ENCRYPTED = 0x10
OFF_SEQ = 0x10
OFF_TRANS_ID = 0x14
OFF_ACCOUNT_CODE = 0x18
OFF_GATEWAY_ID = 0x1C
OFF_FLAGS = 0x20
OFF_MSG_TYPE = 0x22
OFF_RESERVED = 0x23
OFF_PAYLOAD_LEN = 0x24
OFF_FIELD_28 = 0x28
OFF_SHA1 = 0x2C

FIELD_28_DATA = b"\x01\x00\x00\x00"


class MsgType(IntEnum):
    """Message types (byte 0x22)."""

    CONNECT = 0x00
    CONNECT_ACK = 0x01
    RECONNECT = 0x02
    RECONNECT_ACK = 0x03
    POLL = 0x20
    POLL_ACK = 0x21
    KEY_UPDATE = 0x40
    KEY_ACK = 0x41
    EVENT = 0x60
    EVENT_ACK = 0x61
    DATA = 0x80
    DATA_ACK = 0x81
    ERROR = 0xFF


def is_ack_echo(msg_type: int) -> bool:
    """Acknowledgements reuse the sequence number of the frame they answer."""
    return (msg_type & 0xBF) == 0x21 or msg_type in (0x41, 0x81)


class FrameError(Exception):
    """A frame could not be decoded or does not belong to this link."""


@dataclass
class Header:
    """The 64-byte header (the SHA-1 is filled in when encoding)."""

    msg_type: int = 0
    version: int = VERSION
    blocks: int = 0
    conn_id: int = 0
    clear: bytes = bytes(8)
    seq: int = 0
    trans_id: int = 0
    account_code: int = 0
    gateway_id: int = 0
    flags: bytes = bytes(2)
    reserved: int = 0
    payload_len: int = 0
    field_28: bytes = bytes(4)
    sha1: bytes = bytes(SHA1_LEN)

    def to_bytes(self) -> bytes:
        b = bytearray(HEADER_LEN)
        b[0] = MAGIC
        b[1] = self.version
        b[OFF_BLOCKS : OFF_BLOCKS + 2] = self.blocks.to_bytes(2, "big")
        b[OFF_CONN_ID : OFF_CONN_ID + 4] = self.conn_id.to_bytes(4, "big")
        b[OFF_CLEAR : OFF_CLEAR + 8] = self.clear
        b[OFF_SEQ : OFF_SEQ + 4] = self.seq.to_bytes(4, "big")
        b[OFF_TRANS_ID : OFF_TRANS_ID + 4] = self.trans_id.to_bytes(4, "big")
        b[OFF_ACCOUNT_CODE : OFF_ACCOUNT_CODE + 4] = self.account_code.to_bytes(4, "big")
        b[OFF_GATEWAY_ID : OFF_GATEWAY_ID + 4] = self.gateway_id.to_bytes(4, "big")
        b[OFF_FLAGS : OFF_FLAGS + 2] = self.flags
        b[OFF_MSG_TYPE] = self.msg_type
        b[OFF_RESERVED] = self.reserved
        b[OFF_PAYLOAD_LEN : OFF_PAYLOAD_LEN + 4] = self.payload_len.to_bytes(4, "big")
        b[OFF_FIELD_28 : OFF_FIELD_28 + 4] = self.field_28
        b[OFF_SHA1 : OFF_SHA1 + SHA1_LEN] = self.sha1
        return bytes(b)

    @classmethod
    def parse(cls, b: bytes) -> Header:
        def be32(off: int) -> int:
            return int.from_bytes(b[off : off + 4], "big")

        return cls(
            msg_type=b[OFF_MSG_TYPE],
            version=b[1],
            blocks=int.from_bytes(b[OFF_BLOCKS : OFF_BLOCKS + 2], "big"),
            conn_id=be32(OFF_CONN_ID),
            clear=bytes(b[OFF_CLEAR : OFF_CLEAR + 8]),
            seq=be32(OFF_SEQ),
            trans_id=be32(OFF_TRANS_ID),
            account_code=be32(OFF_ACCOUNT_CODE),
            gateway_id=be32(OFF_GATEWAY_ID),
            flags=bytes(b[OFF_FLAGS : OFF_FLAGS + 2]),
            reserved=b[OFF_RESERVED],
            payload_len=be32(OFF_PAYLOAD_LEN),
            field_28=bytes(b[OFF_FIELD_28 : OFF_FIELD_28 + 4]),
            sha1=bytes(b[OFF_SHA1 : OFF_SHA1 + SHA1_LEN]),
        )


@dataclass
class Frame:
    """A decoded frame."""

    header: Header
    payload: bytes = b""
    # All payload blocks, padding included (a part of a reply split over several frames fills
    # its blocks; the header's payload length is then that of the whole reply).
    body: bytes = b""

    @property
    def msg_type(self) -> int:
        return self.header.msg_type


def frame_digest(buf: bytes) -> bytes:
    """SHA-1 over the whole clear-text frame with the SHA-1 field zeroed."""
    h = hashlib.sha1()
    h.update(buf[:OFF_SHA1])
    h.update(bytes(SHA1_LEN))
    h.update(buf[OFF_SHA1 + SHA1_LEN :])
    return h.digest()


def _cipher(key: bytes) -> Cipher:
    if len(key) != 32:
        raise ValueError("the FlexC link key is 32 bytes (64 hex characters)")
    return Cipher(algorithms.AES(key), modes.CBC(IV))


def wire_len(head: bytes) -> int:
    """Total length of a frame from its first 64 (clear) bytes; checks magic and version."""
    if len(head) < HEADER_LEN:
        raise FrameError(f"frame is {len(head)} bytes, at least 64 are needed")
    if head[0] != MAGIC:
        raise FrameError(f"bad magic 0x{head[0]:02x}")
    if head[1] != VERSION:
        raise FrameError(f"bad version 0x{head[1]:02x}")
    blocks = int.from_bytes(head[OFF_BLOCKS : OFF_BLOCKS + 2], "big")
    if blocks > MAX_BLOCKS:
        raise FrameError(f"frame too large: {blocks} blocks")
    return HEADER_LEN + blocks * BLOCK


def encode(
    frame: Frame, key: bytes, *, header_only: bool = False, payload_len: int | None = None
) -> bytes:
    """Fill in N, payload length and SHA-1, then encrypt. Header-only frames keep the header's
    payload length (acknowledgements copy it from the frame they answer). `payload_len` overrides
    the length (one part of a longer message)."""
    if header_only:
        blocks, payload = 0, b""
        header = replace(frame.header, blocks=0, sha1=bytes(SHA1_LEN))
    else:
        payload = frame.payload
        blocks = -(-len(payload) // BLOCK)
        if blocks > MAX_BLOCKS:
            raise FrameError(f"payload too large: {blocks} blocks")
        header = replace(
            frame.header,
            blocks=blocks,
            payload_len=len(payload) if payload_len is None else payload_len,
            sha1=bytes(SHA1_LEN),
        )
    buf = bytearray(HEADER_LEN + blocks * BLOCK)
    buf[:HEADER_LEN] = header.to_bytes()
    buf[HEADER_LEN : HEADER_LEN + len(payload)] = payload
    digest = frame_digest(bytes(buf))
    buf[OFF_SHA1 : OFF_SHA1 + SHA1_LEN] = digest
    frame.header = replace(header, sha1=digest)
    enc = _cipher(key).encryptor()
    buf[OFF_ENCRYPTED:] = enc.update(bytes(buf[OFF_ENCRYPTED:])) + enc.finalize()
    return bytes(buf)


def decode(data: bytes, key: bytes) -> Frame:
    """Decrypt and check one complete frame."""
    total = wire_len(data)
    if len(data) != total:
        raise FrameError(f"frame should be {total} bytes, got {len(data)}")
    buf = bytearray(data)
    dec = _cipher(key).decryptor()
    buf[OFF_ENCRYPTED:] = dec.update(bytes(buf[OFF_ENCRYPTED:])) + dec.finalize()
    if frame_digest(bytes(buf)) != bytes(buf[OFF_SHA1 : OFF_SHA1 + SHA1_LEN]):
        raise FrameError("SHA-1 mismatch (wrong link key?)")
    header = Header.parse(bytes(buf[:HEADER_LEN]))
    # The payload length is not checked against N: acknowledgements copy the header of the
    # frame they answer, so an ack can carry a payload length with N = 0.
    length = min(header.payload_len, total - HEADER_LEN)
    return Frame(
        header=header,
        payload=bytes(buf[HEADER_LEN : HEADER_LEN + length]),
        body=bytes(buf[HEADER_LEN:]),
    )


def ack_for(frame: Frame, msg_type: int, *, zero_field_28: bool = False) -> Frame:
    """An acknowledgement: the incoming header with another type and no payload."""
    header = replace(frame.header, msg_type=msg_type)
    if zero_field_28:
        header = replace(header, field_28=b"\x00" + header.field_28[1:])
    return Frame(header=header)


@dataclass
class SeqCounter:
    """Sequence numbers: acknowledgements echo the frame they answer and move the counter there;
    other frames carry the counter."""

    value: int = 0

    def stamp(self, header: Header) -> Header:
        if is_ack_echo(header.msg_type):
            return header
        return replace(header, seq=self.value)

    def after_send(self, header: Header) -> None:
        if is_ack_echo(header.msg_type):
            self.value = header.seq

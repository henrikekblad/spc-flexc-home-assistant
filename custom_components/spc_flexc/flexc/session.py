"""The FlexC link: a TCP server the panel connects to.

The panel is the client. It opens a TCP connection to the receiver configured on it (this
server), sends Connect (0x00) or Reconnect (0x02), then polls (0x20) about every 8 s and pushes
events (0x60). Commands go the other way as Data frames (0x80): the panel first acknowledges our
request (0x81), then answers with its own Data frame (0x80), which we acknowledge (0x81). One
command at a time. A long answer is split over several Data frames: the first has field 0x28 set
and the length of the whole answer, the others continue it; each one is acknowledged. A command
the panel cannot take is answered with an error frame (0xFF).

Connect gets a new random connection id and transaction id from us; Reconnect keeps the panel's.
Our Data frames must carry that negotiated pair, or the panel ignores them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import replace
import logging
import secrets
import time

from . import frame as fr
from .frame import FIELD_28_DATA, Frame, FrameError, Header, MsgType, SeqCounter

_LOGGER = logging.getLogger(__name__)

# The panel polls about every 8 s; a link silent for this long is taken as dead.
IDLE_TIMEOUT = 60.0
DEFAULT_TIMEOUT = 10.0
# Acknowledgement of a reply carries the reply's payload length, at most one chunk.
REPLY_CHUNK = 0x2800
CONN_ID_RANGE = (2_000_000_000, 2_999_999_999)


class FlexcError(Exception):
    """Base error of the link."""


class FlexcNotConnected(FlexcError):
    """The panel is not connected (or has not said Connect/Reconnect yet)."""


class FlexcTimeout(FlexcError):
    """The panel did not answer a command in time."""


class FlexcPanelError(FlexcError):
    """The panel answered a command with an error frame (0xFF)."""


def _random_conn_id() -> int:
    low, high = CONN_ID_RANGE
    return low + secrets.randbelow(high - low)


class FlexcServer:
    """Accepts the panel's connection and runs commands over it."""

    def __init__(
        self,
        *,
        key: bytes,
        account_code: int,
        gateway_id: int,
        port: int,
        host: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_event: Callable[[bytes], None] | None = None,
        on_link: Callable[[bool], None] | None = None,
    ) -> None:
        if len(key) != 32:
            raise ValueError("the FlexC link key is 32 bytes (64 hex characters)")
        self._key = key
        self._account_code = account_code
        self._gateway_id = gateway_id
        self._port = port
        self._host = host
        self._timeout = timeout
        self.on_event = on_event
        self.on_link = on_link

        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._template: Header | None = None
        self._seq = SeqCounter()
        self._trans_id = 0
        self._reply: asyncio.Future[bytes] | None = None
        # The answer being put together from its Data frames, and its total length.
        self._rx = bytearray()
        self._rx_len: int | None = None
        self._send_lock = asyncio.Lock()
        self._transact_lock = asyncio.Lock()
        self._linked = False
        self.connect_info: bytes = b""
        self.last_frame: float | None = None
        self.connected_since: float | None = None

    # --- lifecycle -----------------------------------------------------------------------------

    @property
    def port(self) -> int:
        if self._server is not None and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    @property
    def connected(self) -> bool:
        """The panel is connected, has said Connect or Reconnect and polled since."""
        return self._writer is not None and self._template is not None and self._linked

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, host=self._host, port=self._port, reuse_address=True
        )
        _LOGGER.debug("FlexC receiver listening on port %s", self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        self._drop()
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._server is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), 2)
            self._server = None

    # --- connection --------------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if self._writer is not None:
            # The panel only connects again when it considers the old link dead.
            _LOGGER.debug("New panel connection from %s replaces the old one", peer)
            self._drop()
        _LOGGER.debug("Panel connected from %s", peer)
        self._writer = writer
        self._reader_task = asyncio.current_task()
        try:
            while True:
                head = await asyncio.wait_for(reader.readexactly(fr.HEADER_LEN), IDLE_TIMEOUT)
                total = fr.wire_len(head)
                rest = b""
                if total > fr.HEADER_LEN:
                    rest = await asyncio.wait_for(
                        reader.readexactly(total - fr.HEADER_LEN), self._timeout
                    )
                try:
                    frame = fr.decode(head + rest, self._key)
                except FrameError:
                    # The raw frame helps to tell a wrong key from other trouble (debug only).
                    _LOGGER.debug("Undecodable frame from %s: %s", peer, (head + rest).hex())
                    raise
                if (
                    frame.header.account_code != self._account_code
                    or frame.header.gateway_id != self._gateway_id
                ):
                    raise FrameError(
                        f"account code {frame.header.account_code} / gateway id "
                        f"{frame.header.gateway_id} do not match the configuration"
                    )
                self.last_frame = time.time()
                _LOGGER.debug(
                    "<- 0x%02x seq=%d trans=%d conn=%d len=%d f28=%s",
                    frame.msg_type,
                    frame.header.seq,
                    frame.header.trans_id,
                    frame.header.conn_id,
                    len(frame.payload),
                    frame.header.field_28.hex(),
                )
                await self._handle_frame(frame)
        except FrameError as err:
            _LOGGER.warning("FlexC link dropped: %s", err)
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError) as err:
            _LOGGER.debug("FlexC link lost: %r", err)
        except asyncio.CancelledError:
            pass
        finally:
            if self._writer is writer:
                self._drop()
            else:
                writer.close()

    def _drop(self) -> None:
        writer, self._writer = self._writer, None
        self._template = None
        self._rx_len = None
        if writer is not None:
            writer.close()
        if self._reply is not None and not self._reply.done():
            self._reply.set_exception(FlexcNotConnected("the panel disconnected"))
        self._set_linked(False)

    def _set_linked(self, linked: bool) -> None:
        if linked == self._linked:
            return
        self._linked = linked
        self.connected_since = time.time() if linked else None
        if self.on_link is not None:
            try:
                self.on_link(linked)
            except Exception:  # a callback must not kill the link
                _LOGGER.exception("FlexC link callback failed")

    async def _send(self, frame: Frame, *, header_only: bool = False) -> None:
        async with self._send_lock:
            writer = self._writer
            if writer is None:
                raise FlexcNotConnected("the panel is not connected")
            frame.header = self._seq.stamp(frame.header)
            wire = fr.encode(frame, self._key, header_only=header_only)
            self._seq.after_send(frame.header)
            _LOGGER.debug(
                "-> 0x%02x seq=%d trans=%d conn=%d len=%d f28=%s",
                frame.msg_type,
                frame.header.seq,
                frame.header.trans_id,
                frame.header.conn_id,
                frame.header.payload_len,
                frame.header.field_28.hex(),
            )
            writer.write(wire)
            await writer.drain()

    # --- incoming ----------------------------------------------------------------------------

    async def _handle_frame(self, frame: Frame) -> None:
        mt = frame.msg_type
        if mt == MsgType.CONNECT:
            conn_id = _random_conn_id()
            trans_id = secrets.randbits(32)
            self._template = self._template_from(frame.header, conn_id, trans_id)
            self._seq.value = frame.header.seq
            self._trans_id = trans_id
            self.connect_info = _first_string(frame.payload)
            ack = fr.ack_for(frame, MsgType.CONNECT_ACK)
            ack.header = replace(ack.header, conn_id=conn_id, trans_id=trans_id)
            # The link counts as up after the panel's first poll (it polls right away): a
            # command sent before that is ignored by the panel.
            self._set_linked(False)
            await self._send(ack, header_only=True)
            _LOGGER.info("FlexC connection established")
        elif mt == MsgType.RECONNECT:
            h = frame.header
            self._template = self._template_from(h, h.conn_id, h.trans_id)
            self._seq.value = h.seq
            self._trans_id = h.trans_id
            self._set_linked(False)
            await self._send(fr.ack_for(frame, MsgType.RECONNECT_ACK), header_only=True)
            _LOGGER.info("FlexC connection re-established")
        elif mt == MsgType.POLL:
            await self._send(
                fr.ack_for(frame, MsgType.POLL_ACK, zero_field_28=True), header_only=True
            )
            if self._template is not None:
                self._set_linked(True)
        elif mt == MsgType.KEY_UPDATE:
            # Key rotation has never been seen from a real panel; acknowledge only.
            await self._send(fr.ack_for(frame, MsgType.KEY_ACK), header_only=True)
        elif mt == MsgType.EVENT:
            await self._send(fr.ack_for(frame, MsgType.EVENT_ACK), header_only=True)
            xml = _first_string(frame.payload) if frame.header.blocks else b""
            if xml and self.on_event is not None:
                try:
                    self.on_event(xml)
                except Exception:
                    _LOGGER.exception("FlexC event callback failed")
        elif mt == MsgType.DATA:
            # Every Data frame from the panel is acknowledged, also one nobody waits for.
            ack = fr.ack_for(frame, MsgType.DATA_ACK, zero_field_28=True)
            ack.header = replace(ack.header, payload_len=min(frame.header.payload_len, REPLY_CHUNK))
            await self._send(ack, header_only=True)
            self._collect(frame)
        elif mt == MsgType.DATA_ACK:
            # The panel acknowledges our request before it answers; only its Data frames are
            # the answer.
            pass
        elif mt == MsgType.ERROR:
            err = FlexcPanelError(
                f"the panel answered with an error frame (field 0x28 {frame.header.field_28.hex()})"
            )
            reply = self._reply
            if reply is not None and not reply.done():
                reply.set_exception(err)
            else:
                _LOGGER.warning("FlexC: %s", err)
        else:
            _LOGGER.debug("Ignoring FlexC message type 0x%02x", mt)

    def _collect(self, frame: Frame) -> None:
        """Add a Data frame to the answer; complete the waiting command when it is whole."""
        reply = self._reply
        if reply is None or reply.done():
            _LOGGER.debug("Data frame with no command waiting (%d bytes)", len(frame.payload))
            self._rx_len = None
            return
        if frame.header.field_28[:1] == b"\x01" or self._rx_len is None:
            self._rx = bytearray()
            self._rx_len = frame.header.payload_len
        self._rx += frame.body
        if len(self._rx) >= self._rx_len:
            reply.set_result(bytes(self._rx[: self._rx_len]))
            self._rx_len = None

    @staticmethod
    def _template_from(h: Header, conn_id: int, trans_id: int) -> Header:
        return Header(
            version=h.version,
            conn_id=conn_id,
            seq=h.seq,
            trans_id=trans_id,
            account_code=h.account_code,
            gateway_id=h.gateway_id,
            flags=h.flags,
        )

    # --- commands ----------------------------------------------------------------------------

    async def transact(self, payload: bytes) -> bytes:
        """Send one Data request and return the panel's reply payload."""
        async with self._transact_lock:
            if not self.connected:
                raise FlexcNotConnected("the panel is not connected")
            self._trans_id = (self._trans_id + 1) & 0xFFFFFFFF
            header = replace(
                self._template,
                msg_type=MsgType.DATA,
                trans_id=self._trans_id,
                field_28=FIELD_28_DATA,
            )
            loop = asyncio.get_running_loop()
            self._reply = loop.create_future()
            self._rx_len = None
            try:
                await self._send(Frame(header=header, payload=payload))
                try:
                    return await asyncio.wait_for(self._reply, self._timeout)
                except TimeoutError as err:
                    # A late answer could otherwise be taken for the answer to the next command:
                    # drop the link, the panel reconnects within seconds.
                    _LOGGER.debug("No answer in time, dropping the FlexC link")
                    self._drop()
                    raise FlexcTimeout("the panel did not answer in time") from err
            finally:
                self._reply = None


def _first_string(payload: bytes) -> bytes:
    end = payload.find(b"\x00")
    return payload if end < 0 else payload[:end]

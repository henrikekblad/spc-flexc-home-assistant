"""The FlexC receiver against a simulated panel."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from custom_components.spc_flexc.flexc import frame as fr
from custom_components.spc_flexc.flexc.frame import Frame, Header, MsgType
from custom_components.spc_flexc.flexc.session import (
    CONN_ID_RANGE,
    FlexcNotConnected,
    FlexcPanelError,
    FlexcServer,
    FlexcTimeout,
)

KEY = bytes(range(32))
ACCOUNT, GATEWAY = 999, 1


class Panel:
    """The panel's side: connects, frames are encrypted with the same key."""

    def __init__(self, port: int, account: int = ACCOUNT, gateway: int = GATEWAY) -> None:
        self.port = port
        self.account = account
        self.gateway = gateway
        self.seq = 100
        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter

    async def open(self) -> None:
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)

    def header(self, msg_type: int, **kw) -> Header:
        self.seq += 1
        return Header(
            msg_type=msg_type,
            seq=self.seq,
            account_code=self.account,
            gateway_id=self.gateway,
            **kw,
        )

    async def send(self, frame: Frame, payload_len: int | None = None) -> None:
        self.writer.write(fr.encode(frame, KEY, payload_len=payload_len))
        await self.writer.drain()

    async def recv(self) -> Frame:
        head = await asyncio.wait_for(self.reader.readexactly(64), 2)
        total = fr.wire_len(head)
        rest = await self.reader.readexactly(total - 64) if total > 64 else b""
        return fr.decode(head + rest, KEY)

    async def close(self) -> None:
        self.writer.close()


@pytest.fixture
async def server(socket_enabled):  # real sockets, on the loopback interface only
    events: list[bytes] = []
    links: list[bool] = []
    srv = FlexcServer(
        key=KEY,
        account_code=ACCOUNT,
        gateway_id=GATEWAY,
        port=0,
        host="127.0.0.1",
        timeout=0.5,
        on_event=events.append,
        on_link=links.append,
    )
    await srv.start()
    srv.events, srv.links = events, links  # type: ignore[attr-defined]
    yield srv
    await srv.stop()


async def _connect(server: FlexcServer) -> tuple[Panel, Frame]:
    panel = Panel(server.port)
    await panel.open()
    await panel.send(
        Frame(
            panel.header(MsgType.CONNECT, conn_id=1000062661),
            b'<CONNECTION_REQUEST SERIAL="1"/>\x00',
        )
    )
    ack = await panel.recv()
    return panel, ack


async def _connect_and_poll(server: FlexcServer) -> Panel:
    panel, _ = await _connect(server)
    await panel.send(Frame(panel.header(MsgType.POLL)))
    assert (await panel.recv()).msg_type == MsgType.POLL_ACK
    return panel


async def test_connect_poll_and_command(server: FlexcServer) -> None:
    panel, ack = await _connect(server)
    assert ack.msg_type == MsgType.CONNECT_ACK
    low, high = CONN_ID_RANGE
    assert low <= ack.header.conn_id < high
    assert server.connect_info == b'<CONNECTION_REQUEST SERIAL="1"/>'
    # Not up until the panel has polled: a command before that is ignored by a real panel.
    assert not server.connected
    with pytest.raises(FlexcNotConnected):
        await server.transact(b"x")

    poll = Frame(panel.header(MsgType.POLL, field_28=b"\x01\x00\x00\x00"))
    await panel.send(poll)
    pack = await panel.recv()
    assert pack.msg_type == MsgType.POLL_ACK
    assert pack.header.seq == poll.header.seq and pack.header.field_28[0] == 0
    assert server.connected and server.links == [True]  # type: ignore[attr-defined]

    async def answer() -> Frame:
        req = await panel.recv()
        # The real panel acknowledges first, then answers.
        await panel.send(Frame(replace(req.header, msg_type=MsgType.DATA_ACK, seq=req.header.seq)))
        await panel.send(
            Frame(
                replace(req.header, msg_type=MsgType.DATA, seq=req.header.seq + 1),
                b'<FLEXML XML_LEN="9"/>\x00<REPLY/>\x00',
            )
        )
        return req

    task = asyncio.create_task(answer())
    reply = await server.transact(b"<FLEXML/>\x00<CMD/>\x00")
    req = await task
    # The request carries the negotiated connection id and the Data marker in field 0x28.
    assert req.msg_type == MsgType.DATA
    assert req.header.conn_id == ack.header.conn_id
    assert req.header.trans_id == (ack.header.trans_id + 1) & 0xFFFFFFFF
    assert req.header.field_28 == fr.FIELD_28_DATA
    assert req.payload == b"<FLEXML/>\x00<CMD/>\x00"
    assert reply == b'<FLEXML XML_LEN="9"/>\x00<REPLY/>\x00'
    dack = await panel.recv()
    assert dack.msg_type == MsgType.DATA_ACK and dack.header.field_28[0] == 0
    await panel.close()


async def test_event_is_acknowledged_and_delivered(server: FlexcServer) -> None:
    panel = await _connect_and_poll(server)
    event = Frame(panel.header(MsgType.EVENT), b'<EVENT sia_code="ZO" zone_id="20"/>\x00')
    await panel.send(event)
    ack = await panel.recv()
    assert ack.msg_type == MsgType.EVENT_ACK and ack.header.seq == event.header.seq
    await asyncio.sleep(0)
    assert server.events == [b'<EVENT sia_code="ZO" zone_id="20"/>']  # type: ignore[attr-defined]
    await panel.close()


async def test_reconnect_keeps_the_panels_ids(server: FlexcServer) -> None:
    panel = Panel(server.port)
    await panel.open()
    await panel.send(Frame(panel.header(MsgType.RECONNECT, conn_id=2222222222, trans_id=77)))
    ack = await panel.recv()
    assert ack.msg_type == MsgType.RECONNECT_ACK
    assert ack.header.conn_id == 2222222222 and ack.header.trans_id == 77
    await panel.send(Frame(panel.header(MsgType.POLL)))
    assert (await panel.recv()).msg_type == MsgType.POLL_ACK
    assert server.connected
    await panel.close()


async def test_reply_over_several_frames(server: FlexcServer) -> None:
    panel = await _connect_and_poll(server)
    answer = b"<FLEXML/>\x00<REPLY>" + b"z" * 50 + b"</REPLY>\x00"
    first, rest = answer[:32], answer[32:]

    async def reply() -> list[Frame]:
        req = await panel.recv()
        await panel.send(Frame(replace(req.header, msg_type=MsgType.DATA_ACK)))
        # The first part has field 0x28 set and the length of the whole answer.
        await panel.send(
            Frame(panel.header(MsgType.DATA, field_28=b"\x01\x00\x00\x00"), first),
            payload_len=len(answer),
        )
        acks = [await panel.recv()]
        await panel.send(Frame(panel.header(MsgType.DATA), rest))
        acks.append(await panel.recv())
        return acks

    task = asyncio.create_task(reply())
    assert await server.transact(b"<CMD/>") == answer
    acks = await task
    assert [a.msg_type for a in acks] == [MsgType.DATA_ACK, MsgType.DATA_ACK]
    await panel.close()


async def test_unsolicited_data_is_acknowledged(server: FlexcServer) -> None:
    panel = await _connect_and_poll(server)
    data = Frame(panel.header(MsgType.DATA), b"<LATE/>\x00")
    await panel.send(data)
    ack = await panel.recv()
    assert ack.msg_type == MsgType.DATA_ACK and ack.header.seq == data.header.seq
    assert server.connected
    await panel.close()


async def test_error_frame_fails_the_command(server: FlexcServer) -> None:
    panel = await _connect_and_poll(server)

    async def refuse() -> None:
        req = await panel.recv()
        await panel.send(Frame(replace(req.header, msg_type=MsgType.ERROR)))

    task = asyncio.create_task(refuse())
    with pytest.raises(FlexcPanelError):
        await server.transact(b"<CMD/>")
    await task
    assert server.connected
    await panel.close()


async def test_timeout_disconnect_and_wrong_ids(server: FlexcServer) -> None:
    with pytest.raises(FlexcNotConnected):
        await server.transact(b"x")
    panel = await _connect_and_poll(server)
    with pytest.raises(FlexcTimeout):
        await server.transact(b"x")
    # The link is dropped, so a late answer cannot be taken for the next command's.
    assert not server.connected and server.links[-1] is False  # type: ignore[attr-defined]
    with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
        await panel.recv()  # our request
        await panel.recv()
    await panel.close()
    for _ in range(50):
        if not server.connected:
            break
        await asyncio.sleep(0.01)
    assert not server.connected and server.links[-1] is False  # type: ignore[attr-defined]

    # A panel with another account code is dropped without an answer.
    stranger = Panel(server.port, account=123)
    await stranger.open()
    await stranger.send(Frame(stranger.header(MsgType.CONNECT)))
    with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
        await stranger.recv()
    assert not server.connected

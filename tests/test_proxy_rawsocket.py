import pytest

import asyncio

from aiorpcx.proxy_rawsocket import ProxyProtocolError, ProxyHeaderV1Data,\
    ProxyProtocolV1Processor, serve_pv1rs
from aiorpcx.rawsocket import RSClient
from aiorpcx.util import NetAddress
from aiorpcx import timeout_after

from test_session import MyServerSession


def test_ProxyHeaderV1Data_parse_ip4():
    src_ip = '1.2.3.4'
    src_pt = 123
    dst_ip = '11.22.33.44'
    dst_pt = 12345
    byte_data = f'PROXY TCP4 {src_ip} {dst_ip} {src_pt} {dst_pt}'
    proxy_data = ProxyHeaderV1Data.from_bytes(byte_data.encode('ascii'))

    assert proxy_data.version == 1
    assert proxy_data.net_proto == 'TCP4'
    assert proxy_data.source == NetAddress(src_ip, src_pt)
    assert proxy_data.dst == NetAddress(dst_ip, dst_pt)


def test_ProxyHeaderV1Data_parse_ip6():
    src_ip = 'ff:abcd::3:2:1'
    src_pt = 123
    dst_ip = '::1'
    dst_pt = 12345
    byte_data = f'PROXY TCP6 {src_ip} {dst_ip} {src_pt} {dst_pt}'
    proxy_data = ProxyHeaderV1Data.from_bytes(byte_data.encode('ascii'))

    assert proxy_data.version == 1
    assert proxy_data.net_proto == 'TCP6'
    assert proxy_data.source == NetAddress(src_ip, src_pt)
    assert proxy_data.dst == NetAddress(dst_ip, dst_pt)


def test_ProxyHeaderV1Data_parse_unknown():
    byte_data = 'PROXY UNKNOWN'
    proxy_data = ProxyHeaderV1Data.from_bytes(byte_data.encode('ascii'))

    assert proxy_data.version == 1
    assert proxy_data.net_proto == 'UNKNOWN'
    assert proxy_data.source is None
    assert proxy_data.dst is None


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_simple():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345\r\n'
    processor.received_bytes(byte_data)
    proxy_data = await processor.receive_message()
    assert proxy_data.source is not None
    assert processor.residual == b''


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_remaining():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345\r\ntest'
    processor.received_bytes(byte_data)
    proxy_data = await processor.receive_message()
    assert proxy_data.source is not None
    assert processor.residual == b'test'


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_incomplete():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345'
    processor.received_bytes(byte_data)
    async with timeout_after(0.5):
        with pytest.raises(asyncio.CancelledError):
            await processor.receive_message()


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_garbage():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY ' * 100
    processor.received_bytes(byte_data)
    with pytest.raises(ProxyProtocolError):
        await processor.receive_message()


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_chunked():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345\r\n'
    for byte in byte_data:
        processor.received_bytes(bytes([byte]))
    proxy_data = await processor.receive_message()
    assert proxy_data.source is not None
    assert processor.residual == b''


@pytest.mark.asyncio
async def test_ProxyProtocolV1Processor_remaining_chunked():
    processor = ProxyProtocolV1Processor()
    byte_data = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345\r\ntest'
    processor.received_bytes(byte_data)
    processor.received_bytes(b'moretest')
    proxy_data = await processor.receive_message()
    assert proxy_data.source is not None
    leftover = processor.residual
    while not processor.queue.empty():
        leftover += processor.queue.get_nowait()
    assert leftover == b'testmoretest'


class ProxyRSClient(RSClient):
    async def __aenter__(self):
        _transport, protocol = await self.create_connection()
        self.session = protocol.session
        msg = b'PROXY TCP4 1.2.3.4 11.22.33.44 123 12345\r\n'
        self.session.transport._asyncio_transport.write(msg)
        return self.session


class ProxyServerSession(MyServerSession):
    async def on_remote_addr(self):
        return str(self.transport._remote_address)


@pytest.fixture
def server_port(unused_tcp_port, event_loop):
    coro = serve_pv1rs(ProxyServerSession, 'localhost', unused_tcp_port, loop=event_loop)
    server = event_loop.run_until_complete(coro)
    yield unused_tcp_port
    if hasattr(asyncio, 'all_tasks'):
        tasks = asyncio.all_tasks(event_loop)
    else:
        tasks = asyncio.Task.all_tasks(loop=event_loop)
    async def close_all():
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.wait(tasks)
    event_loop.run_until_complete(close_all())


@pytest.mark.asyncio
async def test_send_request(server_port):
    async with ProxyRSClient('localhost', server_port) as session:
        assert await session.send_request('echo', [23]) == 23
    assert session.transport._closed_event.is_set()
    assert session.transport._process_messages_task.done()


@pytest.mark.asyncio
async def test_remote_address(server_port):
    async with ProxyRSClient('localhost', server_port) as session:
        assert await session.send_request('remote_addr') == '1.2.3.4:123'
    assert session.transport._closed_event.is_set()
    assert session.transport._process_messages_task.done()

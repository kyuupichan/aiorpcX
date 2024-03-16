import asyncio

import pytest

from aiorpcx import connect_ws, NetAddress, serve_ws

from test_session import MyServerSession


@pytest.fixture
def ws_server(unused_tcp_port, event_loop):
    coro = serve_ws(MyServerSession, 'localhost', unused_tcp_port)
    server = event_loop.run_until_complete(coro)
    yield f'ws://localhost:{unused_tcp_port}'
    tasks = asyncio.all_tasks(event_loop)

    async def close_all():
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.wait(tasks)
    event_loop.run_until_complete(close_all())


@pytest.mark.filterwarnings("ignore:'with .*:DeprecationWarning")
class TestWSTransport:

    @pytest.mark.asyncio
    async def test_send_request(self, ws_server):
        async with connect_ws(ws_server) as session:
            assert await session.send_request('echo', [23]) == 23

    @pytest.mark.asyncio
    async def test_basics(self, ws_server):
        async with connect_ws(ws_server) as session:
            assert session.proxy() is None
            remote_address = session.remote_address()
            assert isinstance(remote_address, NetAddress)
            assert str(remote_address.host) in ('localhost', '::1', '127.0.0.1')
            assert ws_server.endswith(str(remote_address.port))

    @pytest.mark.asyncio
    async def test_is_closing(self, ws_server):
        async with connect_ws(ws_server) as session:
            assert not session.is_closing()
            await session.close()
            assert session.is_closing()

        async with connect_ws(ws_server) as session:
            assert not session.is_closing()
            await session.abort()
            assert session.is_closing()

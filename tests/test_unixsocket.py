import sys
import asyncio
import pytest
import tempfile
from os import path
from aiorpcx import connect_us, serve_us
from test_session import MyServerSession

if sys.platform.startswith("win"):
    pytest.skip("skipping tests not compatible with Windows platform", allow_module_level=True)


@pytest.fixture
def us_server(event_loop):
    with tempfile.TemporaryDirectory() as tmp_folder:
        socket_path = path.join(tmp_folder, 'test.socket')
        coro = serve_us(MyServerSession, socket_path, loop=event_loop)
        server = event_loop.run_until_complete(coro)
        yield socket_path
        tasks = asyncio.all_tasks(event_loop)

        async def close_all():
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.wait(tasks)
        event_loop.run_until_complete(close_all())


class TestUSTransport:

    @pytest.mark.asyncio
    async def test_send_request(self, us_server):
        async with connect_us(us_server) as session:
            assert await session.send_request('echo', [23]) == 23

    @pytest.mark.asyncio
    async def test_is_closing(self, us_server):
        async with connect_us(us_server) as session:
            assert not session.is_closing()
            await session.close()
            assert session.is_closing()

        async with connect_us(us_server) as session:
            assert not session.is_closing()
            await session.abort()
            assert session.is_closing()

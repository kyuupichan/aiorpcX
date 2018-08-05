import asyncio
import time
from contextlib import suppress
from functools import partial

import pytest

from aiorpcx import *
from util import RaiseTest


def raises_method_not_found(message):
    return RaiseTest(JSONRPC.METHOD_NOT_FOUND, message, RPCError)


class MyServerSession(ServerSession):

    current_server = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.notifications = []
        MyServerSession.current_server = self

    async def handle_request(self, request):
        handler = getattr(self, f'on_{request.method}', None)
        invocation = handler_invocation(handler, request)
        return await invocation()

    async def on_unexpected_response(self):
        # Send an unexpected response
        message = self.connection._protocol.response_message(-1, -1)
        self._send_messages((message, ), framed=False)

    async def on_echo(self, value):
        return value

    async def on_notify(self, thing):
        self.notifications.append(thing)

    async def on_bug(self):
        raise ValueError

    async def on_sleepy(self):
        await asyncio.sleep(10)


class MyLogger(object):

    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, msg, **kwargs):
        self.debugs.append(msg)

    def warning(self, msg, **kwargs):
        self.warnings.append(msg)


# This runs all the tests one with plain asyncio, then again with uvloop
@pytest.fixture(scope="session", autouse=True, params=(False, True))
def use_uvloop(request):
    if request.param:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


@pytest.fixture
def server(event_loop, unused_tcp_port):
    port = unused_tcp_port
    server = Server(MyServerSession, 'localhost', port, loop=event_loop)
    event_loop.run_until_complete(server.listen())
    yield server
    event_loop.run_until_complete(server.close())


class TestServer:

    def test_constructor_loop(self, event_loop):
        loop = asyncio.get_event_loop()
        assert loop != event_loop
        s = Server(None)
        assert s.loop == loop
        s = Server(None, loop=None)
        assert s.loop == loop
        s = Server(None, loop=event_loop)
        assert s.loop == event_loop

    @pytest.mark.asyncio
    async def test_close_not_listening(self, event_loop):
        server = Server(None, loop=event_loop)
        assert server.server is None
        # Return immediately - the server isn't listening
        await server.close()

    @pytest.mark.asyncio
    async def test_close_listening(self, server):
        asyncio_server = server.server
        assert asyncio_server is not None
        assert asyncio_server.sockets
        await server.close()
        assert server.server is None
        assert not asyncio_server.sockets


class TestClientSession:

    @pytest.mark.asyncio
    async def test_proxy(self, server):
        proxy = SOCKSProxy(('localhost', 79), SOCKS5, None)
        with pytest.raises(OSError):
            async with ClientSession('localhost', server.port,
                                     proxy=proxy) as session:
                pass

    @pytest.mark.asyncio
    async def test_handlers(self, server):
        async with timeout_after(0.1):
            async with ClientSession('localhost', server.port) as client:
                with raises_method_not_found('something'):
                    await client.send_request('something')
                await client.send_notification('something')
        assert client.is_closing()

    @pytest.mark.asyncio
    async def test_send_request(self, server):
        async with ClientSession('localhost', server.port) as client:
            assert await client.send_request('echo', [23]) == 23

    @pytest.mark.asyncio
    async def test_send_request_buggy_handler(self, server):
        async with ClientSession('localhost', server.port) as client:
            with RaiseTest(JSONRPC.INTERNAL_ERROR, 'internal server error',
                           RPCError):
                await client.send_request('bug')

    @pytest.mark.asyncio
    async def test_unexpected_response(self, server, caplog):
        async with ClientSession('localhost', server.port) as client:
            # A request not a notification so we don't exit immediately
            await client.send_request('unexpected_response')
        assert any('unsent request' in record.message
                   for record in caplog.records)

    @pytest.mark.asyncio
    async def test_send_request_bad_args(self, server):
        async with ClientSession('localhost', server.port) as client:
            # ProtocolError as it's a protocol violation
            with RaiseTest(JSONRPC.INVALID_ARGS, 'list', ProtocolError):
                await client.send_request('echo', "23")

    @pytest.mark.asyncio
    async def test_send_request_timeout0(self, server):
        async with ClientSession('localhost', server.port) as client:
            with pytest.raises(TaskTimeout):
                async with timeout_after(0):
                    await client.send_request('echo', [23])

    @pytest.mark.asyncio
    async def test_send_request_timeout(self, server):
        async with ClientSession('localhost', server.port) as client:
            server_session = MyServerSession.current_server
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.1):
                    await client.send_request('sleepy')
        # Assert the server doesn't treat cancellation as an error
        await asyncio.sleep(0.001)
        assert server_session.errors == 0

    @pytest.mark.asyncio
    async def test_send_notification(self, server):
        async with ClientSession('localhost', server.port) as client:
            await client.send_notification('notify', ['test'])
        await asyncio.sleep(0.001)
        assert MyServerSession.current_server.notifications == ['test']

    @pytest.mark.asyncio
    async def test_abort(self, server):
        async with ClientSession('localhost', server.port) as client:
            client.abort()
            assert client.is_closing()

    @pytest.mark.asyncio
    async def test_verbose_logging(self, server):
        async with ClientSession('localhost', server.port) as client:
            client.logger = MyLogger()
            client.verbosity = 4
            request = await client.send_request('echo', ['wait'])
            assert len(client.logger.debugs) == 2

    @pytest.mark.asyncio
    async def test_framer_MemoryError(self, server):
        framer = NewlineFramer(5)
        async with ClientSession('localhost', server.port,
                                 framer=framer) as client:
            client.logger = MyLogger()
            msg = 'w' * 50
            raw_msg = msg.encode()
            # Even though long it will be sent in one bit
            request = client.send_request('echo', [msg])
            assert await request == msg
            assert not client.logger.warnings
            client.data_received(raw_msg)  # Unframed; no \n
            assert len(client.logger.warnings) == 1

    @pytest.mark.asyncio
    async def test_peer_address(self, server):
        async with ClientSession('localhost', server.port) as client:
            pa = client.peer_address()
            if pa[0] == '::1':
                assert client.peer_address_str() == f'[::1]:{server.port}'
                assert pa[1:] == (server.port, 0, 0)
            else:
                assert pa[0].startswith('127.')
                assert pa[1:] == (server.port, )
                assert client.peer_address_str() == f'{pa[0]}:{server.port}'
            client._address = None
            assert client.peer_address_str() == 'unknown'
            client._address = '1.2.3.4', 56
            assert client.peer_address_str() == '1.2.3.4:56'
            client._address = '::1', 56, 0, 0
            assert client.peer_address_str() == '[::1]:56'

    @pytest.mark.asyncio
    async def test_resource_release(self, server):
        loop = asyncio.get_event_loop()
        tasks = asyncio.Task.all_tasks(loop)
        try:
            client = ClientSession('localhost', 0)
            await client.connect()
        except Exception:
            pass
        assert asyncio.Task.all_tasks(loop) == tasks

        async with ClientSession('localhost', server.port) as client:
            pass

        await asyncio.sleep(0.005)  # Yield to event loop for processing
        assert asyncio.Task.all_tasks(loop) == tasks

    @pytest.mark.asyncio
    async def test_pausing(self, server):
        called = []
        limit = None

        def my_write(data):
            called.append(data)
            if len(called) == limit:
                client.pause_writing()

        async with ClientSession('localhost', server.port) as client:
            try:
                client.transport.write = my_write
            except AttributeError:    # uvloop: transport.write is read-only
                return
            client._send_messages((b'a', ), framed=False)
            assert called
            called.clear()

            limit = 2
            msgs = b'A very long and boring meessage'.split()
            framed_msgs = [client.framer.frame((msg, )) for msg in msgs]
            client.pause_writing()
            for msg in msgs:
                client._send_messages((msg, ), framed=False)
            assert not called
            client.resume_writing()
            assert called == [b''.join(framed_msgs)]
            limit = None
            # Check idempotent
            client.resume_writing()

    @pytest.mark.asyncio
    async def test_concurrency(self, server):
        async with ClientSession('localhost', server.port) as client:
            # Test high bw usage crushes concurrency to 1
            client.bw_charge = 1000 * 1000 * 1000
            prior_mc = client.concurrency.max_concurrent
            await client._update_concurrency()
            assert 1 == client.concurrency.max_concurrent < prior_mc
            # Test passage of time restores it
            client.bw_time -= 1000 * 1000 * 1000
            await client._update_concurrency()
            assert client.concurrency.max_concurrent == prior_mc

    @pytest.mark.asyncio
    async def test_close_on_many_errors(self, server):
        try:
            async with ClientSession('localhost', server.port) as client:
                server_session = MyServerSession.current_server
                for n in range(client.max_errors + 5):
                    with suppress(RPCError):
                        await client.send_request('boo')
        except CancelledError:
            pass
        assert server_session.errors == server_session.max_errors
        assert client.transport is None


@pytest.mark.asyncio
async def test_misc():
    session = ClientSession()
    await session.handle_request(Request('', []))

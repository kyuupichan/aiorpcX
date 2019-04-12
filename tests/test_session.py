import asyncio
import json
import logging
import sys
import time
from contextlib import suppress
from functools import partial

import pytest

from aiorpcx import *
from aiorpcx.session import Concurrency
from util import RaiseTest


if sys.version_info >= (3, 7):
    from asyncio import all_tasks
else:
    from asyncio import Task
    all_tasks = Task.all_tasks


def raises_method_not_found(message):
    return RaiseTest(JSONRPC.METHOD_NOT_FOUND, message, RPCError)


class MyServerSession(RPCSession):

    _current_server = None
    max_errors = 10

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.notifications = []

    @classmethod
    async def current_server(self):
        await sleep(0)
        return self._current_server

    def connection_made(self, transport):
        MyServerSession._current_server = self
        super().connection_made(transport)

    def connection_lost(self, exc):
        MyServerSession._current_server = None
        super().connection_lost(exc)

    async def handle_request(self, request):
        handler = getattr(self, f'on_{request.method}', None)
        invocation = handler_invocation(handler, request)
        return await invocation()

    async def on_send_bad_response(self, response):
        message = json.dumps(response).encode()
        await self._send_message(message)

    async def on_echo(self, value):
        return value

    async def on_notify(self, thing):
        self.notifications.append(thing)

    async def on_bug(self):
        raise ValueError

    async def on_incompatibleversion(self):
        raise FinalRPCError(1, "incompatible version")

    async def on_sleepy(self):
        await sleep(10)


def in_caplog(caplog, message):
    return any(message in record.message for record in caplog.records)


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
    event_loop.run_until_complete(sleep(0.001))


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


class TestRPCSession:

    @pytest.mark.asyncio
    async def test_proxy(self, server):
        proxy = SOCKSProxy(('localhost', 79), SOCKS5, None)
        with pytest.raises(OSError):
            async with Connector(RPCSession, 'localhost', server.port,
                                 proxy=proxy) as session:
                pass

    @pytest.mark.asyncio
    async def test_handlers(self, server):
        async with timeout_after(0.1):
            async with Connector(RPCSession, 'localhost',
                                 server.port) as client:
                with raises_method_not_found('something'):
                    await client.send_request('something')
                await client.send_notification('something')
        assert client.is_closing()

    @pytest.mark.asyncio
    async def test_send_request(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            assert await client.send_request('echo', [23]) == 23
        assert client.closed_event.is_set()

    @pytest.mark.asyncio
    async def test_send_request_buggy_handler(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            with RaiseTest(JSONRPC.INTERNAL_ERROR, 'internal server error',
                           RPCError):
                await client.send_request('bug')

    @pytest.mark.asyncio
    async def test_unexpected_response(self, server, caplog):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            # A request not a notification so we don't exit immediately
            response = {"jsonrpc": "2.0", "result": 2, "id": -1}
            with caplog.at_level(logging.DEBUG):
                await client.send_request('send_bad_response', (response, ))
        assert in_caplog(caplog, 'unsent request')

    @pytest.mark.asyncio
    async def test_unanswered_request_count(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server_session = await MyServerSession.current_server()
            # Wait for the loop to start the message processing task, sigh
            await sleep(0)
            assert client.unanswered_request_count() == 0
            assert server_session.unanswered_request_count() == 0
            async with ignore_after(0.01):
                await client.send_request('sleepy')
            assert client.unanswered_request_count() == 0
            assert server_session.unanswered_request_count() == 1

    @pytest.mark.asyncio
    async def test_send_request_bad_args(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            # ProtocolError as it's a protocol violation
            with RaiseTest(JSONRPC.INVALID_ARGS, 'list', ProtocolError):
                await client.send_request('echo', "23")

    @pytest.mark.asyncio
    async def test_send_request_timeout0(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            with pytest.raises(TaskTimeout):
                async with timeout_after(0):
                    await client.send_request('echo', [23])

    @pytest.mark.asyncio
    async def test_send_request_timeout(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server_session = await MyServerSession.current_server()
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.01):
                    await client.send_request('sleepy')
        # Assert the server doesn't treat cancellation as an error
        assert server_session.errors == 0

    @pytest.mark.asyncio
    async def test_send_ill_formed(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server_session = await MyServerSession.current_server()
            server_session.max_errors = 1
            await client._send_message(b'')
            await sleep(0.002)
            assert server_session.errors == 1
            # Check we got cut-off
            assert client.is_closing()

    @pytest.mark.asyncio
    async def test_send_notification(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server = await MyServerSession.current_server()
            await client.send_notification('notify', ['test'])
        # Ensure we've given the server loop time to process the message
        await asyncio.sleep(0.001)
        assert server.notifications == ['test']

    @pytest.mark.asyncio
    async def test_force_close(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            assert not client.closed_event.is_set()
            await client.close(force_after=0.001)
        assert client.closed_event.is_set()
        assert not client.transport

    @pytest.mark.asyncio
    async def test_verbose_logging(self, server, caplog):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            client.verbosity = 4
            with caplog.at_level(logging.DEBUG):
                await client.send_request('echo', ['wait'])
            assert in_caplog(caplog, "Sending framed message b'{")
            assert in_caplog(caplog, "Received framed message b'{")

    @pytest.mark.asyncio
    async def test_framer_MemoryError(self, server, caplog):
        factory = partial(RPCSession, framer=NewlineFramer(5))
        async with Connector(factory, 'localhost', server.port) as client:
            msg = 'w' * 50
            raw_msg = msg.encode()
            # Even though long it will be sent in one bit
            request = client.send_request('echo', [msg])
            assert await request == msg
            assert not caplog.records
            client.data_received(raw_msg)  # Unframed; no \n
            await sleep(0)
            assert len(caplog.records) == 1
            assert in_caplog(caplog, 'dropping message over 5 bytes')

    @pytest.mark.asyncio
    async def test_peer_address(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
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
        tasks = all_tasks(loop)
        try:
            client = Connector(RPCSession, 'localhost', 0)
            await client.create_connection()
        except OSError:
            pass
        assert all_tasks(loop) == tasks

        async with Connector(RPCSession, 'localhost', server.port):
            pass

        await asyncio.sleep(0.005)  # Let things be processed
        assert all_tasks(loop) == tasks

    @pytest.mark.asyncio
    async def test_pausing(self, server):
        called = []
        limit = None

        def my_write(data):
            called.append(data)
            if len(called) == limit:
                client.pause_writing()

        async with Connector(RPCSession, 'localhost', server.port) as client:
            try:
                client.transport.write = my_write
            except AttributeError:    # uvloop: transport.write is read-only
                return
            await client._send_message(b'a')
            assert called
            called.clear()

            async def monitor():
                await sleep(0.002)
                assert called == [b'A\n', b'very\n']
                client.resume_writing()

            limit = 2
            msgs = b'A very long and boring meessage'.split()
            task = await spawn(monitor)
            for msg in msgs:
                await client._send_message(msg)
            assert called == [client.framer.frame(msg) for msg in msgs]
            limit = None
            # Check idempotent
            client.resume_writing()

    @pytest.mark.asyncio
    async def test_slow_connection_aborted(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            assert client.max_send_delay >= 10
            client.max_send_delay = 0.001
            client.pause_writing()
            assert not client._can_send.is_set()
            tasks = [await spawn(client._send_message(b'a'))
                     for n in range(3)]
            await sleep(client.max_send_delay * 10)
            assert all(task.cancelled() for task in tasks)
            assert client._can_send.is_set()
            assert client.is_closing()

    @pytest.mark.asyncio
    async def test_concurrency(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            # Prevent this interfering
            client.cost_decay_per_sec = 0
            # Test usage below soft limit
            client.cost = client.cost_soft_limit - 10
            client.recalc_concurrency()
            assert client._concurrency.max_concurrent == client.initial_concurrent
            assert client._cost_fraction == 0.0
            # Test usage at soft limit doesn't affect concurrency
            client.cost = client.cost_soft_limit
            client.recalc_concurrency()
            assert client._concurrency.max_concurrent == client.initial_concurrent
            assert client._cost_fraction == 0.0
            # Test usage half-way
            client.cost = (client.cost_soft_limit + client.cost_hard_limit) // 2
            client.recalc_concurrency()
            assert 1 < client._concurrency.max_concurrent < client.initial_concurrent
            assert 0.49 < client._cost_fraction < 0.51
            # Test at hard limit
            client.cost = client.cost_hard_limit
            client.recalc_concurrency()
            assert client._cost_fraction == 1.0
            # Test above hard limit disconnects
            client.cost = client.cost_hard_limit + 1
            client.recalc_concurrency()
            with pytest.raises(ExcessiveSessionCostError):
                async with client._concurrency:
                    pass

    @pytest.mark.asyncio
    async def test_concurrency_decay(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            client.cost_decay_per_sec = 100
            client.cost = 1000
            await sleep(0.01)
            client.recalc_concurrency()
            assert 995 < client.cost < 999

    @pytest.mark.asyncio
    async def test_concurrency_hard_limit_0(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            client.cost = 1_000_000_000
            client.cost_hard_limit = 0
            client.recalc_concurrency()
            assert client._concurrency.max_concurrent == client.initial_concurrent

    @pytest.mark.asyncio
    async def test_extra_cost(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            client.extra_cost = lambda: client.cost_soft_limit + 1
            client.recalc_concurrency()
            assert 1 > client._cost_fraction > 0
            client.extra_cost = lambda: client.cost_hard_limit + 1
            client.recalc_concurrency()
            assert client._cost_fraction > 1

    @pytest.mark.asyncio
    async def test_request_over_hard_limit(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server = await MyServerSession.current_server()
            server.bump_cost(server.cost_hard_limit + 100)
            async with timeout_after(0.1):
                with pytest.raises(RPCError) as e:
                    await client.send_request('echo', [23])
            assert 'excessive resource usage' in str(e.value)

    @pytest.mark.asyncio
    async def test_request_sleep(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            server = await MyServerSession.current_server()
            server.bump_cost((server.cost_soft_limit + server.cost_hard_limit) / 2)
            server.cost_sleep = 0.1
            t1 = time.time()
            await client.send_request('echo', [23])
            t2 = time.time()
            assert t2 - t1 > server.cost_sleep / 2

    @pytest.mark.asyncio
    async def test_close_on_many_errors(self, server):
        try:
            async with Connector(RPCSession, 'localhost', server.port) as client:
                server_session = await MyServerSession.current_server()
                for n in range(server_session.max_errors + 1):
                    with suppress(RPCError):
                        await client.send_request('boo')
        except CancelledError:
            pass
        assert server_session.errors == server_session.max_errors

    @pytest.mark.asyncio
    async def test_finalrpcerror(self, server):
        async with Connector(RPCSession, 'localhost',
                             server.port) as client:
            with pytest.raises(RPCError):
                await client.send_request('incompatibleversion')
            await sleep(0)
            assert client.is_closing()

    @pytest.mark.asyncio
    async def test_send_empty_batch(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            with RaiseTest(JSONRPC.INVALID_REQUEST, 'empty', ProtocolError):
                async with client.send_batch() as batch:
                    pass
            assert len(batch) == 0
            assert batch.batch is None
            assert batch.results is None

    @pytest.mark.asyncio
    async def test_send_batch(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            async with client.send_batch() as batch:
                batch.add_request("echo", [1])
                batch.add_notification("echo", [2])
                batch.add_request("echo", [3])

            assert isinstance(batch.batch, Batch)
            assert len(batch) == 3
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert batch.results == (1, 3)

    @pytest.mark.asyncio
    async def test_send_batch_errors_quiet(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            async with client.send_batch() as batch:
                batch.add_request("echo", [1])
                batch.add_request("bug")

            assert isinstance(batch.batch, Batch)
            assert len(batch) == 2
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert isinstance(batch.results[1], RPCError)

    @pytest.mark.asyncio
    async def test_send_batch_errors(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            with pytest.raises(BatchError) as e:
                async with client.send_batch(raise_errors=True) as batch:
                    batch.add_request("echo", [1])
                    batch.add_request("bug")

            assert e.value.request is batch
            assert isinstance(batch.batch, Batch)
            assert len(batch) == 2
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert isinstance(batch.results[1], RPCError)

    @pytest.mark.asyncio
    async def test_send_batch_cancelled(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            async def send_batch():
                async with client.send_batch(raise_errors=True) as batch:
                    batch.add_request('sleepy')

            task = await spawn(send_batch)
            await client.close()
            with pytest.raises(CancelledError):
                task.result()

    @pytest.mark.asyncio
    async def test_send_batch_bad_request(self, server):
        async with Connector(RPCSession, 'localhost', server.port) as client:
            with RaiseTest(JSONRPC.METHOD_NOT_FOUND, 'string', ProtocolError):
                async with client.send_batch() as batch:
                    batch.add_request(23)


class RPCClient(RPCSession):
    # For tests of wire messages
    def connection_made(self, transport):
        self.transport = transport

    async def send(self, item):
        if not isinstance(item, str):
            item = json.dumps(item)
        item = item.encode()
        await self._send_message(item)

    async def response(self):
        message = await self.framer.receive_message()
        return json.loads(message.decode())


class TestWireResponses(object):
    # These tests are similar to those in the JSON RPC v2 specification

    @pytest.mark.asyncio
    async def test_send_request(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": "echo", "params": [[42, 43]],
                    "id": 1}
            await client.send(item)
            assert await client.response() == {"jsonrpc": "2.0",
                                               "result": [42, 43], "id": 1}

    @pytest.mark.asyncio
    async def test_send_request_named(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": "echo", "params":
                    {"value" : [42, 43]}, "id": 3}
            await client.send(item)
            assert await client.response() == {"jsonrpc": "2.0",
                                               "result": [42, 43], "id": 3}

    @pytest.mark.asyncio
    async def test_send_notification(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": "echo", "params": [[42, 43]]}
            await client.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    await client.response()

    @pytest.mark.asyncio
    async def test_send_non_existent_notification(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": "zz", "params": [[42, 43]]}
            await client.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    await client.response()

    @pytest.mark.asyncio
    async def test_send_non_existent_method(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": "foobar", "id": 0}
            await client.send(item)
            assert await client.response() == {
                "jsonrpc": "2.0", "id": 0, "error":
                {'code': -32601, 'message': 'unknown method "foobar"'}}

    @pytest.mark.asyncio
    async def test_send_invalid_json(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = '{"jsonrpc": "2.0", "method": "foobar, "params": "bar", "b]'
            await client.send(item)
            assert await client.response() == {
                "jsonrpc": "2.0", "error":
                {"code": -32700, "message": "invalid JSON"}, "id": None}

    @pytest.mark.asyncio
    async def test_send_invalid_request_object(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = {"jsonrpc": "2.0", "method": 1, "params": "bar"}
            await client.send(item)
            assert await client.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32602, "message":
                          "invalid request arguments: bar"}}

    @pytest.mark.asyncio
    async def test_send_batch_invalid_json(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = ('[{"jsonrpc": "2.0", "method": "sum", "params": [1,2,4],'
                    '"id": "1"}, {"jsonrpc": "2.0", "method"  ]')
            await client.send(item)
            assert await client.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "invalid JSON"}}

    @pytest.mark.asyncio
    async def test_send_empty_batch(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = []
            await client.send(item)
            assert await client.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "batch is empty"}}

    @pytest.mark.asyncio
    async def test_send_invalid_batch(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = [1]
            await client.send(item)
            assert await client.response() == [{
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message":
                          "request object must be a dictionary"}}]

    @pytest.mark.asyncio
    async def test_send_invalid_batch_3(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = [1, 2, 3]
            await client.send(item)
            assert await client.response() == [{
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message":
                          "request object must be a dictionary"}}] * 3

    @pytest.mark.asyncio
    async def test_send_partly_invalid_batch(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = [1, {"jsonrpc": "2.0", "method": "echo", "params": [42],
                        "id": 0}]
            await client.send(item)
            assert await client.response() == [
                { "jsonrpc": "2.0", "id": None,
                  "error": {"code": -32600, "message":
                            "request object must be a dictionary"}},
                {"jsonrpc": "2.0", "result": 42, "id": 0}]

    @pytest.mark.asyncio
    async def test_send_mixed_batch(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = [
                {"jsonrpc": "2.0", "method": "echo", "params": [40], "id": 3},
                {"jsonrpc": "2.0", "method": "echo", "params": [42]},
                {"jsonrpc": "2.0", "method": "echo", "params": [41], "id": 2}
            ]
            await client.send(item)
            assert await client.response() == [
                {"jsonrpc": "2.0", "result": 40, "id": 3},
                {"jsonrpc": "2.0", "result": 41, "id": 2}
            ]

    @pytest.mark.asyncio
    async def test_send_notification_batch(self, server):
        async with Connector(RPCClient, 'localhost', server.port) as client:
            item = [{"jsonrpc": "2.0", "method": "echo", "params": [42]}] * 2
            await client.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    assert await client.response()


@pytest.mark.asyncio
async def test_base_class_implementation():
    session = RPCSession()
    await session.handle_request(Request('', []))
    from aiorpcx.session import SessionBase
    with pytest.raises(NotImplementedError):
        # default_framer
        SessionBase()
    session = SessionBase(framer=NewlineFramer())
    with pytest.raises(NotImplementedError):
        session._receive_messages()


def test_default_and_passed_connection():
    connection = JSONRPCConnection(JSONRPCv1)
    class RPCClient(RPCSession):
        def default_connection(self):
            return connection

    session = RPCClient()
    assert session.connection == connection

    session = RPCSession(connection=connection)
    assert session.connection == connection


class MessageServer(MessageSession):

    @classmethod
    async def current_server(self):
        await sleep(0)
        return self._current_server

    def connection_made(self, transport):
        MessageServer._current_server = self
        super().connection_made(transport)
        self.messages = []

    def connection_lost(self, exc):
        MessageServer._current_server = None
        super().connection_lost(exc)

    async def handle_message(self, message):
        command, payload = message
        self.messages.append(message)
        if command == b'syntax':
            raise SyntaxError
        elif command == b'protocol':
            raise ProtocolError(2, 'Not allowed')
        elif command == b'cancel':
            raise CancelledError

@pytest.fixture
def msg_server(event_loop, unused_tcp_port):
    port = unused_tcp_port
    server = Server(MessageServer, 'localhost', port, loop=event_loop)
    event_loop.run_until_complete(server.listen())
    yield server
    event_loop.run_until_complete(server.close())


class TestMessageSession(object):

    @pytest.mark.asyncio
    async def test_basic_send(self, msg_server):
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            server_session = await MessageServer.current_server()
            await client.send_message((b'version', b'abc'))
            # Give the receiving task time to process before closing the connection
            await sleep(0.001)
        assert server_session.messages == [(b'version', b'abc')]

    @pytest.mark.asyncio
    async def test_many_sends(self, msg_server):
        count = 12
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            server_session = await MessageServer.current_server()
            for n in range(count):
                await client.send_message((b'version', b'abc'))
            # Give the receiving task time to process before closing the connection
            await sleep(0.01)
        assert server_session.messages == [(b'version', b'abc')] * count

    @pytest.mark.asyncio
    async def test_errors(self, msg_server, caplog):
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            await client.send_message((b'syntax', b''))
            await client.send_message((b'protocol', b''))
            await client.send_message((b'cancel', b''))
            # Give the receiving task time to process before closing the connection
            await sleep(0.01)
        assert in_caplog(caplog, 'exception handling')
        assert in_caplog(caplog, 'Not allowed')

    @pytest.mark.asyncio
    async def test_bad_magic(self, msg_server, caplog):
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            msg = bytearray(client.framer.frame((b'version', b'')))
            msg[0] = msg[0] ^ 1
            client.transport.write(msg)
            # Give the receiving task time to process before closing the connection
            await sleep(0.001)
        assert in_caplog(caplog, 'bad network magic')

    @pytest.mark.asyncio
    async def test_bad_checksum(self, msg_server, caplog):
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            msg = bytearray(client.framer.frame((b'version', b'')))
            msg[-1] = msg[-1] ^ 1
            client.transport.write(msg)
            # Give the receiving task time to process before closing the connection
            await sleep(0.001)
        assert in_caplog(caplog, 'checksum mismatch')

    @pytest.mark.asyncio
    async def test_oversized_message(self, msg_server, caplog):
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            big = 1024 * 1024
            await client.send_message((b'version', bytes(big)))
            # Give the receiving task time to process before closing the connection
            await sleep(0.001)
        assert not in_caplog(caplog, 'oversized payload')
        async with Connector(MessageSession, 'localhost',
                             msg_server.port) as client:
            await client.send_message((b'version', bytes(big + 1)))
            # Give the receiving task time to process before closing the connection
            await sleep(0.001)
        assert in_caplog(caplog, 'oversized payload')

    @pytest.mark.asyncio
    async def test_proxy(self, msg_server):
        proxy = SOCKSProxy(('localhost', 79), SOCKS5, None)
        with pytest.raises(OSError):
            async with Connector(MessageSession, 'localhost',
                                 msg_server.port, proxy=proxy):
                pass

    @pytest.mark.asyncio
    async def test_coverage(self):
        session = MessageSession()
        await session.handle_message(b'')

    @pytest.mark.asyncio
    async def test_request_sleeps(self, msg_server, caplog):
        async with Connector(MessageSession, 'localhost', msg_server.port) as client:
            server = await MessageServer.current_server()
            server.bump_cost((server.cost_soft_limit + server.cost_hard_limit) / 2)
            # Messaging doesn't wait, so this is just for code coverage
            await client.send_message((b'version', b'abc'))

    @pytest.mark.asyncio
    async def test_request_over_hard_limit(self, msg_server):
        async with Connector(MessageSession, 'localhost', msg_server.port) as client:
            server = await MessageServer.current_server()
            server.bump_cost(server.cost_hard_limit + 100)
            await client.send_message((b'version', b'abc'))
            await sleep(0.005)
            assert client.is_closing()


async def concurrency_max(c):
    q = []

    loop = asyncio.get_event_loop()
    fut = loop.create_future()

    async def work():
        async with c:
            q.append(None)
            await fut

    tasks = []
    for n in range(16):
        tasks.append(loop.create_task(work()))
        prior_len = len(q)
        await asyncio.sleep(0)
        if len(q) == prior_len:
            break

    fut.set_result(len(q))
    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, loop=loop, return_exceptions=True)

    return fut.result()


class TestConcurrency:
    def test_concurrency_constructor(self):
        Concurrency(3)
        Concurrency(target=6)
        Concurrency(target=0)
        with pytest.raises(ValueError):
            Concurrency(target=-1)

    @pytest.mark.asyncio
    async def test_max_concurrent(self):
        c = Concurrency(target=3)
        assert c.max_concurrent == 3
        assert await concurrency_max(c) == 3
        c.set_target(3)
        assert c.max_concurrent == 3
        assert await concurrency_max(c) == 3
        c.set_target(1)
        assert c.max_concurrent == 1
        assert await concurrency_max(c) == 1

        c.set_target(0)
        assert c.max_concurrent == 0
        with pytest.raises(ExcessiveSessionCostError):
            async with c:
                pass
        c.set_target(5)
        assert c.max_concurrent == 5
        assert await concurrency_max(c) == 5

import asyncio
import json
import logging
import sys
import time
from contextlib import suppress

import pytest

from aiorpcx import (
    MessageSession, ProtocolError, sleep, spawn, TaskGroup, BitcoinFramer, SOCKS5, SOCKSProxy,
    connect_rs, JSONRPC, Batch, RPCError, TaskTimeout, RPCSession, timeout_after, serve_rs,
    NewlineFramer, BatchError, ExcessiveSessionCostError, SessionKind, ReplyAndDisconnect,
    ignore_after, handler_invocation, CancelledError,
)
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

    sessions = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notifications = []
        self.sessions.append(self)
        assert self.session_kind == SessionKind.SERVER

    @classmethod
    async def current_server(self):
        await sleep(0.05)
        return self.sessions[0]

    async def connection_lost(self):
        await super().connection_lost()
        self.sessions.remove(self)

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

    async def on_costly_error(self, cost):
        raise RPCError(1, "that cost a bunch!", cost=cost)

    async def on_disconnect(self, result=RPCError):
        if result is RPCError:
            raise ReplyAndDisconnect(RPCError(1, 'incompatible version'))
        raise ReplyAndDisconnect(result)

    async def on_sleepy(self):
        await sleep(10)


def in_caplog(caplog, message):
    return any(message in record.message for record in caplog.records)


def caplog_count(caplog, message):
    return sum(message in record.message for record in caplog.records)


@pytest.fixture
def server_port(unused_tcp_port, event_loop):
    coro = serve_rs(MyServerSession, 'localhost', unused_tcp_port, loop=event_loop)
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


class TestRPCSession:

    @pytest.mark.asyncio
    async def test_no_proxy(self, server_port):
        proxy = SOCKSProxy('localhost:79', SOCKS5, None)
        with pytest.raises(OSError):
            async with connect_rs('localhost', server_port, proxy=proxy):
                pass

    @pytest.mark.asyncio
    async def test_handlers(self, server_port):
        async with timeout_after(0.1):
            async with connect_rs('localhost', server_port) as session:
                assert session.session_kind == SessionKind.CLIENT
                assert session.proxy() is None
                with raises_method_not_found('something'):
                    await session.send_request('something')
                await session.send_notification('something')
        assert session.is_closing()

    @pytest.mark.asyncio
    async def test_send_request(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            assert await session.send_request('echo', [23]) == 23
        assert session.transport._closed_event.is_set()
        assert session.transport._process_messages_task.done()

    @pytest.mark.asyncio
    async def test_send_request_buggy_handler(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with RaiseTest(JSONRPC.INTERNAL_ERROR, 'internal server error', RPCError):
                await session.send_request('bug')

    @pytest.mark.asyncio
    async def test_unexpected_response(self, server_port, caplog):
        async with connect_rs('localhost', server_port) as session:
            # A request not a notification so we don't exit immediately
            response = {"jsonrpc": "2.0", "result": 2, "id": -1}
            with caplog.at_level(logging.DEBUG):
                await session.send_request('send_bad_response', (response, ))
        assert in_caplog(caplog, 'unsent request')

    @pytest.mark.asyncio
    async def test_unanswered_request_count(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server_session = await MyServerSession.current_server()
            assert session.unanswered_request_count() == 0
            assert server_session.unanswered_request_count() == 0
            async with ignore_after(0.01):
                await session.send_request('sleepy')
            assert session.unanswered_request_count() == 0
            assert server_session.unanswered_request_count() == 1

    @pytest.mark.asyncio
    async def test_send_request_bad_args(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            # ProtocolError as it's a protocol violation
            with RaiseTest(JSONRPC.INVALID_ARGS, 'list', ProtocolError):
                await session.send_request('echo', "23")

    @pytest.mark.asyncio
    async def test_send_request_timeout0(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with pytest.raises(TaskTimeout):
                async with timeout_after(0):
                    await session.send_request('echo', [23])

    @pytest.mark.asyncio
    async def test_send_request_timeout(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server_session = await MyServerSession.current_server()
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.01):
                    await session.send_request('sleepy')
        # Assert the server doesn't treat cancellation as an error
        assert server_session.errors == 0

    @pytest.mark.asyncio
    async def test_error_base_cost(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server_session = await MyServerSession.current_server()
            server_session.error_base_cost = server_session.cost_hard_limit * 1.1
            await session._send_message(b'')
            await sleep(0.05)
            assert server_session.errors == 1
            assert server_session.cost > server_session.cost_hard_limit
            # Check next request raises and cuts us off
            with pytest.raises(RPCError):
                await session.send_request('echo', [23])
            await sleep(0.02)
            assert session.is_closing()

    @pytest.mark.asyncio
    async def test_RPCError_cost(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server_session = await MyServerSession.current_server()
            err = RPCError(0, 'message')
            assert err.cost == 0
            with pytest.raises(RPCError):
                await session.send_request('costly_error', [1000])
            # It can trigger a cost recalc which refunds a tad
            epsilon = 1
            assert server_session.cost > server_session.error_base_cost + 1000 - epsilon

    @pytest.mark.asyncio
    async def test_send_notification(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server = await MyServerSession.current_server()
            await session.send_notification('notify', ['test'])
        await sleep(0.001)
        assert server.notifications == ['test']

    @pytest.mark.asyncio
    async def test_force_close(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            assert not session.transport._closed_event.is_set()
            await session.close(force_after=0.001)
        assert session.transport._closed_event.is_set()

    @pytest.mark.asyncio
    async def test_force_close_abort_codepath(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            protocol = session.transport
            assert not protocol._closed_event.is_set()
            await session.close(force_after=0)
        assert protocol._closed_event.is_set()

    @pytest.mark.asyncio
    async def test_verbose_logging(self, server_port, caplog):
        async with connect_rs('localhost', server_port) as session:
            session.verbosity = 4
            with caplog.at_level(logging.DEBUG):
                await session.send_request('echo', ['wait'])
            assert in_caplog(caplog, "sending message b")
            assert in_caplog(caplog, "received data b")

    @pytest.mark.asyncio
    async def test_framer_MemoryError(self, server_port, caplog):
        async with connect_rs('localhost', server_port, framer=NewlineFramer(5)) as session:
            msg = 'w' * 50
            raw_msg = msg.encode()
            # Even though long it will be sent in one bit
            request = session.send_request('echo', [msg])
            assert await request == msg
            assert not caplog.records
            session.transport.data_received(raw_msg)  # Unframed; no \n
            await sleep(0)
            assert len(caplog.records) == 1
            assert in_caplog(caplog, 'dropping message over 5 bytes')

    # @pytest.mark.asyncio
    # async def test_resource_release(self, server_port):
    #     loop = asyncio.get_event_loop()
    #     tasks = all_tasks(loop)
    #     try:
    #         session = connect_rs('localhost', 0)
    #         await session.create_connection()
    #     except OSError:
    #         pass
    #     assert all_tasks(loop) == tasks

    #     async with connect_rs('localhost', server_port):
    #         pass

    #     await asyncio.sleep(0.01)   # Let things be processed
    #     assert all_tasks(loop) == tasks

    @pytest.mark.asyncio
    async def test_pausing(self, server_port):
        called = []
        limit = None

        def my_write(data):
            called.append(data)
            if len(called) == limit:
                session.transport.pause_writing()

        async with connect_rs('localhost', server_port) as session:
            protocol = session.transport
            assert protocol._can_send.is_set()
            asyncio_transport = protocol._asyncio_transport
            try:
                asyncio_transport.write = my_write
            except AttributeError:    # uvloop: transport.write is read-only
                return
            await session._send_message(b'a')
            assert protocol._can_send.is_set()
            assert called
            called.clear()

            async def monitor():
                await sleep(0.002)
                assert called == [b'A\n', b'very\n']
                assert not protocol._can_send.is_set()
                protocol.resume_writing()
                assert protocol._can_send.is_set()

            limit = 2
            msgs = b'A very long and boring meessage'.split()
            task = await spawn(monitor)
            for msg in msgs:
                await session._send_message(msg)
            assert called == [session.transport._framer.frame(msg) for msg in msgs]
            limit = None
            # Check idempotent
            protocol.resume_writing()
            assert task.result() is None

    @pytest.mark.asyncio
    async def test_slow_connection_aborted(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            protocol = session.transport
            assert session.max_send_delay >= 10
            session.max_send_delay = 0.004
            protocol.pause_writing()
            assert not protocol._can_send.is_set()
            task = await spawn(session._send_message(b'a'))
            await sleep(0.1)
            assert isinstance(task.exception(), TaskTimeout)
            assert protocol._can_send.is_set()
            assert session.is_closing()

    @pytest.mark.asyncio
    async def test_concurrency(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            # By default clients don't have a hard limit
            assert session.cost_hard_limit == 0
            session.cost_hard_limit = session.cost_soft_limit * 2
            # Prevent this interfering
            session.cost_decay_per_sec = 0
            # Test usage below soft limit
            session.cost = session.cost_soft_limit - 10
            session.recalc_concurrency()
            assert session._incoming_concurrency.max_concurrent == session.initial_concurrent
            assert session._cost_fraction == 0.0
            # Test usage at soft limit doesn't affect concurrency
            session.cost = session.cost_soft_limit
            session.recalc_concurrency()
            assert session._incoming_concurrency.max_concurrent == session.initial_concurrent
            assert session._cost_fraction == 0.0
            # Test usage half-way
            session.cost = (session.cost_soft_limit + session.cost_hard_limit) // 2
            session.recalc_concurrency()
            assert 1 < session._incoming_concurrency.max_concurrent < session.initial_concurrent
            assert 0.49 < session._cost_fraction < 0.51
            # Test at hard limit
            session.cost = session.cost_hard_limit
            session.recalc_concurrency()
            assert session._cost_fraction == 1.0
            # Test above hard limit disconnects
            session.cost = session.cost_hard_limit + 1
            session.recalc_concurrency()
            with pytest.raises(ExcessiveSessionCostError):
                async with session._incoming_concurrency:
                    pass

    @pytest.mark.asyncio
    async def test_concurrency_no_limit_for_outgoing(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            # Prevent this interfering
            session.cost_decay_per_sec = 0
            # Test usage half-way
            session.cost = (RPCSession.cost_soft_limit + RPCSession.cost_hard_limit) // 2
            session.recalc_concurrency()
            assert session._incoming_concurrency.max_concurrent == session.initial_concurrent
            assert session._cost_fraction == 0
            # Test above hard limit does not disconnect
            session.cost = RPCSession.cost_hard_limit + 1
            session.recalc_concurrency()
            async with session._incoming_concurrency:
                pass

    @pytest.mark.asyncio
    async def test_concurrency_decay(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            session.cost_decay_per_sec = 100
            session.cost = 1000
            await sleep(0.1)
            session.recalc_concurrency()
            assert 970 < session.cost < 992

    @pytest.mark.asyncio
    async def test_concurrency_hard_limit_0(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            session.cost = 1_000_000_000
            session.cost_hard_limit = 0
            session.recalc_concurrency()
            assert session._incoming_concurrency.max_concurrent == session.initial_concurrent

    @pytest.mark.asyncio
    async def test_extra_cost(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            # By default clients don't have a hard limit
            assert session.cost_hard_limit == 0
            session.cost_hard_limit = session.cost_soft_limit * 2
            session.extra_cost = lambda: session.cost_soft_limit + 1
            session.recalc_concurrency()
            assert 1 > session._cost_fraction > 0
            session.extra_cost = lambda: session.cost_hard_limit + 1
            session.recalc_concurrency()
            assert session._cost_fraction > 1

    @pytest.mark.asyncio
    async def test_request_over_hard_limit(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server = await MyServerSession.current_server()
            server.bump_cost(server.cost_hard_limit + 100)
            async with timeout_after(0.1):
                with pytest.raises(RPCError) as e:
                    await session.send_request('echo', [23])
            assert 'excessive resource usage' in str(e.value)

    @pytest.mark.asyncio
    async def test_request_sleep(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server = await MyServerSession.current_server()
            server.bump_cost((server.cost_soft_limit + server.cost_hard_limit) / 2)
            server.cost_sleep = 0.1
            t1 = time.time()
            await session.send_request('echo', [23])
            t2 = time.time()
            assert t2 - t1 > (server.cost_sleep / 2) * 0.9  # Fudge factor for Linux

    @pytest.mark.asyncio
    async def test_server_busy(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            server = await MyServerSession.current_server()
            server.processing_timeout = 0.01
            with pytest.raises(RPCError) as e:
                await session.send_request('sleepy')
            assert 'server busy' in str(e.value)
            assert server.errors == 1

    @pytest.mark.asyncio
    async def test_reply_and_disconnect_value(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            value = 42
            assert await session.send_request('disconnect', [value]) == value
            await sleep(0.01)
            assert session.is_closing()

    @pytest.mark.asyncio
    async def test_reply_and_disconnect_error(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with pytest.raises(RPCError) as e:
                assert await session.send_request('disconnect')
            await sleep(0.01)
            exc = e.value
            assert exc.code == 1 and exc.message == 'incompatible version'
            assert session.is_closing()

    @pytest.mark.asyncio
    async def test_send_empty_batch(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with RaiseTest(JSONRPC.INVALID_REQUEST, 'empty', ProtocolError):
                async with session.send_batch() as batch:
                    pass
            assert len(batch) == 0
            assert batch.batch is None
            assert batch.results is None

    @pytest.mark.asyncio
    async def test_send_batch(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            async with session.send_batch() as batch:
                batch.add_request("echo", [1])
                batch.add_notification("echo", [2])
                batch.add_request("echo", [3])

            assert isinstance(batch.batch, Batch)
            assert len(batch) == 3
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert batch.results == (1, 3)

    @pytest.mark.asyncio
    async def test_send_batch_errors_quiet(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            async with session.send_batch() as batch:
                batch.add_request("echo", [1])
                batch.add_request("bug")

            assert isinstance(batch.batch, Batch)
            assert len(batch) == 2
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert isinstance(batch.results[1], RPCError)

    @pytest.mark.asyncio
    async def test_send_batch_errors(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with pytest.raises(BatchError) as e:
                async with session.send_batch(raise_errors=True) as batch:
                    batch.add_request("echo", [1])
                    batch.add_request("bug")

            assert e.value.request is batch
            assert isinstance(batch.batch, Batch)
            assert len(batch) == 2
            assert isinstance(batch.results, tuple)
            assert len(batch.results) == 2
            assert isinstance(batch.results[1], RPCError)

    @pytest.mark.asyncio
    async def test_send_batch_cancelled(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            async def send_batch():
                async with session.send_batch(raise_errors=True) as batch:
                    batch.add_request('sleepy')

            task = await spawn(send_batch)
            await session.close()
            await asyncio.wait([task])
            assert task.cancelled()

    @pytest.mark.asyncio
    async def test_send_batch_bad_request(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            with RaiseTest(JSONRPC.METHOD_NOT_FOUND, 'string', ProtocolError):
                async with session.send_batch() as batch:
                    batch.add_request(23)

    @pytest.mark.asyncio
    async def test_send_request_throttling(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            N = 3
            session.recalibrate_count = N
            prior = session._outgoing_concurrency.max_concurrent
            async with TaskGroup() as group:
                for n in range(N):
                    await group.spawn(session.send_request("echo", ["ping"]))
            current = session._outgoing_concurrency.max_concurrent
            assert prior * 1.2 > current > prior

    @pytest.mark.asyncio
    async def test_send_batch_throttling(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            N = 3
            session.recalibrate_count = N
            prior = session._outgoing_concurrency.max_concurrent
            async with session.send_batch() as batch:
                for n in range(N):
                    batch.add_request("echo", ["ping"])
            current = session._outgoing_concurrency.max_concurrent
            assert prior * 1.2 > current > prior

    @pytest.mark.asyncio
    async def test_sent_request_timeout(self, server_port):
        async with connect_rs('localhost', server_port) as session:
            session.sent_request_timeout = 0.01
            start = time.time()
            with pytest.raises(TaskTimeout):
                await session.send_request('sleepy')
            assert time.time() - start < 0.1

    @pytest.mark.asyncio
    async def test_log_me(self, server_port, caplog):
        async with connect_rs('localhost', server_port) as session:
            server = await MyServerSession.current_server()

            with caplog.at_level(logging.INFO):
                assert server.log_me is False
                await session.send_request('echo', ['ping'])
                assert caplog_count(caplog, '"method":"echo"') == 0

                server.log_me = True
                await session.send_request('echo', ['ping'])
                assert caplog_count(caplog, '"method":"echo"') == 1


class WireRPCSession(RPCSession):
    # For tests of wire messages

    async def send(self, item):
        if not isinstance(item, str):
            item = json.dumps(item)
        item = item.encode()
        await self._send_message(item)

    async def response(self):
        message = await self.transport.receive_message()
        return json.loads(message.decode())


def connect_wire_session(host, port):
    return connect_rs(host, port, session_factory=WireRPCSession)


class TestWireResponses(object):
    # These tests are similar to those in the JSON RPC v2 specification

    @pytest.mark.asyncio
    async def test_send_request(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": "echo", "params": [[42, 43]],
                    "id": 1}
            await session.send(item)
            assert await session.response() == {"jsonrpc": "2.0",
                                                "result": [42, 43], "id": 1}

    @pytest.mark.asyncio
    async def test_send_request_named(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": "echo", "params":
                    {"value": [42, 43]}, "id": 3}
            await session.send(item)
            assert await session.response() == {"jsonrpc": "2.0",
                                                "result": [42, 43], "id": 3}

    @pytest.mark.asyncio
    async def test_send_notification(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": "echo", "params": [[42, 43]]}
            await session.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    await session.response()

    @pytest.mark.asyncio
    async def test_send_non_existent_notification(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": "zz", "params": [[42, 43]]}
            await session.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    await session.response()

    @pytest.mark.asyncio
    async def test_send_non_existent_method(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": "foobar", "id": 0}
            await session.send(item)
            assert await session.response() == {
                "jsonrpc": "2.0", "id": 0, "error":
                {'code': -32601, 'message': 'unknown method "foobar"'}}

    @pytest.mark.asyncio
    async def test_send_invalid_json(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = '{"jsonrpc": "2.0", "method": "foobar, "params": "bar", "b]'
            await session.send(item)
            assert await session.response() == {
                "jsonrpc": "2.0", "error":
                {"code": -32700, "message": "invalid JSON"}, "id": None}

    @pytest.mark.asyncio
    async def test_send_invalid_request_object(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = {"jsonrpc": "2.0", "method": 1, "params": "bar"}
            await session.send(item)
            assert await session.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32602, "message":
                          "invalid request arguments: bar"}}

    @pytest.mark.asyncio
    async def test_send_batch_invalid_json(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = ('[{"jsonrpc": "2.0", "method": "sum", "params": [1,2,4],'
                    '"id": "1"}, {"jsonrpc": "2.0", "method"  ]')
            await session.send(item)
            assert await session.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "invalid JSON"}}

    @pytest.mark.asyncio
    async def test_send_empty_batch(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = []
            await session.send(item)
            assert await session.response() == {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "batch is empty"}}

    @pytest.mark.asyncio
    async def test_send_invalid_batch(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = [1]
            await session.send(item)
            assert await session.response() == [{
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message":
                          "request object must be a dictionary"}}]

    @pytest.mark.asyncio
    async def test_send_invalid_batch_3(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = [1, 2, 3]
            await session.send(item)
            assert await session.response() == [{
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message":
                          "request object must be a dictionary"}}] * 3

    @pytest.mark.asyncio
    async def test_send_partly_invalid_batch(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = [1, {"jsonrpc": "2.0", "method": "echo", "params": [42],
                        "id": 0}]
            await session.send(item)
            assert await session.response() == [
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message":
                           "request object must be a dictionary"}},
                {"jsonrpc": "2.0", "result": 42, "id": 0}]

    @pytest.mark.asyncio
    async def test_send_mixed_batch(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = [
                {"jsonrpc": "2.0", "method": "echo", "params": [40], "id": 3},
                {"jsonrpc": "2.0", "method": "echo", "params": [42]},
                {"jsonrpc": "2.0", "method": "echo", "params": [41], "id": 2}
            ]
            await session.send(item)
            assert await session.response() == [
                {"jsonrpc": "2.0", "result": 40, "id": 3},
                {"jsonrpc": "2.0", "result": 41, "id": 2}
            ]

    @pytest.mark.asyncio
    async def test_send_notification_batch(self, server_port):
        async with connect_wire_session('localhost', server_port) as session:
            item = [{"jsonrpc": "2.0", "method": "echo", "params": [42]}] * 2
            await session.send(item)
            with pytest.raises(TaskTimeout):
                async with timeout_after(0.002):
                    assert await session.response()


class MessageServer(MessageSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        MessageServer._current_session = self
        self.messages = []

    @classmethod
    async def current_server(self):
        while True:
            await sleep(0.001)
            if self._current_session:
                return self._current_session

    async def connection_lost(self):
        await super().connection_lost()
        MessageServer._current_session = None

    async def handle_message(self, message):
        command, payload = message
        self.messages.append(message)
        if command == b'syntax':
            raise SyntaxError
        elif command == b'protocol':
            raise ProtocolError(2, 'Not allowed')
        elif command == b'cancel':
            raise CancelledError
        elif command == b'sleep':
            await sleep(0.2)


@pytest.fixture
def msg_server_port(event_loop, unused_tcp_port):
    coro = serve_rs(MessageServer, 'localhost', unused_tcp_port, loop=event_loop)
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


def connect_message_session(host, port, proxy=None, framer=None):
    return connect_rs(host, port, proxy=proxy, framer=framer, session_factory=MessageSession)


class TestMessageSession(object):

    @pytest.mark.asyncio
    async def test_basic_send(self, msg_server_port):
        async with connect_message_session('localhost', msg_server_port) as session:
            server_session = await MessageServer.current_server()
            await session.send_message((b'version', b'abc'))
        await sleep(0.02)
        assert server_session.messages == [(b'version', b'abc')]

    @pytest.mark.asyncio
    async def test_many_sends(self, msg_server_port):
        count = 12
        async with connect_message_session('localhost', msg_server_port) as session:
            server_session = await MessageServer.current_server()
            for n in range(count):
                await session.send_message((b'version', b'abc'))
        assert server_session.messages == [(b'version', b'abc')] * count

    @pytest.mark.asyncio
    async def test_errors(self, msg_server_port, caplog):
        async with connect_message_session('localhost', msg_server_port) as session:
            await session.send_message((b'syntax', b''))
            await session.send_message((b'protocol', b''))
            await session.send_message((b'cancel', b''))
        assert in_caplog(caplog, 'exception handling')
        assert in_caplog(caplog, 'Not allowed')

    @pytest.mark.asyncio
    async def test_bad_magic(self, msg_server_port, caplog):
        framer = BitcoinFramer(magic=bytes(4))
        async with connect_message_session('localhost', msg_server_port, framer=framer) as session:
            await session.send_message((b'version', b''))
        await sleep(0.01)
        assert in_caplog(caplog, 'bad network magic')

    @pytest.mark.asyncio
    async def test_bad_checksum(self, msg_server_port, caplog):
        framer = BitcoinFramer()
        framer._checksum = lambda payload: bytes(32)
        async with connect_message_session('localhost', msg_server_port, framer=framer) as session:
            await session.send_message((b'version', b''))
        assert in_caplog(caplog, 'checksum mismatch')

    @pytest.mark.asyncio
    async def test_oversized_message(self, msg_server_port, caplog):
        big = BitcoinFramer.max_payload_size
        async with connect_message_session('localhost', msg_server_port) as session:
            await session.send_message((b'version', bytes(big)))
        assert not in_caplog(caplog, 'oversized payload')
        async with connect_message_session('localhost', msg_server_port) as session:
            await session.send_message((b'version', bytes(big + 1)))
        assert in_caplog(caplog, 'oversized payload')

    @pytest.mark.asyncio
    async def test_proxy(self, msg_server_port):
        proxy = SOCKSProxy('localhost:79', SOCKS5, None)
        with pytest.raises(OSError):
            async with connect_message_session('localhost', msg_server_port, proxy=proxy):
                pass

    @pytest.mark.asyncio
    async def test_request_sleeps(self, msg_server_port, caplog):
        async with connect_message_session('localhost', msg_server_port) as session:
            server = await MessageServer.current_server()
            server.bump_cost((server.cost_soft_limit + server.cost_hard_limit) / 2)
            # Messaging doesn't wait, so this is just for code coverage
            await session.send_message((b'version', b'abc'))

    @pytest.mark.asyncio
    async def test_request_over_hard_limit(self, msg_server_port):
        async with connect_message_session('localhost', msg_server_port) as session:
            server = await MessageServer.current_server()
            server.bump_cost(server.cost_hard_limit + 100)
            await session.send_message((b'version', b'abc'))
            await sleep(0.05)
            assert session.is_closing()

    @pytest.mark.asyncio
    async def test_server_busy(self, msg_server_port, caplog):
        async with connect_message_session('localhost', msg_server_port) as session:
            server = await MessageServer.current_server()
            server.processing_timeout = 0.01
            with caplog.at_level(logging.INFO):
                await session.send_message((b'sleep', b''))
                await sleep(0.05)
            assert server.errors == 1
        assert in_caplog(caplog, 'timed out')


class TestConcurrency:
    def test_concurrency_constructor(self):
        Concurrency(3)
        Concurrency(target=6)
        Concurrency(target=0)
        with pytest.raises(ValueError):
            Concurrency(target=-1)

    @pytest.mark.asyncio
    async def test_concurrency_control(self):
        in_flight = 0
        c = Concurrency(target=3)
        pause = 0.01
        counter = 0

        async def make_workers():
            async def worker():
                nonlocal in_flight, counter
                async with c:
                    counter += 1
                    in_flight += 1
                    await sleep(pause)
                    in_flight -= 1

            async with TaskGroup() as group:
                for n in range(100):
                    await group.spawn(worker)

        async def get_stable_in_flight():
            nonlocal in_flight
            prior = in_flight
            while True:
                await sleep(0)
                if in_flight == prior:
                    return in_flight
                prior = in_flight

        task = await spawn(make_workers)
        try:
            await sleep(0)
            assert await get_stable_in_flight() == 3
            c.set_target(3)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 3
            c.set_target(1)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 1
            c.set_target(10)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 10
            c.set_target(1)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 1
            c.set_target(5)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 5
            c.set_target(0)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 0
            # We deliberately don't recover from 0.  To do so set_target needs to release
            # once if existing value is zero.
            c.set_target(3)
            await sleep(pause * 1.1)
            assert await get_stable_in_flight() == 0
        finally:
            task.cancel()
            with suppress(CancelledError, ExcessiveSessionCostError):
                await task

    @pytest.mark.asyncio
    async def test_retarget_accounting(self):
        c = Concurrency(target=2)

        async def worker(n):
            async with c:
                await sleep(0.001 * n)
            if n == 1:
                c.set_target(1)

        for n in range(1, 4):
            await spawn(worker, n)
        # Whilst this task sleeps, the sequence is:
        # Worker 1 grabs C and sleeps
        # Worker 2 grabs C and sleeps
        # Worker 3 cannot grab C, so sleeps
        # Worker 1 wakes up, sets target to 1, ends releasing C
        # Worker 3 wakes up, grabs C, and retargets before entering the context block.
        #          It sleeps trying to acquire the semaphore.
        # Worker 2 wakes up, ends releasing C
        # Worker 3 wakes up, enters the context block, sleeps, and ends releasing C.
        #
        # This is a test that worker 3, when retargetting, decrements C._sem_value before
        # sleeping.  If it doesn't, worker 2, on exiting its context block, thinks nothing else
        # nothing is trying to retarget the semaphore, and so reduces C._sem_value instead
        # of releasing the semaphore.  This means that worker 3 never wakes up.
        await sleep(0.05)
        assert not c._semaphore.locked()

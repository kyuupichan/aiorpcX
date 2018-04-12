import asyncio
from functools import partial
import time

import pytest

from aiorpcx import *
from aiorpcx.rpc import RPCRequest, RPCRequestOut


class MyServerSession(ServerSession):

    current_server = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.notifications = []
        MyServerSession.current_server = self

    def on_ping(self, value):
        return value

    async def on_ping_async(self, value):
        return value

    def on_notify(self, thing):
        self.notifications.append(thing)

    def request_handler(self, method):
        return getattr(self, f'on_{method}', None)

    def notification_handler(self, method):
        if method == 'notify':
            return self.on_notify
        return None


class MyLogger(object):

    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, msg, **kwargs):
        self.debugs.append(msg)

    def warning(self, msg, **kwargs):
        self.warnings.append(msg)


@pytest.fixture
def server(event_loop, unused_tcp_port):
    port = unused_tcp_port
    server = Server(MyServerSession, 'localhost', port, loop=event_loop)
    event_loop.run_until_complete(server.listen())
    yield server
    server.close()
    # Needed to avoid complaints about pending tasks
    event_loop.run_until_complete(asyncio.sleep(0))


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
        await server.wait_closed()

    @pytest.mark.asyncio
    async def test_close_listening(self, server):
        asyncio_server = server.server
        assert asyncio_server is not None
        assert asyncio_server.sockets
        server.close()
        await server.wait_closed()
        assert server.server is None
        assert not asyncio_server.sockets


class TestClientSession:

    @pytest.mark.asyncio
    async def test_proxy_loop(self, server, event_loop):
        # Hack to test the proxy is passed the correct loop
        class MyProxy(SOCKSProxy):
            async def create_connection(self, protocol_factory, host, port, *,
                                        loop=None, **kwargs):
                return loop

        proxy = MyProxy(None, SOCKS5, None)
        session = ClientSession('localhost', server.port, proxy=proxy,
                                loop=event_loop)
        assert await session.create_connection() == event_loop
        session.close()
        await session.wait_closed()

    @pytest.mark.asyncio
    async def test_handlers(self, server):
        async with ClientSession('localhost', server.port) as client:
            assert client.notification_handler('foo') is None
            assert client.request_handler('foo') is None

    @pytest.mark.asyncio
    async def test_send_request(self, server):
        called = set()

        def on_done(task):
            assert task.result() == 23
            called.add(True)

        async with ClientSession('localhost', server.port) as client:
            request = client.send_request('ping', [23], timeout=0.1)
            result = await request
            assert result == 23
            # Test on_done
            request = client.send_request('ping', [23], on_done=on_done)
            result = await request
            assert result == 23
            assert called

        assert client.is_closing()

    @pytest.mark.asyncio
    async def test_send_request_bad_args(self, server):
        async with ClientSession('localhost', server.port) as client:
            with pytest.raises(ValueError):
                client.send_request('ping', "23")

    @pytest.mark.asyncio
    async def test_send_request_timeout(self, server):
        async with ClientSession('localhost', server.port) as client:
            request = client.send_request('ping', [23], timeout=0)
            with pytest.raises(asyncio.TimeoutError):
                await request

    @pytest.mark.asyncio
    async def test_send_notification(self, server):
        async with ClientSession('localhost', server.port) as client:
            req = client.send_notification('notify', ['test'])
            assert isinstance(req, RPCRequest)
        await asyncio.sleep(0.001)  # Yield to event loop for processing
        assert MyServerSession.current_server.notifications == ['test']

    @pytest.mark.asyncio
    async def test_send_batch(self, server):
        async with ClientSession('localhost', server.port) as client:
            batch = client.new_batch()
            req1 = batch.add_request('ping', ['a'])
            req2 = batch.add_notification('notify', ['item'])
            req3 = batch.add_request('ping_async', ['b'])
            client.send_batch(batch, timeout=0.1)
            assert isinstance(req1, RPCRequestOut)
            assert isinstance(req2, RPCRequest)
            assert isinstance(req3, RPCRequestOut)
            assert await batch is False   # No meaningful result of a batch
            assert await req1 == 'a'
            assert await req3 == 'b'
        assert MyServerSession.current_server.notifications == ['item']

    @pytest.mark.asyncio
    async def test_send_batch_timeout(self, server):
        called = set()

        def on_done(task):
            assert isinstance(task.exception(), asyncio.TimeoutError)
            called.add(True)

        async with ClientSession('localhost', server.port) as client:
            batch = client.new_batch()
            batch.add_request('ping', ['a'])
            client.send_batch(batch, on_done=on_done, timeout=0)
            with pytest.raises(asyncio.TimeoutError):
                await batch
            assert called

    @pytest.mark.asyncio
    async def test_create_task(self, server):
        async def double(value):
            return value * 2

        called = set()

        def on_done(task):
            assert task.result() == 12
            called.add(True)

        async with ClientSession('localhost', server.port) as client:
            my_task = client.create_task(double(6))
            my_task.add_done_callback(on_done)
        assert await my_task == 12
        assert called

    @pytest.mark.asyncio
    async def test_abort(self, server):
        async with ClientSession('localhost', server.port) as client:
            client.abort()
            assert client.is_closing()

    @pytest.mark.asyncio
    async def test_request_cancelled_on_close(self, server):
        async with ClientSession('localhost', server.port) as client:
            request = client.send_request('ping', [23])
        await asyncio.sleep(0)  # Yield to event loop for processing
        assert request.cancelled()

    @pytest.mark.asyncio
    async def test_logging(self, server):
        async with ClientSession('localhost', server.port) as client:
            client.logger = MyLogger()
            client.verbosity = 4
            request = client.send_request('ping', ['wait'])
            assert len(client.logger.debugs) == 1
            await request
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
            request = client.send_request('ping', [msg])
            assert await request == msg
            assert not client.logger.warnings
            client.data_received(raw_msg)  # Unframed; no \n
            assert len(client.logger.warnings) == 1

    @pytest.mark.asyncio
    async def test_set_timeout(self, server):
        async with ClientSession('localhost', server.port) as client:
            req = client.send_request('ping', [23])
            client.set_timeout(req, 0)
            with pytest.raises(asyncio.TimeoutError):
                await req
            with pytest.raises(RuntimeError) as err:
                client.set_timeout(req, 0)
            assert 'cannot set a timeout' in str(err.value)
            req = client.send_request('ping', ['a'])
            client.set_timeout(req, 0.1)
            assert await req == 'a'

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

        await asyncio.sleep(0.004)
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
            client.transport.write = my_write
            client.send_message(b'a')
            assert called
            called.clear()

            limit = 2
            msgs = b'A very long and boring meessage'.split()
            framed_msgs = [client.framer.frame((msg, )) for msg in msgs]
            client.pause_writing()
            for msg in msgs:
                client.send_message(msg)
            assert not called
            client.resume_writing()
            assert called == [b''.join(framed_msgs)]
            limit = None
            with pytest.raises(RuntimeError):
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
        messages = []

        async with ClientSession('localhost', server.port) as client:
            client.rpc.message_received = messages.append
            for n in range(client.max_errors + 5):
                client.send_message(b'boo')
            await asyncio.sleep(0.01)
            assert client.transport is None
            assert len(messages) == client.max_errors

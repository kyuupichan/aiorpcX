# Copyright (c) 2018, Neil Booth
#
# All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


__all__ = ('ClientSession', 'ServerSession', 'Server')


import asyncio
import itertools
import logging
import time
from contextlib import suppress

from aiorpcx import *
from aiorpcx.util import Concurrency


class SessionBase(asyncio.Protocol):

    concurrency_recalc_interval = 15
    max_errors = 10

    def __init__(self, protocol=None, framer=None, loop=None):
        protocol = protocol or self.default_protocol()
        self.connection = JSONRPCConnection(protocol)
        self.framer = framer or self.default_framer()
        self.loop = loop or asyncio.get_event_loop()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.transport = None
        # Concurrency
        self.max_concurrent = 6
        # Set when a connection is made
        self._address = None
        self._proxy_address = None
        # For logger.debug messsages
        self.verbosity = 0
        # Pausing sends when socket is full
        self.paused = False
        self.paused_messages = []
        # Close once the send queue is exhausted.  An unfortunate hack
        # necessitated by asyncio's non-blocking socket writes
        self.close_after_send = False
        # Statistics.  The RPC object also keeps its own statistics.
        self.start_time = time.time()
        self.errors = 0
        self.send_count = 0
        self.send_size = 0
        self.last_send = self.start_time
        self.recv_count = 0
        self.recv_size = 0
        self.last_recv = self.start_time
        # Bandwidth usage per hour before throttling starts
        self.bw_limit = 2000000
        self.bw_time = self.start_time
        self.bw_charge = 0
        # Concurrency control
        self.concurrency = Concurrency(self.max_concurrent)
        self.message_queue = Queue()
        self.pm_task = None

    async def _process_messages(self):
        queue_get = self.message_queue.get
        receive_message = self.connection.receive_message
        # wait=object to ensure the task group doesn't keep task references
        async with TaskGroup(wait=object) as group:
            for n in itertools.count():
                item = await queue_get()
                requests = receive_message(item)
                for request in requests:
                    await group.spawn(self._throttled_request(request))
                if n % self.concurrency_recalc_interval == 0:
                    await self._update_concurrency()

    async def _throttled_request(self, request):
        async with self.concurrency.semaphore:
            try:
                result = await self.handle_request(request)
            except (ProtocolError, RPCError) as e:
                result = e
                self.errors += 1
            except CancelledError:
                raise
            except Exception as e:
                self.logger.exception(f'exception handling {request}')
                result = RPCError(JSONRPC.INTERNAL_ERROR,
                                  'internal server error')
                self.errors += 1
            if isinstance(request, Request):
                message = request.send_result(result)
                if message:
                    self._send_messages((message, ), framed=False)

    async def _update_concurrency(self):
        now = time.time()
        # Reduce the recorded usage in proportion to the elapsed time
        refund = (now - self.bw_time) * (self.bw_limit / 3600)
        self.bw_charge = max(0, self.bw_charge - int(refund))
        self.bw_time = now
        # Reduce concurrency allocation by 1 for each whole bw_limit used
        throttle = int(self.bw_charge / self.bw_limit)
        target = max(1, self.max_concurrent - throttle)
        current = self.concurrency.max_concurrent
        if target != current:
            self.logger.info(f'changing task concurrency from {current} '
                             f'to {target}')
            await self.concurrency.set_max_concurrent(target)

    def _using_bandwidth(self, size):
        '''Called when sending or receiving size bytes.'''
        self.bw_charge += size

    def _send_messages(self, messages, *, framed):
        '''Send messages, an iterable.  Framed is true if they are already
        framed.'''
        if self.is_closing():
            return
        if framed:
            framed_message = b''.join(messages)
        else:
            framed_message = self.framer.frame(messages)
        if self.paused:
            self.paused_messages.append(framed_message)
        else:
            self.send_size += len(framed_message)
            self._using_bandwidth(len(framed_message))
            self.send_count += 1
            self.last_send = time.time()
            if self.verbosity >= 4:
                self.logger.debug(f'Sending framed message {framed_message}')
            self.transport.write(framed_message)
            if self.close_after_send or self.errors >= self.max_errors:
                self.transport.close()

    # asyncio framework
    def data_received(self, framed_message):
        '''Called by asyncio when a message comes in.'''
        if self.verbosity >= 4:
            self.logger.debug(f'Received framed message {framed_message}')
        self.recv_size += len(framed_message)
        self._using_bandwidth(len(framed_message))

        count = 0
        try:
            for message in self.framer.messages(framed_message):
                count += 1
                self.message_queue.put_nowait(message)
        except MemoryError as e:
            self.logger.warning(str(e))

        if count:
            self.recv_count += count
            self.last_recv = time.time()

    def pause_writing(self):
        '''Transport calls when the send buffer is full.'''
        self.logger.info('pausing whilst socket drains')
        self.paused = True
        self.transport.pause_reading()

    def resume_writing(self):
        '''Transport calls when the send buffer has room.'''
        if self.paused:
            self.logger.info('resuming processing')
            self.paused = False
            self.transport.resume_reading()
            self._send_messages(self.paused_messages, framed=True)
            self.paused_messages.clear()

    def default_framer(self):
        '''Return a default framer.'''
        return NewlineFramer()

    def connection_made(self, transport):
        '''Called by asyncio when a connection is established.

        Derived classes overriding this method must call this first.'''
        self.transport = transport
        # This would throw if called on a closed SSL transport.  Fixed
        # in asyncio in Python 3.6.1 and 3.5.4
        peer_address = transport.get_extra_info('peername')
        # If the Socks proxy was used then _address is already set to
        # the remote address
        if self._address:
            self._proxy_address = peer_address
        else:
            self._address = peer_address
        self.pm_task = spawn_sync(self._process_messages(), loop=self.loop)

    def connection_lost(self, exc):
        '''Called by asyncio when the connection closes.

        Tear down things done in connection_made.'''
        self._address = None
        self.transport = None
        # Cancel pending requests and message processing
        self.connection.cancel_pending_requests()
        self.pm_task.cancel()
        self.pm_task = None

    # External API
    async def handle_request(self, request):
        pass

    def default_protocol(self):
        '''Return a default protocol if the user provides none.'''
        return JSONRPCv2

    def peer_address(self):
        '''Returns the peer's address (Python networking address), or None if
        no connection or an error.

        This is the result of socket.getpeername() when the connection
        was made.
        '''
        return self._address

    def peer_address_str(self):
        '''Returns the peer's IP address and port as a human-readable
        string.'''
        if not self._address:
            return 'unknown'
        ip_addr_str, port = self._address[:2]
        if ':' in ip_addr_str:
            return f'[{ip_addr_str}]:{port}'
        else:
            return f'{ip_addr_str}:{port}'

    async def send_request(self, method, args=()):
        '''Send an RPC request over the network.'''
        message, event = self.connection.send_request(Request(method, args))
        self._send_messages((message, ), framed=False)
        await event.wait()
        result = event.result
        if isinstance(result, Exception):
            raise result
        return result

    async def send_notification(self, method, args=()):
        '''Send an RPC notification over the network.'''
        message = self.connection.send_notification(Notification(method, args))
        self._send_messages((message, ), framed=False)

    def is_closing(self):
        '''Return True if the connection is closing.'''
        return not self.transport or self.transport.is_closing()

    async def close(self):
        '''Close the connection and return when closed.'''
        if self.transport:
            self.transport.close()
        if self.pm_task:
            with suppress(CancelledError):
                await self.pm_task

    def abort(self):
        '''Cut the connection abruptly.'''
        if self.transport:
            self.transport.abort()


class ClientSession(SessionBase):
    '''A client session.

    To initiate a connection to the remote server call
    create_connection().  Each successful call should have a
    corresponding call to close().

    Alternatively if used in a with statement, the connection is made
    on entry to the block, and closed on exit from the block.
    '''

    def __init__(self, host=None, port=None, *, protocol=None, framer=None,
                 loop=None, proxy=None, **kwargs):
        super().__init__(protocol, framer, loop)
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.proxy = proxy

    async def create_connection(self):
        '''Initiate a connection.'''
        def self_func():
            return self
        if self.proxy:
            return await self.proxy.create_connection(
                self_func, self.host, self.port, **self.kwargs)

        return await self.loop.create_connection(
            self_func, self.host, self.port, **self.kwargs)

    async def __aenter__(self):
        await self.create_connection()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()


class ServerSession(SessionBase):
    '''A server session - created by a listening Server for each incoming
    connection.'''


class Server(object):
    '''A simple wrapper around an asyncio.Server object.'''

    def __init__(self, session_factory, host=None, port=None, *,
                 loop=None, **kwargs):
        self.host = host
        self.port = port
        self.loop = loop or asyncio.get_event_loop()
        self.server = None
        self._session_factory = session_factory
        self._kwargs = kwargs
        self._sessions = set()

    async def listen(self):
        self.server = await self.loop.create_server(
            self._session_factory, self.host, self.port, **self._kwargs)

    async def close(self):
        '''Close the listening socket.  This does not close any ServerSession
        objects created to handle incoming connections.
        '''
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

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


__all__ = ('ClientSession', 'ServerSession', 'Server', 'BatchError')


import asyncio
import itertools
import logging
import time
from contextlib import suppress

from aiorpcx import *
from aiorpcx.util import Concurrency


class BatchError(Exception):

    def __init__(self, request):
        self.request = request   # BatchRequest object


class BatchRequest(object):
    '''Used to build a batch request to send to the server.  Stores
    the

    Attributes batch and results are initially None.

    Adding an invalid request or notification immediately raises a
    ProtocolError.

    On exiting the with clause, it will:

    1) create a Batch object for the requests in the order they were
       added.  If the batch is empty this raises a ProtocolError.

    2) set the "batch" attribute to be that batch

    3) send the batch request and wait for a response

    4) raise a ProtocolError if the protocol was violated by the
       server.  Currently this only happens if it gave more than one
       response to any request

    5) otherwise there is precisely one response to each Request.  Set
       the "results" attribute to the tuple of results; the responses
       are ordered to match the Requests in the batch.  Notifications
       do not get a response.

    6) if raise_errors is True and any individual response was a JSON
       RPC error response, or violated the protocol in some way, a
       BatchError exception is raised.  Otherwise the caller can be
       certain each request returned a standard result.
    '''

    def __init__(self, session, raise_errors):
        self._session = session
        self._raise_errors = raise_errors
        self._requests = []
        self.batch = None
        self.results = None

    def add_request(self, method, args=()):
        self._requests.append(Request(method, args))

    def add_notification(self, method, args=()):
        self._requests.append(Notification(method, args))

    def __len__(self):
        return len(self._requests)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.batch = Batch(self._requests)
            message, event = self._session.connection.send_batch(self.batch)
            self._session._send_messages((message, ), framed=False)
            await event.wait()
            self.results = event.result
            if self._raise_errors:
                if any(isinstance(item, Exception) for item in event.result):
                    raise BatchError(self)


class SessionBase(asyncio.Protocol):

    max_errors = 10

    def __init__(self, connection=None, framer=None, loop=None):
        self.connection = connection or self.default_connection()
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
        self.work_queue = Queue()
        self.pm_task = None

    async def _process_messages(self):
        '''Process incoming messages asynchronously.  They are placed
        syncronously on a queue.
        '''
        async with TaskGroup() as group:
            for n in itertools.count():
                item = await self.work_queue.get()
                # Consume completed request tasks
                if item is None:
                    await group.next_result()
                    continue

                try:
                    requests = self.connection.receive_message(item)
                except ProtocolError as e:
                    self.logger.debug(f'{e}')
                    self.errors += 1
                    if e.code == JSONRPC.PARSE_ERROR:
                        self.close_after_send = True
                    if e.error_message:
                        self._send_messages((e.error_message, ), framed=False)
                else:
                    for request in requests:
                        await group.spawn(self._throttled_request(request))
                if self.errors >= self.max_errors:
                    self.transport.close()
                if n % 10 == 0:
                    await self._update_concurrency()

    async def _throttled_request(self, request):
        '''Process a request.  When complete, put None on the work queue
        to tell the main loop to collect the result.'''
        try:
            async with self.concurrency.semaphore:
                try:
                    result = await self.handle_request(request)
                except (ProtocolError, RPCError) as e:
                    result = e
                    self.errors += 1
                except CancelledError:
                    raise
                except Exception:
                    self.logger.exception(f'exception handling {request}')
                    result = RPCError(JSONRPC.INTERNAL_ERROR,
                                      'internal server error')
                    self.errors += 1
                if isinstance(request, Request):
                    message = request.send_result(result)
                    if message:
                        self._send_messages((message, ), framed=False)
        finally:
            await self.work_queue.put(None)

    async def _update_concurrency(self):
        # A non-positive value means not to limit concurrency
        if self.bw_limit <= 0:
            return
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
                self.work_queue.put_nowait(message)
        except MemoryError as e:
            self.logger.warning(f'{e!r}')

        if count:
            self.recv_count += count
            self.last_recv = time.time()

    def pause_writing(self):
        '''Transport calls when the send buffer is full.'''
        self.paused = True
        self.transport.pause_reading()

    def resume_writing(self):
        '''Transport calls when the send buffer has room.'''
        if self.paused:
            self.paused = False
            self.transport.resume_reading()
            self._send_messages(self.paused_messages, framed=True)
            self.paused_messages.clear()

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
        # This helps task release of server sessions as asyncio
        # doesn't call close() explicitly
        if not isinstance(self, ClientSession):
            self.pm_task = None

    # External API
    def default_connection(self):
        '''Return a default connection if the user provides none.'''
        return JSONRPCConnection(JSONRPCv2)

    def default_framer(self):
        '''Return a default framer.'''
        return NewlineFramer()

    async def handle_request(self, request):
        pass

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

    def send_batch(self, raise_errors=False):
        '''Return a BatchRequest.  Intended to be used like so:

           async with session.send_batch() as batch:
               batch.add_request("method1")
               batch.add_request("sum", (x, y))
               batch.add_notification("updated")

           for result in batch.results:
              ...

        Note that in some circumstances exceptions can be raised; see
        BatchRequest doc string.
        '''
        return BatchRequest(self, raise_errors)

    def is_closing(self):
        '''Return True if the connection is closing.'''
        return not self.transport or self.transport.is_closing()

    async def close(self, *, force_after=30):
        '''Close the connection and return when closed.'''
        if self.transport:
            self.transport.close()
        if self.pm_task:
            task = self.pm_task   # copy as self.pm_task may be set to None
            with suppress(CancelledError):
                async with ignore_after(force_after):
                    await task
                if self.transport:
                    self.transport.abort()
                await task


class ClientSession(SessionBase):
    '''A client session.

    To initiate a connection to the remote server call
    create_connection().  Each successful call should have a
    corresponding call to close().

    Alternatively if used in a with statement, the connection is made
    on entry to the block, and closed on exit from the block.
    '''

    def __init__(self, host=None, port=None, *, connection=None, framer=None,
                 loop=None, proxy=None, **kwargs):
        super().__init__(connection, framer, loop)
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.proxy = proxy
        self.bw_limit = 0

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

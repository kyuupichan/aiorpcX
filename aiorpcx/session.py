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


__all__ = ('ClientSession', 'ServerSession', 'Server', 'ConnectionError')


import asyncio
import logging
import ssl
import time

from .framing import NewlineFramer
from .jsonrpc import JSONRPCv2
from .rpc import (RPCProcessor, RPCRequest, RPCRequestOut,
                  RPCBatchOut, RPCHelperBase)
from .util import TaskSet, Concurrency


class ConnectionError(RuntimeError):
    pass


class SessionBase(asyncio.Protocol, RPCHelperBase):

    concurrency_recalc_interval = 15
    max_errors = 10

    def __init__(self, rpc_protocol=None, framer=None, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.logger = logging.getLogger(self.__class__.__name__)
        rpc_protocol = rpc_protocol or self.default_rpc_protocol()
        self.rpc = RPCProcessor(rpc_protocol, self)
        self.framer = framer or self.default_framer()
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
        # Asyncronous tasks
        self.tasks = TaskSet()
        self.concurrency = Concurrency(self.max_concurrent)

    def semaphore(self):
        return self.concurrency.semaphore

    def using_bandwidth(self, size):
        '''Called when sending or receiving size bytes.'''
        self.bw_charge += size

    async def _concurrency_loop(self):
        '''Reclaculate concurrency setting at regular intervals.'''
        while True:
            await self._update_concurrency()
            await asyncio.sleep(self.concurrency_recalc_interval)

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

    def data_received(self, framed_message):
        '''Called by asyncio when a message comes in.'''
        if self.verbosity >= 4:
            self.logger.debug(f'Received framed message {framed_message}')
        self.recv_size += len(framed_message)
        self.using_bandwidth(len(framed_message))

        count = 0
        try:
            for message in self.framer.messages(framed_message):
                count += 1
                self.rpc.message_received(message)
        except MemoryError as e:
            self.logger.warning(str(e))

        if count:
            self.recv_count += count
            self.last_recv = time.time()

    def send_message(self, message):
        '''Send a message over the connection. It is framed before sending.'''
        framed_message = self.framer.frame((message, ))
        self.send_framed_message(framed_message)

    def send_framed_message(self, framed_message):
        if self.is_closing():
            return
        if self.paused:
            self.paused_messages.append(framed_message)
        else:
            self.send_size += len(framed_message)
            self.using_bandwidth(len(framed_message))
            self.send_count += 1
            self.last_send = time.time()
            if self.verbosity >= 4:
                self.logger.debug(f'Sending framed message {framed_message}')
            self.transport.write(framed_message)
            if self.close_after_send or self.rpc.errors >= self.max_errors:
                self.close()

    def pause_writing(self):
        '''Transport calls when the send buffer is full.'''
        self.logger.info('pausing whilst socket drains')
        self.paused = True
        self.transport.pause_reading()

    def resume_writing(self):
        '''Transport calls when the send buffer has room.'''
        self.logger.info('resuming processing')
        self.paused = False
        self.transport.resume_reading()
        self.send_framed_message(b''.join(self.paused_messages))
        self.paused_messages.clear()

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

    # External API
    def default_rpc_protocol(self):
        '''Return a default rpc helper if the user provides none.'''
        return JSONRPCv2

    def default_framer(self):
        '''Return a default framer.'''
        return NewlineFramer()

    def create_task(self, coro):
        return self.tasks.create_task(coro)

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
        self.tasks.create_task(self._concurrency_loop())

    def connection_lost(self, exc):
        '''Called by asyncio when the connection closes.

        Tear down things done in connection_made.'''
        self._address = None
        self.transport = None
        self.tasks.cancel_all()
        for request in self.all_requests():
            request.set_exception(ConnectionError(
                'connection lost before request completed'))

    # App layer
    async def wait_closed(self):
        '''Returns when all pending tasks and requests have completed.'''
        await self.tasks.wait()
        await asyncio.gather(*self.all_requests(), return_exceptions=True)

    def is_closing(self):
        '''Return True if the connection is closing.'''
        return not self.transport or self.transport.is_closing()

    def close(self):
        '''Close the connection.'''
        if self.transport:
            self.transport.close()

    def abort(self):
        '''Cut the connection abruptly.'''
        if self.transport:
            self.transport.abort()

    def send_request(self, method, args=None, on_done=None, *, timeout=None):
        '''Send an RPC request over the network.'''
        request = RPCRequestOut(method, args, on_done, loop=self.loop)
        self.rpc.send_request(request)
        if timeout is not None:
            self.set_timeout(request, timeout)
        return request

    def send_notification(self, method, args=None):
        '''Send an RPC notification over the network.'''
        request = RPCRequest(method, args, None)
        self.rpc.send_request(request)
        return request

    def new_batch(self):
        return RPCBatchOut(loop=self.loop)

    def send_batch(self, batch, on_done=None, *, timeout=None):
        self.rpc.send_batch(batch, on_done)
        if timeout is not None:
            self.set_timeout(batch, timeout)
        return batch

    def set_timeout(self, request, delay):
        '''Cause a request (or batch request) to time-out after delay
        seconds (a float).'''
        if request not in self.rpc.all_requests():
            raise RuntimeError(f'cannot set a timeout on {request} - it does '
                               'not belong to this session')

        def on_timeout():
            msg = f'{request} timed out after {delay}s'
            request.set_exception(asyncio.TimeoutError(msg))

        def on_done(request):
            handle.cancel()

        handle = self.loop.call_later(delay, on_timeout)
        request.add_done_callback(on_done)

    def all_requests(self):
        '''Returns a list of all requests that have not yet completed.

        If a batch requests is outstanding, it is returned and not the
        individual requests it is comprised of.
        '''
        return self.rpc.all_requests()


class ClientSession(SessionBase):
    '''A client session.

    To initiate a connection to the remote server call
    create_connection().  Each successful call should have a
    corresponding call to close().

    Alternatively if used in a with statement, the connection is made
    on entry to the block, and closed on exit from the block.
    '''

    def __init__(self, host=None, port=None, *, rpc_protocol=None, framer=None,
                 loop=None, proxy=None, **kwargs):
        super().__init__(rpc_protocol, framer, loop)
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
                self_func, self.host, self.port, loop=self.loop, **self.kwargs)

        return await self.loop.create_connection(
            self_func, self.host, self.port, **self.kwargs)

    async def __aenter__(self):
        await self.create_connection()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.close()


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

    async def wait_closed(self):
        if self.server:
            await self.server.wait_closed()
            self.server = None

    def close(self):
        '''Close the listening socket.  This does not close any ServerSession
        objects created to handle incoming connections.
        '''
        if self.server:
            self.server.close()

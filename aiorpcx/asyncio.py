# Copyright (c) 2019, Neil Booth
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

'''Asyncio wrapper.'''

from functools import partial
import sys

from asyncio import Queue, Event, Lock, Semaphore, CancelledError, sleep, get_event_loop
if sys.version_info >= (3, 7):
    from asyncio import current_task
else:
    from asyncio import Task
    current_task = Task.current_task


__all__ = (
    'Queue', 'Event', 'Lock', 'Semaphore', 'sleep', 'Socket', 'CancelledError',
    'current_task'
)


class Socket:
    '''Wraps an existing socket.'''

    def __init__(self, sockobj, loop=None):
        self._socket = sockobj
        # A non-blocking socket is required by loop socket methods.  curio does the same.
        sockobj.setblocking(False)
        if loop is None:
            loop = get_event_loop()
        self._loop = loop
        # Bound methods
        self.accept = partial(loop.sock_accept, sockobj)
        self.connect = partial(loop.sock_connect, sockobj)
        self.recv = partial(loop.sock_recv, sockobj)
        self.recv_into = partial(loop.sock_recv_into, sockobj)
        self.sendall = partial(loop.sock_sendall, sockobj)
        self.getpeername = sockobj.getpeername

    async def close(self):
        if self._socket:
            self._socket.close()
        self._socket = None

    async def __aenter__(self):
        self._socket.__enter__()
        return self

    async def __aexit__(self, *args):
        if self._socket:
            self._socket.__exit__(*args)

    async def create_connection(self, protocol_factory, ssl=None, server_hostname=None):
        return await self._loop.create_connection(protocol_factory, sock=self._socket, ssl=ssl,
                                                  server_hostname=server_hostname)

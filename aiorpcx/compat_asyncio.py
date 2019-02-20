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

from asyncio import Queue, Event, Lock, Semaphore, sleep, get_event_loop
from functools import partial

__all__ = (
    'Queue', 'Event', 'Lock', 'Semaphore', 'sleep', 'Socket'
)


class Socket:
    '''Wraps an existing socket.'''

    def __init__(self, sockobj, loop=None):
        self._socket = sockobj
        # A non-blocking socket is required by loop socket methods.  curio does same.
        sockobj.setblocking(False)
        if loop is None:
            loop = asyncio.get_event_loop()
        # Bound methods
        self.accept = partial(loop.sock_accept, sockobj)
        self.recv = partial(loop.sock_recv, sockobj)
        self.recv_into = partial(loop.sock_recv_into, sockobj)
        self.sendall = partial(loop.sock_sendall, sockobj)
        self.getpeername = sockobj.getpeername

    async def close(self):
        if self._socket:
            self._socket.close()
        self._socket = None

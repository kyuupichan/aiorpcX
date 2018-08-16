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

'''RPC message framing in a byte stream.'''

__all__ = ('FramerBase', 'NewlineFramer')

from itertools import chain
from collections import namedtuple
import struct


class FramerBase(object):
    '''Abstract base class for a framer.

    A framer breaks an incoming byte stream into protocol messages,
    buffering if necesary.  It also frames outgoing messages into
    a byte stream.
    '''

    def frame(self, messages):
        '''Return bytes formed by framing each message in messages,
        an iterable, and concatenating the result.
        '''
        raise NotImplementedError

    def messages(self, data):
        '''A generator that yields messages for the caller to process.

        Raises a MemoryError Exception if the internal buffer
        overflows, so converting to a list before processing risks
        message loss.
        '''
        raise NotImplementedError


class NewlineFramer(FramerBase):
    '''A framer for a protocol where messages are separated by newlines.'''

    def __init__(self, max_size=250 * 4000):
        '''max_size - an anti-DoS measure.  If, after processing an incoming
        message, buffered data would exceed max_size bytes, that
        buffered data is dropped entirely and the framer waits for a
        newline character to re-synchronize the stream.
        '''
        # The default max_size value is motivated by JSONRPC, where a
        # normal request will be 250 bytes or less, and a reasonable
        # batch may contain 4000 requests.
        self.max_size = max_size
        self.parts = []
        self.synchronizing = False

    def frame(self, messages):
        return b''.join(chain.from_iterable((msg, b'\n') for msg in messages))

    def messages(self, data):
        assert isinstance(data, (bytes, bytearray))
        parts = self.parts
        while True:
            npos = data.find(ord('\n'))
            if npos == -1:
                parts.append(data)
                break
            tail, data = data[:npos], data[npos + 1:]
            if self.synchronizing:
                self.synchronizing = False
            else:
                parts.append(tail)
                yield b''.join(parts)
                parts.clear()

        # Ignore over-sized messages; re-synchronize
        buffer_size = sum(len(part) for part in parts)
        if buffer_size > self.max_size:
            parts.clear()
            if not self.synchronizing:
                self.synchronizing = True
                raise MemoryError(f'dropping message over {self.max_size:,d} '
                                  'bytes and re-synchronizing')

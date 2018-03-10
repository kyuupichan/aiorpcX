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

'''SOCKS proxying.'''

import asyncio
import collections
import ipaddress
import logging
import socket
import struct


__all__ = ('SOCKSUserAuth', 'SOCKSProxy')


SOCKSUserAuth = collections.namedtuple("SOCKSUserAuth", "username password")


class SOCKSError(Exception):
    pass


class SOCKSBase(object):

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, *, loop):
        raise NotImplementedError

    @classmethod
    def name(cls):
        return cls.__name__


class SOCKS4(SOCKSBase):
    '''SOCKS4 protocol wrapper.'''

    # See http://ftp.icm.edu.pl/packages/socks/socks4/SOCKS4.protocol
    REPLY_CODES = {
        90: 'request granted',
        91: 'request rejected or failed',
        92: ('request rejected because SOCKS server cannot connect '
             'to identd on the client'),
        93: ('request rejected because the client program and identd '
             'report different user-ids')
    }

    @classmethod
    async def _handshake(cls, socket, dst_host, dst_port, auth, loop):
        if isinstance(dst_host, ipaddress.IPv4Address):
            # SOCKS4
            dst_ip_packed = dst_host.packed
            host_bytes = b''
        else:
            # SOCKS4a
            dst_ip_packed = b'\0\0\0\1'
            host_bytes = dst_host.encode() + b'\0'

        if isinstance(auth, SOCKSUserAuth):
            user_id = auth.username.encode()
        else:
            user_id = b''

        # Send TCP/IP stream CONNECT request
        data = b''.join([b'\4\1', struct.pack('>H', dst_port), dst_ip_packed,
                         user_id, b'\0', host_bytes])
        await loop.sock_sendall(socket, data)

        # Wait for 8-byte response
        data = await loop.sock_recv(socket, 8)
        if len(data) != 8 or data[0] != 0:
            raise SOCKSError(f'invalid {cls.name()} proxy response: {data}')
        reply_code = data[1]
        if reply_code != 90:
            msg = cls.REPLY_CODES.get(
                reply_code, f'unknown {cls.name()} reply code {reply_code}')
            raise SOCKSError(f'{cls.name()} proxy request failed: {msg}')
        # Remaining fields ignored

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, *, loop):
        assert isinstance(dst_host, ipaddress.IPv4Address)
        await cls._handshake(socket, dst_host, dst_port, auth, loop)


class SOCKS4a(SOCKS4):

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, *, loop):
        assert isinstance(dst_host, (str, ipaddress.IPv4Address))
        await cls._handshake(socket, dst_host, dst_port, auth, loop)


class SOCKS5(SOCKSBase):
    '''SOCKS protocol wrapper.'''

    # See https://tools.ietf.org/html/rfc1928
    ERROR_CODES = {
        1: 'general SOCKS server failure',
        2: 'connection not allowed by ruleset',
        3: 'network unreachable',
        4: 'host unreachable',
        5: 'connection refused',
        6: 'TTL expired',
        7: 'command not supported',
        8: 'address type not supported',
    }

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, *, loop):
        # Initial handshake
        if isinstance(auth, SOCKSUserAuth):
            user_bytes = auth.username.encode()
            if not 0 < len(user_bytes) < 256:
                raise SOCKSError(f'invalid username length: {auth.username}')
            pwd_bytes = auth.password.encode()
            if not 0 < len(pwd_bytes) < 256:
                raise SOCKSError(f'invalid password length: {auth.password}')
            methods = [0, 2]
        else:
            methods = [0]

        greeting = b'\5' + bytes([len(methods)]) + bytes(m for m in methods)
        await loop.sock_sendall(socket, greeting)

        # Get response
        data = await loop.sock_recv(socket, 2)
        if len(data) != 2 or data[0] != 5:
            raise SOCKSError(f'invalid SOCKS5 proxy response: {data}')
        if data[1] not in methods:
            raise SOCKSError('SOCKS5 proxy rejected authentication methods')

        # Authenticate if user-password authentication
        if data[1] == 2:
            auth_msg = b''.join([bytes([1, len(user_bytes)]), user_bytes,
                                 bytes([len(pwd_bytes)]), pwd_bytes])
            await loop.sock_sendall(socket, auth_msg)
            data = await loop.sock_recv(socket, 2)
            if data[0] != 1 or len(data) != 2:
                raise SOCKSError(f'invalid SOCKS5 proxy auth response: {data}')
            if data[1] != 0:
                raise SOCKSError(f'SOCKS5 proxy auth failure code: {data[1]}')

        # Send connection request
        if isinstance(dst_host, ipaddress.IPv4Address):
            addr = b'\1' + dst_host.packed
        elif isinstance(dst_host, ipaddress.IPv6Address):
            addr = b'\4' + dst_host.packed
        else:
            host = dst_host.encode()
            if len(host) > 255:
                raise SOCKSError(f'hostname too long: {len(host)} bytes')
            addr = b'\3' + bytes([len(host)]) + host
        data = b''.join([b'\5\1\0', addr, struct.pack('>H', dst_port)])
        await loop.sock_sendall(socket, data)

        # Get response
        data = await loop.sock_recv(socket, 5)
        if (len(data) != 5 or data[0] != 5 or data[2] != 0
            or data[3] not in (1, 3, 4)):
            raise SOCKSError(f'invalid SOCKS5 proxy response: {data}')
        if data[1] != 0:
            raise SOCKSError(cls.ERROR_CODES.get(
                data[1], f'unknown SOCKS5 error code: {data[1]}'))
        if data[3] == 1:
            addr_len, data = 3, data[4:]  # IPv4
        elif data[3] == 3:
            addr_len, data = data[4], b'' # Hostname
        else:
            addr_len, data = 15, data[4:] # IPv6
        remaining_len = addr_len + 2
        rest = await loop.sock_recv(socket, remaining_len)
        if len(rest) != remaining_len:
            raise SOCKSError(f'short SOCKS5 proxy reply: {rest}')


async def _socket(address, loop):
    sock = socket.socket()
    try:
        sock.setblocking(False)
        await loop.sock_connect(sock, address)
    except:
        sock.close()
        raise
    return sock


class SOCKSProxy(object):

    def __init__(self, address, protocol, auth):
        '''For IPv4, address is a (host, port) pair.  For IPv6, address should
        be a (host, port, flowinfo, scopeid) 4-tuple.'''
        self.address = address
        self.protocol = protocol
        self.auth = auth
        # Set on a successful handshake with the proxy; the result of
        # socket.getpeername()
        self.peername = None

    def __str__(self):
        auth = 'username' if self.auth else 'none'
        return f'{self.protocol.name()} proxy at {self.address}, auth: {auth}'

    async def _connect(self, host, port, loop):
        '''Connect to the proxy and does a handshake.

        Return the open socket on success, otherwise raise an
        exception.
        '''
        socket = await _socket(self.address, loop)
        try:
            await self.protocol.handshake(socket, host, port, self.auth,
                                          loop=loop)
        except:
            socket.close()
            raise

        self.peername = socket.getpeername()
        return socket

    async def _test_connection(self, loop):
        # This can raise an exception.
        if self.protocol is SOCKS4a:
            host, port = 'www.google.com', 80
        else:
            host, port = ipaddress.IPv4Address('8.8.8.8'), 53

        socket = await self._connect(host, port, loop)
        socket.close()

    @classmethod
    async def auto_detect(cls, address, auth, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        failures = []
        for protocol in (SOCKS5, SOCKS4a, SOCKS4):
            proxy = cls(address, protocol, auth)
            try:
                await proxy._test_connection(loop)
                return proxy
            except Exception as e:
                failures.append(f'{protocol.name()} proxy detection at '
                                f'{address} failed: {e}')
        return failures

    @classmethod
    async def auto_detect_host(cls, host, ports, auth, *, loop=None):
        failures = []
        for port in ports:
            result = await cls.auto_detect((host, port), auth, loop=loop)
            if isinstance(result, cls):
                return result
            failures.extend(result)

        return failures

    async def create_connection(self, protocol_factory, host, port, *,
                                loop=None, **kwargs):
        '''Set up a connection to (host, port) through the proxy.

        The function signature mimics loop.create_connection() and
        returns the same thing.  Raises all the exceptions that may
        raise plus SocksError if something goes wrong with the proxy
        handshake.

        The caller must not pass 'server_hostname' or 'sock' arguments.
        '''
        loop = loop or asyncio.get_event_loop()
        socket = await self._connect(host, port, loop)
        if kwargs.get('ssl') is None:
            host = None
        return await loop.create_connection(
            protocol_factory, sock=socket, server_hostname=host, **kwargs)

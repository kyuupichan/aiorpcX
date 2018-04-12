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
import socket
import struct

from .util import Timeout


__all__ = ('SOCKSUserAuth', 'SOCKS4', 'SOCKS4a', 'SOCKS5', 'SOCKSProxy',
           'SOCKSError', 'SOCKSProtocolError', 'SOCKSFailure')


SOCKSUserAuth = collections.namedtuple("SOCKSUserAuth", "username password")


class SOCKSError(Exception):
    '''Base class for SOCKS exceptions.  Each raised exception will be
    an instance of a derived class.'''


class SOCKSProtocolError(SOCKSError):
    '''Raised when the proxy does not follow the SOCKS protocol'''


class SOCKSFailure(SOCKSError):
    '''Raised when the proxy refuses or fails to make a connection'''


class SOCKSBase(object):

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, loop):
        raise NotImplementedError

    @classmethod
    def name(cls):
        return cls.__name__

    @classmethod
    async def sock_recv(cls, loop, socket, n):
        result = b''
        while len(result) < n:
            data = await loop.sock_recv(socket, n - len(result))
            if not data:
                break
            result += data
        return result


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
        data = await cls.sock_recv(loop, socket, 8)
        if len(data) != 8 or data[0] != 0:
            raise SOCKSProtocolError(f'invalid {cls.name()} proxy '
                                     f'response: {data}')
        reply_code = data[1]
        if reply_code != 90:
            msg = cls.REPLY_CODES.get(
                reply_code, f'unknown {cls.name()} reply code {reply_code}')
            raise SOCKSFailure(f'{cls.name()} proxy request failed: {msg}')
        # Remaining fields ignored

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, loop):
        if not isinstance(dst_host, ipaddress.IPv4Address):
            try:
                dst_host = ipaddress.IPv4Address(dst_host)
            except ValueError:
                raise SOCKSProtocolError(
                    f'SOCKS4 requires an IPv4 address: {dst_host}') from None
        await cls._handshake(socket, dst_host, dst_port, auth, loop)


class SOCKS4a(SOCKS4):

    @classmethod
    async def handshake(cls, socket, dst_host, dst_port, auth, loop):
        if not isinstance(dst_host, (str, ipaddress.IPv4Address)):
            raise SOCKSProtocolError(
                f'SOCKS4a requires an IPv4 address or host name: {dst_host}')
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
    async def handshake(cls, socket, dst_host, dst_port, auth, loop):
        if not isinstance(dst_host, (str, ipaddress.IPv4Address,
                                     ipaddress.IPv6Address)):
            raise SOCKSProtocolError(f'SOCKS5 requires an IPv4 address, IPv6 '
                                     f'address, or host name: {dst_host}')

        # Initial handshake
        if isinstance(auth, SOCKSUserAuth):
            user_bytes = auth.username.encode()
            pwd_bytes = auth.password.encode()
            methods = [0, 2]
        else:
            methods = [0]

        greeting = b'\5' + bytes([len(methods)]) + bytes(m for m in methods)
        await loop.sock_sendall(socket, greeting)

        # Get response
        data = await cls.sock_recv(loop, socket, 2)
        if len(data) != 2 or data[0] != 5:
            raise SOCKSProtocolError(f'invalid SOCKS5 proxy response: {data}')
        if data[1] not in methods:
            raise SOCKSFailure('SOCKS5 proxy rejected authentication methods')

        # Authenticate if user-password authentication
        if data[1] == 2:
            if not 0 < len(user_bytes) < 256:
                raise SOCKSFailure(f'invalid username length: {auth.username}')
            if not 0 < len(pwd_bytes) < 256:
                raise SOCKSFailure(f'invalid password length: {auth.password}')
            auth_msg = b''.join([bytes([1, len(user_bytes)]), user_bytes,
                                 bytes([len(pwd_bytes)]), pwd_bytes])
            await loop.sock_sendall(socket, auth_msg)
            data = await cls.sock_recv(loop, socket, 2)
            if data[0] != 1 or len(data) != 2:
                raise SOCKSProtocolError(f'invalid SOCKS5 proxy auth '
                                         f'response: {data}')
            if data[1] != 0:
                raise SOCKSFailure(f'SOCKS5 proxy auth failure code: '
                                   f'{data[1]}')

        # Send connection request
        if isinstance(dst_host, ipaddress.IPv4Address):
            addr = b'\1' + dst_host.packed
        elif isinstance(dst_host, ipaddress.IPv6Address):
            addr = b'\4' + dst_host.packed
        else:
            host = dst_host.encode()
            if len(host) > 255:
                raise SOCKSFailure(f'hostname too long: {len(host)} bytes')
            addr = b'\3' + bytes([len(host)]) + host
        data = b''.join([b'\5\1\0', addr, struct.pack('>H', dst_port)])
        await loop.sock_sendall(socket, data)

        # Get response
        data = await cls.sock_recv(loop, socket, 5)
        if (len(data) != 5 or data[0] != 5 or data[2] != 0 or
                data[3] not in (1, 3, 4)):
            raise SOCKSProtocolError(f'invalid SOCKS5 proxy response: {data}')
        if data[1] != 0:
            raise SOCKSFailure(cls.ERROR_CODES.get(
                data[1], f'unknown SOCKS5 error code: {data[1]}'))
        if data[3] == 1:
            addr_len, data = 3, data[4:]   # IPv4
        elif data[3] == 3:
            addr_len, data = data[4], b''  # Hostname
        else:
            addr_len, data = 15, data[4:]  # IPv6
        remaining_len = addr_len + 2
        rest = await cls.sock_recv(loop, socket, remaining_len)
        if len(rest) != remaining_len:
            raise SOCKSProtocolError(f'short SOCKS5 proxy reply: {rest}')


class SOCKSProxy(object):

    def __init__(self, address, protocol, auth):
        '''A SOCKS proxy at an address following a SOCKS protocol.  auth is an
        authentication method to use when connecting, or None.

        address is a (host, port) pair; for IPv6 it can instead be a
        (host, port, flowinfo, scopeid) 4-tuple.
        '''
        self.address = address
        self.protocol = protocol
        self.auth = auth
        # Set on each successful connection via the proxy to the
        # result of socket.getpeername()
        self.peername = None

    def __str__(self):
        auth = 'username' if self.auth else 'none'
        return f'{self.protocol.name()} proxy at {self.address}, auth: {auth}'

    async def _connect_one(self, host, port, loop, timeout):
        '''Connect to the proxy and perform a handshake requesting a
        connection to (host, port).

        Return the open socket on success, or the exception on failure.
        '''
        sock = socket.socket()
        try:
            sock.setblocking(False)
            with Timeout(timeout, loop) as t:
                await t.run(loop.sock_connect(sock, self.address))
                await t.run(self.protocol.handshake(sock, host, port,
                                                    self.auth, loop))
            self.peername = sock.getpeername()
            return sock
        except Exception as e:
            sock.close()
            return e

    async def _connect(self, addresses, loop, timeout):
        '''Connect to the proxy and perform a handshake requesting a
        connection to each address in addresses.

        Return an (open_socket, address) pair on success.
        '''
        assert len(addresses) > 0

        exceptions = []
        for address in addresses:
            host, port = address[:2]
            sock = await self._connect_one(host, port, loop, timeout)
            if isinstance(sock, socket.socket):
                return sock, address
            exceptions.append(sock)

        strings = set(str(exc) for exc in exceptions)
        raise (exceptions[0] if len(strings) == 1 else
               OSError(f'multiple exceptions: {", ".join(strings)}'))

    async def _detect_proxy(self, loop, timeout):
        '''Return True if it appears we can connect to a SOCKS proxy,
        otherwise False.
        '''
        if self.protocol is SOCKS4a:
            host, port = 'www.google.com', 80
        else:
            host, port = ipaddress.IPv4Address('8.8.8.8'), 53

        sock = await self._connect_one(host, port, loop, timeout)
        if isinstance(sock, socket.socket):
            sock.close()
            return True

        # SOCKSFailure indicates something failed, but that we are
        # likely talking to a proxy
        return isinstance(sock, SOCKSFailure)

    @classmethod
    async def auto_detect_address(cls, address, auth, *, loop=None,
                                  timeout=5.0):
        '''Try to detect a SOCKS proxy at address using the authentication
        method (or None).  SOCKS5, SOCKS4a and SOCKS are tried in
        order.  If a SOCKS proxy is detected a SOCKSProxy object is
        returned.

        Returning a SOCKSProxy does not mean it is functioning - for
        example, it may have no network connectivity.

        If no proxy is detected return None.
        '''
        loop = loop or asyncio.get_event_loop()
        for protocol in (SOCKS5, SOCKS4a, SOCKS4):
            proxy = cls(address, protocol, auth)
            if await proxy._detect_proxy(loop, timeout):
                return proxy
        return None

    @classmethod
    async def auto_detect_host(cls, host, ports, auth, *, loop=None,
                               timeout=5.0):
        '''Try to detect a SOCKS proxy on a host on one of the ports.

        Calls auto_detect for the ports in order.  Returns SOCKS are
        tried in order; a SOCKSProxy object for the first detected
        proxy is returned.

        Returning a SOCKSProxy does not mean it is functioning - for
        example, it may have no network connectivity.

        If no proxy is detected return None.
        '''
        for port in ports:
            address = (host, port)
            proxy = await cls.auto_detect_address(address, auth,
                                                  loop=loop, timeout=timeout)
            if proxy:
                return proxy

        return None

    async def create_connection(self, protocol_factory, host, port, *,
                                resolve=False, timeout=30.0, loop=None,
                                ssl=None, family=0, proto=0, flags=0):
        '''Set up a connection to (host, port) through the proxy.

        If resolve is True then host is resolved locally with
        getaddrinfo using family, proto and flags, otherwise the proxy
        is asked to resolve host.

        The function signature is similar to loop.create_connection()
        with the same result.  The attribute _address is set on the
        protocol to the address of the successful remote connection.
        Additionally raises SOCKSError if something goes wrong with
        the proxy handshake.
        '''
        loop = loop or asyncio.get_event_loop()
        if resolve:
            infos = await loop.getaddrinfo(host, port, family=family,
                                           type=socket.SOCK_STREAM,
                                           proto=proto, flags=flags)
            addresses = [info[4] for info in infos]
        else:
            addresses = [(host, port)]

        sock, address = await self._connect(addresses, loop, timeout)

        def set_address():
            protocol = protocol_factory()
            protocol._address = address
            return protocol

        return await loop.create_connection(
            set_address, sock=sock, ssl=ssl,
            server_hostname=host if ssl else None)

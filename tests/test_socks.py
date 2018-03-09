import asyncio
import ipaddress
import os
import random
import socket
import struct

import pytest

from aiorpcx.socks import (SOCKS4, SOCKS4a, SOCKS5, SOCKSProxy,
                           SOCKSError, SOCKSUserAuth, SOCKSBase)


GDNS = (ipaddress.IPv4Address('8.8.8.8'), 53)
GCOM = ('www.google.com', 80)
IPv6 = (ipaddress.IPv6Address('::'), 80)
auth_methods = [None, SOCKSUserAuth('user', 'pass')]
SOCKS4a_addresses = (GDNS, GCOM)
SOCKS5_addresses = (GDNS, GCOM, IPv6)


class FakeServer(asyncio.Protocol):

    responses = []
    received = []

    def connection_made(self, transport):
        self.transport = transport
        FakeServer.received.clear()

    def data_received(self, data):
        FakeServer.received.append(data)
        response = self.responses.pop(0)
        self.transport.write(response)


@pytest.fixture(scope="module")
def proxy_address():
    port = 8484
    loop = asyncio.get_event_loop()
    coro = loop.create_server(FakeServer, host='localhost', port=port)
    server = loop.run_until_complete(coro)
    yield ('localhost', port)
    server.close()


@pytest.fixture(params=auth_methods)
def auth(request):
    return request.param


@pytest.fixture(params=SOCKS4a_addresses)
def addr4a(request):
    return request.param


@pytest.fixture(params=SOCKS5_addresses)
def addr5(request):
    return request.param


@pytest.fixture(params=[0, 2])
def chosen_auth(request):
    return request.param


def server_socket():
    loop = asyncio.get_event_loop()
    sock = socket.socket()
    sock.setblocking(False)
    loop.run_until_complete(loop.sock_connect(sock, FakeServer.proxy_address))
    return loop, sock


def test_server_start(proxy_address):
    FakeServer.proxy_address = proxy_address


def test_base():
    loop = asyncio.get_event_loop()
    coro = SOCKSBase.handshake(None, None, None, None, loop=loop)
    with pytest.raises(NotImplementedError):
        loop.run_until_complete(coro)


class TestSOCKS4(object):

    def assert_good(self, auth, response, addr=GDNS):
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4.handshake(socket, *addr, auth, loop=loop)
        loop.run_until_complete(coro)
        user_id = b'' if not auth else auth.username.encode()
        assert FakeServer.received == [(b'\4\1'
                                       + struct.pack('>H', addr[1])
                                       + addr[0].packed + user_id + b'\0')]

    def assert_raises(self, auth, response, text):
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4.handshake(socket, *GDNS, auth, loop=loop)
        with pytest.raises(SOCKSError) as err:
            loop.run_until_complete(coro)
        assert text in str(err.value)

    def test_short_response(self, auth):
        response = bytes([0, 90, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'invalid SOCKS4 proxy response')

    def test_request_rejected_89(self, auth):
        response = bytes([0, 89, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'unknown SOCKS4 reply code 89')

    def test_request_rejected_91(self, auth):
        response = bytes([0, 91, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'request rejected or failed')

    def test_request_rejected_92(self, auth):
        response = bytes([0, 92, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'cannot connect to identd')

    def test_request_rejected_93(self, auth):
        response = bytes([0, 93, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'report different')

    def test_response_bad_first_byte(self, auth):
        first_byte = random.randrange(1, 256)
        response = bytes([first_byte, 90, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, response, 'invalid SOCKS4 proxy response')

    def test_good_response(self, auth):
        response = bytes([0, 90]) + os.urandom(6)
        self.assert_good(auth, response)

    def test_rejects_domain(self, auth):
        response = bytes([0, 90]) + os.urandom(6)
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4.handshake(socket, *GCOM, auth, loop=loop)
        with pytest.raises(AssertionError):
            loop.run_until_complete(coro)

    def test_rejects_IPv6(self, auth):
        response = bytes([0, 90]) + os.urandom(6)
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4.handshake(socket, *IPv6, auth, loop=loop)
        with pytest.raises(AssertionError):
            loop.run_until_complete(coro)


class TestSOCKS4a(object):

    def assert_good(self, auth, addr, response):
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4a.handshake(socket, *addr, auth, loop=loop)
        loop.run_until_complete(coro)
        user_id = b'' if not auth else auth.username.encode()
        if isinstance(addr[0], str):
            host_bytes = addr[0].encode() + b'\0'
            ip_packed = b'\0\0\0\1'
        else:
            host_bytes = b''
            ip_packed = addr[0].packed
        assert FakeServer.received == [(b'\4\1'
                                        + struct.pack('>H', addr[1])
                                        + ip_packed + user_id + b'\0'
                                        + host_bytes)]

    def assert_raises(self, auth, addr, response, text):
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4a.handshake(socket, *addr, auth, loop=loop)
        with pytest.raises(SOCKSError) as err:
            loop.run_until_complete(coro)
        assert text in str(err.value)

    def test_short_response(self, auth, addr4a):
        response = bytes([0, 90, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response,
                           'invalid SOCKS4a proxy response')

    def test_request_rejected_89(self, auth, addr4a):
        response = bytes([0, 89, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response,
                           'unknown SOCKS4a reply code 89')

    def test_request_rejected_91(self, auth, addr4a):
        response = bytes([0, 91, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response,
                           'request rejected or failed')

    def test_request_rejected_92(self, auth, addr4a):
        response = bytes([0, 92, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response,
                           'cannot connect to identd')

    def test_request_rejected_93(self, auth, addr4a):
        response = bytes([0, 93, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response, 'report different')

    def test_response_bad_first_byte(self, auth, addr4a):
        first_byte = random.randrange(1, 256)
        response = bytes([first_byte, 90, 0, 0, 0, 0, 0, 0])
        self.assert_raises(auth, addr4a, response,
                           'invalid SOCKS4a proxy response')

    def test_good_response(self, auth, addr4a):
        response = bytes([0, 90]) + os.urandom(6)
        self.assert_good(auth, addr4a, response)

    def test_rejects_IPv6(self, auth):
        response = bytes([0, 90]) + os.urandom(6)
        FakeServer.responses = [response]
        loop, socket = server_socket()
        coro = SOCKS4.handshake(socket, *IPv6, auth, loop=loop)
        with pytest.raises(AssertionError):
            loop.run_until_complete(coro)


def SOCKS5_good_responses(auth, chosen_auth):
    responses = []
    responses.append(bytes([5, chosen_auth]))
    if chosen_auth == 2:
        responses.append(bytes([1, 0]))
    response = bytearray([5, 0, 0])
    addr_type = random.randrange(0, 3)
    if addr_type == 0:
        response += bytes([1]) + os.urandom(6)   # IPv4
    elif addr_type == 1:
        n = random.randrange(5, 15)
        response += bytes([3, n]) + b'a' * n + os.urandom(2)
    else:
        response += bytes([4]) + os.urandom(18) # IPv6
    responses.append(response)
    return responses


class TestSOCKS5(object):

    def assert_good(self, auth, chosen_auth, addr):
        received = []
        if auth is not None:
            received.append(bytes([5, 2, 0, 2]))
        else:
            received.append(bytes([5, 1, 0]))
        if chosen_auth == 2:
            received.append(bytes([1, len(auth.username)])
                            + auth.username.encode()
                            + bytes([len(auth.password)])
                            + auth.password.encode())
        req = bytearray([5, 1, 0])
        if isinstance(addr[0], ipaddress.IPv4Address):
            req += bytes([1]) + addr[0].packed
        elif isinstance(addr[0], str):
            req += bytes([3, len(addr[0])]) + addr[0].encode()
        else:
            req += bytes([4]) + addr[0].packed
        req += struct.pack('>H', addr[1])
        received.append(req)

        FakeServer.responses = SOCKS5_good_responses(auth, chosen_auth)

        loop, socket = server_socket()
        coro = SOCKS5.handshake(socket, *addr, auth, loop=loop)
        loop.run_until_complete(coro)
        assert FakeServer.received == received

    def assert_raises(self, auth, addr, responses, text):
        FakeServer.responses = responses.copy()
        loop, socket = server_socket()
        coro = SOCKS5.handshake(socket, *addr, auth, loop=loop)
        with pytest.raises(SOCKSError) as err:
            loop.run_until_complete(coro)
        assert text in str(err.value)

    def test_good(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        self.assert_good(auth, chosen_auth, addr5)

    def test_short_username(self, addr5):
        auth = SOCKSUserAuth(username='', password='password')
        self.assert_raises(auth, addr5, [], 'invalid username')

    def test_long_username(self, addr5):
        auth = SOCKSUserAuth(username='a' * 256, password='password')
        self.assert_raises(auth, addr5, [], 'invalid username')

    def test_short_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='')
        self.assert_raises(auth, addr5, [], 'invalid password')

    def test_long_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='p' * 256)
        self.assert_raises(auth, addr5, [], 'invalid password')

    def test_reject_auth1(self, auth, addr5):
        responses = [b'\5\xff']
        self.assert_raises(auth, addr5, responses,
                           'SOCKS5 proxy rejected authentication methods')

    def test_reject_auth2(self, auth, addr5):
        responses = [b'\5\1']
        self.assert_raises(auth, addr5, responses,
                           'SOCKS5 proxy rejected authentication methods')

    def test_bad_proto_version(self, auth, addr5):
        responses = [b'\4\0']
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_bad_short(self, auth, addr5):
        responses = [b'\5']
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_short_auth_response(self, addr5):
        auth = auth_methods[1]
        responses = [b'\5\2', b'\1']
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy auth response')

    def test_bad_auth_response2(self, addr5):
        auth = auth_methods[1]
        responses = [b'\5\2', b'\0\0']
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy auth response')

    def test_bad_auth_response3(self, addr5):
        auth = auth_methods[1]
        responses = [b'\5\2', b'\1\2']
        self.assert_raises(auth, addr5, responses,
                           'SOCKS5 proxy auth failure code')

    def test_long_host(self, auth):
        if auth is None:
            responses = [b'\5\0']
        else:
            responses = [b'\5\2', b'\1\0']
        self.assert_raises(auth, ('a' * 256, 500), responses,
                           'hostname too long')

    def test_bad_connection_request_response1(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1][0] = 4
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_bad_connection_request_response2(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1][2] = 1
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_bad_connection_request_response3(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1][3] = 2
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_bad_connection_request_response4(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1][1] = 1
        self.assert_raises(auth, addr5, responses,
                           'general SOCKS server failure')
        responses[-1][1] = 2
        self.assert_raises(auth, addr5, responses,
                           'connection not allowed by ruleset')
        responses[-1][1] = 3
        self.assert_raises(auth, addr5, responses,
                           'network unreachable')
        responses[-1][1] = 4
        self.assert_raises(auth, addr5, responses,
                           'host unreachable')
        responses[-1][1] = 5
        self.assert_raises(auth, addr5, responses,
                           'connection refused')
        responses[-1][1] = 6
        self.assert_raises(auth, addr5, responses,
                           'TTL expired')
        responses[-1][1] = 7
        self.assert_raises(auth, addr5, responses,
                           'command not supported')
        responses[-1][1] = 8
        self.assert_raises(auth, addr5, responses,
                           'address type not supported')
        responses[-1][1] = 9
        self.assert_raises(auth, addr5, responses,
                           'unknown SOCKS5 error code: 9')

    def test_short_final_reply1(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1] = responses[-1][:4]
        self.assert_raises(auth, addr5, responses,
                           'invalid SOCKS5 proxy response')

    def test_short_final_reply2(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        responses = SOCKS5_good_responses(auth, chosen_auth)
        responses[-1].pop()
        self.assert_raises(auth, addr5, responses,
                           'short SOCKS5 proxy reply')


class TestSOCKSProxy(object):

    def test_failure(self):
        coro = SOCKSProxy.auto_detect(('8.8.8.8', 53), None)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(coro)
        assert result is None

    def test_cannot_connect(self):
        coro = SOCKSProxy.auto_detect(('0.0.0.0', 53), None)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(coro)
        assert result is None

    def test_good_SOCKS5(self, auth):
        loop = asyncio.get_event_loop()
        chosen_auth = 2 if auth else 0
        FakeServer.responses = SOCKS5_good_responses(auth, chosen_auth)
        coro = SOCKSProxy.auto_detect(FakeServer.proxy_address,
                                      auth, loop=loop)
        result = loop.run_until_complete(coro)
        assert result is not None
        assert result.protocol is SOCKS5
        assert result.address == FakeServer.proxy_address
        assert result.auth == auth

    def test_good_SOCKS4(self, auth):
        loop = asyncio.get_event_loop()
        FakeServer.responses = [bytes([1, 90]),
                                bytes([0,90]) + os.urandom(6)]
        coro = SOCKSProxy.auto_detect(FakeServer.proxy_address,
                                      auth, loop=loop)
        result = loop.run_until_complete(coro)
        assert result is not None
        assert result.protocol is SOCKS4a
        assert result.address == FakeServer.proxy_address
        assert result.auth == auth

    def test_good_SOCKS4(self, auth):
        loop = asyncio.get_event_loop()
        FakeServer.responses = [bytes([1, 90]), bytes([1,90]),
                                bytes([0,90]) + os.urandom(6)]
        coro = SOCKSProxy.auto_detect(FakeServer.proxy_address,
                                      auth, loop=loop)
        result = loop.run_until_complete(coro)
        assert result is not None
        assert result.protocol is SOCKS4
        assert result.address == FakeServer.proxy_address
        assert result.auth == auth

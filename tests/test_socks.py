import asyncio
import ipaddress
import os
import struct
from random import randrange

import pytest

from aiorpcx.socks import NeedData
from aiorpcx import *


# TODO : Server tests - short and close, or just waiting no response

GCOM = ('www.google.com', 80)
IPv6_IPv6 = (ipaddress.IPv6Address('::'), 80)
IPv6_str = ('::', 80)

GDNS_IPv4 = (ipaddress.IPv4Address('8.8.8.8'), 53)
GDNS_str = ('8.8.8.8', 53)
SOCKS4_addresses = (GDNS_IPv4, GDNS_str)
SOCKS4a_addresses = (GDNS_IPv4, GDNS_str, GCOM)
SOCKS5_addresses = (GDNS_IPv4, GDNS_str, GCOM, IPv6_IPv6, IPv6_str)
auth_methods = [None, SOCKSUserAuth('user', 'pass')]


# This runs all the tests one with plain asyncio, then again with uvloop
@pytest.fixture(scope="session", autouse=True, params=(False, True))
def use_uvloop(request):
    if request.param:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

@pytest.fixture(params=SOCKS4_addresses)
def addr4(request):
    return request.param


@pytest.fixture(params=set(SOCKS5_addresses) - set(SOCKS4_addresses))
def addr4_bad(request):
    return request.param


@pytest.fixture(params=SOCKS4a_addresses)
def addr4a(request):
    return request.param


@pytest.fixture(params=[IPv6_IPv6])
def addr4a_bad(request):
    return request.param


@pytest.fixture(params=SOCKS5_addresses)
def addr5(request):
    return request.param


@pytest.fixture(params=auth_methods)
def auth(request):
    return request.param


@pytest.fixture(params=[0, 2])
def chosen_auth(request):
    return request.param


class HangingError(Exception):
    pass


class FakeResponder(object):

    def __init__(self, response):
        self.response = response
        self.messages = []

    def send(self, message):
        self.messages.append(message)

    def read(self, count):
        assert count > 0
        if count > len(self.response):
            raise HangingError
        count = randrange(0, count + 1)
        response = self.response[:count]
        self.response = self.response[count:]
        return response


def run_communication(client, server):
    while True:
        try:
            message = client.next_message()
        except NeedData as e:
            data = server.read(e.args[0])
            client.receive_data(data)
            continue
        if message is None:
            return server.messages
        server.send(message)


class TestSOCKS4(object):

    @classmethod
    def response(cls):
        return bytes([0, 0x5a]) + os.urandom(6)

    def short_bytes(self):
        result = self.response()
        return result[:randrange(0, len(result) - 1)]

    def fail_bytes(self, code):
        return bytes([0, code]) + os.urandom(6)

    def bad_first_byte(self):
        return bytes([randrange(1, 256)]) + self.response()[1:]

    def test_good_response(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.response())
        messages = run_communication(client, server)

        host, port = addr4
        user_id = b'' if not auth else auth.username.encode()
        packed = ipaddress.IPv4Address(host).packed
        data = b''.join((b'\4\1', struct.pack('>H', port),
                         packed, user_id, b'\0'))
        assert messages == [data]

    def test_short_response(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.short_bytes())
        with pytest.raises(HangingError):
            run_communication(client, server)

    def test_request_rejected_89(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.fail_bytes(89))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'unknown SOCKS4 reply code 89' in str(err.value)

    def test_request_rejected_91(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.fail_bytes(91))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.fail_bytes(92))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.fail_bytes(93))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, addr4, auth):
        client = SOCKS4(*addr4, auth)
        server = FakeResponder(self.bad_first_byte())
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS4 proxy response' in str(err.value)

    def test_rejects_others(self, addr4_bad, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS4(*addr4_bad, auth)
        assert 'SOCKS4 requires an IPv4' in str(err.value)


class TestSOCKS4a(object):

    @classmethod
    def response(cls):
        return bytes([0, 0x5a]) + os.urandom(6)

    def short_bytes(self):
        result = self.response()
        return result[:randrange(0, len(result) - 1)]

    def fail_bytes(self, code):
        return bytes([0, code]) + os.urandom(6)

    def bad_first_byte(self):
        return bytes([randrange(1, 256)]) + self.response()[1:]

    def test_good_response(self, addr4, auth):
        client = SOCKS4a(*addr4, auth)
        server = FakeResponder(self.response())
        messages = run_communication(client, server)

        host, port = addr4
        user_id = b'' if not auth else auth.username.encode()
        packed = ipaddress.IPv4Address(host).packed
        data = b''.join((b'\4\1', struct.pack('>H', port),
                         packed, user_id, b'\0'))
        assert messages == [data]

    def test_good_response(self, addr4a, auth):
        host, port = addr4a
        client = SOCKS4a(host, port, auth)
        server = FakeResponder(self.response())
        messages = run_communication(client, server)

        user_id = b'' if not auth else auth.username.encode()
        if isinstance(host, str):
            host_bytes = host.encode() + b'\0'
            ip_packed = b'\0\0\0\1'
        else:
            host_bytes = b''
            ip_packed = host.packed
        expected = b''.join((b'\4\1', struct.pack('>H', port),
                             ip_packed, user_id, b'\0', host_bytes))
        assert messages == [expected]

    def test_short_response(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.short_bytes())
        with pytest.raises(HangingError):
            run_communication(client, server)

    def test_request_rejected_89(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.fail_bytes(89))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'unknown SOCKS4a reply code 89' in str(err.value)

    def test_request_rejected_91(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.fail_bytes(91))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.fail_bytes(92))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.fail_bytes(93))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, addr4a, auth):
        client = SOCKS4a(*addr4a, auth)
        server = FakeResponder(self.bad_first_byte())
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS4a proxy response' in str(err.value)

    def test_rejects_others(self, addr4a_bad, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS4a(*addr4a_bad, auth)
        assert 'SOCKS4a requires an IPv4' in str(err.value)


class TestSOCKS5(object):

    @classmethod
    def host_packed(cls, host, port, client):
        result = b'\5\1\0' if client else b'\5\0\0'
        if isinstance(host, ipaddress.IPv4Address):
            result += bytes([1]) + host.packed
        elif isinstance(host, str):
            result += bytes([3, len(host)]) + host.encode()
        else:
            result += bytes([4]) + host.packed
        return result + struct.pack('>H', port)

    @classmethod
    def response(cls, chosen_auth, host, greeting=None, auth_response=None,
                 conn_response=None):
        if greeting is None:
            greeting = bytes([5, chosen_auth])
        if auth_response is None:
            if chosen_auth == 2:
                auth_response = b'\1\0'
            else:
                auth_response = b''
        if conn_response is None:
            conn_response = cls.host_packed(host, randrange(0, 65536), False)
        return b''.join((greeting, auth_response, conn_response))

    def test_good(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5

        client = SOCKS5(host, port, auth)
        server = FakeResponder(self.response(chosen_auth, host))
        messages = run_communication(client, server)

        expected = []
        if auth is not None:
            expected.append(bytes([5, 2, 0, 2]))
        else:
            expected.append(bytes([5, 1, 0]))
        if chosen_auth == 2:
            expected.append(bytes([1, len(auth.username)]) +
                            auth.username.encode() +
                            bytes([len(auth.password)]) +
                            auth.password.encode())
        expected.append(self.host_packed(host, port, True))
        assert messages == expected

    def test_long_host(self, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5('a' * 256, 50000, auth)
        assert 'hostname too long' in str(err.value)

    def test_rejects_others(self, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5((5, 5), 50000, auth)   # not a host
        assert 'SOCKS5 requires' in str(err.value)

    def test_short_username(self, addr5):
        auth = SOCKSUserAuth(username='', password='password')
        host, port = addr5
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(host, port, auth)
        assert 'username' in str(err.value)

    def test_long_username(self, addr5):
        auth = SOCKSUserAuth(username='u' * 256, password='password')
        host, port = addr5
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(host, port, auth)
        assert 'username' in str(err.value)

    def test_short_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='')
        host, port = addr5
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(host, port, auth)
        assert 'password has invalid length' in str(err.value)

    def test_long_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='p' * 256)
        host, port = addr5
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(host, port, auth)
        assert 'password has invalid length' in str(err.value)

    def test_auth_failure(self, addr5):
        auth = auth_methods[1]
        host, port = addr5
        client = SOCKS5(host, port, auth)
        auth_failure_bytes = self.response(2, host, auth_response = b'\1\xff')
        server = FakeResponder(auth_failure_bytes)
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'SOCKS5 proxy auth failure code: 255' in str(err.value)

    def test_reject_auth_methods(self, addr5):
        auth = auth_methods[1]
        host, port = addr5
        client = SOCKS5(host, port, auth)
        reject_methods_bytes = self.response(2, host, greeting=b'\5\xff')
        server = FakeResponder(reject_methods_bytes)
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'SOCKS5 proxy rejected authentication methods' in str(err.value)

    def test_bad_proto_version(self, auth, addr5):
        host, port = addr5
        client = SOCKS5(host, port, auth)
        bad_proto_bytes = self.response(2, host, greeting=b'\4\0')
        server = FakeResponder(bad_proto_bytes)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_short_greeting(self, auth, addr5):
        host, port = addr5
        client = SOCKS5(host, port, auth)
        short_greeting_bytes = self.response(2, host, greeting=b'\5')
        server = FakeResponder(short_greeting_bytes)
        with pytest.raises(SOCKSError):
            run_communication(client, server)

    def test_short_auth_reply(self, addr5):
        auth = auth_methods[1]
        host, port = addr5
        client = SOCKS5(host, port, auth)
        short_auth_bytes = self.response(2, host, auth_response=b'\1')
        server = FakeResponder(short_auth_bytes)
        with pytest.raises(SOCKSError):
            run_communication(client, server)

    def test_bad_auth_reply(self, addr5):
        auth = auth_methods[1]
        host, port = addr5
        client = SOCKS5(host, port, auth)
        bad_auth_bytes = self.response(2, host, auth_response=b'\0\0')
        server = FakeResponder(bad_auth_bytes)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy auth response' in str(err.value)

    def test_bad_connection_response1(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5
        client = SOCKS5(host, port, auth)
        response = bytearray(self.host_packed(host, 50000, False))
        response[0] = 4  # Should be 5
        response = self.response(chosen_auth, host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_response2(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5
        client = SOCKS5(host, port, auth)
        response = bytearray(self.host_packed(host, 50000, False))
        response[2] = 1  # Should be 0
        response = self.response(chosen_auth, host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_response3(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5
        client = SOCKS5(host, port, auth)
        response = bytearray(self.host_packed(host, 50000, False))
        response[3] = 2  # Should be 1, 3 or 4
        response = self.response(chosen_auth, host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def check_failure(self, auth, chosen_auth, addr5, code, msg):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5
        client = SOCKS5(host, port, auth)
        response = bytearray(self.host_packed(host, 50000, False))
        # Various error codes
        response[1] = code
        response = self.response(chosen_auth, host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert msg in str(err.value)

    def test_error_code_1(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           1, 'general SOCKS server failure')

    def test_error_code_2(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           2, 'connection not allowed by ruleset')

    def test_error_code_3(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           3, 'network unreachable')

    def test_error_code_4(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           4, 'host unreachable')

    def test_error_code_5(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           5, 'connection refused')

    def test_error_code_6(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           6, 'TTL expired')

    def test_error_code_7(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           7, 'command not supported')

    def test_error_code_8(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           8, 'address type not supported')

    def test_error_code_9(self, auth, chosen_auth, addr5):
        self.check_failure(auth, chosen_auth, addr5,
                           9, 'unknown SOCKS5 error code: 9')

    def test_short_req_reply(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5
        client = SOCKS5(host, port, auth)
        response = self.response(chosen_auth, host)[:-1]
        server = FakeResponder(response)
        with pytest.raises(HangingError):
            run_communication(client, server)


class FakeServer(asyncio.Protocol):

    response = None

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        if self.response:
            self.transport.write(self.response)
            self.response = None


@pytest.fixture(scope='function')
def proxy_address(event_loop, unused_tcp_port):
    coro = event_loop.create_server(FakeServer, host='localhost',
                                    port=unused_tcp_port)
    server = event_loop.run_until_complete(coro)
    yield ('localhost', unused_tcp_port)
    server.close()


class TestSOCKSProxy(object):

    @pytest.mark.asyncio
    async def test_failure(self):
        result = await SOCKSProxy.auto_detect_address(('8.8.8.8', 53), None)
        assert result is None

    @pytest.mark.asyncio
    async def test_cannot_connect(self):
        result = await SOCKSProxy.auto_detect_address(('0.0.0.0', 53), None)
        assert result is None

    @pytest.mark.asyncio
    async def test_good_SOCKS5(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth,
                                                  'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', proxy_address[1])

    @pytest.mark.asyncio
    async def test_good_SOCKS4a(self, proxy_address, auth):
        loop = asyncio.get_event_loop()
        FakeServer.response = TestSOCKS4a.response()
        result = await SOCKSProxy.auto_detect_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS4a
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', proxy_address[1])

    @pytest.mark.asyncio
    async def test_good_SOCKS4(self, proxy_address, auth):
        loop = asyncio.get_event_loop()
        FakeServer.response = TestSOCKS4.response()
        result = await SOCKSProxy.auto_detect_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        # FIXME: how to actually distinguish SOCKS4 and SOCKS4a?
        assert result.protocol is SOCKS4a
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', proxy_address[1])

    @pytest.mark.asyncio
    async def test_autodetect_host_success(self, proxy_address, auth):
        host, port = proxy_address
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth,
                                                  'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_host(host, [port], auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', port)

    @pytest.mark.asyncio
    async def test_autodetect_host_failure(self, auth):
        ports = [1, 2]
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth,
                                                  'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_host('localhost', ports, auth)
        assert result is None

    @pytest.mark.asyncio
    async def test_create_connection_connect_failure(self, auth):
        chosen_auth = 2 if auth else 0
        proxy = SOCKSProxy(('localhost', 1), SOCKS5, auth)
        with pytest.raises(OSError):
            await proxy.create_connection(None, *GCOM)

    @pytest.mark.asyncio
    async def test_create_connection_good(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth,
                                                  'wwww.apple.com')
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        _, protocol = await proxy.create_connection(ClientSession, *GCOM)
        assert protocol._address == GCOM
        assert protocol._proxy_address == proxy.peername
        assert proxy.peername == ('127.0.0.1', proxy_address[1])
        assert isinstance(protocol, ClientSession)
        await protocol.close()

    @pytest.mark.asyncio
    async def test_create_connection_resolve_good(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        FakeServer.response = TestSOCKS5.response(chosen_auth,
                                                  'wwww.apple.com')
        _, protocol = await proxy.create_connection(ClientSession, *GCOM,
                                                    resolve=True)
        assert protocol._address[0] not in (None, GCOM[0])
        assert protocol._address[1] == GCOM[1]
        assert proxy.peername == ('127.0.0.1', proxy_address[1])
        assert isinstance(protocol, ClientSession)
        await protocol.close()

    @pytest.mark.asyncio
    async def test_create_connection_resolve_bad(self, proxy_address, auth):
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        with pytest.raises(OSError):
            await proxy.create_connection(ClientSession, 'foobar.onion',
                                          80, resolve=True)

    def test_str(self):
        address = ('localhost', 80)
        p = SOCKSProxy(address, SOCKS4a, None)
        assert str(p) == f'SOCKS4a proxy at {address}, auth: none'
        address = ('www.google.com', 8080)
        p = SOCKSProxy(address, SOCKS5, auth_methods[1])
        assert str(p) == f'SOCKS5 proxy at {address}, auth: username'


def test_basic():
    assert issubclass(SOCKSProtocolError, SOCKSError)
    assert issubclass(SOCKSFailure, SOCKSError)

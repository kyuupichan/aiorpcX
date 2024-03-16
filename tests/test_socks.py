import asyncio
import ipaddress
import os
import struct
from functools import partial
from random import randrange

import pytest

from aiorpcx.socks import NeedData
from aiorpcx.rawsocket import RSTransport
from aiorpcx import (
    RPCSession, NetAddress, SOCKS5, SOCKSProxy, SOCKS4a, SOCKS4, connect_rs,
    SOCKSProtocolError, SOCKSUserAuth, SOCKSFailure, SOCKSError, SOCKSRandomAuth,
)


# TODO : Server tests - short and close, or just waiting no response

GCOM = NetAddress('www.google.com', 80)
IPv6 = NetAddress('::', 80)
GDNS = NetAddress('8.8.8.8', 53)

SOCKS4_addresses = (GDNS, )
SOCKS4a_addresses = (GDNS, GCOM)
SOCKS5_addresses = (GDNS, GCOM, IPv6)
auth_methods = [None, SOCKSUserAuth('user', 'pass')]


@pytest.fixture(params=SOCKS4_addresses)
def addr4(request):
    return request.param


@pytest.fixture(params=set(SOCKS5_addresses) - set(SOCKS4_addresses))
def addr4_bad(request):
    return request.param


@pytest.fixture(params=SOCKS4a_addresses)
def addr4a(request):
    return request.param


@pytest.fixture(params=[IPv6])
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
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.response())
        messages = run_communication(client, server)

        user_id = b'' if not auth else auth.username.encode()
        packed = addr4.host.packed
        data = b''.join((b'\4\1', struct.pack('>H', addr4.port), packed, user_id, b'\0'))
        assert messages == [data]

    def test_short_response(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.short_bytes())
        with pytest.raises(HangingError):
            run_communication(client, server)

    def test_request_rejected_89(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.fail_bytes(89))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'unknown SOCKS4 reply code 89' in str(err.value)

    def test_request_rejected_91(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.fail_bytes(91))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.fail_bytes(92))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.fail_bytes(93))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, addr4, auth):
        client = SOCKS4(addr4, auth)
        server = FakeResponder(self.bad_first_byte())
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS4 proxy response' in str(err.value)

    def test_rejects_others(self, addr4_bad, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS4(addr4_bad, auth)
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

    def test_good_response(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.response())
        messages = run_communication(client, server)

        user_id = b'' if not auth else auth.username.encode()
        if isinstance(addr4a.host, str):
            host_bytes = addr4a.host.encode() + b'\0'
            ip_packed = b'\0\0\0\1'
        else:
            host_bytes = b''
            ip_packed = addr4a.host.packed
        expected = b''.join((b'\4\1', struct.pack('>H', addr4a.port),
                             ip_packed, user_id, b'\0', host_bytes))
        assert messages == [expected]

    def test_short_response(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.short_bytes())
        with pytest.raises(HangingError):
            run_communication(client, server)

    def test_request_rejected_89(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.fail_bytes(89))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'unknown SOCKS4a reply code 89' in str(err.value)

    def test_request_rejected_91(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.fail_bytes(91))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.fail_bytes(92))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.fail_bytes(93))
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, addr4a, auth):
        client = SOCKS4a(addr4a, auth)
        server = FakeResponder(self.bad_first_byte())
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS4a proxy response' in str(err.value)

    def test_rejects_others(self, addr4a_bad, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS4a(addr4a_bad, auth)
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

        client = SOCKS5(addr5, auth)
        server = FakeResponder(self.response(chosen_auth, addr5.host))
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
        expected.append(self.host_packed(addr5.host, addr5.port, True))
        assert messages == expected

    def test_short_username(self, addr5):
        auth = SOCKSUserAuth(username='', password='password')
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(addr5, auth)
        assert 'username' in str(err.value)

    def test_long_username(self, addr5):
        auth = SOCKSUserAuth(username='u' * 256, password='password')
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(addr5, auth)
        assert 'username' in str(err.value)

    def test_short_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='')
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(addr5, auth)
        assert 'password has invalid length' in str(err.value)

    def test_long_password(self, addr5):
        auth = SOCKSUserAuth(username='username', password='p' * 256)
        with pytest.raises(SOCKSProtocolError) as err:
            SOCKS5(addr5, auth)
        assert 'password has invalid length' in str(err.value)

    def test_auth_failure(self, addr5):
        auth = auth_methods[1]
        client = SOCKS5(addr5, auth)
        auth_failure_bytes = self.response(2, addr5.host, auth_response=b'\1\xff')
        server = FakeResponder(auth_failure_bytes)
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'SOCKS5 proxy auth failure code: 255' in str(err.value)

    def test_reject_auth_methods(self, addr5):
        auth = auth_methods[1]
        client = SOCKS5(addr5, auth)
        reject_methods_bytes = self.response(2, addr5.host, greeting=b'\5\xff')
        server = FakeResponder(reject_methods_bytes)
        with pytest.raises(SOCKSFailure) as err:
            run_communication(client, server)
        assert 'SOCKS5 proxy rejected authentication methods' in str(err.value)

    def test_bad_proto_version(self, auth, addr5):
        client = SOCKS5(addr5, auth)
        bad_proto_bytes = self.response(2, addr5.host, greeting=b'\4\0')
        server = FakeResponder(bad_proto_bytes)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_short_greeting(self, auth, addr5):
        client = SOCKS5(addr5, auth)
        short_greeting_bytes = self.response(2, addr5.host, greeting=b'\5')
        server = FakeResponder(short_greeting_bytes)
        with pytest.raises(SOCKSError):
            run_communication(client, server)

    def test_short_auth_reply(self, addr5):
        auth = auth_methods[1]
        client = SOCKS5(addr5, auth)
        short_auth_bytes = self.response(2, addr5.host, auth_response=b'\1')
        server = FakeResponder(short_auth_bytes)
        with pytest.raises(SOCKSError):
            run_communication(client, server)

    def test_bad_auth_reply(self, addr5):
        auth = auth_methods[1]
        client = SOCKS5(addr5, auth)
        bad_auth_bytes = self.response(2, addr5.host, auth_response=b'\0\0')
        server = FakeResponder(bad_auth_bytes)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy auth response' in str(err.value)

    def test_bad_connection_response1(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        client = SOCKS5(addr5, auth)
        response = bytearray(self.host_packed(addr5.host, 50000, False))
        response[0] = 4  # Should be 5
        response = self.response(chosen_auth, addr5.host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_response2(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        client = SOCKS5(addr5, auth)
        response = bytearray(self.host_packed(addr5.host, 50000, False))
        response[2] = 1  # Should be 0
        response = self.response(chosen_auth, addr5.host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_response3(self, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        client = SOCKS5(addr5, auth)
        response = bytearray(self.host_packed(addr5.host, 50000, False))
        response[3] = 2  # Should be 1, 3 or 4
        response = self.response(chosen_auth, addr5.host, conn_response=response)
        server = FakeResponder(response)
        with pytest.raises(SOCKSProtocolError) as err:
            run_communication(client, server)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def check_failure(self, auth, chosen_auth, addr5, code, msg):
        if chosen_auth == 2 and auth is None:
            return
        client = SOCKS5(addr5, auth)
        response = bytearray(self.host_packed(addr5.host, 50000, False))
        # Various error codes
        response[1] = code
        response = self.response(chosen_auth, addr5.host, conn_response=response)
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
        client = SOCKS5(addr5, auth)
        response = self.response(chosen_auth, addr5.host)[:-1]
        server = FakeResponder(response)
        with pytest.raises(HangingError):
            run_communication(client, server)


class FakeServer(asyncio.Protocol):

    response = None

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.transport.write(self.response)


localhosts = ['127.0.0.1', '::1', 'localhost']


@pytest.fixture(params=localhosts)
def proxy_address(request, event_loop, unused_tcp_port):
    host = request.param
    coro = event_loop.create_server(FakeServer, host=host, port=unused_tcp_port)
    server = event_loop.run_until_complete(coro)
    yield NetAddress(host, unused_tcp_port)
    server.close()


def local_hosts(host):
    if host == 'localhost':
        return ['::1', '127.0.0.1']
    return [str(host)]


class TestSOCKSProxy(object):

    @pytest.mark.asyncio
    async def test_good_SOCKS5(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth, 'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_at_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername[0] in local_hosts(proxy_address.host)
        assert result.peername[1] == proxy_address.port

    @pytest.mark.asyncio
    async def test_good_SOCKS4a(self, proxy_address, auth):
        FakeServer.response = TestSOCKS4a.response()
        result = await SOCKSProxy.auto_detect_at_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS4a
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername[0] in local_hosts(proxy_address.host)
        assert result.peername[1] == proxy_address.port

    @pytest.mark.asyncio
    async def test_good_SOCKS4(self, proxy_address, auth):
        FakeServer.response = TestSOCKS4.response()
        result = await SOCKSProxy.auto_detect_at_address(proxy_address, auth)
        assert isinstance(result, SOCKSProxy)
        # FIXME: how to actually distinguish SOCKS4 and SOCKS4a?
        assert result.protocol is SOCKS4a
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername[0] in local_hosts(proxy_address.host)
        assert result.peername[1] == proxy_address.port

    # @pytest.mark.asyncio
    # async def test_auto_detect_at_address_failure(self):
    #     result = await SOCKSProxy.auto_detect_at_address('8.8.8.8:53', None)
    #     assert result is None

    @pytest.mark.asyncio
    async def test_auto_detect_at_address_cannot_connect(self):
        result = await SOCKSProxy.auto_detect_at_address('localhost:1', None)
        assert result is None

    @pytest.mark.asyncio
    async def test_autodetect_at_host_success(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth, 'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_at_host(proxy_address.host,
                                                      [proxy_address.port], auth)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == proxy_address
        assert result.auth == auth
        assert result.peername[0] in local_hosts(proxy_address.host)
        assert result.peername[1] == proxy_address.port

    @pytest.mark.asyncio
    async def test_autodetect_at_host_failure(self, auth):
        ports = [1, 2]
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth, 'wwww.apple.com')
        result = await SOCKSProxy.auto_detect_at_host('localhost', ports, auth)
        assert result is None

    @pytest.mark.asyncio
    async def test_create_connection_connect_failure(self, auth):
        proxy = SOCKSProxy('localhost:1', SOCKS5, auth)
        with pytest.raises(OSError):
            await proxy.create_connection(None, GCOM.host, GCOM.port)

    @pytest.mark.asyncio
    async def test_create_connection_good(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        FakeServer.response = TestSOCKS5.response(chosen_auth, 'wwww.apple.com')
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        async with connect_rs(GCOM.host, GCOM.port, proxy) as session:
            assert session.remote_address() == GCOM
            assert session.proxy() is proxy
            assert proxy.peername[0] in local_hosts(proxy_address.host)
            assert proxy.peername[1] == proxy_address.port
            assert isinstance(session, RPCSession)

    @pytest.mark.asyncio
    async def test_create_connection_resolve_good(self, proxy_address, auth):
        chosen_auth = 2 if auth else 0
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        FakeServer.response = TestSOCKS5.response(chosen_auth, 'wwww.apple.com')
        async with connect_rs(GCOM.host, GCOM.port, proxy, resolve=True) as session:
            assert session.remote_address().host not in (None, GCOM.host)
            assert session.remote_address().port == GCOM.port
            assert proxy.peername[0] in local_hosts(proxy_address.host)
            assert proxy.peername[1] == proxy_address.port
            assert isinstance(session, RPCSession)

    @pytest.mark.asyncio
    async def test_create_connection_resolve_bad(self, proxy_address, auth):
        protocol_factory = partial(RSTransport, RPCSession, 'client')
        proxy = SOCKSProxy(proxy_address, SOCKS5, auth)
        with pytest.raises(OSError):
            await proxy.create_connection(protocol_factory, 'foobar.onion', 80, resolve=True)

    def test_str(self):
        address = NetAddress('localhost', 80)
        p = SOCKSProxy(address, SOCKS4a, None)
        assert str(p) == f'SOCKS4a proxy at {address}, auth: none'
        address = NetAddress('www.google.com', 8080)
        p = SOCKSProxy(address, SOCKS5, auth_methods[1])
        assert str(p) == f'SOCKS5 proxy at {address}, auth: username'

    def test_random(self):
        auth1 = auth_methods[1]
        auth2 = SOCKSRandomAuth()

        # SOCKSRandomAuth is a SOCKSUserAuth
        assert isinstance(auth2, SOCKSUserAuth)

        # Username of SOCKSUserAuth should be constant
        user1a = auth1.username
        user1b = auth1.username
        assert user1a == user1b

        # Password of SOCKSUserAuth should be constant
        pass1a = auth1.password
        pass1b = auth1.password
        assert pass1a == pass1b

        # Username of SOCKSRandomAuth should be random
        user2a = auth2.username
        user2b = auth2.username
        assert user2a != user2b

        # Password of SOCKSRandomAuth should be random
        pass2a = auth2.password
        pass2b = auth2.password
        assert pass2a != pass2b


def test_basic():
    assert issubclass(SOCKSProtocolError, SOCKSError)
    assert issubclass(SOCKSFailure, SOCKSError)

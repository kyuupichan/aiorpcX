import asyncio
import collections
from functools import partial
import ipaddress
import os
import random
import socket
import struct
import time
import traceback

import pytest

from aiorpcx.socks import SOCKSBase
from aiorpcx.util import Timeout
from aiorpcx import *


LSS = collections.namedtuple("LSS", "loop socket server")

GDNS_IPv4 = (ipaddress.IPv4Address('8.8.8.8'), 53)
GDNS_str = ('8.8.8.8', 53)
GCOM = ('www.google.com', 80)
IPv6_IPv6 = (ipaddress.IPv6Address('::'), 80)
IPv6_str = ('::', 80)
auth_methods = [None, SOCKSUserAuth('user', 'pass')]

SOCKS4_addresses = (GDNS_IPv4, GDNS_str)
SOCKS4a_addresses = (GDNS_IPv4, GDNS_str, GCOM)
SOCKS5_addresses = (GDNS_IPv4, GDNS_str, GCOM, IPv6_IPv6, IPv6_str)

my_server = None
my_server_set = asyncio.Event()


class MyProtocol(asyncio.Protocol):
    pass


class FakeServer(asyncio.Protocol):

    def __init__(self, cs):
        self.consume_func = getattr(self, cs)
        self.buff = b''
        self.received = b''
        self.short = False

    def connection_made(self, transport):
        global my_server
        my_server = self
        my_server_set.set()
        self.transport = transport

    def data_received(self, data):
        self.received += data
        self.buff += data
        try:
            self.consume_func()
        except Exception:
            traceback.print_exc()

    def close(self, data=None):
        if data:
            self.transport.write(data)
        self.transport.close()

    def sleep(self):
        loop = asyncio.get_event_loop()
        loop.call_later(1, self.SOCKS5)

    def split_write(self, data, split):
        if split and len(data) > 1:
            n = random.randrange(1, len(data))
            part1, part2 = data[:n], data[n:]
            self.transport.write(part1)
            loop = asyncio.get_event_loop()
            # This causes a split receive and so tests they are combined
            loop.call_later(0.001, self.transport.write, part2)
        else:
            self.transport.write(data)

    def SOCKS4_good_bytes(self):
        return bytes([0, 0x5a]) + os.urandom(6)

    def SOCKS4_fail_bytes(self, code):
        return bytes([0, code]) + os.urandom(6)

    def SOCKS4_bad_first(self, a=False):
        result = self.consume_SOCKS4(a)
        if result:
            first = random.randrange(1, 256)
            self.transport.write(bytes([first])
                                 + self.SOCKS4_good_bytes()[1:])

    def SOCKS4_fail(self, code, a=False):
        result = self.consume_SOCKS4(a)
        if result:
            self.transport.write(self.SOCKS4_fail_bytes(code))

    def SOCKS4_short(self):
        self.transport.write(self.SOCKS4_good_bytes()[:-1])

    def SOCKS4_short_close(self):
        self.close(self.SOCKS4_good_bytes()[:-1])

    def SOCKS4(self, a=False, split=False):
        result = self.consume_SOCKS4(a)
        if result:
            self.split_write(self.SOCKS4_good_bytes(), split)

    def SOCKS4a(self):
        self.SOCKS4(a=True)

    def consume_SOCKS4(self, a=False):
        if self.buff[0] != 4:
            self.close()
        else:
            n = self.buff.find(0, 8)
            if n != -1:
                if self.buff[4:7] == b'\0\0\0' and self.buff[7]:
                    n = self.buff.find(0, n + 1)
                if n != -1:
                    result = self.buff[:n + 1]
                    self.buff = self.buff[n + 1:]
                    return result

    @classmethod
    def SOCKS5_response(cls):
        choices = [b'\1\1\2\3\4', b'\3\6domain', b'\4' * 17]
        return b'\5\0\0' + random.choice(choices) + os.urandom(2)

    def SOCKS5(self, chosen_auth=0, greeting=None, auth_response=b'\1\0',
               req_response=None, split=False):
        n = 2 + self.buff[1]
        consume, self.buff = self.buff[:n], self.buff[n:]
        greeting = bytes([5, chosen_auth]) if greeting is None else greeting
        if len(greeting) < 2:
            self.close(greeting)
        else:
            self.split_write(greeting, split)
        if chosen_auth == 0:
            self.consume_func = partial(self.SOCKS5_req, req_response, split)
        else:
            self.consume_func = partial(self.SOCKS5_auth, auth_response,
                                        req_response, split)

    def SOCKS5_auth(self, auth_response, req_response, split):
        uname_len = self.buff[1]
        pname_len = self.buff[2 + uname_len]
        n = 1 + uname_len + 1 + pname_len + 1
        consume, self.buff = self.buff[:n], self.buff[n:]
        if len(auth_response) < 2:
            self.close(auth_response)
        else:
            self.split_write(auth_response, split)
        self.consume_func = partial(self.SOCKS5_req, req_response, split)

    def SOCKS5_req(self, req_response, split):
        if self.buff[3] == 1:
            n = 6 + 4
        elif self.buff[3] == 3:
            n = 6 + 1 + self.buff[4]
        else:
            n = 6 + 16
        consume, self.buff = self.buff[:n], self.buff[n:]
        if req_response is None:
            req_response = self.SOCKS5_response()
        if self.short:
            self.close(req_response[:-1])
        else:
            self.split_write(req_response, split)


def server_address(cs, port):
    loop = asyncio.get_event_loop()
    coro = loop.create_server(partial(FakeServer, cs),
                              host='localhost', port=port)
    server = loop.run_until_complete(coro)
    yield ('localhost', port)
    server.close()


@pytest.fixture(scope="module")
def SOCKS4_address():
    yield from server_address('SOCKS4', 8484)


@pytest.fixture(scope="module")
def SOCKS4a_address():
    yield from server_address('SOCKS4a', 8485)


@pytest.fixture(scope="module")
def SOCKS5_address():
    yield from server_address('SOCKS5', 8486)


@pytest.fixture(scope="module")
def sleeper_address():
    yield from server_address('sleep', 8487)


@pytest.fixture(scope="module")
def fail_address():
    yield from server_address('SOCKS4_fail', 8488)


def lss(address):
    loop = asyncio.get_event_loop()
    sock = socket.socket()
    try:
        sock.setblocking(False)
        coro = loop.sock_connect(sock, address)
        loop.run_until_complete(coro)
        coro = my_server_set.wait()
        loop.run_until_complete(coro)
        my_server_set.clear()
        yield LSS(loop, sock, my_server)
    finally:
        sock.close()


@pytest.fixture
def lss4(SOCKS4_address):
    yield from lss(SOCKS4_address)


@pytest.fixture
def lss4a(SOCKS4a_address):
    yield from lss(SOCKS4a_address)


@pytest.fixture
def lss5(SOCKS5_address):
    yield from lss(SOCKS5_address)


@pytest.fixture
def lss_sleeper(sleeper_address):
    yield from lss(sleeper_address)


@pytest.fixture(params=[False, True])
def split(request):
    return request.param


@pytest.fixture(params=auth_methods)
def auth(request):
    return request.param


@pytest.fixture(params=[0, 2])
def chosen_auth(request):
    return request.param


@pytest.fixture(params=SOCKS4_addresses)
def addr4(request):
    return request.param


@pytest.fixture(params=set(SOCKS5_addresses) - set(SOCKS4_addresses))
def addr4bad(request):
    return request.param


@pytest.fixture(params=SOCKS4a_addresses)
def addr4a(request):
    return request.param


@pytest.fixture(params=[IPv6_IPv6])
def addr4abad(request):
    return request.param


@pytest.fixture(params=SOCKS5_addresses)
def addr5(request):
    return request.param


def with_timeout(timeout, loop, coro):
    with Timeout(0.05, loop) as t:
        return loop.run_until_complete(t.run(coro))


async def connecta(protocol, lss, consume_func, auth, addr, received=None):
    lss.server.consume_func = consume_func
    await protocol.handshake(lss.socket, *addr, auth, loop=lss.loop)
    assert lss.server.received == received


def connect(protocol, lss, consume_func, auth, addr, received=None):
    coro = connecta(protocol, lss, consume_func, auth, addr, received)
    lss.loop.run_until_complete(coro)


class TestSOCKS4(object):

    def test_good_response(self, lss4, auth, addr4, split):
        host, port = addr4
        user_id = b'' if not auth else auth.username.encode()
        packed = ipaddress.IPv4Address(host).packed
        received = b''.join((b'\4\1', struct.pack('>H', port),
                             packed, user_id, b'\0'))
        consumer = partial(lss4.server.SOCKS4, split=split)
        connect(SOCKS4, lss4, consumer, auth, addr4, received)

    def test_short_response(self, lss4, auth, addr4):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4, lss4, lss4.server.SOCKS4_short_close, auth, addr4)
        assert 'invalid SOCKS4 proxy response' in str(err.value)

    def test_short_waiting(self, lss4a, auth, addr4):
        coro = connecta(SOCKS4, lss4a, lss4a.server.SOCKS4_short, auth, addr4)
        with pytest.raises(asyncio.TimeoutError):
            with_timeout(0.01, lss4a.loop, coro)

    def test_request_rejected_89(self, lss4, auth, addr4):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4, lss4, partial(lss4.server.SOCKS4_fail, 89),
                    auth, addr4)
        assert 'unknown SOCKS4 reply code 89' in str(err.value)

    def test_request_rejected_91(self, lss4, auth, addr4):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4, lss4, partial(lss4.server.SOCKS4_fail, 91),
                    auth, addr4)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, lss4, auth, addr4):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4, lss4, partial(lss4.server.SOCKS4_fail, 92),
                    auth, addr4)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, lss4, auth, addr4):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4, lss4, partial(lss4.server.SOCKS4_fail, 93),
                    auth, addr4)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, lss4, auth, addr4):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4, lss4, lss4.server.SOCKS4_bad_first, auth, addr4)
        assert 'invalid SOCKS4 proxy response' in str(err.value)

    def test_rejects_others(self, lss4, auth, addr4bad):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4, lss4, None, auth, addr4bad)
        assert 'SOCKS4 requires an IPv4' in str(err.value)


class TestSOCKS4a(object):

    def test_good_response(self, lss4a, auth, addr4a, split):
        host, port = addr4a
        user_id = b'' if not auth else auth.username.encode()
        if isinstance(host, str):
            host_bytes = host.encode() + b'\0'
            ip_packed = b'\0\0\0\1'
        else:
            host_bytes = b''
            ip_packed = host.packed
        received = b''.join((b'\4\1', struct.pack('>H', port),
                             ip_packed, user_id, b'\0', host_bytes))
        consumer = partial(lss4a.server.SOCKS4, split=split, a=True)
        connect(SOCKS4a, lss4a, consumer, auth, addr4a, received)

    def test_short_response(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4a, lss4a, lss4a.server.SOCKS4_short_close,
                    auth, addr4a)
        assert 'invalid SOCKS4a proxy response' in str(err.value)

    def test_short_waiting(self, lss4a, auth, addr4a):
        coro = connecta(SOCKS4a, lss4a, lss4a.server.SOCKS4_short,
                        auth, addr4a)
        with pytest.raises(asyncio.TimeoutError):
            with_timeout(0.01, lss4a.loop, coro)

    def test_request_rejected_89(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4a, lss4a,
                    partial(lss4a.server.SOCKS4_fail, 89, a=True),
                    auth, addr4a)
        assert 'unknown SOCKS4a reply code 89' in str(err.value)

    def test_request_rejected_91(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4a, lss4a,
                    partial(lss4a.server.SOCKS4_fail, 91, a=True),
                    auth, addr4a)
        assert 'request rejected or failed' in str(err.value)

    def test_request_rejected_92(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4a, lss4a,
                    partial(lss4a.server.SOCKS4_fail, 92, a=True),
                    auth, addr4a)
        assert 'cannot connect to identd' in str(err.value)

    def test_request_rejected_93(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS4a, lss4a,
                    partial(lss4a.server.SOCKS4_fail, 93, a=True),
                    auth, addr4a)
        assert 'report different' in str(err.value)

    def test_response_bad_first_byte(self, lss4a, auth, addr4a):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4a, lss4a,
                    partial(lss4a.server.SOCKS4_bad_first, a=True),
                    auth, addr4a)
        assert 'invalid SOCKS4a proxy response' in str(err.value)

    def test_rejects_others(self, lss4a, auth, addr4abad):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS4a, lss4a, None, auth, addr4abad)
        assert 'SOCKS4a requires an IPv4' in str(err.value)


class TestSOCKS5(object):

    def test_good(self, lss5, auth, chosen_auth, addr5, split):
        if chosen_auth == 2 and auth is None:
            return
        host, port = addr5

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
        if isinstance(host, ipaddress.IPv4Address):
            req += bytes([1]) + host.packed
        elif isinstance(host, str):
            req += bytes([3, len(host)]) + host.encode()
        else:
            req += bytes([4]) + host.packed
        req += struct.pack('>H', port)
        received.append(req)
        received = b''.join(received)

        # Responses
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth,
                           req_response=None, split=split)

        connect(SOCKS5, lss5, consumer, auth, addr5, received)

    def test_short_username(self, lss5, addr5):
        auth = SOCKSUserAuth(username='', password='password')
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, partial(lss5.server.SOCKS5, chosen_auth=2),
                    auth, addr5)
        assert 'invalid username' in str(err.value)

    def test_long_username(self, lss5, addr5):
        auth = SOCKSUserAuth(username='a' * 256, password='password')
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, partial(lss5.server.SOCKS5, chosen_auth=2),
                    auth, addr5)
        assert 'invalid username' in str(err.value)

    def test_short_password(self, lss5, addr5):
        auth = SOCKSUserAuth(username='username', password='')
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, partial(lss5.server.SOCKS5, chosen_auth=2),
                    auth, addr5)
        assert 'invalid password' in str(err.value)

    def test_long_password(self, lss5, addr5):
        auth = SOCKSUserAuth(username='username', password='p' * 256)
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, partial(lss5.server.SOCKS5, chosen_auth=2),
                    auth, addr5)
        assert 'invalid password' in str(err.value)

    def test_auth_failure(self, lss5, addr5):
        auth = auth_methods[1]
        consumer = partial(lss5.server.SOCKS5, chosen_auth=2,
                           auth_response=b'\1\xff')
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'SOCKS5 proxy auth failure code: 255' in str(err.value)

    def test_reject_auth_methods(self, lss5, addr5):
        auth = auth_methods[1]
        consumer = partial(lss5.server.SOCKS5, chosen_auth=2,
                           greeting=b'\5\xff')
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'SOCKS5 proxy rejected authentication methods' in str(err.value)

    def test_bad_proto_version(self, lss5, auth, addr5):
        consumer = partial(lss5.server.SOCKS5, greeting=b'\4\0')
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_short_greeting(self, lss5, auth, addr5):
        consumer = partial(lss5.server.SOCKS5, greeting=b'\5')
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_short_auth_reply(self, lss5, addr5):
        auth = auth_methods[1]
        consumer = partial(lss5.server.SOCKS5, chosen_auth=2,
                           auth_response=b'\1')
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy auth response' in str(err.value)

    def test_bad_auth_response(self, lss5, addr5):
        auth = auth_methods[1]
        consumer = partial(lss5.server.SOCKS5, chosen_auth=2,
                           auth_response=b'\0\0')
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy auth response' in str(err.value)

    def test_long_host(self, lss5, auth, chosen_auth):
        if chosen_auth == 2 and auth is None:
            return
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth)
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, consumer, auth, ('a' * 256, 500))
        assert 'hostname too long' in str(err.value)

    def test_bad_connection_request_response1(self, lss5, auth, chosen_auth,
                                              addr5):
        if chosen_auth == 2 and auth is None:
            return
        req_response = bytearray(FakeServer.SOCKS5_response())
        req_response[0] = 4  # Should be 5
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth,
                           req_response=req_response)
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_request_response1(self, lss5, auth, chosen_auth,
                                              addr5):
        if chosen_auth == 2 and auth is None:
            return
        req_response = bytearray(FakeServer.SOCKS5_response())
        req_response[2] = 1  # Should be 0
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth,
                           req_response=req_response)
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def test_bad_connection_request_response1(self, lss5, auth, chosen_auth,
                                              addr5):
        if chosen_auth == 2 and auth is None:
            return
        req_response = bytearray(FakeServer.SOCKS5_response())
        req_response[3] = 2  # Should be 1, 3 or 4
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth,
                           req_response=req_response)
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert 'invalid SOCKS5 proxy response' in str(err.value)

    def check_failure(self, lss5, auth, chosen_auth, addr5, code, msg):
        if chosen_auth == 2 and auth is None:
            return
        # Various error codes
        req_response = bytearray(FakeServer.SOCKS5_response())
        req_response[1] = code
        consumer = partial(lss5.server.SOCKS5, chosen_auth=chosen_auth,
                           req_response=req_response)
        with pytest.raises(SOCKSFailure) as err:
            connect(SOCKS5, lss5, consumer, auth, addr5)
        assert msg in str(err.value)

    def test_error_code_1(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           1, 'general SOCKS server failure')

    def test_error_code_2(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           2, 'connection not allowed by ruleset')

    def test_error_code_3(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           3, 'network unreachable')

    def test_error_code_4(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           4, 'host unreachable')

    def test_error_code_5(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           5, 'connection refused')

    def test_error_code_6(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           6, 'TTL expired')

    def test_error_code_7(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           7, 'command not supported')

    def test_error_code_8(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           8, 'address type not supported')

    def test_error_code_9(self, lss5, auth, chosen_auth, addr5):
        self.check_failure(lss5, auth, chosen_auth, addr5,
                           9, 'unknown SOCKS5 error code: 9')

    def test_short_req_reply(self, lss5, auth, chosen_auth, addr5):
        if chosen_auth == 2 and auth is None:
            return
        lss5.server.short = True
        with pytest.raises(SOCKSProtocolError) as err:
            self.test_good(lss5, auth, chosen_auth, addr5, False)
        assert 'short SOCKS5 proxy reply' in str(err.value)

    def test_rejects_others(self, lss5, auth):
        with pytest.raises(SOCKSProtocolError) as err:
            connect(SOCKS5, lss5, None, auth, (5, 5))   # Not an address
        assert 'SOCKS5 requires' in str(err.value)


class TestSOCKSProxy(object):

    def test_failure(self):
        coro = SOCKSProxy.auto_detect_address(('8.8.8.8', 53), None)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(coro)
        assert result is None

    def test_cannot_connect(self):
        coro = SOCKSProxy.auto_detect_address(('0.0.0.0', 53), None)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(coro)
        assert result is None

    def test_good_SOCKS5(self, SOCKS5_address, auth):
        chosen_auth = 2 if auth else 0
        loop = asyncio.get_event_loop()
        coro = SOCKSProxy.auto_detect_address(SOCKS5_address, auth, loop=loop)
        result = loop.run_until_complete(coro)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == SOCKS5_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', SOCKS5_address[1])

    def test_good_SOCKS4a(self, SOCKS4a_address, auth):
        loop = asyncio.get_event_loop()
        coro = SOCKSProxy.auto_detect_address(SOCKS4a_address, auth, loop=None)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(coro)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS4a
        assert result.address == SOCKS4a_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', SOCKS4a_address[1])

    # def test_good_SOCKS4(self, SOCKS4_address, auth):
    #     loop = asyncio.get_event_loop()
    #    coro = SOCKSProxy.auto_detect_address(SOCKS4_address, auth, loop=None)
    #     loop = asyncio.get_event_loop()
    #     result = loop.run_until_complete(coro)
    #     assert isinstance(result, SOCKSProxy)
    #     assert result.protocol is SOCKS4
    #     assert result.address == SOCKS4_address
    #     assert result.auth == auth
    #     assert result.peername == ('127.0.0.1', SOCKS4_address[1])

    def test_autodetect_address_timeout(self, sleeper_address, auth):
        loop = asyncio.get_event_loop()
        coro = SOCKSProxy.auto_detect_address(sleeper_address, auth,
                                              timeout=0.01)
        t = time.time()
        result = loop.run_until_complete(coro)
        assert result is None
        assert time.time() - t < 0.1

    def test_autodetect_host_success(self, SOCKS5_address, auth):
        host, port = SOCKS5_address
        chosen_auth = 2 if auth else 0
        loop = asyncio.get_event_loop()
        coro = SOCKSProxy.auto_detect_host(host, [port], auth, timeout=0.5)
        result = loop.run_until_complete(coro)
        assert isinstance(result, SOCKSProxy)
        assert result.protocol is SOCKS5
        assert result.address == SOCKS5_address
        assert result.auth == auth
        assert result.peername == ('127.0.0.1', port)

    def test_autodetect_host_failure(self, auth):
        ports = [1, 2]
        chosen_auth = 2 if auth else 0
        loop = asyncio.get_event_loop()
        coro = SOCKSProxy.auto_detect_host('localhost', ports, auth,
                                           timeout=0.5)
        result = loop.run_until_complete(coro)
        assert result is None

    def test_autodetect_host_timeout(self, sleeper_address, auth):
        loop = asyncio.get_event_loop()
        port = sleeper_address[1]
        coro = SOCKSProxy.auto_detect_host('localhost', [port], auth,
                                           loop=None, timeout=0.01)
        t = time.time()
        result = loop.run_until_complete(coro)
        assert result is None
        assert time.time() - t < 0.1

    def test_create_connection_connect_failure(self, auth):
        loop = asyncio.get_event_loop()
        proxy = SOCKSProxy(('localhost', 1), SOCKS5, auth)
        coro = proxy.create_connection(None, *GCOM, loop=loop)
        with pytest.raises(OSError):
            loop.run_until_complete(coro)

    def test_create_connection_socks_failure(self, fail_address, auth):
        loop = asyncio.get_event_loop()
        proxy = SOCKSProxy(fail_address, SOCKS4, auth)
        coro = proxy.create_connection(None, *GCOM, loop=loop)
        with pytest.raises(SOCKSProtocolError):
            loop.run_until_complete(coro)

    def test_create_connection_timeout(self, sleeper_address, auth):
        loop = asyncio.get_event_loop()
        proxy = SOCKSProxy(sleeper_address, SOCKS5, auth)
        coro = proxy.create_connection(MyProtocol, *GCOM, timeout=0.01)
        with pytest.raises(asyncio.TimeoutError):
            loop.run_until_complete(coro)

    def test_create_connection_good(self, SOCKS5_address, auth):
        loop = asyncio.get_event_loop()
        proxy = SOCKSProxy(SOCKS5_address, SOCKS5, auth)
        coro = proxy.create_connection(MyProtocol, *GCOM, loop=loop,
                                       timeout=0.05)
        transport, protocol = loop.run_until_complete(coro)
        assert proxy.host == GCOM[0]
        assert proxy.port == GCOM[1]
        assert proxy.peername == ('127.0.0.1', SOCKS5_address[1])
        assert isinstance(protocol, MyProtocol)
        transport.close()

    def test_create_connection_resolve_good(self, SOCKS5_address, auth):
        loop = asyncio.get_event_loop()
        proxy = SOCKSProxy(('localhost', SOCKS5_address[1]), SOCKS5, auth)
        loop = asyncio.get_event_loop()
        coro = proxy.create_connection(MyProtocol, *GCOM, resolve=True)
        transport, protocol = loop.run_until_complete(coro)
        assert proxy.host not in (None, GCOM[0])
        assert proxy.port == GCOM[1]
        assert proxy.peername == ('127.0.0.1', SOCKS5_address[1])
        assert isinstance(protocol, MyProtocol)
        transport.close()

    def test_create_connection_resolve_bad(self, SOCKS5_address, auth):
        proxy = SOCKSProxy(('localhost', SOCKS5_address[1]), SOCKS5, auth)
        loop = asyncio.get_event_loop()
        coro = proxy.create_connection(MyProtocol, 'foobar.onion', 80,
                                       resolve=True)
        with pytest.raises(OSError):
            loop.run_until_complete(coro)


def test_str():
    address = ('localhost', 80)
    p = SOCKSProxy(address, SOCKS4a, None)
    assert str(p) == f'SOCKS4a proxy at {address}, auth: none'
    address = ('www.google.com', 8080)
    p = SOCKSProxy(address, SOCKS5, auth_methods[1])
    assert str(p) == f'SOCKS5 proxy at {address}, auth: username'


def test_basic():
    assert isinstance(SOCKSProtocolError(), SOCKSError)
    assert isinstance(SOCKSFailure(), SOCKSError)
    loop = asyncio.get_event_loop()
    coro = SOCKSBase.handshake(None, None, None, None, loop=loop)
    with pytest.raises(NotImplementedError):
        loop.run_until_complete(coro)

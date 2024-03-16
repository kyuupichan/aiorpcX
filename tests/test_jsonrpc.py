import sys

from itertools import combinations, count
import json
import pytest

from aiorpcx import (
    Request, handler_invocation, Queue, ignore_after, ProtocolError,
    TaskGroup, Batch, Notification, JSONRPCConnection, JSONRPC, JSONRPCv1, JSONRPCv2, JSONRPCLoose,
    RPCError, JSONRPCAutoDetect, timeout_after, Event,
)
from aiorpcx.jsonrpc import Response, CodeMessageError
from util import assert_RPCError, assert_ProtocolError
from random import shuffle


def assert_is_error_response(item, text, code):
    assert isinstance(item, Response)
    item = item.result
    assert isinstance(item, RPCError)
    assert item.code == code
    assert text in item.message


def assert_is_request(item, method, args):
    assert isinstance(item, Request)
    assert item.method == method
    assert item.args == args


def assert_is_notification(item, method, args):
    assert isinstance(item, Notification)
    assert item.method == method
    assert item.args == args


def assert_is_good_response(item, result):
    assert isinstance(item, Response)
    assert item.result == result


def canonical_message(protocol, payload):
    payload = payload.copy()
    if protocol == JSONRPCv2:
        payload['jsonrpc'] = '2.0'
    elif protocol == JSONRPCv1:
        if 'method' in payload and 'params' not in payload:
            payload['params'] = []
        if 'error' in payload and 'result' not in payload:
            payload['result'] = None
        if 'result' in payload and 'error' not in payload:
            payload['error'] = None
    return json.dumps(payload).encode()


def payload_to_item(protocol, payload):
    return protocol.message_to_item(canonical_message(protocol, payload))


@pytest.fixture(params=(JSONRPCv1, JSONRPCv2, JSONRPCLoose, JSONRPCAutoDetect))
def protocol(request):
    return request.param


@pytest.fixture(params=(JSONRPCv1, JSONRPCv2, JSONRPCLoose))
def protocol_no_auto(request):
    return request.param


@pytest.fixture(params=(JSONRPCLoose, JSONRPCv2))
def batch_protocol(request):
    return request.param


# MISC


def test_abstract():
    class MyProtocol(JSONRPC):
        pass

    with pytest.raises(NotImplementedError):
        MyProtocol._message_id({}, True)

    with pytest.raises(NotImplementedError):
        MyProtocol._request_args({})


def test_exception_is_hashable():
    hash(CodeMessageError(0, ''))  # see if raises


# ENCODING


def test_parse_errors(protocol_no_auto):
    protocol = protocol_no_auto
    # Bad encoding
    message = b'123\xff'

    with pytest.raises(ProtocolError) as e:
        protocol.message_to_item(message)
    assert e.value.code == JSONRPC.PARSE_ERROR
    assert 'messages must be encoded in UTF-8' in e.value.message
    assert b'"id":null' in e.value.error_message

    # Bad JSON
    message = b'{"foo",}'
    with pytest.raises(ProtocolError) as e:
        protocol.message_to_item(message)
    assert e.value.code == JSONRPC.PARSE_ERROR
    assert 'invalid JSON' in e.value.message
    assert b'"id":null' in e.value.error_message

    messages = [b'2', b'"foo"', b'2.78']
    for message in messages:
        with pytest.raises(ProtocolError) as e:
            protocol.message_to_item(message)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'must be a dictionary' in e.value.message
        assert b'"id":null' in e.value.error_message


# Requests


def test_request():
    for bad_method in (None, 2, b'', [2], {}):
        with pytest.raises(ProtocolError) as e:
            Request(bad_method, [])
        assert e.value.code == JSONRPC.METHOD_NOT_FOUND
        assert 'must be a string' in e.value.message

        with pytest.raises(ProtocolError) as e:
            Notification(bad_method, [])
        assert e.value.code == JSONRPC.METHOD_NOT_FOUND
        assert 'must be a string' in e.value.message

    for bad_args in (2, "foo", None, False):
        with pytest.raises(ProtocolError) as e:
            Request('method', bad_args)
        assert e.value.code == JSONRPC.INVALID_ARGS
        assert 'arguments' in e.value.message

        with pytest.raises(ProtocolError) as e:
            Notification('', bad_args)
        assert e.value.code == JSONRPC.INVALID_ARGS
        assert 'arguments' in e.value.message

    assert repr(Request('m', [2])) == "Request('m', [2])"
    assert repr(Request('m', [])) == "Request('m', [])"
    assert repr(Request('m', {})) == "Request('m', {})"
    assert repr(Request('m', {"a": 0})) == "Request('m', {'a': 0})"


def test_Batch():
    b = Batch([Request("m", []), Request("n", [])])
    assert repr(b) == "Batch(2 items)"
    with pytest.raises(ProtocolError) as e:
        Batch([Request('m', []), Response(2)])
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'homogeneous' in e.value.message

    with pytest.raises(ProtocolError) as e:
        Batch([b])
    assert e.value.code == JSONRPC.INVALID_REQUEST

    with pytest.raises(ProtocolError) as e:
        Batch(2)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'must be a list' in e.value.message

    with pytest.raises(ProtocolError) as e:
        Batch((x for x in (1, )))
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'must be a list' in e.value.message
    assert b[:2] == b.items[:2]


def test_JSONRPCv1_ill_formed():
    protocol = JSONRPCv1
    # Named arguments
    payloads = [
        {"method": "a", "params": {}, "id": 123},
        {"method": "a", "params": {"a": 1, "b": "c"}, "id": 123},
    ]
    for payload in payloads:
        message = canonical_message(protocol, payload)
        with pytest.raises(ProtocolError) as e:
            protocol.message_to_item(message)
        assert e.value.code == JSONRPC.INVALID_ARGS
        assert 'invalid request arguments' in e.value.message
        assert b'123' in e.value.error_message

    request = Request('a', {"a": 1})
    with pytest.raises(ProtocolError) as e:
        protocol.request_message(request, 0)
    assert e.value.code == JSONRPC.INVALID_ARGS
    assert 'named arguments' in e.value.message

    # Requires an ID
    payload = {"method": "a", "params": [1, "foo"]}
    message = canonical_message(protocol, payload)
    with pytest.raises(ProtocolError) as e:
        protocol.message_to_item(message)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'no "id"' in e.value.message
    assert b'"id":null' in e.value.error_message


def test_bad_requests(protocol_no_auto):
    protocol = protocol_no_auto
    payload = {"method": 2, "params": 3, "id": 0}

    with pytest.raises(ProtocolError) as e:
        payload_to_item(protocol, payload)
    assert e.value.code == JSONRPC.INVALID_ARGS
    assert 'invalid request arguments' in e.value.message
    assert b'"id":0' in e.value.error_message


def test_good_requests(protocol_no_auto):
    protocol = protocol_no_auto
    payload = {"method": "", "id": -1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == -1
    assert_is_request(item, '', [])
    # recommended against in the spec, but valid
    payload = {"method": "", "id": None}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id is None
    assert_is_notification(item, '', [])
    # recommended against in the spec, but valid
    payload = {"method": "", "id": 2.5}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 2.5
    assert_is_request(item, '', [])
    payload = {"method": "a", "id": 0}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 0
    assert_is_request(item, 'a', [])
    payload = {"method": "a", "params": [], "id": ""}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == ""
    assert_is_request(item, 'a', [])
    # Rest do not apply to JSONRPCv1; tested to fail elsewhere
    if protocol == JSONRPCv1:
        return
    payload = {"method": "a", "params": [1, "foo"]}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_notification(item, 'a', [1, "foo"])
    payload = {"method": "a", "params": {}}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_notification(item, 'a', {})
    payload = {"method": "a", "params": {"a": 1, "b": "c"}}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_notification(item, 'a', {"a": 1, "b": "c"})
    payload = {"method": "a", "params": {}, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_request(item, 'a', {})
    payload = {"method": "a", "params": {"a": 1, "b": "c"}, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_request(item, 'a', {"a": 1, "b": "c"})


# # RESPONSES


def test_response_bad(protocol_no_auto):
    protocol = protocol_no_auto
    # Missing ID
    payload = {"result": 2}
    with pytest.raises(ProtocolError) as e:
        payload_to_item(protocol, payload)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'no "id"' in e.value.message
    assert not e.value.error_message
    assert e.value.response_msg_id is None

    payload = {"error": {"code": 2, "message": "try harder"}}
    with pytest.raises(ProtocolError) as e:
        payload_to_item(protocol, payload)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'no "id"' in e.value.message
    assert not e.value.error_message
    assert e.value.response_msg_id is None

    # Result and error
    if protocol != JSONRPCv1:
        payload = {"result": 0, "error": {"code": 2, "message": ""},
                   "id": 0}
        with pytest.raises(ProtocolError) as e:
            payload_to_item(protocol, payload)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'both "result" and' in e.value.message
        assert e.value.response_msg_id == 0
        assert not e.value.error_message

        payload = {"result": 1, "error": None, "id": 0}
        if protocol == JSONRPCLoose:
            payload_to_item(protocol, payload)
        else:
            with pytest.raises(ProtocolError) as e:
                payload_to_item(protocol, payload)
            assert e.value.code == JSONRPC.INVALID_REQUEST
            assert 'both "result" and' in e.value.message
            assert e.value.response_msg_id == 0
            assert not e.value.error_message

        # No result, also no error
        payload = {"foo": 1, "id": 1}
        with pytest.raises(ProtocolError) as e:
            payload_to_item(protocol, payload)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'neither "result" nor' in e.value.message
        assert e.value.response_msg_id == 1
        assert not e.value.error_message

        # Bad ID
        payload = {"result": 2, "id": []}
        with pytest.raises(ProtocolError) as e:
            payload_to_item(protocol, payload)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'invalid "id"' in e.value.message
        assert e.value.response_msg_id is None
        assert not e.value.error_message


def test_response_good(protocol_no_auto):
    protocol = protocol_no_auto
    # Integer
    payload = {"result": 2, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_good_response(item, 2)
    # Float
    payload = {"result": 2.1, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, 2.1)
    # String
    payload = {"result": "f", "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, "f")
    # None
    payload = {"result": None, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_good_response(item, None)
    # Array
    payload = {"result": [1, 2], "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, [1, 2])
    # Dictionary
    payload = {"result": {"a": 1}, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, {"a": 1})
    # Additional junk
    payload = {"result": 2, "id": 1, "junk": 0}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, 2)


def test_JSONRPCv2_response_error_bad():
    payloads = [
        {"error": 2, "id": 1},
        {"error": "bar", "id": 1},
        {"error": {"code": 1}, "id": 1},
        {"error": {"message": "foo"}, "id": 1},
        {"error": {"code": None, "message": "m"}, "id": 1},
        {"error": {"code": 1, "message": None}, "id": 1},
        {"error": {"code": "s", "message": "error"}, "id": 1},
        {"error": {"code": 2, "message": 2}, "id": 1},
        {"error": {"code": 2.5, "message": "bar"}, "id": 1},
    ]
    protocol = JSONRPCv2
    for payload in payloads:
        with pytest.raises(ProtocolError) as e:
            payload_to_item(protocol, payload)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'ill-formed' in e.value.message
        assert e.value.response_msg_id == 1
        assert not e.value.error_message


def test_JSONRPCLoose_responses():
    protocol = JSONRPCLoose
    payload = {"result": 0, "error": None, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_good_response(item, 0)
    payload = {"result": None, "error": None, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_good_response(item, None)
    payload = {"result": None, "error": 2, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert_is_error_response(item, 'no error message', 2)
    payload = {"result": 4, "error": 2, "id": 1}
    with pytest.raises(ProtocolError) as e:
        payload_to_item(protocol, payload)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'both' in e.value.message
    assert e.value.response_msg_id == 1
    assert not e.value.error_message


def test_JSONRPCv2_required_jsonrpc():
    protocol = JSONRPCv2
    payloads = [
        {"error": {"code": 2, "message": "bar"}, "id": 1},
        {"result": 1, "id": 2},
    ]
    for payload in payloads:
        with pytest.raises(ProtocolError) as e:
            message = json.dumps(payload).encode()
            protocol.message_to_item(message)
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'jsonrpc' in e.value.message
        assert not e.value.error_message

    payload = {"method": "f"}
    with pytest.raises(ProtocolError) as e:
        message = json.dumps(payload).encode()
        protocol.message_to_item(message)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'jsonrpc' in e.value.message
    # Respond to ill-formed "notification"
    assert b'"id":null' in e.value.error_message

    payload = {"method": "f", "id": 0}
    with pytest.raises(ProtocolError) as e:
        message = json.dumps(payload).encode()
        protocol.message_to_item(message)
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'jsonrpc' in e.value.message
    assert b'jsonrpc' in e.value.error_message
    assert b'"id":0' in e.value.error_message


def test_JSONRPCv1_errors():
    protocol = JSONRPCv1
    payloads = [
        {"error": 2, "id": 1},
        {"error": "bar", "id": 1},
        {"error": {"code": 1}, "id": 1},
        {"error": {"message": "foo"}, "id": 1},
        {"error": {"code": None, "message": "m"}, "id": 1},
        {"error": {"code": 1, "message": None}, "id": 1},
        {"error": {"code": "s", "message": "error"}, "id": 1},
        {"error": {"code": 2, "message": 2}, "id": 1},
        {"error": {"code": 2.5, "message": "bar"}, "id": 1},
    ]
    for payload in payloads:
        item, request_id = payload_to_item(protocol, payload)

        code = protocol.ERROR_CODE_UNAVAILABLE
        error = payload['error']
        message = 'no error message provided'
        if isinstance(error, str):
            message = error
        elif isinstance(error, int):
            code = error
        elif isinstance(error, dict):
            if isinstance(error.get('message'), str):
                message = error['message']
            if isinstance(error.get('code'), int):
                code = error['code']
        assert request_id == 1
        assert_is_error_response(item, message, code)

    payload = {"error": 2, "id": 1}
    with pytest.raises(ProtocolError) as e:
        protocol.message_to_item(json.dumps(payload).encode())
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert '"result" and' in e.value.message
    assert not e.value.error_message

    payload = {"result": 4, "error": 2, "id": 1}
    with pytest.raises(ProtocolError) as e:
        protocol.message_to_item(json.dumps(payload).encode())
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert '"result" and' in e.value.message
    assert not e.value.error_message


def test_response_error_good(protocol_no_auto):
    protocol = protocol_no_auto
    payload = {"error": {"code": 5, "message": "bar"}, "id": 1}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == 1
    assert_is_error_response(item, 'bar', 5)
    payload = {"error": {"code": 3, "message": "try again"}, "id": "a",
               "jnk": 0}
    item, request_id = payload_to_item(protocol, payload)
    assert request_id == "a"
    assert_is_error_response(item, 'again', 3)


# BATCHES


def test_batch_not_allowed(protocol):
    if not protocol.allow_batches:
        with pytest.raises(ProtocolError) as e:
            protocol.message_to_item(b'[]')
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'dictionary' in e.value.message
        assert b'"id":null' in e.value.error_message

        batch = Batch([Request('', [])])
        with pytest.raises(ProtocolError) as e:
            protocol.batch_message(batch, {1})
        assert e.value.code == JSONRPC.INVALID_REQUEST
        assert 'permit batch' in e.value.message
        assert not e.value.error_message


def test_empty_batch():
    with pytest.raises(ProtocolError) as e:
        Batch([])
    assert e.value.code == JSONRPC.INVALID_REQUEST
    assert 'empty' in e.value.message
    assert not e.value.error_message


# Message contruction


def test_batch_message_from_parts(protocol):
    with pytest.raises(ProtocolError) as e:
        protocol.batch_message_from_parts([])
    assert 'empty' in e.value.message
    assert protocol.batch_message_from_parts([b'1']) == b'[1]'
    assert protocol.batch_message_from_parts([b'1', b'2']) == b'[1, 2]'
    # An empty part is not valid, but anyway.
    assert (protocol.batch_message_from_parts([b'1', b'', b'[3]']) ==
            b'[1, , [3]]')


def test_encode_payload(protocol):
    assert protocol.encode_payload(2) == b'2'
    assert protocol.encode_payload([2, 3]) == b'[2,3]'
    assert protocol.encode_payload({"a": 1}) == b'{"a":1}'
    assert protocol.encode_payload(True) == b'true'
    assert protocol.encode_payload(False) == b'false'
    assert protocol.encode_payload(None) == b'null'
    assert protocol.encode_payload("foo") == b'"foo"'
    with pytest.raises(ProtocolError) as e:
        protocol.encode_payload(b'foo')
    assert e.value.code == JSONRPC.INTERNAL_ERROR
    assert 'JSON' in e.value.message


def test_JSONRPCv2_and_JSONRPCLoose_request_messages():
    requests = [
        (Request('foo', []), 2,
         {"jsonrpc": "2.0", "method": "foo", "id": 2}),
        (Request('foo', ()), 2,
         {"jsonrpc": "2.0", "method": "foo", "id": 2}),
        (Request('foo', {}), 2,
         {"jsonrpc": "2.0", "params": {}, "method": "foo", "id": 2}),
        (Request('foo', (1, 2)), 2,
         {"jsonrpc": "2.0", "method": "foo", "params": [1, 2], "id": 2}),
        (Request('foo', [1, 2]), 2,
         {"jsonrpc": "2.0", "method": "foo", "params": [1, 2], "id": 2}),
        (Request('foo', {"bar": 3, "baz": "bat"}), "it",
         {"jsonrpc": "2.0", "method": "foo",
          "params": {"bar": 3, "baz": "bat"}, "id": "it"}),
    ]

    notifications = [
        (Notification('foo', []),
         {"jsonrpc": "2.0", "method": "foo"}),
    ]

    batches = [
        (Batch([
            Request('foo', []),
            Notification('bar', [2]),
            Request('baz', {'a': 1}),
        ]), [2, 3], [
            {"jsonrpc": "2.0", "method": "foo", "id": 2},
            {"jsonrpc": "2.0", "method": "bar", "params": [2]},
            {"jsonrpc": "2.0", "method": "baz", "params": {'a': 1}, "id": 3},
        ]),
    ]

    responses = [
        ('foo', "it",
         {"jsonrpc": "2.0", "result": "foo", "id": "it"}),
        (2, "it",
         {"jsonrpc": "2.0", "result": 2, "id": "it"}),
        (None, -2,
         {"jsonrpc": "2.0", "result": None, "id": -2}),
        ([1, 2], -1,
         {"jsonrpc": "2.0", "result": [1, 2], "id": -1}),
        ({"kind": 1}, 0,
         {"jsonrpc": "2.0", "result": {"kind": 1}, "id": 0}),
        (RPCError(3, "j"), 1,
         {"jsonrpc": "2.0", "error": {"code": 3, "message": "j"}, "id": 1}),
    ]

    for protocol in [JSONRPCv2, JSONRPCLoose]:
        for item, request_id, payload in requests:
            binary = protocol.request_message(item, request_id)
            test_payload = json.loads(binary.decode())
            assert test_payload == payload

        for item, payload in notifications:
            binary = protocol.notification_message(item)
            test_payload = json.loads(binary.decode())
            assert test_payload == payload

        for result, request_id, payload in responses:
            binary = protocol.response_message(result, request_id)
            test_payload = json.loads(binary.decode())
            assert test_payload == payload

        for batch, request_ids, payload in batches:
            binary = protocol.batch_message(batch, request_ids)
            test_payload = json.loads(binary.decode())
            assert test_payload == payload


def test_JSONRPCv1_messages():
    requests = [
        (Request('foo', []), 2,
         {"method": "foo", "params": [], "id": 2}),
        (Request('foo', [1, 2]), "s",
         {"method": "foo", "params": [1, 2], "id": "s"}),
        (Request('foo', [1, 2]), ["x"],
         {"method": "foo", "params": [1, 2], "id": ["x"]}),
    ]
    notifications = [
        (Notification('foo', []),
         {"method": "foo", "params": [], "id": None}),
    ]
    responses = [
        ('foo', "it",
         {"result": "foo", "error": None, "id": "it"}),
        (2, "it",
         {"result": 2, "error": None, "id": "it"}),
        (None, -2,
         {"result": None, "error": None, "id": -2}),
        ([1, 2], -1,
         {"result": [1, 2], "error": None, "id": -1}),
        ({"kind": 1}, [1],
         {"result": {"kind": 1}, "error": None, "id": [1]}),
        (RPCError(3, "j"), 1,
         {"result": None, "error": {"code": 3, "message": "j"}, "id": 1}),
    ]

    protocol = JSONRPCv1
    for item, request_id, payload in requests:
        binary = protocol.request_message(item, request_id)
        test_payload = json.loads(binary.decode())
        assert test_payload == payload

    for item, payload in notifications:
        binary = protocol.notification_message(item)
        test_payload = json.loads(binary.decode())
        assert test_payload == payload

    for result, request_id, payload in responses:
        binary = protocol.response_message(result, request_id)
        test_payload = json.loads(binary.decode())
        assert test_payload == payload

    with pytest.raises(TypeError):
        protocol.request_message(Request('foo', {}, 2))
    with pytest.raises(TypeError):
        protocol.request_message(Request('foo', {"x": 1}, 2))


def test_protocol_detection():
    bad_syntax_tests = [b'', b'\xf5', b'{"method":']

    tests = [
        (b'[]', JSONRPCLoose),
        (b'""', JSONRPCLoose),
        (b'{"jsonrpc": "2.0"}', JSONRPCv2),
        (b'{"jsonrpc": "1.0"}', JSONRPCv1),
        # No ID
        (b'{"method": "part"}', JSONRPCLoose),
        (b'{"error": 2}', JSONRPCLoose),
        (b'{"result": 3}', JSONRPCLoose),
        # Just ID
        (b'{"id": 2}', JSONRPCLoose),
        # Result or error alone
        (b'{"result": 3, "id":2}', JSONRPCLoose),
        (b'{"error": 3, "id":2}', JSONRPCLoose),
        (b'{"result": 3, "error": null, "id":2}', JSONRPCv1),
        # Method with or without params
        (b'{"method": "foo", "id": 1}', JSONRPCLoose),
        (b'{"method": "foo", "params": [], "id":2}', JSONRPCLoose),
    ]

    for message in bad_syntax_tests:
        with pytest.raises(ProtocolError):
            JSONRPCAutoDetect.detect_protocol(message)

    for message, answer in tests:
        result = JSONRPCAutoDetect.detect_protocol(message)
        assert answer == result

    test_by_answer = {}
    for message, answer in tests:
        test_by_answer[answer] = message

    # Batches.  Test every combination...
    bm_from_parts = JSONRPC.batch_message_from_parts
    for length in range(1, len(test_by_answer)):
        for combo in combinations(test_by_answer, length):
            batch = bm_from_parts(test_by_answer[answer] for answer in combo)
            protocol = JSONRPCAutoDetect.detect_protocol(batch)
            if JSONRPCv2 in combo:
                assert protocol == JSONRPCv2
            elif JSONRPCv1 in combo:
                assert protocol == JSONRPCv1
            else:
                assert protocol == combo[0]


#
# Connection tests
#

@pytest.mark.asyncio
async def test_send_request_and_response(protocol):
    '''Test sending a request gives the correct outgoing message, waits
    for a response, and returns it.  Also raises if the response is an
    error.
    '''
    req = Request('sum', [1, 2, 3])
    connection = JSONRPCConnection(protocol)
    waiting = Event()
    send_message = None

    async def send_mess():
        nonlocal send_message
        send_message, future = connection.send_request(req)
        waiting.set()
        assert await future == 6
        # Test receipt of an error response
        send_message, future = connection.send_request(req)
        waiting.set()
        try:
            await future
        except Exception as e:
            assert_RPCError(e, JSONRPC.METHOD_NOT_FOUND, "cannot add up")
        send_message, future = connection.send_request(req)
        waiting.set()
        try:
            await future
        except Exception as e:
            assert_ProtocolError(e, JSONRPC.INVALID_REQUEST, '"result"')

    async def send_response():
        for n in range(3):
            await waiting.wait()
            waiting.clear()
            assert connection.pending_requests() == [req]
            payload = json.loads(send_message.decode())
            if protocol == JSONRPCv2:
                assert payload.get("jsonrpc") == "2.0"
            assert payload.get("method") == "sum"
            assert payload.get("params") == [1, 2, 3]
            if n == 0:
                message = protocol.response_message(6, payload["id"])
            elif n == 1:
                error = RPCError(protocol.METHOD_NOT_FOUND, "cannot add up")
                message = protocol.response_message(error, payload["id"])
            else:
                message = protocol.response_message(6, payload["id"])
                message = message.replace(b'result', b'res')
            connection.receive_message(message)

    async with TaskGroup() as group:
        await group.spawn(send_mess)
        await group.spawn(send_response)

    assert not connection.pending_requests()


@pytest.mark.asyncio
async def test_receive_message_unmatched_response(protocol):
    '''Test receiving a response with an unmatchable request raises
    a ProtocolError to receive_message.
    '''
    connection = JSONRPCConnection(protocol)

    message = protocol.response_message(1, 12345)
    with pytest.raises(ProtocolError) as e:
        await connection.receive_message(message)
    assert 'response to unsent request (ID: 12345)' in e.value.message

    message = protocol.response_message(1, None)
    with pytest.raises(ProtocolError) as e:
        await connection.receive_message(message)
    assert 'response to unsent request (ID: None)' in e.value.message

    error = RPCError(1, 'messed up')
    message = protocol.response_message(error, None)
    with pytest.raises(ProtocolError) as e:
        await connection.receive_message(message)
    assert 'diagnostic error received' in e.value.message
    assert 'messed up' in e.value.message


@pytest.mark.asyncio
async def test_send_response_round_trip(protocol):
    '''Test sending a request, receiving it, replying to it, and getting
    the response.
    '''
    req = Request('sum', [1, 2, 3])
    connection = JSONRPCConnection(protocol)
    queue = Queue()

    async def send_request():
        message, future = connection.send_request(req)
        await queue.put(message)
        assert await future == 6

    async def receive_request():
        # This will be the request sent
        message = await queue.get()
        assert isinstance(message, bytes)
        assert connection.pending_requests() == [req]
        # Pretend we actually received this
        requests = connection.receive_message(message)
        assert requests == [req]
        # Send the result
        message = requests[0].send_result(6)
        # Receive the result
        requests = connection.receive_message(message)
        assert not requests

    async with timeout_after(0.2):
        async with TaskGroup() as group:
            await group.spawn(receive_request)
            await group.spawn(send_request)

    assert not connection.pending_requests()


@pytest.mark.asyncio
async def test_send_batch_round_trip(batch_protocol):
    '''Test sending a batch (with both Requests and Notifications),
    receiving it, replying to it in a random order, and getting the
    response in the correct order.
    '''
    protocol = batch_protocol
    items = [Request('echo', [n]) for n in range(15)]
    answers = [n for n in range(len(items))]
    # Replace a couple of answers with errors and throw in some notifications
    for pos in range(0, len(answers), 4):
        answers[pos] = RPCError(pos, 'division by zero')
        items.insert(pos, Notification('n', [pos]))
    batch = Batch(items)
    connection = JSONRPCConnection(protocol)
    queue = Queue()

    async def send_request():
        # Check the returned answers are in the correct order
        message, future = connection.send_batch(batch)
        await queue.put(message)
        assert await future == tuple(answers)

    async def receive_request():
        # This will be the batch request sent
        message = await queue.get()
        assert connection.pending_requests() == [batch]
        # Pretend we actually received this
        requests = connection.receive_message(message)
        # Check we get the requests separately
        answer_iter = iter(answers)
        req_ans = []
        for request, req in zip(requests, batch):
            assert request == req
            if isinstance(request, Request):
                req_ans.append((request, next(answer_iter)))
        # Send the responses in a random order
        shuffle(req_ans)
        for request, answer in req_ans:
            message = request.send_result(answer)
            if message:
                assert not connection.receive_message(message)
                assert not connection.pending_requests()
            else:
                assert connection.pending_requests()

    async with TaskGroup() as group:
        await group.spawn(receive_request)
        await group.spawn(send_request)

    assert not connection.pending_requests()


@pytest.mark.asyncio
async def test_send_notification_batch(batch_protocol):
    '''Test that a notification batch does not wait for a response.'''
    protocol = batch_protocol
    batch = Batch([Notification('n', [n]) for n in range(10)])
    connection = JSONRPCConnection(protocol)
    queue = Queue()

    async def send_request():
        message, event = connection.send_batch(batch)
        assert not connection.pending_requests()
        await queue.put(message)
        assert event is None

    async def receive_request():
        # This will be the batch request sent
        message = await queue.get()
        # Pretend we actually received this
        requests = connection.receive_message(message)
        # Check we get the requests separately
        for req, request in zip(batch, requests):
            assert req == request

    async with timeout_after(0.2):
        async with TaskGroup() as group:
            await group.spawn(receive_request)
            await group.spawn(send_request)

    assert not connection.pending_requests()


@pytest.mark.asyncio
async def test_batch_fails(batch_protocol):
    '''Test various failure cases for batches.'''
    protocol = batch_protocol
    batch = Batch([
        Request('test', [1, 2, 3]),
    ])
    connection = JSONRPCConnection(protocol)
    queue = Queue()

    async def send_request():
        message, future = connection.send_batch(batch)
        await queue.put(message)
        async with ignore_after(0.01):
            await future

    async def receive_request():
        # This will be the batch request sent
        message = await queue.get()
        assert connection.pending_requests() == [batch]
        # Send a batch response we didn't get
        parts = [protocol.response_message(2, "bad_id")]
        fake_message = protocol.batch_message_from_parts(parts)
        with pytest.raises(ProtocolError) as e:
            connection.receive_message(fake_message)
        assert 'response to unsent batch' in e.value.message
        assert connection.pending_requests() == [batch]

        # Send a batch with a duplicate response
        data = json.loads(message.decode())
        parts = [protocol.response_message(2, data[0]['id'])] * 2
        fake_message = protocol.batch_message_from_parts(parts)

        with pytest.raises(ProtocolError) as e:
            await connection.receive_message(fake_message)
        assert 'response to unsent batch' in e.value.message

    async with TaskGroup() as group:
        await group.spawn(receive_request)
        await group.spawn(send_request)

    assert connection.pending_requests() == [batch]


@pytest.mark.asyncio
async def test_send_notification(protocol):
    '''Test sending a notification doesn't wait.'''
    req = Notification('wakey', [])
    connection = JSONRPCConnection(protocol)
    queue = Queue()

    async def send_request():
        message = connection.send_notification(req)
        assert isinstance(message, bytes)
        await queue.put(message)
        assert not connection.pending_requests()

    async def receive_request():
        # This will be the notification sent
        message = await queue.get()
        assert not connection.pending_requests()
        # Pretend we actually received this
        requests = connection.receive_message(message)
        assert requests == [req]

    async with timeout_after(0.2):
        async with TaskGroup() as group:
            await group.spawn(receive_request)
            await group.spawn(send_request)

    assert not connection.pending_requests()


@pytest.mark.asyncio
async def test_max_response_size(protocol):
    request = Request('', [])
    result = "a"
    size = len(protocol.response_message(result, 0))
    queue = Queue()

    JSONRPCConnection._id_counter = count()

    async def send_request_good(request):
        message, future = connection.send_request(request)
        await queue.put(message)
        assert await future == result

    async def send_request_bad(request):
        message, future = connection.send_request(request)
        await queue.put(message)
        try:
            await future
            assert False
        except Exception as e:
            assert_RPCError(e, JSONRPC.INVALID_REQUEST, "response too large")

    async def receive_request(count):
        # This will be the notification sent
        message = await queue.get()
        # Pretend we actually received this
        requests = connection.receive_message(message)
        for req in requests:
            message = req.send_result(result)
            # Receive the result
            if message:
                assert not connection.receive_message(message)

    connection = JSONRPCConnection(protocol)
    connection.max_response_size = size
    async with TaskGroup() as group:
        await group.spawn(receive_request(1))
        await group.spawn(send_request_good(request))

    connection.max_response_size = size - 1
    async with TaskGroup() as group:
        await group.spawn(receive_request(1))
        await group.spawn(send_request_bad(request))

    async def send_batch(batch):
        message, future = connection.send_batch(batch)
        await queue.put(message)
        results = await future
        for n, part_result in enumerate(results):
            if n == 0:
                assert part_result == result
            else:
                assert "too large" in part_result.message

    if protocol.allow_batches:
        connection.max_response_size = size + 3
        batch = Batch([request, request, request])
        async with TaskGroup() as group:
            await group.spawn(receive_request(len(batch)))
            await group.spawn(send_batch(batch))


def test_misc(protocol):
    '''Misc tests to get full coverage.'''
    connection = JSONRPCConnection(protocol)

    with pytest.raises(ProtocolError):
        connection.receive_message(b'[]')

    with pytest.raises(AssertionError):
        connection.send_request(Response(2))

    request = Request('a', [])
    assert request.send_result(2) is None


def test_handler_invocation():
    # Peculiar function signatures

    # pow - Built-in; 2 positional args, 1 optional 3rd named arg
    powb = pow

    def add_3(x, y, z=0):
        return x + y + z

    def add_many(first, second=0, *values):
        values += (first, second)
        return sum(values)

    def echo_2(first, *, second=2):
        return [first, second]

    def kwargs(start, *kwargs):
        return start + len(kwargs)

    def both(start=2, *args, **kwargs):
        return start + len(args) * 10 + len(kwargs) * 4

    good_requests = (
        (Request('add_3', (1, 2, 3)), 6),
        (Request('add_3', [5, 7]), 12),
        (Request('add_3', {'x': 5, 'y': 7}), 12),
        (Request('add_3', {'x': 5, 'y': 7, 'z': 3}), 15),
        (Request('add_many', [1]), 1),
        (Request('add_many', [5, 50, 500]), 555),
        (Request('add_many', list(range(10))), 45),
        (Request('add_many', {'first': 1}), 1),
        (Request('add_many', {'first': 1, 'second': 10}), 11),
        (Request('powb', [2, 3]), 8),
        (Request('powb', [2, 3, 5]), 3),
        (Request('echo_2', ['ping']), ['ping', 2]),
        (Request('echo_2', {'first': 1, 'second': 8}), [1, 8]),
        (Request('kwargs', [1]), 1),
        (Request('kwargs', [1, 2]), 2),
        (Request('kwargs', {'start': 3}), 3),
        (Request('both', []), 2),
        (Request('both', [1]), 1),
        (Request('both', [5, 2]), 15),
        (Request('both', {'end': 4}), 6),
        (Request('both', {'start': 3}), 3),
        (Request('both', {'start': 3, 'end': 1, '3rd': 1}), 11),
    )

    for request, result in good_requests:
        handler = locals()[request.method]
        invocation = handler_invocation(handler, request)
        assert invocation() == result

    if sys.version_info < (3, 8):
        powb_request = (Request('powb', {"x": 2, "y": 3}), 'cannot be called')
    else:
        powb_request = (Request('powb', {"x": 2, "y": 3}), 'requires parameters')

    bad_requests = [
        (Request('missing_method', []), 'unknown method'),
        (Request('add_many', []), 'requires 1'),
        (Request('add_many', {'first': 1, 'values': []}), 'values'),
        powb_request,
        (Request('echo_2', ['ping', 'pong']), 'at most 1'),
        (Request('echo_2', {'first': 1, 'second': 8, '3rd': 1}), '3rd'),
        (Request('kwargs', []), 'requires 1'),
        (Request('kwargs', {'end': 4}), "start"),
        (Request('kwargs', {'start': 3, 'end': 1, '3rd': 1}), '3rd'),
    ]

    for request, text in bad_requests:
        with pytest.raises(RPCError) as e:
            handler = locals().get(request.method)
            handler_invocation(handler, request)
        assert text in e.value.message

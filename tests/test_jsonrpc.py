from itertools import chain, combinations
import json
import pytest

from aiorpcx.jsonrpc import *
from aiorpcx.rpc import RPCRequest, RPCResponse, RPCBatch, RPCError

rpcs = [
    JSONRPCv1,
    JSONRPCv2,
    JSONRPCLoose,
]
batch_rpcs = [rpc for rpc in rpcs if rpc.allow_batches]
no_batch_rpcs = [rpc for rpc in rpcs if not rpc.allow_batches]


def assert_is_request_batch(item, n):
    assert item.is_request_batch()
    assert len(item.items) == n


def assert_is_response_batch(item, n):
    assert not item.is_request_batch()
    assert len(item.items) == n


def assert_is_error(item, text, code, request_id, is_request=True):
    if is_request:
        assert isinstance(item, RPCRequest)
        assert item.request_id == request_id
        item = item.method
    else:
        assert isinstance(item, RPCResponse)
        assert item.request_id == request_id
        item = item.result
    assert isinstance(item, RPCError)
    assert item.code == code
    assert text in item.message
    assert item.request_id == request_id


def assert_is_error_response(*args):
    assert_is_error(*args, is_request=False)


def assert_is_request(item, method, args, request_id):
    assert isinstance(item, RPCRequest)
    assert item.method == method
    assert item.args == args
    assert item.request_id == request_id


def assert_is_good_response(item, result, request_id):
    assert isinstance(item, RPCResponse)
    assert item.result == result
    assert item.request_id == request_id


def rpc_message_to_item(rpc, message):
    message = message.copy()
    if rpc == JSONRPCv2:
        message['jsonrpc'] = '2.0'
    elif rpc == JSONRPCv1:
        if 'method' in message and 'params' not in message:
            message['params'] = []
        if 'error' in message and 'result' not in message:
            message['result'] = None
        if 'result' in message and 'error' not in message:
            message['error'] = None
    return rpc.message_to_item(json.dumps(message).encode())


def rpc_process_batch(rpc, batch):
    batch = batch.copy()
    if rpc == JSONRPCv2:
        for item in batch:
            if isinstance(item, dict):
                item['jsonrpc'] = '2.0'
    return rpc.message_to_item(json.dumps(batch).encode())

# MISC


def test_base_class():
    class MyJSONProtocol(JSONRPC):
        pass
    with pytest.raises(NotImplementedError):
        MyJSONProtocol._message_id({}, True)
    assert MyJSONProtocol._validate_message({}) is None
    with pytest.raises(NotImplementedError):
        MyJSONProtocol._request_args({})

# ENCODING


def test_bad_encoding():
    message = b'123\xff'
    for rpc in rpcs:
        item = rpc.message_to_item(message)
        assert_is_error(item, 'UTF-8', rpc.PARSE_ERROR, None)


def test_bad_json():
    message = b'{"foo", }'
    for rpc in rpcs:
        item = rpc.message_to_item(message)
        assert_is_error(item, 'JSON', rpc.PARSE_ERROR, None)


def test_bad_request():
    messages = [b'2', b'"foo"', b'2.78']
    for rpc in rpcs:
        for message in messages:
            item = rpc.message_to_item(message)
            assert_is_error(item, 'dict', rpc.INVALID_REQUEST, None)

# REQUESTS


def test_request_bad_method():
    for rpc in rpcs:
        message = {"method": None, "id": 0}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'method', rpc.METHOD_NOT_FOUND, 0)
        message = {"method": 2, "id": 3}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'method', rpc.METHOD_NOT_FOUND, 3)
        message = {"method": [2], "id": 3}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'method', rpc.METHOD_NOT_FOUND, 3)
        message = {"method": {}, "id": 4}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'method', rpc.METHOD_NOT_FOUND, 4)


def test_request_bad_args():
    for rpc in rpcs:
        message = {"method": "a", "params": 2, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'argu', rpc.INVALID_ARGS, 1)
        message = {"method": "a", "params": "foo", "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'argu', rpc.INVALID_ARGS, 1)
        message = {"method": "a", "params": None, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'argu', rpc.INVALID_ARGS, 1)


def test_request_bad_id():
    messages = [
        {"method": "a", "params": 2, "id": []},
        {"method": "a", "params": {}, "id": {}},
    ]
    for rpc in rpcs:
        for message in messages:
            item = rpc_message_to_item(rpc, message)
            # The ID is good for JSONRPCv1
            if rpc == JSONRPCv1:
                assert_is_error(item, "argu", rpc.INVALID_ARGS,
                                message['id'])
            else:
                assert_is_error(item, '"id"', rpc.INVALID_REQUEST, None)


def test_request_ill_formed():
    for rpc in rpcs:
        message = b'"yabberdabberdoo"'
        item = rpc.message_to_item(message)
        assert_is_error(item, 'dict', rpc.INVALID_REQUEST, None)


def test_JSONRPCv1_ill_formed():
    rpc = JSONRPCv1
    messages = [
        {"method": "a", "params": {}, "id": 1},
        {"method": "a", "params": {"a": 1, "b": "c"}, "id": 1},
    ]
    for message in messages:
        item = rpc_message_to_item(rpc, message)
        assert_is_error(item, 'argu', rpc.INVALID_ARGS, 1)

    # Requires an ID
    message = {"method": "a", "params": [1, "foo"]}
    item = rpc_message_to_item(rpc, message)
    assert_is_error(item, '"id"', rpc.INVALID_REQUEST, None)


def test_good_requests():
    for rpc in rpcs:
        message = {"method": "", "id": -1}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, '', [], -1)
        # recommended against in the spec, but valid
        message = {"method": "", "id": None}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, '', [], None)
        # recommended against in the spec, but valid
        message = {"method": "", "id": 2.5}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, '', [], 2.5)
        message = {"method": "a", "id": 0}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', [], 0)
        message = {"method": "a", "params": [], "id": ""}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', [], "")
        # Rest do not applay to JSONRPCv1; tested to fail elsewhere
        if rpc == JSONRPCv1:
            continue
        message = {"method": "a", "params": [1, "foo"]}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', [1, "foo"], None)
        message = {"method": "a", "params": {}}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', {}, None)
        message = {"method": "a", "params": {"a": 1, "b": "c"}}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', {"a": 1, "b": "c"}, None)
        message = {"method": "a", "params": {}, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', {}, 1)
        message = {"method": "a", "params": {"a": 1, "b": "c"}, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_request(item, 'a', {"a": 1, "b": "c"}, 1)

# # RESPONSES


def test_response_bad():
    for rpc in rpcs[1:2]:
        # Missing ID
        message = {"result": 2}
        item = rpc_message_to_item(rpc, message)
        assert_is_error_response(item, 'id', rpc.INVALID_REQUEST, None)
        message = {"error": {"code": 2, "message": "try harder"}}
        item = rpc_message_to_item(rpc, message)
        assert_is_error_response(item, 'id', rpc.INVALID_REQUEST, None)
        # Result and error
        if rpc != JSONRPCv1:
            message = {"result": 0, "error": {"code": 2, "message": ""},
                       "id": 0}
            item = rpc_message_to_item(rpc, message)
            assert_is_error_response(item, 'result',
                                     rpc.INVALID_REQUEST, 0)
            message = {"result": 1, "error": None, "id": 0}
            item = rpc_message_to_item(rpc, message)
            if rpc != JSONRPCLoose:
                assert_is_error_response(item, 'result',
                                         rpc.INVALID_REQUEST, 0)
        # No result, also no error
        message = {"foo": 1, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_error_response(item, 'error', rpc.INVALID_REQUEST, 1)


def test_response_good():
    for rpc in rpcs:
        # Integer
        message = {"result": 2, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, 2, 1)
        # Float
        message = {"result": 2.1, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, 2.1, 1)
        # String
        message = {"result": "f", "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, "f", 1)
        # None
        message = {"result": None, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, None, 1)
        # Array
        message = {"result": [1, 2], "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, [1, 2], 1)
        # Dictionary
        message = {"result": {"a": 1}, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, {"a": 1}, 1)
        # Additional junk
        message = {"result": 2, "id": 1, "junk": 0}
        item = rpc_message_to_item(rpc, message)
        assert_is_good_response(item, 2, 1)


def test_response_error_bad():
    messages = [
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
    for rpc in rpcs:
        if rpc in (JSONRPCv1, JSONRPCLoose):
            continue
        for message in messages:
            item = rpc_message_to_item(rpc, message)
            assert_is_error_response(item, 'ill-formed',
                                     rpc.INVALID_REQUEST, 1)


def test_JSONRPCLoose_responses():
    rpc = JSONRPCLoose
    message = {"result": 0, "error": None, "id": 1}
    item = rpc_message_to_item(rpc, message)
    assert_is_good_response(item, 0, 1)
    message = {"result": None, "error": None, "id": 1}
    item = rpc_message_to_item(rpc, message)
    assert_is_good_response(item, None, 1)
    message = {"result": None, "error": 2, "id": 1}
    item = rpc_message_to_item(rpc, message)
    assert_is_error_response(item, 'no error message', 2, 1)
    message = {"result": 4, "error": 2, "id": 1}
    item = rpc.message_to_item(json.dumps(message).encode())
    assert_is_error_response(item, 'both', rpc.INVALID_REQUEST, 1)


def test_JSONRPCv2_required_jsonrpc():
    rpc = JSONRPCv2
    messages = [
        {"error": {"code": 2, "message": "bar"}, "id": 1},
        {"result": 1, "id": 2},
    ]
    for message in messages:
        item = rpc.message_to_item(json.dumps(message).encode())
        assert_is_error_response(item, 'jsonrpc',
                                 rpc.INVALID_REQUEST, message.get("id"))
    messages = [
        {"method": "f"}
    ]
    for message in messages:
        item = rpc.message_to_item(json.dumps(message).encode())
        assert_is_error(item, 'jsonrpc',
                        rpc.INVALID_REQUEST, message.get("id"))


def test_JSONRPCv1_errors():
    rpc = JSONRPCv1
    messages = [
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
    for message in messages:
        item = rpc_message_to_item(rpc, message)

        code = rpc.ERROR_CODE_UNAVAILABLE
        error = message['error']
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
        assert_is_error_response(item, message, code, 1)

    message = {"error": 2, "id": 1}
    item = rpc.message_to_item(json.dumps(message).encode())
    assert_is_error_response(item, 'result', rpc.INVALID_REQUEST, 1)
    message = {"result": 4, "error": 2, "id": 1}
    item = rpc.message_to_item(json.dumps(message).encode())
    assert_is_error_response(item, 'both', rpc.INVALID_REQUEST, 1)


def test_response_error_good():
    for rpc in rpcs:
        message = {"error": {"code": 5, "message": "bar"}, "id": 1}
        item = rpc_message_to_item(rpc, message)
        assert_is_error_response(item, 'bar', 5, 1)
        message = {"error": {"code": 3, "message": "try again"}, "id": "a",
                   "jnk": 0}
        item = rpc_message_to_item(rpc, message)
        assert_is_error_response(item, 'again', 3, "a")

# # BATCHES


def test_batch_not_allowed():
    for rpc in no_batch_rpcs:
        item = rpc.message_to_item(b'[]')
        assert_is_error(item, 'dict', rpc.INVALID_REQUEST, None)
        with pytest.raises(RuntimeError):
            rpc.batch_message(RPCBatch([RPCRequest('', [], 0)]))


def test_empty_batch():
    message = b'[]'
    for rpc in batch_rpcs:
        item = rpc.message_to_item(message)
        assert_is_error(item, 'empty', rpc.INVALID_REQUEST, None)


def test_single_request_batch():
    message = [{"method": "a", "id": 5}]
    for rpc in batch_rpcs:
        batch = rpc_process_batch(rpc, message)
        assert_is_request_batch(batch, 1)
        assert_is_request(batch.items[0], 'a', [], 5)


def test_double_request_batch():
    message = [{"method": "a", "id": 5},
               {"method": "b", "id": 7, "params": [2]}]

    for rpc in batch_rpcs:
        batch = rpc_process_batch(rpc, message)
        assert_is_request_batch(batch, 2)
        assert_is_request(batch.items[0], 'a', [], 5)
        assert_is_request(batch.items[1], 'b', [2], 7)


def test_request_batch_bad():
    parts = [
        {"method": "b", "id": 7, "params": [2]},
        {"method": "foo"},
        {"method": 5},
        {"method": None, "id": ""},
        {"method": "b", "id": 0, "params": 2},
        {"method": "", "id": "bar", "params": {"x": 1, "y": "foo"}},
        {"method": "m", "params": None},
    ]
    for rpc in batch_rpcs:
        batch = rpc_process_batch(rpc, parts)
        assert_is_request_batch(batch, len(parts))
        assert_is_request(batch.items[0], 'b', [2], 7)
        assert_is_request(batch.items[1], 'foo', [], None)
        assert_is_error(batch.items[2], 'method', rpc.METHOD_NOT_FOUND,
                        None)
        assert_is_error(batch.items[3], 'method', rpc.METHOD_NOT_FOUND,
                        "")
        assert_is_error(batch.items[4], 'argu', rpc.INVALID_ARGS, 0)
        assert_is_request(batch.items[5], '', {"x": 1, "y": "foo"}, "bar")
        assert_is_error(batch.items[6], 'argu', rpc.INVALID_ARGS, None)


def test_batch_response_bad():
    for rpc in batch_rpcs:
        batch = rpc.message_to_item(b'[6]')
        assert_is_response_batch(batch, 1)
        assert_is_error_response(batch.items[0], 'dict',
                                 rpc.INVALID_REQUEST, None)
        parts = [
            {"id": 5},
            [],
            {},
            {"result": 2},
            {"result": 2, "id": 1},
            {"error": {"code": 2, "message": "help"}, "id": 1},
        ]
        batch = rpc_process_batch(rpc, parts)
        assert_is_response_batch(batch, len(parts))
        assert_is_error_response(batch.items[0], 'result',
                                 rpc.INVALID_REQUEST, 5)
        assert_is_error_response(batch.items[1], 'dict',
                                 rpc.INVALID_REQUEST, None)
        assert_is_error_response(batch.items[2], '"id"',
                                 rpc.INVALID_REQUEST, None)
        assert_is_error_response(batch.items[3], '"id"',
                                 rpc.INVALID_REQUEST, None)
        assert_is_good_response(batch.items[4], 2, 1)
        assert_is_error_response(batch.items[5], "help", 2, 1)

# Message contruction


def test_batch_message_from_parts():
    for rpc in rpcs:
        assert rpc.batch_message_from_parts([]) == b''
        assert rpc.batch_message_from_parts([b'1']) == b'[1]'
        assert rpc.batch_message_from_parts([b'1', b'2']) == b'[1, 2]'
        # An empty part is not valid, but anyway.
        assert (rpc.batch_message_from_parts([b'1', b'', b'[3]'])
                == b'[1, , [3]]')


def test_built_in_errors():
    for rpc in rpcs:
        error = rpc.internal_error(5)
        assert isinstance(error, RPCError)
        assert error.code == rpc.INTERNAL_ERROR
        assert error.request_id == 5
        error = rpc.timeout_error("a")
        assert isinstance(error, RPCError)
        assert error.code == rpc.TIMEOUT_ERROR
        assert error.request_id == "a"
        error = rpc.args_error('fix your args')
        assert isinstance(error, RPCError)
        assert error.code == rpc.INVALID_ARGS
        assert error.request_id is None
        assert error.message == 'fix your args'
        error = rpc.invalid_request('no freebies')
        assert isinstance(error, RPCError)
        assert error.code == rpc.INVALID_REQUEST
        assert error.request_id is None
        assert error.message == 'no freebies'
        error = rpc.method_not_found('look harder')
        assert isinstance(error, RPCError)
        assert error.code == rpc.METHOD_NOT_FOUND
        assert error.request_id is None
        assert error.message == 'look harder'


def test_encode_payload():
    for rpc in rpcs:
        assert rpc.encode_payload(2) == b'2'
        assert rpc.encode_payload([2, 3]) == b'[2, 3]'
        assert rpc.encode_payload({"a": 1}) == b'{"a": 1}'
        assert rpc.encode_payload(True) == b'true'
        assert rpc.encode_payload(False) == b'false'
        assert rpc.encode_payload(None) == b'null'
        assert rpc.encode_payload("foo") == b'"foo"'
        error = rpc.error_message(rpc.internal_error(None))
        assert rpc.encode_payload(b'foo') == error
        error = rpc.error_message(rpc.internal_error(4))
        assert rpc.encode_payload({'id': 4, b'foo': 5}) == error


def test_JSONRPCv2_and_JSONRPCLoosemessages():
    tests = [
        (RPCRequest('foo', [], 2),
         {"jsonrpc": "2.0", "method": "foo", "id": 2}),
        (RPCRequest('foo', [], None),
         {"jsonrpc": "2.0", "method": "foo"}),
        (RPCRequest('foo', {}, 2),
         {"jsonrpc": "2.0", "params": {}, "method": "foo", "id": 2}),
        (RPCRequest('foo', [1, 2], 2),
         {"jsonrpc": "2.0", "method": "foo", "params": [1, 2], "id": 2}),
        (RPCRequest('foo', {"bar": 3, "baz": "bat"}, "it"),
         {"jsonrpc": "2.0", "method": "foo",
          "params": {"bar": 3, "baz": "bat"}, "id": "it"}),
        (RPCResponse('foo', "it"),
         {"jsonrpc": "2.0", "result": "foo", "id": "it"}),
        (RPCResponse(2, "it"),
         {"jsonrpc": "2.0", "result": 2, "id": "it"}),
        (RPCResponse(None, -2),
         {"jsonrpc": "2.0", "result": None, "id": -2}),
        (RPCResponse([1, 2], -1),
         {"jsonrpc": "2.0", "result": [1, 2], "id": -1}),
        (RPCResponse({"kind": 1}, 0),
         {"jsonrpc": "2.0", "result": {"kind": 1}, "id": 0}),
        (RPCResponse(RPCError(3, "j"), 1),
         {"jsonrpc": "2.0", "error": {"code": 3, "message": "j"}, "id": 1}),
        (RPCBatch([
            RPCRequest('foo', [], 2),
            RPCRequest('bar', [2], None)
        ]),
         [{"jsonrpc": "2.0", "method": "foo", "id": 2},
          {"jsonrpc": "2.0", "method": "bar", "params": [2]}]),
    ]

    for rpc in [JSONRPCv2, JSONRPCLoose]:
        for item, payload in tests:
            if isinstance(item, RPCRequest):
                binary = rpc.request_message(item)
            elif isinstance(item, RPCResponse):
                binary = rpc.response_message(item)
            else:
                binary = rpc.batch_message(item)
            test_payload = json.loads(binary.decode())
            if rpc is JSONRPCLoose:
                for p in payload if isinstance(payload, list) else [payload]:
                    p.pop('jsonrpc')
            assert test_payload == payload


def test_JSONRPCv1_messages():
    tests = [
        (RPCRequest('foo', [], 2),
         {"method": "foo", "params": [], "id": 2}),
        (RPCRequest('foo', [], None),
         {"method": "foo", "params": [], "id": None}),
        (RPCRequest('foo', [1, 2], "s"),
         {"method": "foo", "params": [1, 2], "id": "s"}),
        (RPCRequest('foo', [1, 2], ["x"]),
         {"method": "foo", "params": [1, 2], "id": ["x"]}),
        (RPCResponse('foo', "it"),
         {"result": "foo", "error": None, "id": "it"}),
        (RPCResponse(2, "it"),
         {"result": 2, "error": None, "id": "it"}),
        (RPCResponse(None, -2),
         {"result": None, "error": None, "id": -2}),
        (RPCResponse([1, 2], -1),
         {"result": [1, 2], "error": None, "id": -1}),
        (RPCResponse({"kind": 1}, [1]),
         {"result": {"kind": 1}, "error": None, "id": [1]}),
        (RPCResponse(RPCError(3, "j"), 1),
         {"result": None, "error": {"code": 3, "message": "j"}, "id": 1}),
    ]
    rpc = JSONRPCv1
    for item, payload in tests:
        if isinstance(item, RPCRequest):
            binary = rpc.request_message(item)
        else:
            binary = rpc.response_message(item)
        test_payload = json.loads(binary.decode())
        assert test_payload == payload

    with pytest.raises(TypeError):
        rpc.request_message(RPCRequest('foo', {}, 2))
    with pytest.raises(TypeError):
        rpc.request_message(RPCRequest('foo', {"x": 1}, 2))


def test_bad_implemntation():
    class Mine(JSONRPCv2):

        @classmethod
        def _validate_message(cls, message):
            silly

    rpc = Mine
    item = rpc_message_to_item(rpc, {"method": "foo", "id": 5})
    assert_is_error(item, 'internal', rpc.INTERNAL_ERROR, 5)
    item = rpc_message_to_item(rpc, {"result": 6, "id": 5})
    assert_is_error_response(item, 'internal', rpc.INTERNAL_ERROR, 5)


def test_protocol_detection():
    bad_syntax_tests = [
        (b'', None),
        (b'\xf5', None),
        (b'{"method":', None),
    ]
    tests = [
        (b'[]', None),
        (b'""', None),
        (b'{"jsonrpc": "2.0"}', JSONRPCv2),
        (b'{"jsonrpc": "1.0"}', JSONRPCv1),
        # No ID
        (b'{"method": "part"}', JSONRPCLoose),
        (b'{"error": 2}', JSONRPCLoose),
        (b'{"result": 3}', JSONRPCLoose),
        # Just ID
        (b'{"id": 2}', None),
        # Result or error alone
        (b'{"result": 3, "id":2}', JSONRPCLoose),
        (b'{"error": 3, "id":2}', JSONRPCLoose),
        (b'{"result": 3, "error": null, "id":2}', JSONRPCv1),
        # Method with or without params
        (b'{"method": "foo", "id": 1}', JSONRPCLoose),
        (b'{"method": "foo", "params": [], "id":2}', None),
    ]

    for message, answer in chain(bad_syntax_tests, tests):
        result = JSONRPC.detect_protocol(message)
        assert answer == result

    test_by_answer = {}
    for message, answer in tests:
        test_by_answer[answer] = message

    # Batches.  Test every combination...
    batch_message = JSONRPC.batch_message_from_parts
    for length in range(1, len(test_by_answer)):
        for combo in combinations(test_by_answer, length):
            batch = batch_message(test_by_answer[answer] for answer in combo)
            protocol = JSONRPC.detect_protocol(batch)
            if JSONRPCv2 in combo:
                assert protocol == JSONRPCv2
            elif JSONRPCv1 in combo:
                assert protocol == JSONRPCv1
            elif len(set(combo)) == 1:
                assert protocol == combo[0]
            else:
                assert protocol == JSONRPCLoose

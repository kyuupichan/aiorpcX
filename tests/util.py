from aiorpcx import ProtocolError, RPCError


def assert_ProtocolError(exception, code, message):
    assert isinstance(exception, ProtocolError), \
        f'expected ProtocolError got {exception.__class__.__name__}'
    assert exception.code == code, \
        f'expected {code} got {exception.code}'
    if message:
        assert message in exception.message, \
            f'{message} not in {exception.message}'


def assert_RPCError(exception, code, message):
    assert isinstance(exception, RPCError), \
        f'expected RPCError got {exception.__class__.__name__}'
    assert exception.code == code, \
        f'expected {code} got {exception.code}'
    if message:
        assert message in exception.message, \
            f'{message} not in {exception.message}'


class RaiseTest(object):

    def __init__(self, code, message, exc_type):
        self.code = code
        self.message = message
        self.exc_type = exc_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        assert exc_type is self.exc_type, \
            f'expected {self.exc_type} got {exc_type}'
        assert exc_value.code == self.code, \
            f'expected {self.code} got {exc_value.code}'
        if self.message:
            assert self.message in exc_value.message, \
                f'{self.message} not in {exc_value.message}'
        self.value = exc_value
        return True

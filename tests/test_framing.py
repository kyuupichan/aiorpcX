import pytest

from aiorpcx import *


def test_FramerBase():
    framer = FramerBase()
    with pytest.raises(NotImplementedError):
        framer.messages('')
    with pytest.raises(NotImplementedError):
        framer.frame([])


def test_NewlineFramer_framing():
    framer = NewlineFramer()
    assert framer.frame([]) == b''
    assert framer.frame((b'foo', b'bar')) == b'foo\nbar\n'


def test_NewlineFramer_messages():
    framer = NewlineFramer()
    messages = list(framer.messages(b'abc\ndef\ng'))
    assert messages == [b'abc', b'def']
    messages = list(framer.messages(b'h'))
    assert messages == []
    messages = list(framer.messages(b'i\n'))
    assert messages == [b'ghi']


def test_NewlineFramer_overflow():
    framer = NewlineFramer(max_size=5)
    messages = list(framer.messages(b'abcde'))
    # Accepts 5 bytes
    assert messages == []
    # Rejects 6 bytes
    with pytest.raises(MemoryError):
        list(framer.messages(b'f'))
    # Resynchronizes, returns b'yz' and stores 'AB'
    messages = list(framer.messages(b'ghijklmnopqrstuvwx\nyz\nAB'))
    assert messages == [b'yz']
    # Add 'C'
    messages = list(framer.messages(b'C'))
    assert messages == []
    # Accepts over-sized message as doesn't need to store it
    messages = list(framer.messages(b'DEFGHIJKL\nMNOPQRSTUVWX\nYZ'))
    assert messages == [b'ABCDEFGHIJKL', b'MNOPQRSTUVWX']
    # Accepts over-sized message as doesn't need to store it
    messages = list(framer.messages(b'\n'))
    assert messages == [b'YZ']

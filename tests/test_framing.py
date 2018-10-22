import pytest

from aiorpcx import *


@pytest.mark.asyncio
async def test_FramerBase():
    framer = FramerBase()
    with pytest.raises(NotImplementedError):
        framer.received_bytes(b'')
    with pytest.raises(NotImplementedError):
        await framer.receive_message()
    with pytest.raises(NotImplementedError):
        framer.frame(b'')


def test_NewlineFramer_framing():
    framer = NewlineFramer()
    assert framer.frame(b'foo') == b'foo\n'


@pytest.mark.asyncio
async def test_NewlineFramer_messages():
    framer = NewlineFramer()
    framer.received_bytes(b'abc\ndef\ngh')
    assert await framer.receive_message() == b'abc'
    assert await framer.receive_message() == b'def'

    async def receive_message():
        return await framer.receive_message()
    async def put_rest():
        await sleep(0.001)
        framer.received_bytes(b'i\n')

    async with TaskGroup() as group:
        task = await group.spawn(receive_message)
        await group.spawn(put_rest)
    assert task.result() == b'ghi'


@pytest.mark.asyncio
async def test_NewlineFramer_overflow():
    framer = NewlineFramer(max_size=5)
    framer.received_bytes(b'abcde\n')
    assert await framer.receive_message() == b'abcde'
    framer.received_bytes(b'abcde')
    framer.received_bytes(b'f')
    with pytest.raises(MemoryError):
        await framer.receive_message()

    # Resynchronizes to next \n, returns b'yz' and stores 'AB'
    framer.received_bytes(b'ghijklmnopqrstuvwx\nyz\nAB')
    assert await framer.receive_message() == b'yz'
    # Add 'C'
    framer.received_bytes(b'C')
    async with TaskGroup() as group:
        task = await group.spawn(framer.receive_message())
        await sleep(0.001)
        framer.received_bytes(b'DEFGHIJKL\nYZ')
    # Accepts over-sized message as doesn't need to store it
    assert task.result() == b'ABCDEFGHIJKL'
    framer.received_bytes(b'\n')
    assert await framer.receive_message() == b'YZ'

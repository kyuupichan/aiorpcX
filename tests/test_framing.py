import os
import random

import pytest

from aiorpcx import (
    BinaryFramer, BitcoinFramer, OversizedPayloadError, BadMagicError,
    BadChecksumError, FramerBase, NewlineFramer, TaskGroup, sleep, timeout_after,
)
from aiorpcx.framing import ByteQueue


@pytest.mark.asyncio
async def test_FramerBase():
    framer = FramerBase()
    with pytest.raises(NotImplementedError):
        framer.received_bytes(b'')
    with pytest.raises(NotImplementedError):
        await framer.receive_message()
    with pytest.raises(NotImplementedError):
        framer.frame(b'')
    with pytest.raises(NotImplementedError):
        framer.frame(TypeError)


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

    framer = NewlineFramer(max_size=0)
    framer.received_bytes(b'abc')
    async with TaskGroup() as group:
        task = await group.spawn(framer.receive_message())
        await sleep(0.001)
        framer.received_bytes(b'\n')
    assert task.result() == b'abc'


@pytest.mark.asyncio
async def test_ByteQueue():
    bq = ByteQueue()

    lengths = [random.randrange(0, 15) for n in range(40)]
    data = os.urandom(sum(lengths))

    answer = []
    cursor = 0
    for length in lengths:
        answer.append(data[cursor: cursor + length])
        cursor += length
    assert b''.join(answer) == data

    async def putter():
        cursor = 0
        while cursor < len(data):
            size = random.randrange(0, min(15, len(data) - cursor + 1))
            bq.put_nowait(data[cursor:cursor + size])
            cursor += size
            await sleep(random.random() * 0.005)

    async def getter():
        result = []
        for length in lengths:
            item = await bq.receive(length)
            result.append(item)
            await sleep(random.random() * 0.005)
        return result

    async with timeout_after(1):
        async with TaskGroup() as group:
            await group.spawn(putter)
            gjob = await group.spawn(getter)

    assert gjob.result() == answer
    assert bq.parts == [b'']
    assert bq.parts_len == 0


class TestBitcoinFramer():

    def test_framing(self):
        framer = BitcoinFramer()
        result = framer.frame((b'version', b'payload'))
        assert result == b'\xe3\xe1\xf3\xe8version\x00\x00\x00\x00\x00'  \
            b'\x07\x00\x00\x00\xe7\x871\xbbpayload'

    @pytest.mark.asyncio
    async def test_not_implemented(self):
        framer = BinaryFramer()
        with pytest.raises(NotImplementedError):
            framer._checksum(b'')
        with pytest.raises(NotImplementedError):
            framer._build_header(b'', b'')
        with pytest.raises(NotImplementedError):
            await framer._receive_header()

    def test_oversized_command(self):
        framer = BitcoinFramer()
        with pytest.raises(ValueError):
            framer._build_header(bytes(13), b'')

    @pytest.mark.asyncio
    async def test_oversized_message(self):
        framer = BitcoinFramer()
        framer.max_payload_size = 2000
        framer._max_block_size = 10000
        header = framer._build_header(b'', bytes(framer.max_payload_size))
        framer.received_bytes(header)
        await framer._receive_header()
        header = framer._build_header(b'', bytes(framer.max_payload_size + 1))
        framer.received_bytes(header)
        with pytest.raises(OversizedPayloadError):
            await framer._receive_header()
        header = framer._build_header(b'block', bytes(framer._max_block_size))
        framer.received_bytes(header)
        await framer._receive_header()
        header = framer._build_header(b'block', bytes(framer._max_block_size + 1))
        framer.received_bytes(header)
        with pytest.raises(OversizedPayloadError):
            await framer._receive_header()

    @pytest.mark.asyncio
    async def test_receive_message(self):
        framer = BitcoinFramer()
        result = framer.frame((b'version', b'payload'))
        framer.received_bytes(result)

        command, payload = await framer.receive_message()
        assert command == b'version'
        assert payload == b'payload'

    @pytest.mark.asyncio
    async def test_bad_magic(self):
        framer = BitcoinFramer()
        good_msg = framer.frame((b'version', b'payload'))
        pos = random.randrange(0, 24)

        for n in range(4):
            msg = bytearray(good_msg)
            msg[n] ^= 1
            framer.received_bytes(msg[:pos])
            # Just header should trigger the error
            framer.received_bytes(msg[pos:24])
            with pytest.raises(BadMagicError):
                await framer.receive_message()

    @pytest.mark.asyncio
    async def test_bad_checksum(self):
        framer = BitcoinFramer()
        good_msg = framer.frame((b'version', b'payload'))

        pos = random.randrange(0, len(good_msg))
        for n in range(20, 24):
            msg = bytearray(good_msg)
            msg[n] ^= 1
            framer.received_bytes(msg[:pos])
            framer.received_bytes(msg[pos:])
            with pytest.raises(BadChecksumError):
                await framer.receive_message()

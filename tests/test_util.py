import asyncio
from functools import partial

import pytest

from aiorpcx.util import (SignatureInfo, signature_info, Concurrency,
                          is_async_call)


async def coro(x, y):
    pass


def test_is_async_call():
    z = coro(2, 3)
    assert not is_async_call(z)
    assert is_async_call(coro)
    assert is_async_call(partial(coro, 3, 4))
    assert is_async_call(partial(partial(coro, 3), 4))
    assert not is_async_call(test_is_async_call)
    assert not is_async_call(partial(is_async_call))
    # Lose a warning
    asyncio.get_event_loop().run_until_complete(z)


def test_concurrency_constructor():
    Concurrency(3)
    Concurrency(max_concurrent=0)
    Concurrency(max_concurrent=32)
    with pytest.raises(RuntimeError):
        Concurrency(max_concurrent=-1)
    with pytest.raises(RuntimeError):
        Concurrency(max_concurrent=2.5)


async def concurrency_max(c):
    q = []

    loop = asyncio.get_event_loop()
    fut = loop.create_future()

    async def work():
        async with c:
            q.append(None)
            await fut

    tasks = []
    for n in range(16):
        tasks.append(loop.create_task(work()))
        prior_len = len(q)
        await asyncio.sleep(0)
        if len(q) == prior_len:
            break

    fut.set_result(len(q))
    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, loop=loop, return_exceptions=True)

    return fut.result()


@pytest.mark.asyncio
async def test_max_concurrent():
    c = Concurrency(max_concurrent=3)
    assert c.max_concurrent == 3
    assert await concurrency_max(c) == 3
    await c._set_max_concurrent(3)
    assert c.max_concurrent == 3
    assert await concurrency_max(c) == 3
    await c._set_max_concurrent(1)
    assert c.max_concurrent == 1
    assert await concurrency_max(c) == 1
    await c._set_max_concurrent(0)
    assert c._semaphore._value == 0
    assert c.max_concurrent == 0
    assert await concurrency_max(c) == 0
    await c._set_max_concurrent(5)
    assert c.max_concurrent == 5
    assert await concurrency_max(c) == 5
    with pytest.raises(RuntimeError):
        await c._set_max_concurrent(-1)
    with pytest.raises(RuntimeError):
        await c._set_max_concurrent(2.6)


@pytest.mark.asyncio
async def test_force_recalc():
    c = Concurrency(max_concurrent=6)
    c.force_recalc(lambda: 4)
    assert c.max_concurrent == 6
    async with c:
        assert c.max_concurrent == 4
        assert c._recalc_func is None
    assert c.max_concurrent == 4

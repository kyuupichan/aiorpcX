import asyncio
from functools import partial
import logging
import time

import pytest

from aiorpcx.util import (SignatureInfo, signature_info, Concurrency,
                          Timeout, TaskSet, is_async_call)


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


def test_function_info():

    def f1():
        pass

    async def f2(x):
        pass

    def f3(x, y=2):
        pass

    async def f4(x, y=2, *, z=3):
        pass

    def f5(x, y=2, *, z=3, **kwargs):
        pass

    async def f6(x, y=2, *args):
        pass

    def f7(x, *args, **kwargs):
        pass

    f8 = pow

    assert signature_info(f1) == SignatureInfo(0, 0, [], [])
    assert signature_info(f2) == SignatureInfo(1, 1, ['x'], [])
    assert signature_info(f3) == SignatureInfo(1, 2, ['x'], ['y'])
    assert signature_info(f4) == SignatureInfo(1, 2, ['x'], ['y', 'z'])
    assert signature_info(f5) == SignatureInfo(1, 2, ['x'], any)
    assert signature_info(f6) == SignatureInfo(1, None, ['x'], ['y'])
    assert signature_info(f7) == SignatureInfo(1, None, ['x'], any)
    assert signature_info(f8) == SignatureInfo(2, 3, [], None)

def run_briefly(loop):
    async def once():
        pass
    gen = once()
    t = loop.create_task(gen)
    loop.run_until_complete(t)

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
        async with c.semaphore:
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
    await c.set_max_concurrent(3)
    assert c.max_concurrent == 3
    assert await concurrency_max(c) == 3
    await c.set_max_concurrent(1)
    assert c.max_concurrent == 1
    assert await concurrency_max(c) == 1
    await c.set_max_concurrent(0)
    assert c.semaphore._value == 0
    assert c.max_concurrent == 0
    assert await concurrency_max(c) == 0
    await c.set_max_concurrent(5)
    assert c.max_concurrent == 5
    assert await concurrency_max(c) == 5
    with pytest.raises(RuntimeError):
        await c.set_max_concurrent(-1)
    with pytest.raises(RuntimeError):
        await c.set_max_concurrent(2.6)


def test_task_set():
    tasks = TaskSet()
    loop = tasks.loop

    # Test with empty tasks
    assert not tasks
    loop.run_until_complete(tasks.wait())

    fut = loop.create_future()
    async def work():
        await fut

    count = 3
    my_tasks = []
    for _ in range(count):
        my_tasks.append(tasks.create_task(work()))
    run_briefly(loop)

    assert len(tasks) == count
    tasks.cancel_all()

    loop.run_until_complete(tasks.wait())

    assert all(task.cancelled() for task in my_tasks)
    assert not tasks


def test_timeout():
    async def timeout():
        timed_out = False
        try:
            with Timeout(0.01, loop) as t:
                await t.run(asyncio.sleep(1))
        except asyncio.TimeoutError:
            timed_out = True
        return timed_out and t.timed_out

    async def no_timeout():
        with Timeout(1, loop) as t:
            await t.run(asyncio.sleep(0.01))
        return False

    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(timeout()) is True
    assert loop.run_until_complete(no_timeout()) is False

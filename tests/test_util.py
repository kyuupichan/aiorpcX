import asyncio
from functools import partial
import logging
import time

import pytest

from aiorpcx.util import (SignatureInfo, signature_info, WorkQueue,
                          Timeout, is_async_call)


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

def test_wq_constructor():
    wq = WorkQueue()
    assert wq.loop == asyncio.get_event_loop()
    event_loop = asyncio.new_event_loop()
    wq = WorkQueue(loop=event_loop)
    assert wq.loop == event_loop
    WorkQueue(max_concurrent=0)
    WorkQueue(max_concurrent=32)
    with pytest.raises(RuntimeError):
        WorkQueue(max_concurrent=-1)
    with pytest.raises(RuntimeError):
        WorkQueue(max_concurrent=2.5)


def wq_max(wq):
    q = []

    while wq.tasks:
        run_briefly(wq.loop)

    fut = wq.loop.create_future()
    async def work():
        q.append(None)
        await fut

    for n in range(16):
        wq.create_task(work())
        prior_len = len(q)
        run_briefly(wq.loop)
        if len(q) == prior_len:
            break

    fut.set_result(len(q))
    if not wq.max_concurrent:   # Ugh
        wq.max_concurrent = 1
    if wq.tasks:
        wq.loop.run_until_complete(asyncio.gather(*wq.tasks, loop=wq.loop))

    return fut.result()


def test_max_concurrent():
    wq = WorkQueue(max_concurrent=3)
    assert wq.max_concurrent == 3
    assert wq_max(wq) == 3
    wq.max_concurrent = 3
    assert wq.max_concurrent == 3
    assert wq_max(wq) == 3
    wq.max_concurrent = 1
    assert wq.max_concurrent == 1
    assert wq_max(wq) == 1
    wq.max_concurrent = 0
    assert wq.semaphore._value == 1
    assert wq.max_concurrent == 0
    assert wq_max(wq) == 0
    wq.max_concurrent = 5
    assert wq.max_concurrent == 5
    assert wq_max(wq) == 5
    with pytest.raises(RuntimeError):
        wq.max_concurrent = -1
    with pytest.raises(RuntimeError):
        wq.max_concurrent = 2.6


def test_cancel_all():
    wq = WorkQueue()

    fut = wq.loop.create_future()
    async def work():
        await fut

    count = 3
    for _ in range(count):
        wq.create_task(work())
    run_briefly(wq.loop)

    tasks = wq.tasks
    assert len(tasks) == count
    wq.cancel_all()
    run_briefly(wq.loop)

    assert all(task.cancelled() for task in tasks)
    assert not wq.tasks


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

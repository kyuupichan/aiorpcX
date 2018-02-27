import asyncio
from functools import partial
import logging
import time

import pytest

from aiorpcx.util import SignatureInfo, signature_info, JobQueue, is_async_call


class MyLogger(logging.Logger):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logged_msg = ''

    def exception(self, msg, *args, exc_info=True, **kwargs):
        self.logged_msg = msg


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


def test_job_queue():
    jq = JobQueue(asyncio.get_event_loop())
    assert not jq
    assert not len(jq)
    t = time.time()
    jq.process_some(t + 5)
    jq.process_some(t - 5)
    jq.loop.run_until_complete(jq.wait_for_all())
    assert time.time() < t + 0.1
    assert not jq

    counter = slept = 0

    def job1():
        nonlocal counter
        counter += 1

    def bad_job():
        undefined_var

    def slow_job():
        nonlocal slept
        time.sleep(0.01)
        slept += 1

    # Synchronous jobs
    logger = MyLogger('test')
    jq = JobQueue(asyncio.get_event_loop(), logger=logger)
    jq.add_job(job1)
    assert jq
    assert len(jq) == 1
    assert counter == 0
    jq.add_job(job1)
    assert len(jq) == 2
    assert counter == 0 and jq
    t = time.time()
    jq.process_some(t)
    assert counter == 0 and jq
    jq.process_some(t - 1)
    assert counter == 0 and jq
    jq.process_some(t + 1)
    assert counter == 2
    assert not jq
    assert not logger.logged_msg

    # Timeouts work?
    jq.add_job(slow_job)
    jq.add_job(slow_job)
    assert jq
    assert len(jq) == 2
    assert slept == 0
    t = time.time()
    # This will not allow any to complete
    jq.process_some(time.time())
    assert len(jq) == 2
    # This will allow one to complete but then will exit
    jq.process_some(time.time() + 0.01)
    assert len(jq) == 1
    assert slept == 1
    # Let the last slow job finish
    jq.process_some(time.time() + 0.01)
    assert not jq
    assert slept == 2

    # Exception raising job
    counter = 0
    t = time.time()
    jq.add_job(job1)
    jq.add_job(bad_job)
    assert len(jq) == 2
    jq.process_some(t + 1)
    assert counter == 1  # bad job doesn't touch
    assert 'exception raised' in logger.logged_msg
    assert not jq

    # Async jobs
    acounter = adone = 0

    def ajob_done(task):
        assert task.done()
        nonlocal adone
        adone += 1
        task.result()  # Collect the result

    async def ajob():
        nonlocal acounter
        acounter += 1

    async def ajob_bad():
        nonlocal acounter
        acounter += 1
        undefined_var

    jq.add_coroutine(ajob(), ajob_done)
    assert jq and len(jq) == 1
    jq.add_coroutine(ajob(), ajob_done)
    assert jq and len(jq) == 2
    assert not adone and not acounter
    jq.loop.run_until_complete(jq.wait_for_all())
    assert not jq and len(jq) == 0
    assert acounter == 2
    assert adone == 2

    # Null handlers
    adone = acounter = 0
    jq.add_coroutine(ajob(), None)
    assert jq and len(jq) == 1
    jq.add_coroutine(ajob(), None)
    assert jq and len(jq) == 2
    assert not adone and not acounter
    jq.loop.run_until_complete(jq.wait_for_all())
    assert not jq and len(jq) == 0
    assert acounter == 2
    assert adone == 0

    # Bad handlers
    logger.logged_msg = ''
    t = time.time()
    adone = acounter = 0
    jq.add_coroutine(ajob_bad(), None)
    assert jq and len(jq) == 1
    assert not adone and not acounter
    jq.loop.run_until_complete(jq.wait_for_all())
    assert not jq and len(jq) == 0
    assert acounter == 1
    assert adone == 0
    assert 'exception raised' in logger.logged_msg
    logger.logged_msg = ''
    jq.add_coroutine(ajob_bad(), ajob_done)
    assert jq and len(jq) == 1
    jq.loop.run_until_complete(jq.wait_for_all())
    assert not jq and len(jq) == 0
    assert acounter == 2
    assert adone == 1
    assert 'exception raised' in logger.logged_msg


def test_jq_cancellation():
    logger = MyLogger('test')
    jq = JobQueue(asyncio.get_event_loop(), logger=logger)

    # Async and sync jobs
    acounter = adone = acancelled = counter = 0

    def ajob_done(task):
        assert task.done()
        nonlocal adone, acancelled
        adone += 1
        if task.cancelled():
            acancelled += 1
        task.result()  # Collect the result

    async def ajob():
        nonlocal acounter
        acounter += 1

    def sjob():
        nonlocal counter
        counter += 1

    jq.add_coroutine(ajob(), ajob_done)
    assert len(jq) == 1
    jq.add_job(sjob)
    assert len(jq) == 2
    assert not acounter and not adone and not counter and not acancelled
    jq.cancel_all()

    assert len(jq) == 1  # The async job only
    assert not acounter and not adone and not counter and not acancelled
    assert not logger.logged_msg
    jq.loop.run_until_complete(jq.wait_for_all())
    jq.process_some(time.time() + 1)
    assert not jq
    assert not acounter and not counter
    assert adone == 1
    assert acancelled == 1
    # Test the JobQueue catches and ignores the cancellation exception
    assert not logger.logged_msg

    # Add a sync and async job
    jq.add_job(sjob)
    assert not jq   # Ignored
    adone = acancelled = acounter = 0
    jq.add_coroutine(ajob(), ajob_done)
    # Effect only happens when loop runs
    assert not acounter and not acancelled and not adone
    jq.loop.run_until_complete(jq.wait_for_all())
    assert not acounter
    assert acancelled == 1
    assert adone == 1

    # Re-cancel to no effect
    adone = acancelled = acounter = 0
    jq.cancel_all()
    jq.process_some(time.time() + 1)
    assert not acounter and not acancelled and not adone and not counter
    assert not logger.logged_msg

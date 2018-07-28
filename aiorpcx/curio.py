# The code below is mostly my own but based on the interfaces of the
# curio library by David Beazley.  I'm considering switching to using
# curio.  In the mean-time this is an attempt to provide a similar
# clean, pure-async interface and move away from direct
# framework-specific dependencies.  As asyncio differs in its design
# it is not possible to provide identical semantics.
#
# The curio library is distributed under the following licence:
#
# Copyright (C) 2015-2017
# David Beazley (Dabeaz LLC)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# * Redistributions of source code must retain the above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * Neither the name of the David Beazley or Dabeaz LLC may be used to
#   endorse or promote products derived from this software without
#   specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging
from asyncio import CancelledError, get_event_loop, Event, sleep, Task
from collections import deque

from aiorpcx.util import normalize_corofunc


__all__ = (
    'run_in_thread', 'spawn',
    'TaskGroupError', 'TaskGroup',
    'TaskTimeout', 'TimeoutCancellationError', 'UncaughtTimeoutError',
    'timeout_after', 'timeout_at', 'ignore_after', 'ignore_at',
)


async def run_in_thread(func, *args):
    '''Run a function in a separate thread, and await its completion.'''
    return await get_event_loop().run_in_executor(None, func, *args)


async def spawn(coro, *args):
    coro = normalize_corofunc(coro, args)
    return get_event_loop().create_task(coro)


class TaskGroupError(Exception):
    '''
    Raised if one or more tasks in a task group raised an error.
    The .failed attribute contains a list of all tasks that died.
    The .errors attribute contains a set of all exceptions raised.
    '''
    def __init__(self, failed):
        self.args = (failed,)
        self.failed = failed
        self.errors = {type(task.exception()) for task in failed}

    def __str__(self):
        errors = ', '.join(err.__name__ for err in self.errors)
        return f'TaskGroupError({errors})'

    def __iter__(self):
        return self.failed.__iter__()


class TaskGroup(object):
    '''A class representing a group of executing tasks. tasks is an
    optional set of existing tasks to put into the group. New tasks
    can later be added using the spawn() method below. wait specifies
    the policy used for waiting for tasks. See the join() method
    below. Each TaskGroup is an independent entity. Task groups do not
    form a hierarchy or any kind of relationship to other previously
    created task groups or tasks. Moreover, Tasks created by the top
    level spawn() function are not placed into any task group. To
    create a task in a group, it should be created using
    TaskGroup.spawn() or explicitly added using TaskGroup.add_task().

    completed attribute: the first task that completed with a result
    in the group.  Takes into account the wait option used in the
    TaskGroup constructor (but not in the join method)`.
    '''

    def __init__(self, tasks=(), *, wait=all, report_crash=True):
        self._done = deque()
        self._pending = set()
        self._wait = wait
        self._done_event = Event()
        self._logger = logging.getLogger(self.__class__.__name__)
        self._closed = False
        self.completed = None
        for task in tasks:
            self._add_task(task, report_crash)

    def _add_task(self, task, report_crash):
        '''Add an already existing task to the task group.'''
        if hasattr(task, '_task_group'):
            raise RuntimeError('task is already part of a group')
        if self._closed:
            raise RuntimeError('task group is closed')
        task._task_group = self
        if task.done():
            self._done.append(task)
        else:
            task._report_crash = report_crash
            self._pending.add(task)
            task.add_done_callback(self._on_done)

    def _on_done(self, task):
        task._task_group = None
        self._pending.remove(task)
        self._done.append(task)
        self._done_event.set()
        if self.completed is None:
            if not task.cancelled() and not task.exception():
                if self._wait is object and task.result() is None:
                    pass
                else:
                    self.completed = task
        if task._report_crash and not task.cancelled():
            try:
                task.result()
            except Exception as e:
                self._logger.error('task crashed: %r', task, exc_info=True)

    async def spawn(self, coro, *args, report_crash=True):
        '''Create a new task that’s part of the group. Returns a Task
        instance. The report_crash flag controls whether a traceback
        is logged when a task exits with an uncaught exception.
        '''
        task = await spawn(coro, *args)
        await self.add_task(task, report_crash=report_crash)
        return task

    async def add_task(self, task, *, report_crash=True):
        '''Add an already existing task to the task group.'''
        self._add_task(task, report_crash)

    async def next_done(self):
        '''Returns the next completed task.  Returns None if no more tasks
        remain. A TaskGroup may also be used as an asynchronous iterator.
        '''
        if not self._done and self._pending:
            self._done_event.clear()
            await self._done_event.wait()
        if self._done:
            return self._done.popleft()
        return None

    async def next_result(self):
        '''Returns the result of the next completed task. If the task failed
        with an exception, that exception is raised. A RuntimeError
        exception is raised if this is called when no remaining tasks
        are available.'''
        task = await self.next_done()
        if not task:
            raise RuntimeError('no tasks remain')
        return task.result()

    async def join(self, *, wait=all):
        '''Wait for tasks in the group to terminate.  If there are none,
        return immediately.

        If wait is all, then wait for all tasks to complete.

        If wait is any then wait for any task to complete and cancel
        remaining tasks.

        If wait is object, then wait for any task to complete and
        return a non-None object.

        While doing the above, if any task raises an error, then all
        remaining tasks are immediately cancelled and a TaskGroupError
        exception is raised.

        If the join() operation itself is cancelled, all remaining
        tasks in the group are also cancelled.

        If a TaskGroup is used as a context manager, the join() method
        is called on context-exit.

        Once join() returns, no more tasks may be added to the task
        group.  Tasks can be added while join() is running.
        '''
        def errored(task):
            return not task.cancelled() and task.exception()

        if wait not in (any, all, object):
            raise ValueError('invalid wait argument')

        try:
            if wait in (all, object):
                while True:
                    task = await self.next_done()
                    if task is None:
                        return
                    if errored(task):
                        break
                    if wait is object and task.result() is not None:
                        return
            else:  # any
                task = await self.next_done()
                if task is None or not errored(task):
                    return
        finally:
            await self.cancel_remaining()
            self._closed = True

        bad_tasks = [task]
        bad_tasks.extend(task for task in self._done if errored(task))
        if bad_tasks:
            raise TaskGroupError(bad_tasks)

    async def cancel_remaining(self):
        '''Cancel all remaining tasks.'''
        self._closed = True
        pending = list(self._pending)
        for task in pending:
            task.cancel()
        await sleep(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        task = await self.next_done()
        if task:
            return task
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type:
            await self.cancel_remaining()
        else:
            await self.join(wait=self._wait)


class TaskTimeout(CancelledError):
    pass


class TimeoutCancellationError(CancelledError):
    pass


class UncaughtTimeoutError(Exception):
    pass


def _set_new_deadline(task, deadline):
    def timeout_task():
        # Unfortunately task.cancel is all we can do with asyncio
        task.cancel()
        task._timed_out = deadline
    task._deadline_handle = task._loop.call_at(deadline, timeout_task)


def _set_task_deadline(task, deadline):
    deadlines = getattr(task, '_deadlines', [])
    if deadlines:
        if deadline < min(deadlines):
            task._deadline_handle.cancel()
            _set_new_deadline(task, deadline)
    else:
        _set_new_deadline(task, deadline)
    deadlines.append(deadline)
    task._deadlines = deadlines
    task._timed_out = None


def _unset_task_deadline(task):
    deadlines = task._deadlines
    timed_out_deadline = task._timed_out
    uncaught = timed_out_deadline not in deadlines
    task._deadline_handle.cancel()
    deadlines.pop()
    if deadlines:
        _set_new_deadline(task, min(deadlines))
    return timed_out_deadline, uncaught


class TimeoutAfter(object):

    def __init__(self, deadline, *, ignore=False, absolute=False):
        self._deadline = deadline
        self._ignore = ignore
        self._absolute = absolute
        self.expired = False

    async def __aenter__(self):
        task = Task.current_task()
        if not self._absolute:
            self._deadline += task._loop.time()
        _set_task_deadline(task, self._deadline)
        self.expired = False
        self._task = task
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        timed_out_deadline, uncaught = _unset_task_deadline(self._task)
        if exc_type not in (CancelledError, TaskTimeout,
                            TimeoutCancellationError):
            return False
        if timed_out_deadline == self._deadline:
            self.expired = True
            if self._ignore:
                return True
            raise TaskTimeout(timed_out_deadline) from None
        if timed_out_deadline is None:
            assert exc_type is CancelledError
            return False
        if uncaught:
            raise UncaughtTimeoutError('uncaught timeout received')
        if exc_type is TimeoutCancellationError:
            return False
        raise TimeoutCancellationError(timed_out_deadline) from None


async def _timeout_after_func(seconds, absolute, coro, args):
    coro = normalize_corofunc(coro, args)
    async with TimeoutAfter(seconds, absolute=absolute):
        return await coro


def timeout_after(seconds, coro=None, *args):
    '''Execute the specified coroutine and return its result. However,
    issue a cancellation request to the calling task after seconds
    have elapsed.  When this happens, a TaskTimeout exception is
    raised.  If coro is None, the result of this function serves
    as an asynchronous context manager that applies a timeout to a
    block of statements.

    timeout_after() may be composed with other timeout_after()
    operations (i.e., nested timeouts).  If an outer timeout expires
    first, then TimeoutCancellationError is raised instead of
    TaskTimeout.  If an inner timeout expires and fails to properly
    TaskTimeout, a UncaughtTimeoutError is raised in the outer
    timeout.

    '''
    if coro:
        return _timeout_after_func(seconds, False, coro, args)

    return TimeoutAfter(seconds)


def timeout_at(clock, coro=None, *args):
    '''Execute the specified coroutine and return its result. However,
    issue a cancellation request to the calling task after seconds
    have elapsed.  When this happens, a TaskTimeout exception is
    raised.  If coro is None, the result of this function serves
    as an asynchronous context manager that applies a timeout to a
    block of statements.

    timeout_after() may be composed with other timeout_after()
    operations (i.e., nested timeouts).  If an outer timeout expires
    first, then TimeoutCancellationError is raised instead of
    TaskTimeout.  If an inner timeout expires and fails to properly
    TaskTimeout, a UncaughtTimeoutError is raised in the outer
    timeout.

    '''
    if coro:
        return _timeout_after_func(clock, True, coro, args)

    return TimeoutAfter(clock, absolute=True)


async def _ignore_after_func(seconds, absolute, coro, args, timeout_result):
    coro = normalize_corofunc(coro, args)
    async with TimeoutAfter(seconds, absolute=absolute, ignore=True):
        return await coro

    return timeout_result


def ignore_after(seconds, coro=None, *args, timeout_result=None):
    '''Execute the specified coroutine and return its result. Issue a
    cancellation request after seconds have elapsed. When a timeout
    occurs, no exception is raised. Instead, timeout_result is
    returned.

    If coro is None, the result is an asynchronous context manager
    that applies a timeout to a block of statements. For the context
    manager case, the resulting context manager object has an expired
    attribute set to True if time expired.

    Note: ignore_after() may also be composed with other timeout
    operations. TimeoutCancellationError and UncaughtTimeoutError
    exceptions might be raised according to the same rules as for
    timeout_after().
    '''
    if coro:
        return _ignore_after_func(seconds, False, coro, args, timeout_result)

    return TimeoutAfter(seconds, ignore=True)


def ignore_at(clock, coro=None, *args, timeout_result=None):
    '''
    Stop the enclosed task or block of code at an absolute
    clock value. Same usage as ignore_after().
    '''
    if coro:
        return _ignore_after_func(clock, True, coro, args, timeout_result)

    return TimeoutAfter(clock, absolute=True, ignore=True)

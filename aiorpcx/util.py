# Copyright (c) 2018, Neil Booth
#
# All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

__all__ = ('JobQueue', )

import asyncio
from collections import deque, namedtuple
from functools import partial
import inspect
import logging
import time
import traceback


def is_async_call(func):
    '''inspect.iscoroutinefunction that looks through partials.'''
    while isinstance(func, partial):
        func = func.func
    return inspect.iscoroutinefunction(func)


# other_params: None means cannot be called with keyword arguments only
# any means any name is good
SignatureInfo = namedtuple('SignatureInfo', 'min_args max_args '
                           'required_names other_names')


def signature_info(func):
    params = inspect.signature(func).parameters
    min_args = max_args = 0
    required_names = []
    other_names = []
    no_names = False
    for p in params.values():
        if p.kind == p.POSITIONAL_OR_KEYWORD:
            max_args += 1
            if p.default is p.empty:
                min_args += 1
                required_names.append(p.name)
            else:
                other_names.append(p.name)
        elif p.kind == p.KEYWORD_ONLY:
            other_names.append(p.name)
        elif p.kind == p.VAR_POSITIONAL:
            max_args = None
        elif p.kind == p.VAR_KEYWORD:
            other_names = any
        elif p.kind == p.POSITIONAL_ONLY:
            max_args += 1
            if p.default is p.empty:
                min_args += 1
            no_names = True

    if no_names:
        other_names = None

    return SignatureInfo(min_args, max_args, required_names, other_names)


class JobQueue(object):
    '''A JobQueue for use with a loop framework such as asyncio.'''

    def __init__(self, loop, *, logger=None):
        self.loop = loop
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self.tasks = set()
        self.jobs = deque()
        self.cancelled = False

    def _on_task_done(self, on_done, task):
        self.tasks.remove(task)
        try:
            if on_done:
                on_done(task)
            else:
                task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            self.logger.exception('exception raised processing task result')

    def add_coroutine(self, coro, on_done):
        task = self.loop.create_task(coro)
        task.add_done_callback(partial(self._on_task_done, on_done))
        self.tasks.add(task)
        if self.cancelled:
            task.cancel()

    def add_job(self, job):
        if not self.cancelled:
            self.jobs.append(job)

    def process_some(self, until):
        '''Process synchronous jobs until the given time is reached.

        Returns when the first job completes on or after until.'''
        while self.jobs and time.time() < until:
            job = self.jobs.popleft()
            try:
                job()
            except Exception:
                self.logger.exception('exception raised processing job')

    def __len__(self):
        return len(self.tasks) + len(self.jobs)

    def cancel_all(self):
        '''Drop all uncompleted synchronous jobs.  Cancel all uncompleted
        async tasks.

        Once cancel_all() is called, adding a syncronous job will be ignored,
        adding an asynchronous job will cause it to be immediately cancelled.

        If called more than once, subsequent calls have no effect.
        '''
        if self.cancelled:
            return
        self.cancelled = True
        self.jobs.clear()
        for task in self.tasks:
            task.cancel()

    async def wait_for_all(self):
        '''Waits for all tasks to complete.'''
        # For some reason wait requires non-empty set...
        if self.tasks:
            await asyncio.wait(self.tasks)

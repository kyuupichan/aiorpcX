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

'''RPC message processing, independent of transport and RPC protocol.'''

__all__ = ('RPCError', 'RPCProtocolBase')

from asyncio import Future, CancelledError
from functools import partial
import itertools
import logging

from .util import is_async_call, signature_info


class RPCRequest(object):
    '''An RPC request or notification that has been received, or an
    outgoing notification.

    Outgoing requests are represented by RPCRequestOut.
    '''

    def __init__(self, method, args, request_id):
        self.method = method
        if args is None:
            self.args = []
        else:
            if not isinstance(args, (list, tuple, dict)):
                raise ValueError('request args must be a list or dictionary')
            self.args = args
        self.request_id = request_id
        # Ill-formed requests for which the protocol couldn't
        # determine a meaning pass an RPCError as their method
        if isinstance(method, RPCError):
            method.request_id = request_id

    def __repr__(self):
        return (f'RPCRequest({self.method!r}, {self.args!r}, '
                f'{self.request_id!r})')

    def is_notification(self):
        return self.request_id is None


class RPCRequestOut(RPCRequest, Future):
    '''Represents an outgoing RPC request which expects a response.

    You can specify a callback to call when a response arrives; it is
    passed the request object.  The result can be retrieved with its
    result() method.

    The request can be await-ed on and/or it can be given a handler to
    call on completion.
    '''

    id_counter = itertools.count()

    def __init__(self, method, args, on_done, *, loop=None):
        '''Initialize a request using the next unique request ID.

        on_done - can be None
        '''
        RPCRequest.__init__(self, method, args, next(self.id_counter))
        Future.__init__(self, loop=loop)
        if on_done:
            self.add_done_callback(on_done)


class RPCResponse(object):
    '''An RPC response, incoming or outgoing.

    An error is indicated by a result that is an RPCError.
    '''

    def __init__(self, result, request_id):
        # result is an RPCError object if an error was returned
        self.result = result
        self.request_id = request_id
        if isinstance(result, RPCError):
            result.request_id = request_id

    def __repr__(self):
        return f'RPCResponse({self.result!r}, {self.request_id!r})'


class RPCError(Exception):
    '''An RPC error.

    When an RPC error response is received, an object of this type is
    embedded in the RPCResponse object as its "result" and passed to
    the user-defined response handler.

    When protocol.message_to_item() parses an incoming request (or
    batch item), if it is ill-formed (for example, it cannot be
    parsed, or the method name is not a string),
    protocol.message_to_item() should return it embedded in an
    RPCRequest object as its "method".  When the request is processed
    the framework will embed it in a RPCResponse object to send over
    the network.

    A request handler can raise it to cause the framework to send an
    error response.
    '''

    def __init__(self, code, message, request_id=None):
        super().__init__(message, code)
        self.message = message
        self.code = code
        self.request_id = request_id

    def __repr__(self):
        if self.request_id is None:
            return f'RPCError({self.code:d}, {self.message!r})'
        else:
            return (f'RPCError({self.code:d}, {self.message!r}, '
                    f'{self.request_id!r})')

    __str__ = __repr__


class RPCBatch(object):
    '''An incoming or outgoing RPC response batch, or an incoming RPC
    request batch.
    '''

    def __init__(self, items):
        self.items = items
        assert isinstance(items, list)
        assert items
        assert (all(isinstance(item, RPCRequest) for item in items) or
                all(isinstance(item, RPCResponse) for item in items))

    def requests(self):
        '''An iterable of the batch items that are not notifications.

        For a response batch simply returns everything.'''
        for item in self.items:
            if item.request_id is not None:
                yield item

    def request_ids(self):
        '''Return a frozenset of all request IDs in the batch, ignoring
        notifications.
        '''
        return frozenset(item.request_id for item in self.requests())

    def is_request_batch(self):
        return isinstance(self.items[0], RPCRequest)

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __repr__(self):
        return f'RPCBatch({self.items!r})'


class RPCBatchOut(RPCBatch, Future):
    '''Represents an outgoing RPC batch request.

    You can specify a callback to call when all requests in the batch
    have completed; it is passed the batch object.  The batch object
    does not have a meaningful result.

    The batch can be await-ed on and/or it can be given a handler to
    call on completion.  This is also true for its member requests.
    '''
    def __init__(self, *, loop=None):
        '''Create an empty outgoing batch request.  Members can be
        added with add_request and add_notification.'''
        # We don't call RPCBatch.__init__()
        self.items = []
        Future.__init__(self, loop=loop)

    def _cancel_requests(self):
        for request in self.requests():
            request.cancel()

    def cancel(self):
        super().cancel()
        self._cancel_requests()

    def set_result(self, result):
        super().set_result(result)
        self._cancel_requests()

    def set_exception(self, exception):
        super().set_exception(exception)
        self._cancel_requests()

    def add_request(self, method, args=None, on_done=None):
        request = RPCRequestOut(method, args, on_done)
        self.items.append(request)
        return request

    def add_notification(self, method, args=None):
        request = RPCRequest(method, args, None)
        self.items.append(request)
        return request


class RPCHelperBase(object):
    '''Abstract base class of an object that handles RPC requests, job
    queueing and message sending for RPCProcessor.'''

    # The maximum response size.  Responses above this size send an
    # error response instead.
    max_response_size = 1000000

    def send_message(self, message):
        '''Called when there is a message to send over the network.  The
        message is unframed.  If a request was cancelled, send_message
        is called with an empty message, which should be ignored.

        The derived class may want to queue several messages and send
        them as a batch, or delay / throttle the sends in some way.
        '''
        raise NotImplementedError

    def create_task(self, coro):
        '''Create a task to run the given coroutine.  Return the task.'''
        raise NotImplementedError

    def semaphore(self):
        '''Return a semaphore to limit concurrency.'''
        raise NotImplementedError

    def notification_handler(self, method):
        '''Return the handler for the given notification.

        The handler can be synchronous or asynchronous.  When called
        the return value is ignored.
        '''
        return None

    def request_handler(self, method):
        '''Return the handler for the given request method.

        The handler can be synchronous or asynchronous.  The return value
        is sent as an RPC response.'''
        return None


class RPCProtocolBase(object):

    @classmethod
    def request_message(cls, item):
        '''Convert an RPCRequest item to a message.'''
        raise NotImplementedError

    @classmethod
    def response_message(cls, item):
        '''Convert an RPCResponse item to a message.'''
        raise NotImplementedError

    @classmethod
    def error_message(cls, item):
        '''Convert an RPCError item to a message.'''
        raise NotImplementedError

    @classmethod
    def batch_message(cls, item):
        '''Convert an RPCBatch item (a request batch) to a message.'''
        raise NotImplementedError

    @classmethod
    def batch_message_from_parts(cls, messages):
        '''Convert messages, one per batch item, into a batch message.  At
        least one message must be passed.
        '''
        raise NotImplementedError

    @classmethod
    def internal_error(cls, request_id):
        '''Return an internal error RPCError object.'''
        return RPCError(cls.INTERNAL_ERROR,
                        'internal error processing request', request_id)

    @classmethod
    def args_error(cls, message):
        '''Return an invalid args RPCError object.'''
        return RPCError(cls.INVALID_ARGS, message, None)

    @classmethod
    def invalid_request(cls, message, request_id=None):
        '''Return an invalid request RPCError object.'''
        return RPCError(cls.INVALID_REQUEST, message, request_id)

    @classmethod
    def method_not_found(cls, message):
        '''Return a method-not-found RPCError object.'''
        return RPCError(cls.METHOD_NOT_FOUND, message, None)


class RPCProcessor(object):
    '''Handles RPC message processing.

    Coordinates the processing of incoming and outgoing RPC requests,
    responses and notifications.
    '''

    def __init__(self, protocol, helper):
        self.protocol = protocol
        self.helper = helper
        self.logger = logging.getLogger(self.__class__.__name__)
        # Sent requests and batch requests awaiting a response.  For an
        # RPCRequestOut object the key is its request ID; for a batch
        # it is its frozenset of request IDs
        self.requests = {}
        # Statistics
        # Count of requests sent and received (a batch of 10 counts as 10)
        # Responses are not tracked
        self.sent_count = 0
        self.recv_count = 0
        # Number of requests or notifications that raised an RPCError
        # during processing because the request was invalid in some way.
        self.errors = 0
        # This includes internal errors, which are exceptions other than
        # RPCError raised processing requests.
        self.internal_errors = 0
        # Incoming unprocessed request count
        self.pending_requests = 0

    def _error_message(self, request, error, force=False):
        '''Return an error message when an exception (error) is raised.'''
        # Batches don't have a request ID
        request_id = getattr(request, 'request_id', None)
        if isinstance(error, RPCError):
            self.errors += 1
            error.request_id = request_id
            self.logger.debug('error processing request: %s %s',
                              repr(error), repr(request))
            # Notifications don't get a response, unless ill-formed
            if request_id is None and not force:
                return None
        else:
            self.internal_errors += 1
            self.logger.exception('exception raised processing request: %s',
                                  repr(request))
            if request_id is None:
                return None
            error = self.protocol.internal_error(request_id)

        return self.protocol.error_message(error)

    def _response_message(self, request, task):
        '''Return a response message to a request, or None if no message
        should be sent.  Log errors.

        Notifications return a response only if ill-formed.'''
        try:
            result = task.result()
            if request.request_id is None:
                return None
            response = RPCResponse(result, request.request_id)
            return self.protocol.response_message(response)
        except CancelledError as error:
            return None
        except Exception as error:
            return self._error_message(request, error,
                                       isinstance(request.method, RPCError))

    def _send_response(self, response, request):
        if len(response) > self.helper.max_response_size > 0:
            msg = f'response too large (at least {len(response)} bytes)'
            error = self.protocol.invalid_request(msg)
            response = self._error_message(request, error, True)
        self.helper.send_message(response)

    def _handle_request(self, request, task):
        response = self._response_message(request, task)
        if response:
            self._send_response(response, request)

    def _rpc_call(self, request):
        '''Return a partial function call that calls the RPC function
        to handle the request with the appropriate arguments.

        If the request is bad an RPCError is raised.  Any exceptions
        raised when determining the handler function are passed on.
        '''
        # Raise ill-formed requests here so that they are logged
        method = request.method
        if isinstance(method, RPCError):
            raise method

        # Let through any exceptions raised when determining the handler
        request_id = request.request_id
        if request_id is None:
            handler = self.helper.notification_handler(method)
        else:
            handler = self.helper.request_handler(method)
        if not handler:
            raise self.protocol.method_not_found(f'unknown method "{method}"')

        # We must test for too few and too many arguments.  How
        # depends on whether the arguments were passed as a list or as
        # a dictionary.
        info = signature_info(handler)
        args = request.args
        if isinstance(args, list):
            if len(args) < info.min_args:
                s = '' if len(args) == 1 else 's'
                raise self.protocol.args_error(
                    f'{len(args)} argument{s} passed to method '
                    f'"{method}" but it requires {info.min_args}')
            if info.max_args is not None and len(args) > info.max_args:
                s = '' if len(args) == 1 else 's'
                raise self.protocol.args_error(
                    f'{len(args)} argument{s} passed to method '
                    f'{method} taking at most {info.max_args}')
            return partial(handler, *args)

        # Arguments passed by name
        if info.other_names is None:
            raise self.protocol.args_error(f'method "{method}" cannot '
                                           f'be called with named arguments')

        missing = set(info.required_names).difference(args)
        if missing:
            s = '' if len(missing) == 1 else 's'
            missing = ', '.join(sorted(f'"{name}"' for name in missing))
            raise self.protocol.args_error(f'method "{method}" requires '
                                           f'parameter{s} {missing}')

        if info.other_names is not any:
            excess = set(args).difference(info.required_names)
            excess = excess.difference(info.other_names)
            if excess:
                s = '' if len(excess) == 1 else 's'
                excess = ', '.join(sorted(f'"{name}"' for name in excess))
                raise self.protocol.args_error(f'method "{method}" does not '
                                               f'take parameter{s} {excess}')
        return partial(handler, **args)

    async def _process_request(self, request):
        '''Process a request or notification and return its result.

        Raises RPCError to ill-formed or erroneous requests.'''
        self.recv_count += 1
        self.pending_requests += 1
        try:
            async with self.helper.semaphore():
                rpc_call = self._rpc_call(request)
                if is_async_call(rpc_call):
                    return await rpc_call()
                else:
                    return rpc_call()
        finally:
            self.pending_requests -= 1

    def _process_request_batch(self, batch):
        '''Process a request batch.  Create a task for each sub-request, and
        collect the results.  Send a response, if not empty, once all
        results have come in.
        '''
        def send_batch_response():
            response = self.protocol.batch_message_from_parts(parts)
            self._send_response(response, batch)

        def fail_batch():
            nonlocal failed
            failed = True
            for task in tasks:
                task.cancel()

        def collect_response(request, task):
            nonlocal failed, response_size
            # Collect the response even if we don't use it to avoid
            # diagnostics about uncomsumed exceptions
            response = self._response_message(request, task)
            if response:
                parts.append(response)
                response_size += len(response)
                if response_size > self.helper.max_response_size > 0:
                    send_batch_response()
                    fail_batch()
            tasks.remove(task)
            # If any task was cancelled fail the batch
            if task.cancelled() and not failed:
                fail_batch()
            if not tasks and parts and not failed:
                send_batch_response()

        parts = []
        failed = False
        response_size = 0
        tasks = set()
        for request in batch:
            task = self.helper.create_task(self._process_request(request))
            task.add_done_callback(partial(collect_response, request))
            tasks.add(task)

    def _handle_request_response(self, request, response):
        if isinstance(response.result, RPCError):
            self.logger.debug('request returned errror: %s %s',
                              repr(request), repr(response.result))
            request.set_exception(response.result)
        else:
            request.set_result(response.result)

    def _process_response(self, response):
        request_id = response.request_id
        if request_id is None:
            self.logger.debug('missing id: %s', repr(response))
            return

        request = self.requests.pop(request_id, None)
        if request:
            self._handle_request_response(request, response)
        else:
            self.logger.debug('response to unsent or forced request: %s',
                              repr(response))

    def _process_response_batch(self, batch):
        request_ids = batch.request_ids()
        batch_request = self.requests.pop(request_ids, None)
        if batch_request:
            requests_by_id = {item.request_id: item
                              for item in batch_request.requests()}
            for response in batch:
                request_id = response.request_id
                if request_id is None:
                    self.logger.debug('batch response missing id: %s',
                                      repr(response))
                else:
                    request = requests_by_id[request_id]
                    self._handle_request_response(request, response)
        else:
            self.logger.debug('response to unsent or forced '
                              'batch request: %s', repr(batch))

    # External API - methods for use by a session layer
    def message_received(self, message):
        '''Analyse an incoming message and queue it for processing.

        Any response will be sent to send_msessage.  This can happen
        before or after this function returns.
        '''
        item = self.protocol.message_to_item(message)
        if isinstance(item, RPCRequest):
            task = self.helper.create_task(self._process_request(item))
            task.add_done_callback(partial(self._handle_request, item))
        elif isinstance(item, RPCResponse):
            self._process_response(item)
        elif isinstance(item, RPCBatch):
            if item.is_request_batch():
                self._process_request_batch(item)
            else:
                self._process_response_batch(item)
        else:
            # Protocol auto-detection
            assert isinstance(item(), RPCProtocolBase)
            self.protocol = item
            self.message_received(message)

    def send_request(self, request):
        '''Send a request.

        If it is not a notification record the request ID so that an
        incoming response can be handled.
        '''
        self.sent_count += 1
        if isinstance(request, RPCRequestOut):
            def request_done(request):
                self.requests.pop(request.request_id, None)

            self.requests[request.request_id] = request
            request.add_done_callback(request_done)
        self.helper.send_message(self.protocol.request_message(request))

    def send_batch(self, batch, on_done=None):
        '''Send a batch request.

        Unless it is all notifications, record the request IDs of the
        batch memebers so that an incoming batch response can be
        handled.
        '''
        if not batch:
            raise RuntimeError('request batch cannot be empty')

        self.sent_count += len(batch)
        if on_done:
            batch.add_done_callback(on_done)

        request_ids = batch.request_ids()
        if request_ids:
            def request_done(request):
                nonlocal remaining
                remaining -= 1
                if not remaining and not batch.done():
                    batch.set_result(False)

            def batch_done(batch):
                self.requests.pop(request_ids, None)

            remaining = len(request_ids)
            self.requests[request_ids] = batch
            for request in batch.requests():
                request.add_done_callback(request_done)
            batch.add_done_callback(batch_done)
        else:
            batch.set_result(False)

        self.helper.send_message(self.protocol.batch_message(batch))

    def all_requests(self):
        '''Returns a list of all requests that have not yet completed.

        If a batch requests is outstanding, it is returned and not the
        individual requests it is comprised of.
        '''
        return list(self.requests.values())

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

__all__ = ()

from asyncio import Future, CancelledError, sleep
from collections import deque
from functools import partial
import logging
import traceback

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

    _next_id = 0

    @classmethod
    def next_id(cls):
        result = cls._next_id
        cls._next_id += 1
        return result

    def __init__(self, method, args, on_done, *, loop=None):
        '''Initialize a request using the next unique request ID.

        on_done - can be None
        '''
        RPCRequest.__init__(self, method, args, self.next_id())
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
        assert (all(isinstance(item, RPCRequest) for item in items)
                or all(isinstance(item, RPCResponse) for item in items))

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
        self.items.append(RPCRequestOut(method, args, on_done))

    def add_notification(self, method, args=None):
        self.items.append(RPCRequest(method, args, None))


class RPCHelperBase(object):
    '''Abstract base class of an object that handles RPC requests, job
    queueing and message sending for RPCProcessor.'''

    def send_message(self, message):
        '''Called when there is a message to send over the network.  The
        message is unframed.  It might be empty, in which case it
        should be ignored.

        The derived class may want to queue several messages and send
        them as a batch, or delay / throttle the sends in some way.
        '''
        raise NotImplementedError

    def add_coroutine(self, coro, on_done):
        '''Schedule a coroutine as an asyncio task.

        If on_done is not None, call on_done(task).'''
        raise NotImplementedError

    def add_job(self, job):
        '''Schedule a synchronous function call.'''
        raise NotImplementedError

    def cancel_all(self):
        '''Cancel all uncompleted scheduled tasks and jobs.'''
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


class RPCProcessor(object):
    '''Handles RPC message processing.

    Coordinates the processing of incoming and outgoing RPC requests,
    responses and notifications.
    '''

    def __init__(self, protocol, helper, logger=None):
        self.protocol = protocol
        self.helper = helper
        self.logger = logger or logging.getLogger(self.__class__.__name__)
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

    def _evaluate(self, request, func):
        '''Evaluate func in the context of processing a request.

        If the call returns a result, return it.
        If the call raises a CancelledError, return the error.
        If the call raises an RPC error, log debug it and return the error
        with its request_id set to match that of the request.
        If the call raises any other exception, it indicates a bug in
        the software.  Log the exception, and return an RPCError indicating
        an internal error.
        '''
        try:
            return func()
        except CancelledError as error:
            return error
        except RPCError as error:
            self.errors += 1
            error.request_id = request.request_id
            self.logger.debug('error processing request: %s %s',
                              repr(error), repr(request))
            return error
        except Exception:
            self.internal_errors += 1
            self.logger.exception('exception raised processing request: %s',
                                  repr(request))
            return self.protocol.internal_error(request.request_id)

    def _result_to_message(self, result, request_id):
        '''Convert an RPC call result from _evalute to a message.'''
        if isinstance(result, RPCError):
            return self.protocol.error_message(result)
        elif isinstance(result, CancelledError):
            return b''
        else:
            response = RPCResponse(result, request_id)
            return self.protocol.response_message(response)

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

    def _process_request(self, request, send_func):
        '''Process a request or notification.

        If it is not a notification, send_func will be called with
        the response bytes, either now or later.
        '''
        self.recv_count += 1

        # Wrap the call to _rpc_call in _evaluate in order to
        # correctly handle exceptions it might raise.
        rpc_call = self._evaluate(request, partial(self._rpc_call, request))
        request_id = request.request_id

        if isinstance(rpc_call, RPCError):
            # Always send responses to ill-formed notifications
            if request_id is not None or isinstance(request.method, RPCError):
                send_func(self.protocol.error_message(rpc_call))
            return

        # Handling depends on whether the handler is async or not.
        # Notifications just evaluate the RPC call; requests send the result.
        def evaluate_send(func):
            result = self._evaluate(request, func)
            if request_id is not None:
                send_func(self._result_to_message(result, request_id))

        def on_async_done(task):
            evaluate_send(task.result)

        if is_async_call(rpc_call):
            self.helper.add_coroutine(rpc_call(), on_async_done)
        else:
            self.helper.add_job(partial(evaluate_send, rpc_call))

    def _process_request_batch(self, batch):
        '''For request batches, queue each request individually except
        that the results must be collected and not sent.  The response
        is only sent when all the individual responses have come in.
        '''
        def on_done(response):
            if response:
                parts.append(response)
            nonlocal remaining
            remaining -= 1
            if not remaining:
                message = self.protocol.batch_message_from_parts(parts)
                self.helper.send_message(message)

        parts = []
        remaining = len([item for item in batch
                         if item.request_id is not None or
                         isinstance(item.method, RPCError)])
        for request in batch:
            self._process_request(request, on_done)

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
            self._process_request(item, self.helper.send_message)
        elif isinstance(item, RPCResponse):
            self._process_response(item)
        else:
            assert isinstance(item, RPCBatch)
            if item.is_request_batch():
                self._process_request_batch(item)
            else:
                self._process_response_batch(item)

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

    async def close(self):
        '''Cancel all scheduled tasks and jobs: those for responses to
        incoming requests, and those waiting for responses to outgoing
        requests.

        Yields to the event loop so that cancellations and callbacks
        can be processed before returning.
        '''
        self.helper.cancel_all()
        for request in self.all_requests():
            # cancel() is a no-op if the future is done
            request.cancel()
        await sleep(0)
        assert not self.requests

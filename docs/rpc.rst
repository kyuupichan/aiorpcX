.. currentmodule:: aiorpcx

RPC items
=========

The :mod:`aiorpcx` module defines some classes, instances of which
will be returned by some of its APIs.  You should not need to
instantiate these objects directly.

An instance of one of these classes is called an :dfn:`item`.


.. class:: RPCRequest

  An RPC request or notification that has been received, or an
  outgoing notification.

  Outgoing requests are represented by :class:`RPCRequestOut` objects.

  .. attribute:: method

     The RPC method being invoked, a string.

     If an incoming request is ill-formed, so that, e.g., its method
     could not be determined, then this will be an :class:`RPCError`
     instance that describes the error.

  .. attribute:: args

     The arguments passed to the RPC method.  This is a list or a
     dictionary, a dictionary if the arguments were passed by
     parameter name.

  .. attribute:: request_id

     The ID given to the request so that responses can be associated
     with requests.  Normally an integer, or :const:`None` if the
     request is a :dfn:`notification`.  Rarely it might be a floating
     point number or string.

  .. method:: is_notification()

     Returns :const:`True` if the request is a notification (its
     :attr:`request_id` is :const:`None`), otherwise :const:`False`.


.. class:: RPCRequestOut

  An outgoing RPC request that is not a notification.  A subclass of
  :class:`RPCRequest` and :class:`asyncio.Future
  <https://docs.python.org/3/library/asyncio-task.html#asyncio.Future>`.

  When an outgoing request is created, typically via the
  :meth:`send_request` method of a client or server session, you can
  specify a callback to be called when the request is done.  The
  callback is passed the request object, and the result can be
  obtained via its :meth:`result` method.

  A request can also be await-ed.  Currently the result of await-ing
  is the same as calling :meth:`result` on the request but this may
  change in future.


.. class:: RPCResponse

  An incoming or outgoing response.  Outgoing response objects are
  automatically created by the framework when a request handler
  returns its result.

  .. attribute:: result

     The response result, a Python object.  If an error occurred this
     will be an :class:`RPCError` object describing the error.

  .. attribute:: request_id

     The ID of the request this is a repsonse to.  Notifications do
     not get responses so this will never be :const:`None`.

     If :attr:`result` in an :class:`RPCError` their
     :attr:`request_id` attributes will match.


.. class:: RPCError

  Represents an error, either in an :class:`RPCResponse` object if an
  error occurred processing a request, or in a :class:`RPCRequest` if
  an incoming request was ill-formed.

  .. attribute:: message

     The error message as a string.

  .. attribute:: code

     The error code, an integer.

  .. attribute:: request_id

     The ID of the request that gave an error if it could be
     determined, otherwise :const:`None`.


.. class:: RPCBatch

  Represents an incoming or outgoing RPC response batch, or an
  incoming RPC request batch.

  .. attribute:: items

     A list of the items in the batch.  The list cannot be empty, and
     each item will be an :class:`RPCResponse` object for a response
     batch, and an :class:`RPCRequest` object for a request batch.

     Notifications and requests can be mixed together.

     Batches are iterable through their items, and taking their length
     returns the length of the items list.

  .. method:: requests

     A generator that yields non-notification items of a request
     batch, or each item for a response batch.

  .. method:: request_ids

     A *frozenset* of all request IDs in the batch, ignoring
     notifications.

  .. method:: is_request_batch

     Return :const:`True` if the batch is a request batch.


.. class:: RPCBatchOut

  An outgoing RPC batch.  A subclass of :class:`RPCBatch` and
  :class:`asyncio.Future
  <https://docs.python.org/3/library/asyncio-task.html#asyncio.Future>`.

  When an outgoing request batch is created, typically via the
  :meth:`new_batch` method of a client or server session, you can
  specify a callback to be called when the batch is done.  The
  callback is passed the batch object.

  Each non-notification item in an :class:`RPCBatchOut` object is
  itself an :class:`RPCRequestOut` object that can be independently
  waited on or cancelled.  Notification items are :class:`RPCRequest`
  objects.  Since batches are responded to as a whole, all member
  requests will be completed simultaneously.  The order of callbacks
  of member requests, and of the batch itself, is unspecified.

  Cancelling a batch, or calling its :meth:`set_result` or
  :meth:`set_exception` methods cancels all its requests.

  .. method:: add_request(method, args=None, on_done=None)

    Add a request to the batch.  A callback can be specified that will
    be called when the request completes.  Returns the
    :class:`RPCRequestOut` request that was added to the batch.

  .. method:: add_notification(method, args=None)

    Add a notification to the batch.


RPC Protocol Classes
--------------------

RPC protocol classes should inherit from :class:`RPCProtocolBase`.
The base class provides a few utility functions returning
:class:`RPCError` objects.  The derived class should redefine some
constant class attributes.


.. class:: RPCProtocolBase


  .. attribute:: INTERNAL_ERROR

   The integer error code to use for an internal error.

  .. attribute:: INVALID_ARGS

   The integer error code to use when an RPC request passes invalid
   arguments.

  .. attribute:: INVALID_REQUEST

   The integer error code to use when an RPC request is invalid.

  .. attribute:: METHOD_NOT_FOUND

   The integer error code to use when an RPC request is for a
   non-existent method.

  .. classmethod:: JSONRPC.internal_error(request_id)

   Return an :class:`RPCError` object with error code
   :attr:`INTERNAL_ERROR` for the given request ID.  The error message
   will be ``"internal error processing request"``.

   :param request_id: the request ID, normally an integer or string
   :return: the error object
   :rtype: :class:`RPCError`

  .. classmethod:: JSONRPC.args_error(message)

   Return an :class:`RPCError` object with error code
   :attr:`INVALID_ARGS` with the given error message and a request ID
   of :const:`None`.

   :param str message: the error message
   :return: the error object
   :rtype: :class:`RPCError`

  .. classmethod:: JSONRPC.invalid_request(message, request_id=None)

   Return an :class:`RPCError` object with error code
   :attr:`INVALID_REQUEST` with the given error message and
   request ID.

   :param str message: the error message
   :param request_id: the request ID, normally an integer or string
   :return: the error object
   :rtype: :class:`RPCError`

  .. classmethod:: JSONRPC.method_not_found(message)

   Return an :class:`RPCError` object with error code
   :attr:`METHOD_NOT_FOUND` with the given error message and a request
   ID :const:`None`.

   :param str message: the error message
   :return: the error object
   :rtype: :class:`RPCError`

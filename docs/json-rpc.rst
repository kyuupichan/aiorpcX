.. currentmodule:: jsonrpc

JSON RPC
========

The :mod:`jsonrpc` module provides classes to interpret and construct
JSON RPC protocol messages.  Class instances are not used; all methods
are class methods.  Just call methods on the classes directly.

.. class:: JSONRPC

An abstract base class for concrete protocol classes.
:class:`JSONRPCv1` and :class:`JSONRPCv2` are derived protocol classes
implementing JSON RPC versions 1.0 and 2.0 in a strict way.

.. class:: JSONRPCv1

  A derived class of :class:`JSONRPC` implementing version 1.0 of the
  specification.

.. class:: JSONRPCv2

  A derived class of :class:`JSONRPC` implementing version 2.0 of the
  specification.

.. class:: JSONRPCLoose

  A derived class of :class:`JSONRPC`.  It accepts messages that
  conform to either version 1.0 or version 2.0.  As it is loose, it
  will also accept messages that conform strictly to neither version.

  Unfortunately it is not possible to send messages that are
  acceptable to strict implementations of both versions 1.0 and 2.0,
  so it sends version 2.0 messages.


Message interpretation
----------------------

.. classmethod:: JSONRPC.message_to_item(message)

  Convert a binary message into an RPC object describing the message
  and return it.

  :param bytes message: the message to interpret
  :return: the RPC object
  :rtype: :class:`RPCRequest`, :class:`RPCResponse` or
          :class:`RPCBatch`.

  If the message is ill-formed, return an :class:`RPCRequest` object
  with its :attr:`method` set to an :class:`RPCError` instance
  describing the error.

.. classmethod:: JSONRPC.detect_protocol(message)

  Attempt to detect the protocol version a JSON RPC message most
  closely conforms to.

  :param bytes message: the message to interpret
  :return: the protocol class, or :const:`None`
  :rtype: :class:`JSONRPC`

  If the message does not conform to either JSON RPC version 1.0 or
  version 2.0 :class:`JSONRPCLoose` or :const:`None` will be returned.
  Note that, e.g., a return value of :class:`JSONRPCv2` does *not*
  guarantee that the message is a valid JSON RPC version 2.0 message,
  only that it is closer to that protocol version than any other.


Message construction
--------------------

These functions convert an RPC item into a binary message that can be
passed over the network after framing.

.. classmethod:: JSONRPC.request_message(item)

   Convert an :class:`RPCRequest` item to a message.

   :param RPCRequest item: the request item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.response_message(item)

   Convert an :class:`RPCResponse` item to a message.

   :param RPCResponse item: the response item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.error_message(item)

   Convert an :class:`RPCError` item to a message.

   :param RPCError item: the error item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.batch_message(item)

   Convert an :class:`RPCError` item to a message.

   :param RPCBatch item: the batch item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.encode_payload(payload)

   Encode a Python object as a JSON string and convert it to bytes.
   If the object cannot be encoded as JSON, a JSON "internal error"
   error message is returned instead, with ID equal to the "id" member
   of `payload` if that is a dictionary, otherwise :const:`None`.

   :param payload: a Python object that can be represented as JSON.
      Numbers, strings, lists, dictionaries, :const:`True`,
      :const:`False` and :const:`None` are all valid.
   :return: a JSON message
   :rtype: bytes


Utility functions
-----------------

A few utility functions return :class:`RPCError` objects with error
codes set as desribed in the JSON RPC 2.0 standard but formatted
appropriately for the class's protocol version.

.. classmethod:: JSONRPC.internal_error(request_id)

   Return an :class:`RPCError` object representing a JSON RPC internal
   error for the given request ID.  The error message will be "internal
   error processing request".

   :param item: the request ID, normally an integer or string
   :return: the error object
   :rtype: :class:`RPCError`

.. classmethod:: JSONRPC.args_error(message)

   Return an :class:`RPCError` object representing a JSON RPC invalid
   arguments error with the given error message and a request ID of
   :const:`None`.

   :param str message: the error message
   :return: the error object
   :rtype: :class:`RPCError`

.. classmethod:: JSONRPC.invalid_request(message)

   Return an :class:`RPCError` object representing a JSON RPC invalid
   request error with the given error message and a request ID of
   :const:`None`.

   :param str message: the error message
   :return: the error object
   :rtype: :class:`RPCError`

.. classmethod:: JSONRPC.method_not_found(message)

   Return an :class:`RPCError` object representing a JSON RPC "method
   not found" error with the given error message and a request ID of
   :const:`None`.

   :param str message: the error message
   :return: the error object
   :rtype: :class:`RPCError`

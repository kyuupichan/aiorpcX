.. currentmodule:: aiorpcx

JSON RPC
========

The :mod:`aiorpcx` module provides classes to interpret and construct
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

.. class:: JSONRPCAutoDetect

  Auto-detects the JSON RPC protocol version spoken by the remote side
  based on the first incoming message, from :class:`JSONRPCv1`,
  :class:`JSONRPCv2` and :class:`JSONRPCLoose`.  The RPC processor
  will then switch to that protocol version.


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


Message construction
--------------------

These functions convert an RPC item into a binary message that can be
passed over the network after framing.

.. classmethod:: JSONRPC.request_message(item)

   Convert a request item to a message.

   :param item: an :class:`RPCRequest` item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.response_message(item)

   Convert a response item to a message.

   :param item: an :class:`RPCResponse` item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.error_message(item)

   Convert an error item to a message.

   :param item: an :class:`RPCError` item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.batch_message(item)

   Convert a batch item to a message.

   :param item: an :class:`RPCBatch` item
   :return: the message
   :rtype: bytes

.. classmethod:: JSONRPC.encode_payload(payload)

   Encode a Python object as a JSON string and convert it to bytes.
   If the object cannot be encoded as JSON, a JSON "internal error"
   error message is returned instead, with ID equal to the "id" member
   of `payload` if that is a dictionary, otherwise :const:`None`.

   :param payload: a Python object that can be represented as JSON.
                   Numbers, strings, lists, dictionaries,
                   :const:`True`, :const:`False` and :const:`None` are
                   all valid.
   :return: a JSON message
   :rtype: bytes

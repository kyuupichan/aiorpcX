.. currentmodule:: aiorpcx

Exceptions
----------

.. exception:: ConnectionError

   When a connection is lost that has pending requests, this exception is set on
   those requests.


Server
======

A simple wrapper around an :class:`asyncio.Server` object (see
`asyncio.Server
<https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.Server>`_).

.. class:: Server(protocol_factory, host=None, port=None, *, loop=None, \
           **kwargs)

  Creates a server that listens for connections on *host* and *port*.
  The server does not actually start listening until :meth:`listen` is
  await-ed.

  *protocol_factory* is any callable returning an
  :class:`asyncio.Protocol` instance.  You might find returning an
  instance of :class:`ServerSession`, or a class derived from it, more
  useful.

  *loop* is the event loop to use, or :func:`asyncio.get_event_loop()`
  if :const:`None`.

  *kwargs* are passed through to `loop.create_server()`_.

  A server instance has the following attributes:

  .. attribute:: loop

     The event loop being used.

  .. attribute:: host

     The host passed to the constructor

  .. attribute:: port

     The port passed to the constructor

  .. attribute:: server

     The underlying :class:`asyncio.Server` object when the server is
     listening, otherwise :const:`None`.

  .. method:: listen()

    Start listening for incoming connections.  Return an
    :class:`asyncio.Server` instance, which can also be accessed via
    :attr:`server`.

    This method is a `coroutine`_.

  .. method:: close()

    Close the listening socket if the server is listening, and wait
    for it to close.  Return immediately if the server is not
    listening.

    This does nothing to protocols and transports handling existing
    connections.  On return :attr:`server` is :const:`None`.

  .. method:: wait_closed()

    Returns when the server has closed.

    This method is a `coroutine`_.

Sessions
========

Convenience classes are provided for client and server sessions.


.. class:: ClientSession(host, port, *, rpc_protocol=None, framer=None, \
           scheduler=None, loop=None, proxy=None, **kwargs)

  An instance of an :class:`asyncio.Protocol` class that represents an
  RPC session with a remote server at *host* and *port*, as documented
  in `loop.create_connection()`_.`

  If *proxy* is not given, :meth:`create_connection` uses
  :meth:`loop.create_connection` to attempt a connection, otherwise
  :meth:`SOCKSProxy.create_connection`.  You can pass additional
  arguments to those functions with *kwargs* (*host* and *port* and
  *loop* are used as given).

  *rpc_protocol* specifies the RPC protocol the server speaks.  If
  :const:`None` the protocol returned by :meth:`default_rpc_protocol`
  is used.

  *framer* handles RPC message framing, and if :const:`None` then the
  framer returned by :meth:`default_framer` is used.

  *scheduler* should be left as :const:`None`.

  Logging will be sent to *logger*, :const:`None` will use a logger
  specific to the :class:`ClientSession` object's class.

  .. method:: create_connection()

    Make a connection attempt to the remote server.  If successful
    this return a ``(transport, protocol)`` pair.

    This method is a `coroutine`_.

  .. method:: default_rpc_protocol()

    You can override this method to provide a default RPC protocol.
    :class:`JSONRPCv2` is returned by the default implementation.

  .. method:: default_framer()

    You can override this method to provide a default message frmaer.
    A new :class:`NewlineFramer` instance is returned by the default
    implementation.


The :class:`ClientSession` and :class:`ServerSession` classes share a
base class that has the following attributes and methods:


.. _loop.create_connection():
   https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.AbstractEventLoop.create_connection
.. _loop.create_server():
   https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.AbstractEventLoop.create_server

.. _coroutine:
   https://docs.python.org/3/library/asyncio-task.html#coroutine

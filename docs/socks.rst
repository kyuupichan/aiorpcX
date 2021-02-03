.. currentmodule:: aiorpcx

SOCKS Proxy
===========

The :mod:`aiorpcx` package includes a `SOCKS
<https://en.wikipedia.org/wiki/SOCKS>`_ proxy client.  It understands
the ``SOCKS4``, ``SOCKS4a`` and ``SOCKS5`` protocols.

Exceptions
----------

.. exception:: SOCKSError

   The base class of SOCKS exceptions.  Each raised exception will be
   an instance of a derived class.

.. exception:: SOCKSProtocolError

   A subclass of :class:`SOCKSError`.  Raised when the proxy does not
   follow the ``SOCKS`` protocol.

.. exception:: SOCKSFailure

   A subclass of :class:`SOCKSError`.  Raised when the proxy refuses
   or fails to make a connection.


Authentication
--------------

Currently the only supported authentication method is with a username
and password.  Usernames can be used by all SOCKS protocols, but only
``SOCKS5`` uses the password.

.. class:: SOCKSUserAuth

  A :class:`namedtuple` for authentication with a SOCKS server.  It
  has two members:

  .. attribute:: username

     A string.

  .. attribute:: password

     A string.  Ignored by the :class:`SOCKS4` and :class:`SOCKS4a`
     protocols.


Protocols
---------

When creating a :class:`SocksProxy` object, a protocol must be
specified and be one of the following.

.. class:: SOCKS4

  An abstract class representing the ``SOCKS4`` protocol.

.. class:: SOCKS4a

  An abstract class representing the ``SOCKS4a`` protocol.

.. class:: SOCKS5

  An abstract class representing the ``SOCKS5`` protocol.


Proxy
-----

You can create a :class:`SOCKSProxy` object directly, but using one
of its auto-detection class methods is likely more useful.

.. class:: SOCKSProxy(address, protocol, auth)

  An object representing a SOCKS proxy.  The address is a Python
  socket `address
  <https://docs.python.org/3/library/socket.html#socket-families>`_
  typically a (host, port) pair for IPv4, and a (host, port, flowinfo,
  scopeid) tuple for IPv6.

  The *protocol* is one of :class:`SOCKS4`, :class:`SOCKS4a` and
  :class:`SOCKS5`.

  *auth* is a :class:`SOCKSUserAuth` object or :const:`None`.

  After construction, :attr:`host`, :attr:`port` and :attr:`peername`
  are set to :const:`None`.

  .. classmethod:: auto_detect_address(address, auth, \*, \
                   loop=None, timeout=5.0)

     Try to detect a SOCKS proxy at *address*.

     Protocols :class:`SOCKS5`, :class:`SOCKS4a` and :class:`SOCKS4`
     are tried in order.  If a SOCKS proxy is detected return a
     :class:`SOCKSProxy` object, otherwise :const:`None`.  Returning a
     proxy object only means one was detected, not that it is
     functioning - for example, it may not have full network
     connectivity.

     *auth* is a :class:`SOCKSUserAuth` object or :const:`None`.

     If testing any protocol takes more than *timeout* seconds, it is
     timed out and taken as not detected.

     This class method is a `coroutine`_.

  .. classmethod:: auto_detect_host(host, ports, auth, \*, \
                   loop=None, timeout=5.0)

     Try to detect a SOCKS proxy on *host* on one of the *ports*.

     Call :meth:`auto_detect_address` for each ``(host, port)`` pair
     until a proxy is detected, and return it, otherwise
     :const:`None`.

     *auth* is a :class:`SOCKSUserAuth` object or :const:`None`.

     If testing any protocol on any port takes more than *timeout*
     seconds, it is timed out and taken as not detected.

     This class method is a `coroutine`_.

  .. method:: create_connection(protocol_factory, host, port, \*, \
              resolve=False, loop=None, ssl=None, family=0, proto=0, \
              flags=0, timeout=30.0)

     Connect to (host, port) through the proxy in the background.
     When successful, the coroutine returns a ``(transport, protocol,
     address)`` triple, and sets the proxy attribute :attr:`peername`.

     * If *resolve* is :const:`True`, *host* is resolved locally
       rather than by the proxy.  *family*, *proto*, *flags* are the
       optional address family, protocol and flags passed to
       `loop.getaddrinfo()`_ to get a list of remote addresses.  If
       given, these should all be integers from the corresponding
       :mod:`socket` module constants.

     * *ssl* is as documented for `loop.create_connection()`_.

     If successfully connected the :attr:`_address` member of the
     protocol is set.  If *resolve* is :const:`True` it is set to the
     successful address, otherwise ``(host, port)``.

     If connecting takes more than *timeout* seconds an
     :exc:`asyncio.TimeoutError` exception is raised.

     This method is a `coroutine`_.

  .. attribute:: host

     Set on a successful :meth:`create_connection` to the host passed
     to the proxy server.  This will be the resolved address if its
     *resolve* argument was :const:`True`.

  .. attribute:: port

     Set on a successful :meth:`create_connection` to the host passed
     to the proxy server.

  .. attribute:: peername

     Set on a successful :meth:`create_connection` to the result of
     :meth:`socket.getpeername` on the socket connected to the proxy.

.. _loop.create_connection():
   https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.AbstractEventLoop.create_connection
.. _loop.getaddrinfo():
   https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.AbstractEventLoop.getaddrinfo
.. _coroutine:
   https://docs.python.org/3/library/asyncio-task.html#coroutine

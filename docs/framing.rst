.. currentmodule:: aiorpcx

Framing
=======

Message :dfn:`framing` is the method by which RPC messages are wrapped
in a byte stream so that message boundaries can be determined.

:mod:`aiorpcx` provides an abstract base class for framers, and a
single implementation: :class:`NewlineFramer`.  A framer must know how
to take outgoing messages and frame them, and also how to break an
incoming byte stream into message frames in order to extract the RPC
messages from it.

.. class:: FramerBase

  Derive from this class to implement your own message framing
  methodology.

  .. method:: frame(messages)

    Frame each message and return the concatenated result.

    :param message: an iterable; each message should be of type
                    :class:`bytes` or :class:`bytearray`
    :return: the concatenated bytestream
    :rtype: bytes

  .. method:: messages(data)

    :param data: incoming data of type :class:`bytes` or
                 :class:`bytearray`
    :raises MemoryError: if the internal data buffer overflows

    .. note:: since this may raise an exception, the caller should
              process messages as they are yielded.  Converting the
              messages to a list will lose earlier ones if an
              exception is raised later.


.. class:: NewlineFramer(max_size=1000000)

  A framer where messages are delimited by an ASCII newline character in
  a text stream.  The internal buffer for partial messages will hold up
  to *max_size* bytes.

ChangeLog
=========

.. note:: The aiorpcX API will change quite a bit for version 0.6

Version 0.5.7 (27 Jul 2018)
---------------------------

* Implement some handy abstractions from curio on top of asyncio

Version 0.5.6
-------------

* Define a ConnectionError exception, and set it on uncomplete
  requests when a connection is lost.  Previously, those requests were
  cancelled, which does not give an informative error message.

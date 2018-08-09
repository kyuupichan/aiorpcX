ChangeLog
=========

.. note:: The aiorpcX API changed quite a bit for version 0.6 and
          is still unstable

Version 0.7.1 (09 Aug 2018)
---------------------------

* TaskGroup.cancel_remaining() must wait for the tasks to complete
* Fix some tests whose success / failure depended on time races
* fix `#3`_

Version 0.7.0 (08 Aug 2018)
---------------------------

* Fix wait=object and cancellation
* Change Session and JSONRPCConnection APIs
* Fix a test that would hang on some systems

Version 0.6.2 (06 Aug 2018)
---------------------------

* Fix a couple of issues shown up by use in ElectrumX; add testcases

Version 0.6.0 (04 Aug 2018)
---------------------------

* Rework the API; docs are not yet updated
* New JSONRPCConnection object that manages the state of a connection,
  replacing the RPCProcessor class.  It hides the concept of request
  IDs from higher layers; allowing simpler and more intuitive RPC
  datastructures
* The API now prefers async interfaces.  In particular, request handlers
  must be async
* The API generally throws exceptions earlier for nonsense conditions
* TimeOut and TaskSet classes removed; use the superior curio
  primitives that 0.5.7 introduced instead
* SOCKS protocol implementation made i/o agnostic so the code can be
  used whatever your I/O framework (sync, async, threads etc).  The
  Proxy class, like the session class, remains asyncio
* Testsuite cleaned up and shrunk, now works in Python 3.7 and also
  tests uvloop

Version 0.5.9 (29 Jul 2018)
---------------------------

* Remove "async" from __aiter__ which apparently breaks Python 3.7

Version 0.5.8 (28 Jul 2018)
---------------------------

* Fix __str__ in TaskGroupError

Version 0.5.7 (27 Jul 2018)
---------------------------

* Implement some handy abstractions from curio on top of asyncio

Version 0.5.6
-------------

* Define a ConnectionError exception, and set it on uncomplete
  requests when a connection is lost.  Previously, those requests were
  cancelled, which does not give an informative error message.

.. _#3: https://github.com/kyuupichan/aiorpcX/issues/3

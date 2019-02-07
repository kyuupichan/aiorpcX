ChangeLog
=========

.. note:: The aiorpcX API changes regularly and is still unstable


Version 0.10.3 (07 Feb 2019)
----------------------------

* NewlineFramer: max_size of 0 does not limit buffering (SomberNight)
* trivial code / deprecation warning cleanups

Version 0.10.2 (29 Dec 2018)
----------------------------

* TaskGroup: faster cancellation (SomberNight)
* as for curio, remove wait argument to TaskGroup.join()
* setup.py: read the file to extract the version; see `#10`_

Version 0.10.1 (07 Nov 2018)
----------------------------

* bugfixes for transport closing and session task spawning

Version 0.10.0 (05 Nov 2018)
----------------------------

* add session.spawn() method
* make various member variables private

Version 0.9.1 (04 Nov 2018)
---------------------------

* abort sessions which wait too long to send a message

Version 0.9.0 (25 Oct 2018)
---------------------------

* support of binary messaging and framing
* support of plain messaging protocols.  Messages do not have an ID
  and do not expect a response; any response cannot reference the
  message causing it as it has no ID (e.g. the Bitcoin network
  protocol).
* removed the client / server session distinction.  As a result there
  is now only a single session class for JSONRPC-style messaging,
  namely RPCSession, and a single session class for plain messaging
  protocols, MessageSession.  Client connections are initiated by the
  session-independent Connector class.

Version 0.8.2 (25 Sep 2018)
---------------------------

* bw_limit defaults to 0 for ClientSession, bandwidth limiting is mainly
  intended for servers
* don't close proxy sockets on an exception during the initial SOCKS
  handshake; see `#8`_.  This works around an asyncio bug still present
  in Python 3.7
* make CodeMessageError hashable.  This works around a Python bug fixed
  somewhere between Python 3.6.4 and 3.6.6

Version 0.8.1 (12 Sep 2018)
---------------------------

* remove report_crash arguments from TaskGroup methods
* ignore bandwidth limits if set <= 0

Version 0.8.0 (12 Sep 2018)
---------------------------

* change TaskGroup semantics: the first error of a member task is
  raised by the TaskGroup instead of TaskGroupError (which is now
  removed).  Code wanting to query the status / results of member
  tasks should loop on group.next_done().

Version 0.7.3 (17 Aug 2018)
---------------------------

* fix `#5`_; more tests added

Version 0.7.2 (16 Aug 2018)
---------------------------

* Restore batch functionality in Session class
* Less verbose logging
* Increment and test error count on protocol errors
* fix `#4`_

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
.. _#4: https://github.com/kyuupichan/aiorpcX/issues/4
.. _#5: https://github.com/kyuupichan/aiorpcX/issues/5
.. _#8: https://github.com/kyuupichan/aiorpcX/issues/8
.. _#10: https://github.com/kyuupichan/aiorpcX/issues/10

ChangeLog
=========

.. note:: The aiorpcX API changes regularly and is still unstable.  I hope to finalize it
          for a 1.0 release in the coming months.


Version 0.21.0 (11 Mar 2021)
----------------------------

* There have been significant semantic and API changes for TaskGroups.  Their behaviour is
  now consistent, reliable and they have the same semantics as curio.  As such I consider
  their API finalized and stable.  In addition to the notes below for 0.20.x:

* closed() became the attribute joined.
* cancel_remaining() does not cancel daemonic tasks.  As before it waits for the
  cancelled tasks to complete.
* On return from join() all tasks including deamonic ones have been cancelled, but nothing
  is waited for.  If leaving a TaskGroup context because of an exception,
  cancel_remaining() - which can block - is called before join().

Version 0.20.2 (10 Mar 2021)
----------------------------

* result, exception, results and exceptions are now attributes.  They raise a RuntimeError
  if called before a TaskGroup's join() operation has returned.


Version 0.20.1 (06 Mar 2021)
----------------------------

* this release contains some significant API changes which users will need to carefully check
  their code for.
* the report_crash argument to spawn() is removed; instead a new one is named daemon.  A
  daemon task's exception (if any) is ignored by a TaskGroup.
* the join() method of TaskGroup (and so also when TaskGroup is used as a context manager)
  does not raise the exception of failed tasks.  The full semantics are precisely
  described in the TaskGroup() docstring.  Briefly: any task being cancelled or raising an
  exception causes join() to finish and all remaining tasks, including daemon tasks, to be
  cancelled.  join() does not propagate task exceptions.
* the cancel_remaining() method of TaskGroup does not propagate any task exceptions
* TaskGroup supports the additional attributes 'tasks' and 'daemons'.  Also, after join()
  has completed, result() returns the result (or raises the exception) of the first
  completed task.  exception() returns the exception (if any) of the first completed task.
  results() returns the results of all tasks and exceptions() returns the exceptions
  raised by all tasks.  daemon tasks are ignored.
* The above changes bring the implementation in line with curio proper and the semantic
  changes it made over a year ago, and ensure that join() behaves consistently when called
  more than once.

Version 0.18.4 (20 Nov 2019)
----------------------------

* handle time.time() not making progress. fixing `#26`_  (SomberNight)
* handle SOCKSError in _connect_one (SomberNight)
* add SOCKSRandomAuth: Jeremy Rand

Version 0.18.3 (19 May 2019)
----------------------------

* minor bugfix release, fixing `#22`_
* make JSON IDs independent across sessions, make websockets dependency optional (SomberNight)

Version 0.18.2 (19 May 2019)
----------------------------

* minor bugfix release

Version 0.18.1 (09 May 2019)
----------------------------

* convert incoming websocket text frames to binary.  Convert outgoing messages to text
  frames if possible.

Version 0.18.0 (09 May 2019)
----------------------------

* Add *websocket* support as client and server by using Aymeric Augustin's excellent
  `websockets <https://github.com/aaugustin/websockets/>`_ package.

  Unfortunately this required changing several APIs.  The code now distinguishes the
  previous TCP and SSL based-connections as *raw sockets* from the new websockets.  The
  old Connector and Server classes are gone.  Use `connect_rs()` and `serve_rs()` to
  connect a client and start a server for raw sockets; and `connect_ws()` and `serve_ws()`
  to do the same for websockets.

  SessionBase no longer inherits `asyncio.Protocol` as it is now transport-independent.
  Sessions no longer take a framer in their constructor: websocket messages are already
  framed, so instead a framer is passed to `connect_rs()` and `serve_rs()` if the default
  `NewlineFramer` is not wanted.

  A session is only instantiated when a connection handshake is completed, so
  `connection_made()` is no longer a method.  `connection_lost()` and `abort()` are now
  coroutines; if overriding either be sure to call the base class implementation.

  `is_send_buffer_full()` was removed.
* Updated and added new examples
* JSON RPC message handling was made more efficient by using futures instead of events
  internally

Version 0.17.0 (22 Apr 2019)
----------------------------

* Add some new APIs, update others
* Add Service, NetAddress, ServicePart, validate_port, validate_protocol
* SessionBase: new API proxy() and remote_address().  Remove peer_address()
  and peer_address_str()
* SOCKSProxy: auto_detect_address(), auto_detect_host() renamed auto_detect_at_address()
  and auto_detect_at_host().  auto_detect_at_address() takes a NetAddress.

Version 0.16.2 (21 Apr 2019)
----------------------------

* fix force-close bug

Version 0.16.1 (20 Apr 2019)
----------------------------

* resolve socks proxy host using getaddrinfo.  In particular, IPv6 is supported.
* add two new APIs

Version 0.16.0 (19 Apr 2019)
----------------------------

* session closing is now robust; it is safe to await session.close() from anywhere
* API change: FinalRPCError removed; raise ReplyAndDisconnect instead.  This responds with
  a normal result, or an error, and then disconnects.  e.g.::

    raise ReplyAndDisconnect(23)
    raise ReplyAndDisconnect(RPCError(1, "message"))

* the session base class' private method _close() is removed.  Use await close() instead.
* workaround uvloop bug `<https://github.com/MagicStack/uvloop/issues/246>`_

Version 0.15.0 (16 Apr 2019)
----------------------------

* error handling improved to include costing

Version 0.14.1 (16 Apr 2019)
----------------------------

* fix a bad assertion

Version 0.14.0 (15 Apr 2019)
----------------------------

* timeout handling improvements
* RPCSession: add log_me, send_request_timeout
* Concurrency: respect semaphore queue ordering
* cleaner protocol auto-detection

Version 0.13.6 (14 Apr 2019)
----------------------------

* RPCSession: concurrency control of outgoing requests to target a given response time
* SessionBase: processing_timeout will time-out processing of incoming requests.   This
  helps prevent ever-growing request backlogs.
* SessionBase: add is_send_buffer_full()

Version 0.13.5 (13 Apr 2019)
----------------------------

* robustify concurrency handling

Version 0.13.3 (13 Apr 2019)
----------------------------

* export Concurrency class.  Tweak some default constants.

Version 0.13.2 (12 Apr 2019)
----------------------------

* wait for task to complete on close.  Concurrency improvements.

Version 0.13.0 (12 Apr 2019)
----------------------------

* fix concurrency handling; bump version as API changed

Version 0.12.1 (09 Apr 2019)
----------------------------

* improve concurrency handling; expose new API

Version 0.12.0 (09 Apr 2019)
----------------------------

* switch from bandwidth to a generic cost metric for sessions

Version 0.11.0 (06 Apr 2019)
----------------------------

* rename 'normalize_corofunc' to 'instantiate_coroutine'
* remove spawn() member of SessionBase
* add FinalRPCError (ghost43)
* more reliable cancellation on connection closing

Version 0.10.5 (16 Feb 2019)
----------------------------

* export 'normalize_corofunc'
* batches: fix handling of session loss; add test

Version 0.10.4 (07 Feb 2019)
----------------------------

* SessionBase: add closed_event, tweak closing process
* testsuite cleanup

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
.. _#22: https://github.com/kyuupichan/aiorpcX/issues/22
.. _#26: https://github.com/kyuupichan/aiorpcX/issues/26

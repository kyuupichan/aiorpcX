ChangeLog
=========

Version 0.5.6
-------------

* Define a ConnectionError exception, and set it on uncomplete
  requests when a connection is lost.  Previously, those requests were
  cancelled, which does not give an informative error message.

=======
aiorpcX
=======

.. image:: https://badge.fury.io/py/aiorpcX.svg
    :target: http://badge.fury.io/py/aiorpcX
.. image:: https://travis-ci.org/kyuupichan/aiorpcX.svg?branch=master
    :target: https://travis-ci.org/kyuupichan/aiorpcX
.. image:: https://coveralls.io/repos/github/kyuupichan/aiorpcX/badge.svg
    :target: https://coveralls.io/github/kyuupichan/aiorpcX

A generic asyncio library implementation of RPC suitable for an
application that is a client, server or both.

The package includes a module with full coverage of `JSON RPC
<http://www.jsonrpc.org/>`_ versions 1.0 and 2.0, JSON RPC protocol
auto-detection, and arbitrary message framing.  It also comes with a
SOCKS proxy client.

The current version is |release|.

The library API is not stable and may change radically.  These docs
are out of date and will be updated when the API settles.

Source Code
===========

The project is hosted on `GitHub
<https://github.com/kyuupichan/aiorpcX/>`_.  and uses `Travis
<https://travis-ci.org/kyuupichan/aiorpcX>`_ for Continuous
Integration.

Python version at least 3.6 is required.

Please submit an issue on the `bug tracker
<https://github.com/kyuupichan/aiorpcX/issues>`_ if you have found a
bug or have a suggestion to improve the library.

Authors and License
===================

Neil Booth wrote the code, which is derived from the original JSON RPC
code of `ElectrumX <https://github.com/kyuupichan/electrumx/>`_.

The code is released under the `MIT Licence
<https://github.com/kyuupichan/aiorpcX/LICENCE>`_.

Documentation
=============

.. toctree::

   changelog
   framing
   json-rpc
   rpc
   session
   socks

Indices and tables
==================

* :ref:`genindex`
* :ref:`search`

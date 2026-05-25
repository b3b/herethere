herethere
=========

.. start-badges
.. image:: https://img.shields.io/pypi/v/herethere.svg
    :target: https://pypi.python.org/pypi/herethere
    :alt: Latest version on PyPI
.. image:: https://img.shields.io/pypi/pyversions/herethere.svg
    :target: https://pypi.python.org/pypi/herethere
    :alt: Supported Python versions
.. image:: https://github.com/b3b/herethere/actions/workflows/tests.yml/badge.svg?branch=master
    :target: https://github.com/b3b/herethere/actions/workflows/tests.yml?query=branch%3Amaster
    :alt: CI Status
.. image:: https://codecov.io/github/b3b/herethere/coverage.svg?branch=master
    :target: https://codecov.io/github/b3b/herethere?branch=master
    :alt: Code coverage status
.. end-badges

Run Python interactively inside live apps and devices.

``herethere`` starts a small SSH-backed server inside a Python process, then
lets you connect from another Python session or Jupyter notebook to inspect,
modify, and interact with a namespace in that running process.

It was created for workflows where Python is running inside an app, device,
or environment that is awkward to interact with directly.
The same idea is useful for Raspberry Pi and robotics projects,
Kivy/mobile apps, containers, long-running experiments, server-side apps,
and other cases where logs or a separate remote shell are not enough.

``herethere`` is based on the `AsyncSSH <https://github.com/ronf/asyncssh>`_
library. AsyncSSH provides the SSH toolkit; ``herethere`` adds a small
Python and Jupyter workflow layer on top.

:Code repository: https://github.com/b3b/herethere
:Documentation: https://herethere.me/library

Installation
------------

Install ``herethere`` in the Python environment that will start the server:

.. code-block:: bash

   pip install herethere

If you want to connect from Jupyter using the IPython magics, install the
``magic`` extra in the notebook/client environment:

.. code-block:: bash

   pip install "herethere[magic]"

Quickstart
----------

Target process
~~~~~~~~~~~~~~

Start ``herethere`` inside the Python process you want to interact with.

.. code-block:: python

   from herethere.here import ServerConfig, start_server

   state = {"speed": 1}

   await start_server(ServerConfig.load(prefix="here"), namespace=globals())

Jupyter notebook / client
~~~~~~~~~~~~~~~~~~~~~~~~~

Connect from your local Jupyter notebook:

.. code-block:: python

   %load_ext herethere.magic
   %connect-there

Use the ``%%there`` cell magic to inspect variables, call functions, or update
state while the target process keeps running:

.. code-block:: python

   %%there

   print(state)
   state["speed"] = 3
   print(state)

Trust model
-----------

A connected client can execute Python code inside the target process. Treat
access to ``herethere`` like access to a Python REPL running inside your app.

Use it only for development workflows where you control both sides of the
connection. Do not expose it publicly or give access to untrusted users.


Related resources
-----------------

* `PythonHere <https://herethere.me/pythonhere>`_: an application that uses the
  ``herethere`` library
* `Kivy Remote Shell <https://github.com/kivy/kivy-remote-shell>`_: a remote
  SSH + Python interactive shell application using Twisted
* `Twisted Manhole <https://twistedmatrix.com/documents/8.1.0/api/twisted.manhole.html>`_:
  interactive interpreter and direct manipulation support for Twisted

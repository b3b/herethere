herethere
=========

.. start-badges
.. image:: https://img.shields.io/pypi/v/herethere.svg
    :target: https://pypi.python.org/pypi/herethere
    :alt: Latest version on PyPi
.. image:: https://img.shields.io/pypi/pyversions/herethere.svg
    :target: https://pypi.python.org/pypi/herethere
    :alt: Supported Python versions
.. image:: https://github.com/b3b/herethere/workflows/ci/badge.svg?branch=master
     :target: https://github.com/b3b/herethere/actions?workflow=CI
     :alt: CI Status
.. image:: https://codecov.io/github/b3b/herethere/coverage.svg?branch=master
    :target: https://codecov.io/github/b3b/herethere?branch=master
    :alt: Code coverage Status
.. end-badges

An async Python library for interactive code execution on a remote machine via SSH.

*herethere* is based on the `AsyncSSH <https://github.com/ronf/asyncssh>`_ library.

:Code repository: https://github.com/b3b/herethere
:Documentation: https://herethere.me/library

Installation
------------

*herethere* can be installed from the PyPi::

    pip install herethere[magic]


The `magic` extension can be omitted if Jupyter magic commands will not be used.


Related resources
-----------------

* `PythonHere <https://herethere.me/pythonhere>`_: application that uses *herethere* library
* `Kivy Remote Shell <https://github.com/kivy/kivy-remote-shell>`_ : Remote SSH+Python interactive shell application (uses Twisted)
* `Twisted Manhole <https://twistedmatrix.com/documents/8.1.0/api/twisted.manhole.html>`_: interactive interpreter and direct manipulation support for Twisted

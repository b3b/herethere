Changelog
=========

0.2.0
-----

This release adds support for Python 3.10 through 3.14, modernizes the project packaging, and fixes several SSH command execution and shutdown hangs.

  * Added support for Python 3.10 through 3.14
  * Removed support for Python versions older than 3.10
  * Modernized project packaging by moving to ``pyproject.toml``
  * Replaced ``nest_asyncio`` with ``nest-asyncio2`` for the optional IPython magic integration
  * Fixed SSH command execution hangs when running long-lived commands
  * Fixed SSH server shutdown hangs with active clients, including on Python 3.12+
  * Avoided relying on AsyncSSH private internals when configuring PTY terminal modes

0.1.2
-----

  * Fixed ``%there`` argument handling when arguments contain spaces
  * Fixed ``.env`` lookup to start from the current working directory
  * Improved background SSH command handling by creating new SSH connections
  * Fixed ``sys.stderr.unregister()`` errors
  * Increased background task capacity for concurrent work
  * Added documentation

0.1.1
-----

  * Added ``%there --delay`` option
  * Passed ``stdout`` and ``stderr`` through to ``runcode``

0.1.0
-----

  * Added ``%there log`` command
  * Added a wrapper for a running SSH server instance
  * Fixed IO stream redirection to affect only the current thread
  * Adjusted ``%%there`` code wrapping so error line numbers match notebook cell lines

0.0.5
-----

  * Added ``server_factory`` argument to ``start_server``
  * Updated IPython magic dependency handling

0.0.4
-----

  * Added ``nest_asyncio`` dependency for IPython magic support

0.0.3
-----

  * Initial tagged release

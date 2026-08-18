Changelog
=========

0.3.2
-----

* Added ``--worker`` for blocking or long-running operations in Jupyter ``%%there``
  and CLI ``run``/``get`` commands, replacing ``--background`` for synchronous
  CLI worker execution while keeping it as a compatibility alias

0.3.1
-----

* Added ``--background`` worker-thread execution to ``there run`` and
  ``there get`` for blocking or long-running operations

* Fixed successful standalone ``there`` commands returning a non-zero exit
  status
* Removed application-level input size limits from ``there run`` and
  ``there get``
* Changed the default SSH host-key path from ``./key.rsa`` to
  ``./ssh_host_key`` and now generate missing host keys as Ed25519 instead of
  RSA  

0.3.0
-----

This release adds a standalone ``there`` command-line client for users and
coding agents.

* Added ``ping``, ``run``, ``get``, ``logs``, ``shell``, ``upload``, and
  ``download`` commands
* Added structured JSON output with bounded capture and stable exit codes
* Added configurable operation timeouts
* Added support for CLI command plugins
* Added a bundled ``there-cli`` agent skill

0.2.3
-----

* Changed ``%%there ai`` prompt resolution so configured prompt stacks use the
  exact prompt names provided by ``set_ai_prompts(...)`` and
  ``THERE_AI_PROMPTS``; include ``default`` explicitly when it should be part
  of the stack

0.2.2
-----

This release adds AI-assisted local generation of %%there notebook cells.

* Added ``%%there ai`` to use a configured language model to generate a new ``%%there`` Python cell from a natural language request
* Inserted generated cells into the notebook for inspection or editing before they are run on the connected target
* Added ``%%there ai --fix`` to generate a corrected replacement after a ``%%there`` Python cell fails or needs adjustment
* Added configurable AI prompts so notebooks and integrations can include project-, runtime-, or framework-specific context

0.2.1
-----

* Added ``%there get`` command for retrieving remote Python values
* Added ``%there download`` command for downloading remote files and directories
* Made single-path ``%there upload`` and ``%there download`` default to ``.`` as the destination
* Added default local connection settings for ``here`` and ``there``
* Renamed ``HERE_CHROOT`` to ``HERE_SFTP_ROOT`` and kept ``HERE_CHROOT`` as a deprecated alias
* Improved ``%there`` magic command parsing, including support for quoted paths and arguments
* Replaced nested event loop handling in Jupyter magics with a dedicated background event loop
* Made ``here`` server logs quieter
* Fixed port reuse after server shutdown
* Fixed cleanup behavior for background commands and remote log streaming

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

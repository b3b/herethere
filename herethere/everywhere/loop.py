"""Async helpers for synchronous IPython magic methods.

The IPython magic API used by herethere is synchronous: methods such as
``%connect-there`` and foreground ``%there`` commands must return their result
before the magic call finishes. The underlying implementation is async because
SSH and SFTP operations are handled by AsyncSSH.

In a normal Python process, a generic sync-to-async bridge could use
``asyncio.run(awaitable)``. herethere deliberately does not do that here. Magic
commands share AsyncSSH-backed client objects, and those objects can be tied to
the loop they were created on. Running one command on a temporary loop and the
next command on another loop risks mixed loop ownership.

Jupyter/ipykernel adds another reason to avoid the current thread's loop. The
kernel already owns a running asyncio loop in the main thread, so calling
``asyncio.run()`` fails, and calling ``loop.run_until_complete()`` on the kernel
loop requires nested-loop patching. That nested re-entry is fragile across
Python/ipykernel versions and has produced contextvars errors and kernel
crashes.

This module avoids both problems. All sync magic calls submit their awaitables
to one private background event loop and wait synchronously for the result. This
keeps the public magic behavior blocking, keeps AsyncSSH work on a consistent
loop, and isolates herethere from ipykernel's event loop internals.

Timeouts bound the synchronous wait. If a timeout expires, herethere requests
cancellation of the submitted future. Cancellation is cooperative: the coroutine,
remote process, or SSH operation may take additional time to observe it.
Background magic commands use ``run_background()`` directly when they
intentionally should not block the magic call.

The bridge accepts already-created awaitables because that keeps the existing
magic call sites small. A callable-based API would avoid constructing a
coroutine before scheduling is known to be possible, but it would add churn to
all call sites. Keep awaitables created immediately at the ``run_sync()`` or
``run_background()`` call boundary so scheduling failures have the smallest
possible surface.
"""

import asyncio
import atexit
import threading
from collections.abc import Awaitable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TypeVar

T = TypeVar("T")


class BackgroundLoop:
    """Dedicated asyncio loop for sync IPython magic methods.

    The loop is created lazily so importing herethere does not start a thread.
    It is reused across magic calls because AsyncSSH objects can be tied to the
    loop they were created on, and because creating a thread per command would
    add avoidable overhead.
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def get(self) -> asyncio.AbstractEventLoop:
        """Return a running background loop."""
        with self._lock:
            # Reuse only a loop with a live thread behind it. A loop object can
            # still report running briefly around thread shutdown, so check both
            # pieces before handing it to run_coroutine_threadsafe().
            if (
                self._loop is not None
                and self._loop.is_running()
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return self._loop

            self._close_stale_loop()

            loop = asyncio.new_event_loop()
            ready = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(loop, ready),
                name="HerethereMagicLoop",
                daemon=True,
            )
            self._loop = loop
            self._thread = thread
            thread.start()
            # Thread startup should be immediate. Bound the wait so a broken
            # runtime or failed thread start raises instead of hanging the
            # notebook kernel forever.
            if not ready.wait(timeout=5):
                self._loop = None
                self._thread = None
                if not loop.is_running() and not loop.is_closed():
                    loop.close()
                raise RuntimeError("Timed out starting HerethereMagicLoop")
            return loop

    def stop(self):
        """Stop the background loop."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None

        if loop is None:
            return

        if loop.is_running():
            try:
                # Give pending background tasks a chance to see cancellation and
                # run their cleanup before the loop is stopped.
                asyncio.run_coroutine_threadsafe(
                    self._cancel_pending(),
                    loop,
                ).result(timeout=1)
            except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                pass

            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=1)
                if thread.is_alive():
                    return

        if not loop.is_closed():
            loop.close()

    def _close_stale_loop(self):
        """Close a previously stored loop after its thread has stopped."""
        if self._loop is None or self._loop.is_running() or self._loop.is_closed():
            return
        self._loop.close()

    @staticmethod
    async def _cancel_pending():
        """Cancel tasks running on the background loop during shutdown."""
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop, ready: threading.Event):
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            asyncio.set_event_loop(None)


_background_loop = BackgroundLoop()
atexit.register(_background_loop.stop)


async def _await(awaitable: Awaitable[T]) -> T:
    """Wrap any awaitable object in a coroutine for thread-safe submission."""
    return await awaitable


def run_sync(awaitable: Awaitable[T], timeout: float | None = None) -> T:
    """Run an awaitable from synchronous magic code.

    All sync magic operations use the private background loop, even when there
    is no event loop running in the caller's thread. This keeps AsyncSSH-backed
    client objects on one loop across connect, execute, upload, and disconnect
    operations.
    """
    future = run_background(awaitable)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        # concurrent.futures.Future.result(timeout=...) only stops waiting. It
        # does not stop the coroutine, so request cancellation explicitly.
        future.cancel()
        raise


def run_background(awaitable: Awaitable[T]) -> Future[T]:
    """Schedule an awaitable on the background magic loop.

    This is used by background magic commands such as ``%there -b``. The caller
    receives a ``concurrent.futures.Future`` and may choose whether to wait,
    inspect errors, or cancel it.
    """
    loop = _background_loop.get()
    return asyncio.run_coroutine_threadsafe(_await(awaitable), loop)

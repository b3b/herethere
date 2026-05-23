import asyncio
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest

from herethere.everywhere.loop import (
    BackgroundLoop,
    _background_loop,
    run_background,
    run_sync,
)


async def _return(value):
    return value


@pytest.fixture(autouse=True)
def stop_background_loop_after_test():
    yield
    _background_loop.stop()


class FutureStub:
    def __init__(self, result):
        self.timeout = None
        self._result = result

    def result(self, timeout=None):
        self.timeout = timeout
        return self._result


def test_run_sync_uses_background_loop(mocker):
    future = Future()
    future.set_result("done")
    runner = mocker.patch(
        "herethere.everywhere.loop.run_background",
        return_value=future,
    )

    assert run_sync("awaitable") == "done"

    runner.assert_called_once_with("awaitable")


def test_background_loop_start_timeout_closes_loop(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = False
    loop.is_closed.return_value = False
    thread = mocker.Mock()
    event = mocker.Mock()
    event.wait.return_value = False
    mocker.patch("herethere.everywhere.loop.asyncio.new_event_loop", return_value=loop)
    mocker.patch("herethere.everywhere.loop.threading.Event", return_value=event)
    mocker.patch("herethere.everywhere.loop.threading.Thread", return_value=thread)

    with pytest.raises(RuntimeError, match="Timed out starting HerethereMagicLoop"):
        background_loop.get()

    event.wait.assert_called_once_with(timeout=5)
    thread.start.assert_called_once_with()
    loop.close.assert_called_once_with()


def test_background_loop_start_timeout_does_not_close_running_loop(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = True
    loop.is_closed.return_value = False
    event = mocker.Mock()
    event.wait.return_value = False
    mocker.patch("herethere.everywhere.loop.asyncio.new_event_loop", return_value=loop)
    mocker.patch("herethere.everywhere.loop.threading.Event", return_value=event)
    mocker.patch("herethere.everywhere.loop.threading.Thread")

    with pytest.raises(RuntimeError, match="Timed out starting HerethereMagicLoop"):
        background_loop.get()

    loop.close.assert_not_called()


def test_background_loop_stop_without_loop_returns():
    BackgroundLoop().stop()


def test_background_loop_stop_closes_not_running_loop(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = False
    loop.is_closed.return_value = False
    background_loop._loop = loop

    background_loop.stop()

    loop.close.assert_called_once_with()


def test_background_loop_stop_running_loop(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = True
    loop.is_closed.return_value = False
    thread = mocker.Mock()
    thread.is_alive.return_value = False
    wait = mocker.Mock()
    wait.result.return_value = None
    runner = mocker.patch(
        "herethere.everywhere.loop.asyncio.run_coroutine_threadsafe",
        return_value=wait,
    )
    mocker.patch.object(
        background_loop,
        "_cancel_pending",
        new=mocker.Mock(return_value="cleanup"),
    )
    background_loop._loop = loop
    background_loop._thread = thread

    background_loop.stop()

    runner.assert_called_once_with("cleanup", loop)
    loop.call_soon_threadsafe.assert_called_once_with(loop.stop)
    thread.join.assert_called_once_with(timeout=1)
    loop.close.assert_called_once_with()


def test_background_loop_stop_ignores_cleanup_errors(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = True
    loop.is_closed.return_value = True
    wait = mocker.Mock()
    wait.result.side_effect = RuntimeError("cleanup failed")
    mocker.patch(
        "herethere.everywhere.loop.asyncio.run_coroutine_threadsafe",
        return_value=wait,
    )
    mocker.patch.object(
        background_loop,
        "_cancel_pending",
        new=mocker.Mock(return_value="cleanup"),
    )
    background_loop._loop = loop

    background_loop.stop()

    loop.call_soon_threadsafe.assert_called_once_with(loop.stop)
    loop.close.assert_not_called()


def test_background_loop_stop_returns_if_thread_stays_alive(mocker):
    background_loop = BackgroundLoop()
    loop = mocker.Mock()
    loop.is_running.return_value = True
    thread = mocker.Mock()
    thread.is_alive.return_value = True
    wait = mocker.Mock()
    wait.result.return_value = None
    mocker.patch(
        "herethere.everywhere.loop.asyncio.run_coroutine_threadsafe",
        return_value=wait,
    )
    mocker.patch.object(
        background_loop,
        "_cancel_pending",
        new=mocker.Mock(return_value="cleanup"),
    )
    background_loop._loop = loop
    background_loop._thread = thread

    background_loop.stop()

    thread.join.assert_called_once_with(timeout=1)
    loop.close.assert_not_called()


def test_background_loop_closes_stale_loop(mocker):
    background_loop = BackgroundLoop()
    stale_loop = mocker.Mock()
    stale_loop.is_running.return_value = False
    stale_loop.is_closed.return_value = False
    new_loop = mocker.Mock()
    event = mocker.Mock()
    event.wait.return_value = True
    background_loop._loop = stale_loop
    mocker.patch(
        "herethere.everywhere.loop.asyncio.new_event_loop",
        return_value=new_loop,
    )
    mocker.patch("herethere.everywhere.loop.threading.Event", return_value=event)
    mocker.patch("herethere.everywhere.loop.threading.Thread")

    assert background_loop.get() is new_loop

    stale_loop.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_background_loop_cancel_pending_cancels_other_tasks():
    task = asyncio.create_task(asyncio.Event().wait())

    await BackgroundLoop._cancel_pending()

    assert task.cancelled()


def test_run_sync_passes_timeout_to_background_future(mocker):
    future = FutureStub("done")
    runner = mocker.patch(
        "herethere.everywhere.loop.run_background",
        return_value=future,
    )

    result = run_sync("awaitable", timeout=5)

    runner.assert_called_once_with("awaitable")
    assert future.timeout == 5
    assert result == "done"


@pytest.mark.asyncio
async def test_run_sync_cancels_background_future_on_timeout(mocker):
    future = Future()
    runner = mocker.patch(
        "herethere.everywhere.loop.run_background",
        return_value=future,
    )

    with pytest.raises(FutureTimeoutError):
        run_sync("awaitable", timeout=0)

    runner.assert_called_once_with("awaitable")
    assert future.cancelled()


@pytest.mark.asyncio
async def test_run_background_uses_background_loop():
    future = run_background(_return("done"))

    assert future.result(timeout=1) == "done"

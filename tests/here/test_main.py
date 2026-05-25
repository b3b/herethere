import asyncio
import logging
import runpy
import sys

import pytest

from herethere.here import __main__ as here_main


class EventWaitReturns:
    async def wait(self):
        return None


def test_configure_logging_uses_info_defaults_herethere_debug_and_asyncssh_warning(
    mocker,
):
    root_logger = logging.getLogger()
    herethere_logger = logging.getLogger("herethere")
    original_root_level = root_logger.level
    original_herethere_level = herethere_logger.level
    basic_config = mocker.patch.object(here_main.logging, "basicConfig")
    set_log_level = mocker.patch.object(here_main.asyncssh, "set_log_level")

    try:
        here_main.configure_logging()

        basic_config.assert_called_once_with(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        assert root_logger.level == logging.INFO
        assert herethere_logger.level == logging.DEBUG
        set_log_level.assert_called_once_with("WARNING")
    finally:
        root_logger.setLevel(original_root_level)
        herethere_logger.setLevel(original_herethere_level)


def test_main_starts_server_and_stops_on_exit(mocker, tmp_environ):
    tmp_environ["HERE_PORT"] = "0"
    config = mocker.Mock()
    server = mocker.Mock()
    server.stop = mocker.AsyncMock()

    mocker.patch.object(here_main.ServerConfig, "load", return_value=config)
    start_server = mocker.patch.object(
        here_main,
        "start_server",
        new=mocker.AsyncMock(return_value=server),
    )
    mocker.patch.object(here_main.asyncio, "Event", return_value=EventWaitReturns())

    here_main.main()

    start_server.assert_called_once_with(config, namespace={})
    server.stop.assert_awaited_once_with()


def test_main_handles_keyboard_interrupt(mocker):
    mocker.patch.object(here_main, "configure_logging")
    mocker.patch.object(here_main, "serve", side_effect=KeyboardInterrupt)

    here_main.main()


def test_main_propagates_other_errors(mocker):
    mocker.patch.object(here_main, "configure_logging")
    mocker.patch.object(here_main, "serve", side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        here_main.main()


def test_module_entrypoint_calls_main(mocker):
    mocker.patch("asyncio.run", side_effect=lambda coroutine: coroutine.close())

    module = sys.modules.pop("herethere.here.__main__", None)
    try:
        runpy.run_module("herethere.here.__main__", run_name="__main__")
    finally:
        if module:
            sys.modules["herethere.here.__main__"] = module

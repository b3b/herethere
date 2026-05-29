import pytest

from herethere.there import local_commands
from herethere.there.history import RecentThereHistory


@pytest.fixture(autouse=True)
def clear_local_commands():
    original = dict(local_commands._LOCAL_THERE_HANDLERS)
    local_commands._LOCAL_THERE_HANDLERS.clear()
    yield
    local_commands._LOCAL_THERE_HANDLERS.clear()
    local_commands._LOCAL_THERE_HANDLERS.update(original)


def test_unknown_local_command_returns_false():
    assert not local_commands.maybe_handle_local_there_command(
        "missing arg", "print(1)", shell=None, history=RecentThereHistory()
    )


def test_registered_local_command_handles_cell(mocker):
    handler = mocker.Mock()
    shell = object()
    local_commands.register_local_there_command("ai", handler)
    history = RecentThereHistory()

    handled = local_commands.maybe_handle_local_there_command(
        "ai --flag", "prompt", shell=shell, history=history
    )

    assert handled
    handler.assert_called_once()
    command = handler.call_args.args[0]
    assert command.line == "--flag"
    assert command.cell == "prompt"
    assert command.shell is shell
    assert command.history is history


def test_registered_local_command_receives_context():
    calls = []

    def handler(command):
        calls.append(command)

    shell = object()
    local_commands.register_local_there_command("old", handler)
    history = RecentThereHistory()

    handled = local_commands.maybe_handle_local_there_command(
        "old --flag", "prompt", shell=shell, history=history
    )

    assert handled
    assert len(calls) == 1
    assert calls[0].line == "--flag"
    assert calls[0].cell == "prompt"
    assert calls[0].shell is shell
    assert calls[0].history is history


def test_empty_local_command_name_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        local_commands.register_local_there_command(" ", lambda **_: None)

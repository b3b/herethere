import click
import pytest

from herethere.there.ai import prompts
from herethere.there.ai.command import (
    HEADER,
    find_suspicious_terms,
    handle_ai,
    parse_ai_line,
)
from herethere.there.ai.llm import AIProviderError
from herethere.there.history import RecentThereHistory
from herethere.there.local_commands import LocalThereCommand


def local_command(line="", cell="", shell=None, history=None):
    return LocalThereCommand(
        line=line,
        cell=cell,
        shell=shell,
        history=history or RecentThereHistory(),
    )


def test_empty_prompt_prints_friendly_message(capfd):
    handle_ai(local_command())

    captured = capfd.readouterr()
    assert "requires a prompt" in captured.out


def test_missing_config_prints_friendly_message(capfd, tmp_environ):
    handle_ai(local_command(cell="debug remote"))

    captured = capfd.readouterr()
    assert "Set THERE_AI_MODEL" in captured.out


def test_provider_error_prints_friendly_message(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        side_effect=AIProviderError("boom"),
    )

    handle_ai(local_command(cell="debug remote", shell=mocker.Mock()))

    captured = capfd.readouterr()
    assert "herethere AI generation failed: boom" in captured.out


def test_prompt_error_prints_friendly_message(capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"

    handle_ai(local_command(line="--prompts missing", cell="debug remote"))

    captured = capfd.readouterr()
    assert "Unknown %%there ai prompt: 'missing'" in captured.out


def test_help_exits_without_error(capfd):
    handle_ai(local_command(line="--help", cell="debug remote"))

    captured = capfd.readouterr()
    assert "Usage: %%there ai [OPTIONS]" in captured.out
    assert "ipykernel_launcher.py" not in captured.out
    assert "--prompts TEXT" in captured.out


def test_empty_generated_code_prints_friendly_message(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    shell = mocker.Mock()
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="```python\n%%there\n```",
    )

    handle_ai(local_command(cell="debug remote", shell=shell))

    shell.set_next_input.assert_not_called()
    captured = capfd.readouterr()
    assert "AI provider returned no code." in captured.out


def test_missing_ipython_shell_prints_friendly_message(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )
    mocker.patch("herethere.there.ai.command.get_ipython", return_value=None)

    handle_ai(local_command(cell="debug remote"))

    captured = capfd.readouterr()
    assert "Could not find active IPython shell" in captured.out


def test_unexpected_error_is_not_swallowed(mocker, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        side_effect=RuntimeError("bug"),
    )

    with pytest.raises(RuntimeError, match="bug"):
        handle_ai(local_command(cell="debug remote", shell=mocker.Mock()))


def test_valid_generated_code_inserts_cell(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    provider = mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="```python\n%%there\nprint('remote')\n```",
    )
    shell = mocker.Mock()

    handle_ai(local_command(cell="debug remote", shell=shell))

    provider.assert_called_once()
    shell.set_next_input.assert_called_once()
    generated_cell = shell.set_next_input.call_args.args[0]
    assert generated_cell.startswith("%%there\n")
    assert HEADER in generated_cell
    assert "print('remote')" in generated_cell
    assert "```" not in generated_cell
    shell.set_next_input.assert_called_once_with(generated_cell, replace=False)
    captured = capfd.readouterr()
    assert "Generating %%there cell with AI" in captured.out
    assert "Generated a %%there cell in " in captured.out


def test_ai_line_parses_prompt_options():
    options = parse_ai_line("--prompts kivy,pyjnius,midi")

    assert options.prompts == ("kivy", "pyjnius", "midi")
    assert options.fix is False


def test_ai_line_parses_fix():
    options = parse_ai_line("--fix --prompts custom")

    assert options.prompts == ("custom",)
    assert options.fix is True


def test_ai_line_rejects_malformed_quoting():
    with pytest.raises(click.UsageError, match="No closing quotation"):
        parse_ai_line('--prompts "broken')


def test_malformed_ai_line_prints_friendly_message(capfd):
    handle_ai(local_command(line='--prompts "broken', cell="debug remote"))

    captured = capfd.readouterr()
    assert "No closing quotation" in captured.out


def test_prompt_options_are_passed_to_message_builder(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "hi"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )

    handle_ai(
        local_command(
            line="--prompts kivy,midi",
            cell="debug remote",
            shell=mocker.Mock(),
        )
    )

    build_messages.assert_called_once_with(
        "debug remote",
        ("kivy", "midi"),
    )
    captured = capfd.readouterr()
    assert "Generating %%there cell with AI" in captured.out
    assert "Generated a %%there cell in " in captured.out


def test_suspicious_code_prints_warning(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value='open("x", "w").write("data")\nos.system("true")',
    )

    handle_ai(local_command(cell="write a file", shell=mocker.Mock()))

    captured = capfd.readouterr()
    assert "Warning: generated code contains terms" in captured.out
    assert "os.system" in captured.out
    assert 'open(..., "w")' in captured.out


def test_find_suspicious_terms_case_insensitive():
    assert "requests.post" in find_suspicious_terms("REQUESTS.POST(url)")


def test_fix_without_recent_cell_prints_friendly_message(capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"

    handle_ai(local_command(line="--fix", cell="fix it"))

    captured = capfd.readouterr()
    assert "No previous %%there cell is available to fix" in captured.out


def test_fix_requires_instruction(capfd):
    handle_ai(local_command(line="--fix"))

    captured = capfd.readouterr()
    assert "requires a fix instruction" in captured.out


def test_fix_passes_previous_cell_to_message_builder(mocker, capfd, tmp_environ):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "fix"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print(app.state.value)",
    )
    shell = mocker.Mock()
    history = RecentThereHistory()
    history.remember(line="-b", cell="print(app_state.value)")

    handle_ai(
        local_command(
            line="--fix --prompts kivy",
            cell="Use app.state instead.",
            shell=shell,
            history=history,
        )
    )

    build_messages.assert_called_once()
    user_request = build_messages.call_args.args[0]
    assert "%%there -b" in user_request
    assert "print(app_state.value)" in user_request
    assert "Use app.state instead." in user_request
    assert build_messages.call_args.args[1] == ("kivy", "fix")
    assert build_messages.call_args.kwargs == {}
    generated_cell = shell.set_next_input.call_args.args[0]
    assert "# AI mode: fix" in generated_cell
    assert "# AI mode: fix\n\n" not in generated_cell
    assert "print(app.state.value)" in generated_cell
    captured = capfd.readouterr()
    assert "Generated a %%there cell in " in captured.out


def test_config_prompt_options_are_passed_to_message_builder(
    mocker,
    capfd,
    tmp_environ,
):
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    tmp_environ["THERE_AI_PROMPTS"] = "kivy,midi"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "hi"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )

    handle_ai(local_command(cell="debug remote", shell=mocker.Mock()))

    build_messages.assert_called_once_with(
        "debug remote",
        ("kivy", "midi"),
    )
    captured = capfd.readouterr()
    assert "Generated a %%there cell in " in captured.out


def test_session_prompt_options_are_used_before_config_prompt_options(
    mocker,
    tmp_environ,
):
    prompts.reset_ai_prompt_store()
    prompts.register_ai_prompt("session", "session rules")
    prompts.set_ai_prompts("session")
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    tmp_environ["THERE_AI_PROMPTS"] = "file"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "hi"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )

    try:
        handle_ai(local_command(cell="debug remote", shell=mocker.Mock()))
    finally:
        prompts.reset_ai_prompt_store()

    build_messages.assert_called_once_with(
        "debug remote",
        ("session",),
    )


def test_explicit_prompt_options_are_appended_to_session_prompt_options(
    mocker,
    tmp_environ,
):
    prompts.reset_ai_prompt_store()
    prompts.register_ai_prompt("session", "session rules")
    prompts.set_ai_prompts("session")
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "hi"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )

    try:
        handle_ai(
            local_command(
                line="--prompts explicit",
                cell="debug remote",
                shell=mocker.Mock(),
            )
        )
    finally:
        prompts.reset_ai_prompt_store()

    build_messages.assert_called_once_with(
        "debug remote",
        ("session", "explicit"),
    )


def test_explicit_prompt_options_keep_pythonhere_runtime_prompt_stack(
    mocker,
    tmp_environ,
):
    prompts.reset_ai_prompt_store()
    prompts.set_ai_prompts(
        "default",
        "kivy-runtime",
        "kivy-kv",
        "android-runtime",
        "jnius",
        "android-permissions",
        "android-packages",
        "android-media",
        "plyer",
    )
    tmp_environ["THERE_AI_MODEL"] = "test-model"
    build_messages = mocker.patch(
        "herethere.there.ai.command.build_messages",
        return_value=[{"role": "user", "content": "hi"}],
    )
    mocker.patch(
        "herethere.there.ai.command.call_openai_compatible",
        return_value="print('remote')",
    )

    try:
        handle_ai(local_command(line="--prompts midi", cell="debug remote"))
    finally:
        prompts.reset_ai_prompt_store()

    build_messages.assert_called_once_with(
        "debug remote",
        (
            "default",
            "kivy-runtime",
            "kivy-kv",
            "android-runtime",
            "jnius",
            "android-permissions",
            "android-packages",
            "android-media",
            "plyer",
            "midi",
        ),
    )

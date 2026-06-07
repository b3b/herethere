"""Local %%there ai command."""

import re
import shlex
import time
from dataclasses import dataclass

import click
from IPython import get_ipython

from herethere.there.history import RecentThereCell
from herethere.there.local_commands import LocalThereCommand

from .config import AIConfigError, get_ai_config
from .llm import AIProviderError, call_openai_compatible
from .postprocess import postprocess_code
from .prompts import (
    FIX_AI_PROMPT,
    AIPromptError,
    build_messages,
    resolve_ai_prompt_options,
)

HEADER = """# Generated locally by %%there ai. Review before running.
"""

SUSPICIOUS_TERMS = [
    "os.system",
    "subprocess",
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "rm -rf",
    'open("/sdcard',
    "contacts",
    "sms",
    "call_log",
    "request_permission",
    "android.permissions",
    "urllib.request.urlopen",
    "requests.post",
]


@dataclass(frozen=True)
class AICommandOptions:
    """Parsed options for a local %%there ai command."""

    prompts: tuple[str, ...] | None
    fix: bool


@dataclass(frozen=True)
class AIRequest:
    """Prepared AI generation request."""

    user_request: str
    options: AICommandOptions


@dataclass(frozen=True)
class GeneratedAICell:
    """Generated review cell and timing metadata."""

    cell: str
    elapsed_seconds: float


def _split_prompt_names(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@click.command("ai")
@click.option(
    "--prompts",
    default="",
    help=("Comma-separated prompt sections to append to the active prompt stack."),
)
@click.option(
    "--fix",
    is_flag=True,
    help="Fix the last executed %%there Python cell using the prompt as guidance.",
)
def ai_command(prompts: str, fix: bool) -> AICommandOptions:
    return AICommandOptions(
        prompts=_split_prompt_names(prompts) if prompts else None,
        fix=fix,
    )


def parse_ai_line(line: str) -> AICommandOptions:
    """Parse the local %%there ai line with Click without letting Click exit."""
    try:
        args = shlex.split(line)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    result = ai_command.main(
        args=args,
        prog_name="%%there ai",
        standalone_mode=False,
    )
    if isinstance(result, int):
        raise click.exceptions.Exit(result)
    return result


def find_suspicious_terms(code: str) -> list[str]:
    lowered = code.lower()
    found = []
    for term in SUSPICIOUS_TERMS:
        if term.lower() in lowered:
            found.append(term)
    if re.search(
        r"\bopen\s*\([^)]*(?:,\s*[\"'][^\"']*w|mode\s*=\s*[\"'][^\"']*w)",
        code,
    ):
        found.append('open(..., "w")')
    return found


def _build_fix_request(fix_instruction: str, previous: RecentThereCell) -> str:
    """Build dynamic user content for --fix using the last executable cell."""
    return (
        f"Previous magic line:\n%%there {previous.line}".rstrip() + "\n\n"
        "Previous cell body:\n"
        "```python\n"
        f"{previous.cell.rstrip()}\n"
        "```\n\n"
        "User's description of the failure or requested change:\n"
        f"{fix_instruction.strip()}"
    )


def _prepare_user_request(
    options: AICommandOptions,
    user_request: str,
    command: LocalThereCommand,
) -> str | None:
    if user_request:
        if not options.fix:
            return user_request
        previous = command.history.latest()
        if previous is None:
            click.echo(
                "No previous %%there cell is available to fix. "
                "Run the cell you want to fix, then retry."
            )
            return None
        return _build_fix_request(user_request, previous)

    if options.fix:
        click.echo("%%there ai --fix requires a fix instruction in the cell body.")
    else:
        click.echo("%%there ai requires a prompt in the cell body.")
    return None


def _parse_request(command: LocalThereCommand) -> AIRequest | None:
    user_request = command.cell.strip()
    options = parse_ai_line(command.line)
    user_request = _prepare_user_request(options, user_request, command)
    if user_request is None:
        return None
    return AIRequest(user_request=user_request, options=options)


def _generate_code(request: AIRequest) -> str | None:
    config = get_ai_config()
    prompt_names = resolve_ai_prompt_options(
        request.options.prompts,
        config_prompt_names=config.prompts,
    )
    if request.options.fix:
        # The fix prompt is a reusable system prompt section. The previous cell
        # and user instruction stay in the user message built above.
        prompt_names = (*tuple(prompt_names or ()), FIX_AI_PROMPT)
    messages = build_messages(
        request.user_request,
        prompt_names,
    )
    click.echo(
        f"Generating %%there cell with AI... this can take up to {config.timeout:g}s."
    )
    raw_code = call_openai_compatible(messages, config)
    generated_code = postprocess_code(raw_code)
    if not generated_code:
        click.echo("AI provider returned no code.")
        return None
    return generated_code


def _build_generated_cell(code: str, options: AICommandOptions) -> str:
    header = HEADER
    if options.fix:
        header += "# AI mode: fix\n"
    return "%%there\n" + header + code.strip() + "\n"


def _generate_ai_cell(command: LocalThereCommand) -> GeneratedAICell | None:
    """Return a generated review cell, handling expected user/provider errors."""
    generated_cell = None
    started_at = time.monotonic()
    try:
        request = _parse_request(command)
        if request is not None:
            generated_code = _generate_code(request)
            if generated_code is not None:
                generated_cell = GeneratedAICell(
                    cell=_build_generated_cell(generated_code, request.options),
                    elapsed_seconds=time.monotonic() - started_at,
                )
    except click.exceptions.Exit:
        pass
    except click.ClickException as exc:
        click.echo(str(exc))
    except AIPromptError as exc:
        click.echo(str(exc))
    except AIConfigError as exc:
        click.echo(str(exc))
    except AIProviderError as exc:
        click.echo(f"herethere AI generation failed: {exc}")
    return generated_cell


def handle_ai(command: LocalThereCommand) -> None:
    result = _generate_ai_cell(command)
    if result is None:
        return

    ip = command.shell or get_ipython()
    if ip is None:
        click.echo("Could not find active IPython shell to insert generated cell.")
        return

    ip.set_next_input(result.cell, replace=False)
    click.echo(
        f"Generated a %%there cell in {result.elapsed_seconds:.1f}s. "
        "Review it, then run it to execute on the connected target."
    )

    suspicious = find_suspicious_terms(result.cell)
    if suspicious:
        click.echo(
            "Warning: generated code contains terms that may need careful review: "
            + ", ".join(sorted(set(suspicious)))
        )

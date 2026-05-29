"""AI-assisted local generation of %%there notebook cells."""

from herethere.there.local_commands import register_local_there_command

from .config import clear_ai_config_path, set_ai_config_path
from .prompts import (
    build_ai_template,
    build_messages,
    clear_ai_prompts,
    get_ai_prompt,
    list_ai_prompts,
    register_ai_prompt,
    set_ai_prompts,
)


def register_ai_commands() -> None:
    from .command import handle_ai  # noqa: PLC0415

    register_local_there_command("ai", handle_ai)


__all__ = [
    "build_ai_template",
    "build_messages",
    "clear_ai_config_path",
    "clear_ai_prompts",
    "get_ai_prompt",
    "list_ai_prompts",
    "register_ai_commands",
    "register_ai_prompt",
    "set_ai_config_path",
    "set_ai_prompts",
]

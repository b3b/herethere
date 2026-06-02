"""Prompt sections for %%there ai."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.resources import files

DEFAULT_AI_TEMPLATE_RESOURCE = "prompts/default.md"
FIX_AI_TEMPLATE_RESOURCE = "prompts/fix.md"
DEFAULT_AI_PROMPT = "default"
FIX_AI_PROMPT = "fix"


class AIPromptError(RuntimeError):
    """Raised when %%there ai prompt composition fails."""


@dataclass
class AIPromptStore:
    """Mutable prompt definitions and active prompt stack.

    Built-in prompts live in the same registry as user prompts so
    register_ai_prompt() can intentionally override any prompt section.
    """

    active_prompts: tuple[str, ...] | None = None
    registry: dict[str, str] = field(default_factory=dict)


def _read_prompt_resource(name: str) -> str:
    return (
        files("herethere.there.ai").joinpath(name).read_text(encoding="utf-8").strip()
    )


def _builtin_prompt_registry() -> dict[str, str]:
    return {
        DEFAULT_AI_PROMPT: _read_prompt_resource(DEFAULT_AI_TEMPLATE_RESOURCE),
        FIX_AI_PROMPT: _read_prompt_resource(FIX_AI_TEMPLATE_RESOURCE),
    }


_ai_prompt_store = AIPromptStore(registry=_builtin_prompt_registry())


def reset_ai_prompt_store() -> None:
    """Reset active prompt state and restore built-in prompt definitions."""
    _ai_prompt_store.active_prompts = None
    _ai_prompt_store.registry.clear()
    _ai_prompt_store.registry.update(_builtin_prompt_registry())


def _normalize_prompt_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("AI prompt name cannot be empty")
    return normalized


def _normalize_prompt_text(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError("AI prompt cannot be empty")
    return normalized


def _split_prompt_names(value: str) -> tuple[str, ...]:
    names = []
    for part in value.split(","):
        normalized = part.strip()
        if normalized:
            names.append(normalized)
    return tuple(names)


def _dedupe_prompt_names(names: Iterable[str]) -> tuple[str, ...]:
    seen = set()
    deduped = []
    for name in names:
        normalized = _normalize_prompt_name(name)
        if normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return tuple(deduped)


def register_ai_prompt(name: str, prompt: str) -> None:
    """Register or override a reusable prompt section."""
    _ai_prompt_store.registry[_normalize_prompt_name(name)] = _normalize_prompt_text(
        prompt
    )


def list_ai_prompts() -> tuple[str, ...]:
    """Return registered and built-in prompt section names."""
    return tuple(sorted(_ai_prompt_store.registry))


def get_ai_prompt(name: str) -> str:
    """Return one registered or built-in prompt section."""
    normalized = _normalize_prompt_name(name)
    try:
        return _ai_prompt_store.registry[normalized]
    except KeyError as exc:
        raise AIPromptError(f"Unknown %%there ai prompt: {normalized!r}") from exc


def set_ai_prompts(*names: str) -> None:
    """Set the session prompt stack used by %%there ai."""
    if len(names) == 1 and "," in names[0]:
        names = _split_prompt_names(names[0])

    prompt_names = build_ai_prompt_names(names)
    _ai_prompt_store.active_prompts = prompt_names


def clear_ai_prompts() -> None:
    """Clear session-level %%there ai prompt overrides."""
    _ai_prompt_store.active_prompts = None


def build_ai_prompt_names(
    prompt_names: Iterable[str],
) -> tuple[str, ...]:
    """Build an ordered, deduplicated prompt stack."""
    deduped = _dedupe_prompt_names(prompt_names)
    if not deduped:
        raise ValueError("AI prompt stack cannot be empty")
    return deduped


def build_ai_template(
    prompt_names: Iterable[str] | None = None,
) -> str:
    """Compose the named prompt sections into one system prompt."""
    names = (
        (DEFAULT_AI_PROMPT,)
        if prompt_names is None
        else build_ai_prompt_names(prompt_names)
    )
    return "\n\n".join(get_ai_prompt(name) for name in names)


def resolve_ai_prompt_options(
    prompt_names: Iterable[str] | None = None,
    *,
    config_prompt_names: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Resolve command, session, and config prompt names into one stack."""
    session_prompts = _ai_prompt_store.active_prompts

    if session_prompts is not None:
        base_prompts = session_prompts
    elif config_prompt_names is not None:
        base_prompts = build_ai_prompt_names(config_prompt_names)
    elif prompt_names is None:
        return (DEFAULT_AI_PROMPT,)
    else:
        base_prompts = ()

    if prompt_names is not None:
        return build_ai_prompt_names((*base_prompts, *prompt_names))
    return base_prompts


def get_ai_template(
    prompt_names: Iterable[str] | None = None,
) -> str:
    if prompt_names is not None:
        return build_ai_template(prompt_names)

    session_prompts = _ai_prompt_store.active_prompts
    if session_prompts is not None:
        return build_ai_template(session_prompts)

    return build_ai_template()


def build_messages(
    user_request: str,
    prompt_names: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    template = get_ai_template(prompt_names)
    return [
        {"role": "system", "content": template},
        {"role": "user", "content": user_request.strip()},
    ]

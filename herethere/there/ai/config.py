"""Configuration for %%there ai."""

from dataclasses import dataclass
from pathlib import Path

from herethere.everywhere.config import load_prefixed_env, prefixed_key


@dataclass(frozen=True)
class AIConfig:
    """OpenAI-compatible provider configuration for %%there ai."""

    base_url: str
    model: str
    api_key: str
    temperature: float
    timeout: float
    prompts: tuple[str, ...] | None = None

    @classmethod
    def load(cls, prefix: str = "there_ai", path: str = None) -> "AIConfig":
        """Load AI configuration from dotenv and environment variables."""
        env = load_prefixed_env(prefix=prefix, path=path)
        return cls.load_from_dict(env=env, prefix=prefix)

    @classmethod
    def load_from_dict(cls, *, env: dict[str, str], prefix: str) -> "AIConfig":
        base_url = env.get(
            prefixed_key(prefix=prefix, key="base_url"),
            "https://api.openai.com/v1",
        )
        model = env.get(prefixed_key(prefix=prefix, key="model"), "").strip()
        api_key = env.get(prefixed_key(prefix=prefix, key="api_key"), "")
        temperature_text = env.get(
            prefixed_key(prefix=prefix, key="temperature"),
            "0.2",
        )
        timeout_text = env.get(prefixed_key(prefix=prefix, key="timeout"), "300")
        prompts_text = env.get(prefixed_key(prefix=prefix, key="prompts"), "").strip()

        if not model:
            raise AIConfigError(
                "herethere AI generation is not configured. "
                f"Set {prefixed_key(prefix=prefix, key='model')}. "
                "For hosted providers, also set "
                f"{prefixed_key(prefix=prefix, key='api_key')}. "
                "Optionally set "
                f"{prefixed_key(prefix=prefix, key='base_url')}."
            )

        try:
            temperature = float(temperature_text)
        except ValueError as exc:
            raise AIConfigError(
                f"{prefixed_key(prefix=prefix, key='temperature')} must be a number, "
                f"got {temperature_text!r}."
            ) from exc
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise AIConfigError(
                f"{prefixed_key(prefix=prefix, key='timeout')} must be a number, "
                f"got {timeout_text!r}."
            ) from exc
        if timeout <= 0:
            raise AIConfigError(
                f"{prefixed_key(prefix=prefix, key='timeout')} must be greater than 0."
            )

        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            prompts=_split_prompt_names(prompts_text) or None,
        )


class AIConfigError(RuntimeError):
    """Raised when AI generation is not configured."""


@dataclass
class AIConfigStore:
    """Mutable session state for %%there ai config loading."""

    path: str | None = None


_ai_config_store = AIConfigStore()


def _split_prompt_names(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def set_ai_config_path(path: str) -> None:
    """Set the session AI config file path used by %%there ai."""
    normalized = path.strip()
    if not normalized:
        raise ValueError("AI config path cannot be empty")
    if not Path(normalized).is_file():
        raise AIConfigError(f"AI config file does not exist: {normalized}")
    _ai_config_store.path = normalized


def clear_ai_config_path() -> None:
    """Clear the session AI config path and return to default discovery."""
    _ai_config_store.path = None


def get_ai_config() -> AIConfig:
    return AIConfig.load(path=_ai_config_store.path)

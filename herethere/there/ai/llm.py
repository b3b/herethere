"""OpenAI-compatible HTTP adapter for %%there ai."""

import json
import urllib.error
import urllib.request

from .config import AIConfig


class AIProviderError(RuntimeError):
    """Raised when the AI provider request fails."""


def call_openai_compatible(messages: list[dict[str, str]], config: AIConfig) -> str:
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
    }

    headers = {
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AIProviderError(
            f"AI provider request failed: HTTP {exc.code}: {body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AIProviderError(f"AI provider request failed: {exc}") from exc
    except TimeoutError as exc:
        raise AIProviderError(
            f"AI provider request timed out after {config.timeout:g} seconds."
        ) from exc
    except json.JSONDecodeError as exc:
        raise AIProviderError("AI provider returned invalid JSON.") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(
            "AI provider response did not contain choices[0].message.content."
        ) from exc

    if not isinstance(content, str) or not content.strip():
        raise AIProviderError("AI provider returned empty content.")

    return content

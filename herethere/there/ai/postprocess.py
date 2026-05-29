"""Post-processing for generated %%there code."""

import re


def strip_markdown_fences(text: str) -> str:
    stripped = text.strip()

    fence_match = re.fullmatch(
        r"```(?:python|py)\s*\n(?P<code>.*?)\n```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        return fence_match.group("code").strip()

    generic_fence_match = re.fullmatch(
        r"```\s*\n(?P<code>.*?)\n```",
        stripped,
        flags=re.DOTALL,
    )
    if generic_fence_match:
        return generic_fence_match.group("code").strip()

    return stripped


def remove_leading_there_magic(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("%%there"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def postprocess_code(text: str) -> str:
    code = strip_markdown_fences(text)
    code = remove_leading_there_magic(code)
    return code.strip()

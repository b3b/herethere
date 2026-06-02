---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.19.3
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# %%there ai: prompt-to-cell generator

`%%there ai` generates a `%%there` cell from a plain-language request.
It adds the generated cell below the current one,
ready for you to review, edit, and run.


```{code-cell}
%load_ext herethere.magic
%connect-there
```

## Configuration

`%%there ai` uses an OpenAI-compatible chat API. Put the default settings in
`there_ai.env`:

```text
THERE_AI_MODEL=gpt-5.5
THERE_AI_API_KEY=sk-...
THERE_AI_BASE_URL=https://api.openai.com/v1
THERE_AI_TEMPERATURE=0.2
THERE_AI_TIMEOUT=300
```

Only `THERE_AI_MODEL` is required for local providers that do not need an API
key. Hosted providers usually need `THERE_AI_API_KEY`.

`THERE_AI_BASE_URL` defaults to `https://api.openai.com/v1`.
`THERE_AI_TEMPERATURE` defaults to `0.2`.
`THERE_AI_TIMEOUT` defaults to `300` seconds.

Environment variables with the same names override values from `there_ai.env`:

```{code-cell}
:tags: ["remove-output"]
%env THERE_AI_TIMEOUT=120
```

Use a different settings file for the current notebook session:

```{code-cell}
from herethere.there.ai import clear_ai_config_path, set_ai_config_path

set_ai_config_path("there_ai.local.env")
```

Example local-provider settings:

```text
THERE_AI_MODEL=qwen2.5-coder
THERE_AI_BASE_URL=http://localhost:11434/v1
THERE_AI_TEMPERATURE=0.1
THERE_AI_TIMEOUT=300
```

## Basic usage

Write the request in the cell body:

```{code-cell}
%%there ai
show Python version, platform details, and the current working directory
```

The generated cell may look like this:

```python
%%there
# Generated locally by %%there ai. Review before running.
import sys
import platform
import os
from pathlib import Path

print(f"Python version: {sys.version}")
print(f"Platform: {platform.platform()}")
print(f"Architecture: {platform.architecture()[0]}")
print(f"Machine: {platform.machine()}")
print(f"Processor: {platform.processor()}")
print(f"System: {platform.system()} {platform.release()}")
print(f"Current working directory: {os.getcwd()}")
print(f"CWD as Path: {Path.cwd()}")
```

## Prompt sections

`%%there ai` builds one system prompt from named prompt sections.

The built-in `default` section is used as the fallback when no prompt stack is
configured. It tells the model to generate code for herethere cells, keep
execution remote, and return only code.

Register extra sections when the model needs project-specific context:

```{code-cell}
from herethere.there.ai import register_ai_prompt, set_ai_prompts

register_ai_prompt(
    "fastapi-runtime",
    """
    The remote process is a running FastAPI application.
    The remote namespace may contain app, settings, engine, SessionLocal, or logger.
    Prefer inspecting app.routes, app.state, dependency_overrides, and settings.
    Do not call uvicorn.run() or create a second FastAPI app.
    Do not modify routes, dependency overrides, or database state unless asked.
    """,
)

set_ai_prompts("default", "fastapi-runtime")
```

Now normal `%%there ai` requests include both `default` and `fastapi-runtime`:

```{code-cell}
%%there ai
list FastAPI routes with methods and paths, then summarize app.state keys
```

Clear notebook-level prompt sections:

```{code-cell}
from herethere.there.ai import clear_ai_prompts

clear_ai_prompts()
```

## Prompt lookup order

Prompt names can come from three places. Session prompts are the base stack for
the notebook, config prompts are the base stack for the project, and command
prompts add one-request guidance to whichever base is active.

1. `set_ai_prompts(...)` for the current notebook session.
2. `%%there ai --prompts ...` for one request.
3. `THERE_AI_PROMPTS=...` in `there_ai.env` or the environment.

`set_ai_prompts(...)` and `THERE_AI_PROMPTS` use exactly the prompt names listed.
Include `default` explicitly when that base prompt should be part of the stack.
When command prompts are provided, `--prompts` appends to the active session or
config stack. If no session/config stack exists, `--prompts` uses only the listed
prompts. If no prompt source exists at all, `%%there ai` falls back to `default`.

Use prompt names in the command line for one request:

```{code-cell}
%%there ai --prompts fastapi-runtime
list FastAPI routes with methods and paths, then summarize app.state keys
```

That command uses only `fastapi-runtime` unless a session or config stack is active.

Multiple prompt sections are comma-separated:

```{code-cell}
%%there ai --prompts fastapi-runtime,sqlalchemy-runtime
show route count and database engine URL driver name without printing credentials
```

Use `THERE_AI_PROMPTS` when a notebook or project should use the same prompt
sections by default:

```text
THERE_AI_PROMPTS=default,fastapi-runtime
```

## Inspect prompts

List available prompt sections:

```{code-cell}
:tags: ["hide-output"]
from herethere.there.ai import list_ai_prompts

print(list_ai_prompts())
```

Preview the full system prompt:

```{code-cell}
:tags: ["hide-output"]
from herethere.there.ai import build_ai_template

print(build_ai_template(["fastapi-runtime"]))
```

Inspect one registered prompt section:

```{code-cell}
:tags: ["hide-output"]
from herethere.there.ai import get_ai_prompt

print(get_ai_prompt("default"))
```

Inspect the messages sent to the provider:

```{code-cell}
:tags: ["hide-output"]
from herethere.there.ai import build_messages

messages = build_messages(
    "list FastAPI routes with methods and paths, then summarize app.state keys",
    ["fastapi-runtime"],
)

for message in messages:
    print(f"--- {message['role']} ---")
    print(message["content"])
```

## Fix a previous %%there cell

`%%there ai --fix` is for the normal notebook loop: run a `%%there` cell, see
an error, and ask AI to generate a new fixed `%%there` cell.

For example, suppose this cell was run:

```python
%%there
print(app_state.value)
```

And it failed because the remote object is named `app.state`, not `app_state`.
Ask for a fix:

```{code-cell}
:tags: ["remove-output"]
%%there ai --fix
It failed with NameError: app_state is not defined
```

`%%there ai --fix` sends the last Python `%%there` cell you ran plus your fix
instruction to the AI provider. It also adds the prompt section named `fix`.
The result is a new `%%there` cell below the current one. The old cell is not
changed.

The generated cell may look like this:

```python
%%there
# Generated locally by %%there ai. Review before running.
# AI mode: fix
# Fixed app_state to use app.state.
print(app.state.value)
```

Include the important error message in the `--fix` cell. The previous output
and traceback are not added automatically.

The built-in prompt section named `fix` says to preserve the original intent
and return a full replacement cell body. You can inspect it:

```{code-cell}
from herethere.there.ai import get_ai_prompt

print(get_ai_prompt("fix"))
```

You can register your own `fix` section with the same name. For example, this
version makes `--fix` generate an additional cell that continues from the
previous one:

```{code-cell}
from herethere.there.ai import register_ai_prompt

register_ai_prompt(
    "fix",
    """
    You are generating an additional %%there Python cell.
    Assume the previous cell already ran successfully.
    Build on variables, files, or state created by the previous cell.
    Do not repeat expensive work from the previous cell unless the user asks.
    Use the user's instruction as the requested next step.
    Keep output concise and store detailed results in a named global variable.
    Return only the new follow-up Python cell body, not a replacement for the
    previous cell, patch, or explanation.
    """,
)
```

With that prompt registered, a follow-up request can look like this:

```{code-cell}
:tags: ["remove-output"]
%%there ai --fix
Add a short summary of the collected values and print only the top 5 items.
Do not collect the data again.
```

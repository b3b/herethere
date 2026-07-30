---
name: there-cli
description: "Operate a running herethere target with the installed `there` terminal CLI: check connectivity, inspect live Python state and logs, execute Python or remote shell commands, and transfer files. Use when a task mentions herethere, a `there.env` connection, the `there` command, remote live-process debugging, remote code execution, or moving files to or from a herethere server."
---

# Use the `there` CLI

Use `there` to work with an already configured herethere server. Treat Python
and shell execution as actions in the live remote process or host, not as a
local sandbox.

## Workflow

1. Resolve the command used to invoke `there`, then use it consistently:
   - Use an explicit command or runner supplied by the user first.
   - Otherwise follow governing project instructions such as `AGENTS.md`. For
     example, when the project requires uv, invoke the CLI as `uv run there`.
   - Otherwise invoke `there` directly when it is available on `PATH`.
   - If the project has a known existing environment with an unambiguous
     `there` executable, such as `.venv/bin/there` or
     `.venv/Scripts/there.exe`, it may be invoked directly.
   - Do not infer uv, Poetry, or another environment runner merely from
     `library-skills`, a lockfile, or tool availability. A runner may create,
     synchronize, or modify an environment.
   - If no resolved invocation is available, report that the herethere package
     must be installed or its project runner identified. Do not install,
     upgrade, or synchronize dependencies unless authorized.
   - Examples in this skill use `there`; replace it with the resolved invocation
     prefix, preserving all following root options and commands.
2. Discover the installed interface without repeating unnecessary help calls:
   - Run `there --help` once per session when first using the CLI, unless the
     interface is already established by recent output.
   - Run `there COMMAND --help` only when the command is unfamiliar, its syntax
     or flags are version-sensitive, required arguments remain unclear, or a
     previous attempt failed with a usage or unknown-option error.
   - Do not run subcommand help mechanically before every invocation.
   - Re-check help when the installed `there` version changes. Extensions may
     add commands.
3. Locate the connection:
   - Use the user's `--config PATH` when provided.
   - Otherwise let `there` search the current directory and its parents for
     `there.env`.
   - Never print or expose passwords from the config or environment.
4. Choose the command by the required operation and result:
   - `ping` when checking or diagnosing connectivity, authentication, or
     command routing.
   - `get` to evaluate exactly one Python expression and return its value.
     Use it only for small-to-medium inspectable values such as counters,
     summaries, or configuration. It rejects statements, but the expression
     itself can have side effects.
     In JSON mode, return a JSON-compatible value. When readable inspection of
     a known binary or third-party value is sufficient, convert it in the
     original expression, for example `there --json get "repr(value)"`; this
     returns a string rather than structured data. For structured inspection,
     build a JSON-compatible summary instead. Plain text `there get` already
     prints the value's `repr`.
   - `logs` for recent Python logs.
   - `run` for Python statements, multiline programs, or output-oriented work
     in the live interpreter.
   - `shell` for a subprocess on the remote host.
   - `upload` or `download` for recursive SFTP transfer.
   - For a large result, prefer returning a remote summary or slice. When the
     complete result is required, use `run` to save it to a remote file
     accessible to `download`, then retrieve it.
5. Prefer `--json` for agent-driven calls. Check the process exit code, `ok`,
   `error`, and truncation fields before interpreting output.
6. Verify mutations with a narrow observation of the intended state. Use `get`
   when that observation is naturally a returned expression value, or use
   `logs` or another user-relevant check. Summarize what ran, which
   target/config was selected without disclosing secrets, and the observed result.

Place every root option before the command:

```console
there --json --config ./there.env --timeout 30 run --code "print(status)"
```

## Safety

- Obtain normal authorization for consequential remote mutations. A connection
  config identifies a target; it does not itself authorize unrelated changes.
- Use `ping` and `logs` for diagnostics that do not require evaluating code.
- Do not treat `get` as read-only. It evaluates arbitrary expression syntax in
  the live Python namespace; function calls, property access, operators, and
  comprehensions can execute code or mutate state.
- Do not retry a mutating command merely because output was truncated or the
  connection dropped; first determine whether it may already have executed.
- Use `--timeout` for potentially blocking work. `ping` defaults to 10 seconds,
  while other commands have no total timeout by default.
- Avoid putting multiline or quoting-sensitive programs inline. Write or reuse
  a local UTF-8 file and pass it to `run` or `shell`.
- Remember that `get` and `run` operate in the live application's Python
  namespace. State persists across invocations and evaluation or execution may
  leave partial changes.
- Remember that SFTP paths are rooted by the server configuration, but Python
  and shell execution are not constrained by that SFTP root.

## Command selection

Use these common forms:

```console
there --json get "app.status"
there --json logs --records 100
there --json run --code "cache.clear()"
there --json run local_script.py
there --json shell --command "uname -a"
there --json upload ./artifact.bin remote-directory
there --json download remote-result.json ./remote-result.json
```

Pass `-` to `run` or `shell` only when deliberately sending local stdin. A
positional argument to `shell` is always a local script file; use
`shell --command "./script.sh"` to run a file already present remotely.

Read [references/commands.md](references/commands.md) before composing transfers,
handling failures, parsing JSON fields, or using an unfamiliar command.

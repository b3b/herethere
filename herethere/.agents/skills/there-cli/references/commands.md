# `there` command reference

## Invocation

```text
there [ROOT OPTIONS] COMMAND [COMMAND OPTIONS] [ARGUMENTS]
```

Root options:

| Option | Meaning |
| --- | --- |
| `--config PATH` | Select a connection file instead of searching for `there.env` |
| `--timeout SECONDS` | Set a positive total operation timeout |
| `--max-output BYTES` | Retain 1–1,048,576 bytes per output stream in JSON mode; default 65,536 |
| `--format text|json` | Select output format |
| `--json` | Alias for `--format json` |

Always place root options before the command name. Use `there --help` to find
commands supplied by installed extension packages and
`there COMMAND --help` for the exact installed syntax.

## Connection

Without `--config`, `there` searches the current directory and its parents for
`there.env`. The file uses:

```dotenv
THERE_HOST=127.0.0.1
THERE_PORT=8022
THERE_USERNAME=here
THERE_PASSWORD=secret
```

Variables in the process environment override values from the file.

`there ping` validates connection, authentication, command routing, and the
herethere response. It does not prove that application-specific state is ready.

## Commands

### `get`

Evaluate exactly one Python expression in the live namespace and return its
value directly:

```console
there --json get "counter"
there --json get "type(app).__name__"
there --json get --background "generation_done.wait(180)"
```

Use `get` when the expression's resulting value is the desired output, avoiding
`print` and stdout parsing. Statements are rejected locally. Text mode prints
the value's `repr`; JSON-compatible results appear in `value`, while other
Python values cause a structured serialization error.

Keep returned values small-to-medium, such as counters, summaries, slices, or
configuration. Pickle payloads larger than 32 MiB are rejected by default. For
a large generated result, return a remote summary or slice when sufficient.
When the complete result is required, use `run` to save it to a remote file
accessible to `download`, then retrieve it.

The expression-only restriction is syntactic, not a read-only guarantee.
Calls, property access, operators, and comprehensions may execute arbitrary
Python or mutate the live process.

Use `--background` for an expression which blocks or waits. The CLI remains
attached, the expression runs in a server worker thread, and its value is
returned normally. Prefer finite waits because disconnecting or timing out does
not cancel worker code already running. Keep APIs tied to the application's main
or event-loop thread in a foreground command or explicitly schedule them onto
their required thread.

### `logs`

Retrieve a finite snapshot of recent remote Python logs:

```console
there --json logs
there --json logs --records 100
```

JSON adds `text`, `bytes`, `records`, `truncated`, and `server_truncated`.

### `run`

Execute Python in the existing remote process and namespace:

```console
there --json run script.py
there --json run -
there --json run --code "counter += 1"
there --json run --background --code "perform_expensive_work()"
```

Supply exactly one local file, local stdin (`-`), or inline code (`--code` /
`-c`). The maximum input is 64 KiB. A `ProtocolVersionError` means the remote
herethere server is too old for the operation; report the mismatch rather than
blindly retrying.

Background `run` remains attached and executes in a server worker thread. It
discards user stdout and stderr but retains success, exception, traceback, and
exit-status reporting. Keep APIs tied to the application's main or event-loop
thread in a foreground command or explicitly schedule them onto their required
thread.

### `shell`

Execute text with the remote platform shell:

```console
there --json shell local-script.sh
there --json shell -
there --json shell --command "python --version"
there --json shell --command "./already-remote.sh"
```

Supply exactly one local file, local stdin (`-`), or inline command
(`--command` / `-c`). A positional file is read locally and its contents are
sent; it is not a remote path. JSON adds `returncode`.

### `upload`

Upload files or directories recursively:

```console
there --json upload local.py
there --json upload local.py data remote-directory
```

With one argument, the remote destination is `.`. With multiple arguments, the
last is the remote destination and all preceding arguments are local sources.
JSON adds `local_paths` and `remote_path`.

### `download`

Download files or directories recursively:

```console
there --json download result.csv
there --json download result.csv remote-directory ./downloads
```

With one argument, the local destination is `.`. With multiple arguments, the
last is the local destination and all preceding arguments are remote sources.
JSON adds `remote_paths` and `local_path`.

## JSON envelope

Every JSON response contains at least:

```json
{
  "ok": true,
  "command": "ping",
  "exit_code": 0,
  "stdout": "",
  "stdout_bytes": 0,
  "stdout_truncated": false,
  "stderr": "",
  "stderr_bytes": 0,
  "stderr_truncated": false,
  "error": null
}
```

On failure, `ok` is `false`, and `error` includes a type, phase, and readable
message. JSON mode emits no extra traceback or messages outside this object.
When a stream is truncated, the retained text is the end of that stream.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Command completed successfully |
| `2` | Invalid usage or connection configuration |
| `3` | Connection or authentication failure |
| `4` | Remote operation failure |
| `5` | Local file or I/O failure |
| `124` | Timeout |

Treat the structured `error` fields as the primary diagnosis. For an ambiguous
failure during mutation, inspect the remote state before retrying.

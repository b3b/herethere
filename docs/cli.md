# Using `there` from a terminal

`there` is the terminal entry point for herethere commands. To see the
commands available in the current environment:

```console
there --help
```

Installed extension packages can add commands to this list. To see the
arguments and options accepted by a command:

```console
there COMMAND --help
```

## Output format

Commands produce readable text by default:

```console
there COMMAND [OPTIONS]
```

For scripts and other tools, request one JSON object:

```console
there --json COMMAND [OPTIONS]
there --format json COMMAND [OPTIONS]
```

`--json` and `--format` configure the whole invocation, so they must appear
before the command name. `--format text` explicitly selects normal terminal
output.

JSON responses always contain:

```json
{
  "ok": true,
  "command": "COMMAND",
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

On failure, `ok` is `false` and `error` describes the error type, the phase
which failed, and a readable message. JSON mode writes no additional messages
or tracebacks outside this object.

## Connecting to a target

Commands which connect to a herethere target accept:

```text
--config PATH
--timeout SECONDS
--max-output BYTES
```

Use `--config` to select a specific connection file:

```console
there --json COMMAND --config ./there.env
```

Without `--config`, herethere searches the current directory and its parents
for `there.env`. A connection file uses these variables:

```dotenv
THERE_HOST=127.0.0.1
THERE_PORT=8022
THERE_USERNAME=here
THERE_PASSWORD=secret
```

Environment variables with the same names override values from the file.

`--timeout` limits how long the operation may take. `--max-output` controls
how many bytes of each output stream JSON mode retains. The default is 65536
bytes and the maximum is 1048576 bytes. When output is larger, herethere keeps
the end of the stream and sets the corresponding `*_truncated` field.

## Running code in the live interpreter

`run` executes Python in the existing remote process and namespace:

```console
there run app.py
there run -
there run --code "counter += 1"
there --json run --config ./there.env app.py
```

Supply exactly one file, `-` for UTF-8 code on stdin, or `--code`. Remote
stdout and stderr are kept separate. A remote Python exception produces exit
code `4`; JSON mode includes its Python type, message, and bounded traceback.

`get` exposes the same expression operation as Jupyter's `%there get`:

```console
there get "counter"
there --json get --config ./there.env "app.root"
```

Text mode prints the returned Python value. JSON-compatible results are returned
in the `value` field; other values produce a structured serialization error.
Statements are rejected before connecting.

When `there run` reports `ProtocolVersionError`, upgrade herethere on the remote
target before retrying.

## Transferring files and directories

`upload` and `download` expose the same recursive SFTP operations and path
semantics as Jupyter's `%there upload` and `%there download`:

```console
there upload local.py
there upload local.py data remote-directory
there download result.csv
there download result.csv remote-directory ./downloads
there --json upload --config ./there.env local.py .
there --json download --config ./there.env result.csv ./result.csv
```

With one upload path, the remote destination defaults to `.`. With multiple
paths, the final argument is the remote destination and all preceding arguments
are local sources. Upload sources must exist before herethere connects.

With one download path, the local destination defaults to `.`. With multiple
paths, the final argument is the local destination and all preceding arguments
are remote sources. Files and directories are transferred recursively.

Transfer paths are resolved through the server's configured SFTP root. This
root controls SFTP path resolution only; it does not restrict Python code
executed in the remote process.

Successful JSON responses add these command-specific fields to the common
envelope:

```json
{
  "local_paths": ["local.py", "data"],
  "remote_path": "remote-directory"
}
```

```json
{
  "remote_paths": ["result.csv", "remote-directory"],
  "local_path": "./downloads"
}
```

Missing or unreadable local sources and unwritable local destinations use exit
code `5`. SFTP operation failures, including missing remote paths, use exit
code `4`. A connection lost during transfer uses exit code `3`, and a transfer
timeout uses exit code `124`. JSON mode reports these as structured errors.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Command completed successfully |
| `2` | Invalid command usage or connection configuration |
| `3` | Connection or authentication failed |
| `4` | The remote operation failed |
| `5` | A local file or I/O operation failed |
| `124` | The operation timed out |

---
jupyter:
  jupytext:
    text_representation:
      extension: .md
      format_name: markdown
      format_version: '1.2'
      jupytext_version: 1.7.1
  kernelspec:
    display_name: Python 3
    language: python
    name: python3
---

# %there: SSH client

## Jupyter magic commands

Commands are provided by the *herethere.magic* extension.

```python
%load_ext herethere.magic
```

### %connect-there
**Connect to remote interpreter via SSH.**

Command takes a single optional argument: location of connection config.<br>
If argument is not provided, values are loaded from the **there.env** file.

Config values can be overridden by environment variables with same names.

```python
%env THERE_PORT=8022
%connect-there there.env
```

#### there.env example
```
# Hostname or address to connect to.
# Defaults to 127.0.0.1.
THERE_HOST=127.0.0.1

# Port number to connect to.
# Defaults to 8022.
THERE_PORT=8022

# Credentials
THERE_USERNAME=debug
THERE_PASSWORD=xxx
```


### %there group of commands

```python
%there --help
```

By default, *%there* executes Python code when no command is specified.


#### %%there
**Execute Python code on the remote side.**<br>

```python
%%there 
import this
```

#### %%there shell

```python
%there shell --help
```

```python
%%there shell
for i in 1 2 3
do
    echo -n "$i"
done
```

Periodically run the `top` command in the background and show the last two lines of output:

```python
%%there -bl 2 shell
while :; do
    top -b | head -n 2
    sleep 10
done
```

#### %there get

Evaluate one Python expression on the remote side and return the result as a
local Python value.

```python
%%there
x = 10
```

```python
value = %there get x + 1
value
```

Returned values are serialized with pickle. Nested values are supported when
every contained object is pickle-serializable, and any custom classes must be
available in the local environment. `%there get` is intended for small to
medium inspectable values such as counters, summaries, or configuration.
To avoid accidental large transfers, values whose pickle payload is larger
than 32 MiB are rejected by default.

#### %there upload

```python
%there upload --help
```

The SFTP root directory is set by the `HERE_SFTP_ROOT` value of the here-server
config. If unset, it defaults to the here-server process current directory.
This is not a process chroot or sandbox.

```python
%there upload sample-note.txt sample-script.py sample-dir .
```

When uploading one file or directory, the remote destination defaults to the
current SFTP directory:

```python
%there upload sample-note.txt
```

```python
%%there shell
find . -maxdepth 2 -type f | sort
```

#### %there download

```python
%there download --help
```

Files and directories are downloaded from the same SFTP root used by `%there upload`.

```python
%there download sample-note.txt ./downloaded-note.txt
```

```python
from pathlib import Path
Path("downloaded-note.txt").read_text()
```

When downloading one file or directory, the local destination defaults to the
current local directory. Directories use the same command:

```python
%there download sample-dir ./downloaded-sample-dir
```

```python
Path("downloaded-sample-dir/nested.txt").read_text()
```

For generated outputs that are too large for `%there get`, save a file under
the SFTP root and fetch it:

```python
%%there
import csv

rows = [
    {"sensor": "greenhouse-1", "metric": "temperature_c", "value": 21.8},
    {"sensor": "greenhouse-1", "metric": "humidity_pct", "value": 58.2},
    {"sensor": "greenhouse-2", "metric": "temperature_c", "value": 20.9},
    {"sensor": "greenhouse-2", "metric": "humidity_pct", "value": 61.4},
]

with open("sample-data.csv", "w", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=["sensor", "metric", "value"])
    writer.writeheader()
    writer.writerows(rows)
```

```python
%there download sample-data.csv ./sample-data.csv
```

```python
Path("sample-data.csv").read_text()
```

#### %there log

```python
%there log --help
```

```{note}
Since the command blocks and never ends, it is useful to run with the `--background` (`-b`) option
```

```python
%there -b -l 10 log
```

Emit a log record on the remote side. The record is streamed into the
background output above in an interactive notebook.

```python
%%there -d 0.2
import logging

logging.warning("hello from remote logging")
```

Example output:

```text
[WARNING] 2026-05-25 14:00:00 SSHServerHereThread_0 root: hello from remote logging
```

#### Custom subcommands

New subcommands can be registered with the {py:func}`@there_code_shortcut <herethere.there.commands.there_code_shortcut>` decorator and [click](https://click.palletsprojects.com/en/master/options/) options:


```python
from herethere.there.commands import there_code_shortcut
import click

@there_code_shortcut
@click.option("-n", "--number_to_print", type=int)
def mycommand(code: str, number_to_print):
    return f"print({number_to_print})"

%there mycommand -n 123
```


## Using it in code

```python
from herethere.everywhere import ConnectionConfig
from herethere.there import Client

config = ConnectionConfig.load(prefix="there")
client = Client()
await client.connect(config)
```

```python
await client.runcode("print('Hello there :)')")
```

```python
await client.runcode("x = 10")
value = await client.get("x + 1")
```

```python
await client.shell("sleep 1 ; ping -c 1 8.8.8.8")
```

```python
await client.disconnect()
```

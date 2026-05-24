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

Command takes single optional argument: location of connection config.<br>
If argument is not provided, values are loaded from the **there.env** file.

Config values could be overridden by environment variables with same names.

```python
import os
os.environ["THERE_PORT"] = "8022"
```

```python
%connect-there there.env
```

#### there.env example
```
# Hostname or address to connect to
THERE_HOST=127.0.0.1

# Port number to connect to
THERE_PORT=8023

# Credentials
THERE_USERNAME=debug
THERE_PASSWORD=xxx
```


### %there group of commands

```python
%there --help
```

Default action for *%there*, if command is not specified - execute python code.


#### there
**Execute python code on the remote side.**<br>

```python
%%there 
import this
```

#### shell

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

Priodically run the `top` command in the background and show last two lines of output:

```python
%%there -bl 2 shell
while :; do
    top -b | head -n 2
    sleep 10
done
```

#### get

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

The expression is evaluated in the same remote namespace used by `%there` and
`%%there`.

Returned values are serialized with pickle. Nested values are supported when
every contained object is pickle-serializable, and any custom classes must be
available in the local environment. `%there get` is intended for small to
medium inspectable values; for large data, return a summary or use `download`
instead. To avoid accidental large transfers, values whose pickle payload is
larger than 32 MiB are rejected by default.

#### upload

```python
%there upload --help
```

The SFTP root directory is set by the `HERE_CHROOT` value of the here-server
config.

```python
!touch some.ico script.py
!mkdir -p dir1/dir2
```

```python
%there upload some.ico script.py dir1 /
```

```python
%%there shell
ls some.ico script.py
```

#### download

```python
%there download --help
```

Files and directories are downloaded from the same SFTP root used by
`%there upload`.

```python
%there download some.ico ./some.ico
```

```python
%there download dir1 ./downloaded-dir1
```

For large data, write a file remotely and download it:

```python
%%there
data.to_csv("test-data.csv")
```

```python
%there download test-data.csv ./test-data.csv
```

#### log

```python
%there log --help
```

```{note}
Since the command blocks and never ends, it is useful to run with --backgroud (-b) option
```

```python
%there -b -l 10 log
```

#### Custom subcomands

New subcommands could be registered with the [@there_code_shortcut](api.html#herethere.there.commands.there_code_shortcut) decorator and [click](https://click.palletsprojects.com/en/master/options/) options:


```python
from herethere.there.commands import there_code_shortcut
import click

@there_code_shortcut
@click.option("-n", "--number_to_print", type=int)
def mycommand(code: str, number_to_print):
        return f"print({number_to_print})"

%there mycommand -n 123
```


## Using in the code

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

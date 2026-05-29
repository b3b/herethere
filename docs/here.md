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

# %here: SSH server

## Run server from the Jupyter notebook 

Commands are provided by the *herethere.magic* extension.

```python
%load_ext herethere.magic
```

### %here command
**Start a listener for remote connections.**

Command takes a single optional argument: location of server config.<br>
If argument is not provided, values are loaded from the **here.env** file.

Config values can be overridden by environment variables with same names.

```python
%env HERE_PORT=8023
```

#### here.env example
```
# Hostname or address to listen on.
# Defaults to 127.0.0.1 for local-only access.
# Use 0.0.0.0 or a specific interface to accept remote connections.
HERE_HOST=127.0.0.1

# Port number to listen on.
# Defaults to 8022.
HERE_PORT=8022

# Credentials
HERE_USERNAME=admin
HERE_PASSWORD=xxx

# Path to store the generated private key.
# Defaults to ./key.rsa.
HERE_KEY_PATH=./key.rsa

# Path to the root directory for the SFTP session (%there upload/download commands)
# Defaults to the here-server process current directory.
HERE_SFTP_ROOT=.
```


## Run from the command line

```
export HERE_PORT=8023
python -m herethere.here
```

This is the same as the *%here* command: configuration is loaded from here.env and the environment.


## Using it in code

```python
from herethere.here import ServerConfig, start_server
config = ServerConfig.load(prefix="here")
config.port = 8024
server = await start_server(config)
print(server)
```

```python
await server.stop()
```

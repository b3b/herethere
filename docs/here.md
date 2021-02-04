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
**Start a remote connections listener.**

Command takes single optional argument: location of server config.<br>
If argument is not provided, values are loaded from the **here.env** file.

Config values could be overridden by environment variables with same names.

```python
import os
os.environ["HERE_PORT"] = "8022"

%here
```

#### here.env example
```
# Hostname (localhost) or address (127.0.0.1) to listen on.
# Could be empty to listen for all addresses.
HERE_HOST=

# Port number to listen on
HERE_PORT=8023

# Credentials
HERE_USERNAME=admin
HERE_PASSWORD=xxx

# Path to store the generated private key
HERE_KEY_PATH=./key.rsa

# Path to the root directory for the SFTP session (%upload command)
HERE_CHROOT=.
```


## Run from the command line

```
export HERE_PORT=8023
python -m herethere.here
```

Same as the *%here* command, configuration is loaded from the **here.env** file and environment.


## Run from the code

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

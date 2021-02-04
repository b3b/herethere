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

# Running with AIOHTTP

<!-- #region -->
## Run the server
Run the SSH server on aiohttp startup

```python
from aiohttp import web
from herethere.here import ServerConfig, start_server

async def start_server_here(app):
    server = await start_server(
        ServerConfig.load(prefix="here"),
        namespace={"app": app}
    )

app = web.Application()
app.on_startup.append(start_server_here)

if __name__ == '__main__':
    web.run_app(app)
```
<!-- #endregion -->

## Connect to the SSH server from the Jupyter

```python
%load_ext herethere.magic
%connect-there
```

```python
%%there
from aiohttp import web

async def handle(request):
    return web.Response(text="...")

print(f"app instance is available: {app}")
app.router._frozen = False
app.add_routes([web.get('/debug', handle)])
```

```python
!curl http://127.0.0.1:8080/debug
```

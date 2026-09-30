English | [简体中文](README.zh-CN.md)

# inksync

The sync server for the handwriting pad: board storage (`.wbz`), the operation log with broadcast and autosave, and the WebSocket protocol. It mounts on any aiohttp app, so another project's server can store and sync its own boards without the whiteboard app running.

```python
from aiohttp import web
from inksync import BoardStore, Hub, mount

app = web.Application()
mount(app, Hub(BoardStore("data")), path="/ws")
web.run_app(app, port=8848)
```

On the page, point the pad at it with `createInkPad(element, {app, board, transport: {url: "ws://…/ws"}})`, or omit `transport` when the page is served by the same server.

## Installing

```bash
pip install "git+https://github.com/Maimai-l/white-board.git#subdirectory=packages/inksync"
```

Requires Python 3.10 or later and aiohttp 3.9 or later.

## `mount(app, hub, path="/ws", permissions=None, info=None)`

| Argument | Description |
| --- | --- |
| `app` | The aiohttp application. |
| `hub` | A `Hub` over a `BoardStore`. One hub per storage directory. |
| `path` | Route of the WebSocket endpoint. |
| `permissions` | `request -> frozenset` of `manage`, `settings`, `clear`, `export`. Default: all four. Writing never needs a permission. |
| `info` | `request -> dict` sent to clients in `init` / `sync` as `info`. Default: `{"version": …}`. |

`mount` also starts autosave (every 3 s) and saves and closes connections when the app shuts down.

## Modules

| Module | Contents |
| --- | --- |
| `inksync.ws` | Protocol handling (`Session`), `websocket_handler`, `mount`, `detect_role` |
| `inksync.hub` | `Hub` (boards in memory, pinned connections, folders, broadcast, autosave), `BoardRuntime`, `Client` |
| `inksync.store` | `BoardStore`: `index.json`, `.wbz` read and write, read-only detection, backups before unlocking |
| `inksync.models` | Validation of metadata, strokes, masks and underlays |
| `inksync.codec` | Point encoding |

`BoardStore` has two hooks for files an app keeps next to the boards: `_delete_extras(board_id)` and `_import_extras(path, old_id, board_id, meta)`. The whiteboard app uses them for thumbnails and document-board originals (`whiteboard/store.py`).

## References

- Protocol: [docs/protocol.md](../../docs/protocol.md)
- File format: [docs/format.md](../../docs/format.md)
- Front-end pad and transports: [docs/embed.md](../../docs/embed.md)

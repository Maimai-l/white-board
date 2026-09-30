English | [简体中文](README.zh-CN.md)

# inksync

The sync component of the handwriting pad. The server stores boards, syncs operations and controls access by rules; the front-end pad ships inside the package. It mounts on any aiohttp application, so a host project's own server can store and sync boards without running the whiteboard app.

This document is the inksync 2.0 interface reference. Protocol details are in [docs/protocol.md](../../docs/protocol.md), the file format in [docs/format.md](../../docs/format.md), and a complete example in [examples/qb-server](../../examples/qb-server/server.py).

## 1. Parts

| Part | Contents |
| --- | --- |
| Server | The Python package `inksync`: board storage, sync, access rules, Bonjour registration. Mounted on the host's own aiohttp application. |
| Front end | ES modules shipped in the Python package and served by the server. Entry point `inkpad.js`, which exports `createInkPad`. |
| iPad shell | The native app of the whiteboard project. It finds the host's server by service name, opens its page, and hands Apple Pencil samples (about 240 per second) to the pads on the page. Hosts do not modify the shell. |

Installing:

```bash
pip install "inksync[discovery] @ git+https://github.com/Maimai-l/white-board.git@v2.0.0#subdirectory=packages/inksync"
# from a local checkout
pip install ./packages/inksync
```

Requires Python 3.10 or later and aiohttp 3.9 or later. `[discovery]` is needed to register the Bonjour service on systems other than macOS.

## 2. Minimal server

```python
from pathlib import Path
from aiohttp import web
from inksync import DefaultPolicy, FileStorage, Hub, Principal, mount, serve_sdk
from inksync.netinfo import advertise, is_local_request

PORT = 8900
ROOT = Path(__file__).parent


def authenticate(request: web.Request) -> Principal:
    return Principal(
        id=request.cookies.get("qb_user"),          # the host's own identity, may be None
        local=is_local_request(request),
        address=request.remote or "",
    )


class QbPolicy(DefaultPolicy):
    def can_open(self, who, board_id, meta):
        return who.local or (who.id is not None and board_id.startswith(f"u{who.id}-"))

    def can_create(self, who, board_id, spec):
        return self.can_open(who, board_id, None)


app = web.Application()
hub = Hub(FileStorage(ROOT / "data" / "ink"), policy=QbPolicy())
mount(app, hub, path="/ws", authenticate=authenticate)
serve_sdk(app, prefix="/inksync/")
app.router.add_static("/static/", ROOT / "static")
advertise(app, port=PORT, source="qb", path="/")      # set Source to @qb in the iPad shell
web.run_app(app, port=PORT)
```

Page:

```html
<div id="pad" style="height: 70vh"></div>
<script type="module">
  import { createInkPad } from "/inksync/inkpad.js";

  const pad = createInkPad(document.getElementById("pad"), {
    board: "u42-9709-s23-12-q3",
    create: {
      name: "9709 s23 P12 Q3",
      canvas: { mode: "fixed", width: 800, height: 1400 },
      background: { pattern: "blank" },
      layers: [{ src: "/static/q/9709-s23-12-q3.png", x: 0, y: 0, width: 800 }],
      data: { paper: "9709_s23_12", question: 3 },
    },
  });
  pad.on("status", (s) => console.log("sync status", s));
</script>
```

## 3. Server interface

### 3.1 Exported names

```python
from inksync import (
    FileStorage, Hub, Spaces,              # storage, one space, many spaces
    Policy, DefaultPolicy, Principal,      # rules and identity
    mount, serve_sdk,                      # mount the sync endpoint, serve the front end
    __version__,
)
from inksync.netinfo import advertise, is_local_request
```

Other modules and names are internal.

### 3.2 `mount(app, target, path="/ws", authenticate=None, info=None)`

| Argument | Description |
| --- | --- |
| `target` | A `Hub` (one space, whose name is the empty string) or a `Spaces` (many spaces). |
| `authenticate` | `request -> Principal`. Called once when a connection is established. Default: `id` is None and `local` follows `is_local_request`. |
| `info` | `request -> dict`, sent to the front end with the snapshot (the pad's `info` property). Default: an empty object. |

`mount` starts autosave on startup, closes all connections on shutdown, and waits for saves to finish on cleanup.

### 3.3 `serve_sdk(app, prefix="/inksync/")`

Serves the front-end files under `prefix`; the entry point is `<prefix>inkpad.js`. Every response carries `Cache-Control: no-cache`. The front-end version equals the server version.

### 3.4 `FileStorage(root, convert_meta=None)`

| Argument | Description |
| --- | --- |
| `root` | The storage directory. `boards/<id>.wbz` are board files, `index.sqlite` is the index (can be rebuilt at any time), and `space.json` holds space-level data (`storage.kv`). |
| `convert_meta` | Optional. Called when a 1.x board file is read, with metadata already converted to 2.0 fields; returns the completed metadata. |

### 3.5 `Hub(storage, policy=None, autosave=3.0, idle_unload=120.0)`

The runtime of one space. All methods are called on the event loop thread.

| Method | Description |
| --- | --- |
| `hub.on(event, callback)` | `"created"`, `"saved"`, `"deleted"`: `callback(board_id, meta)`. `"changed"`: `callback(board_id, meta, op)`, once per accepted operation. Callbacks run on the event loop thread and must not block. Returns a function that unsubscribes. |
| `hub.board_meta(board_id)` | The metadata, or None when the board does not exist. |
| `hub.list_boards(offset=0, limit=100, prefix="", order="updated")` | A list of metadata, taken from the in-memory index without reading board files. `order` is `updated`, `created` (newest first), `name` (ascending), or a list of board ids (listed boards first, in list order). `limit=None` means no limit. |
| `hub.count(prefix="")` | The number of boards. |
| `await hub.strokes(board_id)` | A copy of all current strokes in stacking order, in the format of section 5.3. None when the board does not exist. |
| `await hub.create_board(board_id, spec)` | Creates a board from the server; `spec` is the same as the front end's `create`. Raises `ValueError` when the board exists. |
| `await hub.edit_meta(board_id, patch)` | Edits metadata from the server, by the rules of section 5.2; not subject to `Policy` or `protected_data_keys`. Connected front ends receive the new metadata at once. |
| `await hub.delete_board(board_id)` | Deletes a board and returns whether it was deleted. Front ends showing it receive the `deleted` event; later operations on it are rejected (`rejected: "deleted"`) and the board is never written back. |
| `await hub.import_file(path)` | Copies a `.wbz` from outside the storage directory into a new board. Returns `(meta, original metadata, original id)`. |
| `await hub.flush()` | Waits for saves in progress, then saves all changes. |
| `hub.connections()` | A read-only list of current connections, each with `id`, `principal`, `board`, `readonly`, `device`. |
| `await hub.refresh()` | Call after the facts the rules depend on change: recomputes `caps` for every connection and notifies the front ends. |
| `await hub.reauthenticate(authenticate)` | Call after the way identity is determined changes: recomputes each connection's identity from its saved request, returns the connections whose identity changed, then calls `refresh()`. |
| `hub.register(name, handler)` | Registers an extension message; `handler(conn, msg)` is a coroutine function. `await conn.send(msg)` sends a message on that connection. |

Saving runs on a worker thread: the event loop thread only copies the content, and encoding and writing to disk do not block sync. A board with no connections that is saved and unused for `idle_unload` seconds is released from memory and read again when next opened.

### 3.6 `Spaces(factory)`

Used for many spaces. `factory(name) -> Hub | None` is called the first time a space is requested; None means the space does not exist. `Spaces.get(name)` returns a Hub already created; `await Spaces.close(name)` closes a space. A space with no connections for 10 minutes is closed automatically.

### 3.7 `Principal` and `Policy`

```python
@dataclass(frozen=True)
class Principal:
    id: str | None
    local: bool
    address: str
    attrs: Mapping[str, Any] = field(default_factory=dict)
```

`Policy` methods. `DefaultPolicy` implements them all; hosts subclass it and override what they need:

| Method | `DefaultPolicy` |
| --- | --- |
| `can_open(who, board_id, meta) -> bool` | True |
| `can_create(who, board_id, spec) -> bool` | True |
| `can_write(who, meta) -> bool` | True |
| `can_clear(who, meta) -> bool` | True |
| `can_edit_meta(who, meta, patch) -> bool` | `who.local` |
| `can_unlock(who, meta) -> bool` | `who.local` |
| `create_limit(who) -> tuple[int, float] \| None` | None (no limit) for this machine; otherwise `(60, 60.0)`: at most 60 new boards per 60 seconds. Counted per `who.id`, or per `who.address` when the id is None. |
| `validate_data(data) -> dict \| None` | Returns the data unchanged. None means rejected. |
| `protected_data_keys: frozenset[str]` | Empty. The listed `data` keys can be changed only by `hub.edit_meta`. |
| `allow_src(src) -> bool` | Paths that start with `/` and contain no `..`, `\` or `:`, and do not start with `//`. |

`meta` is None before a board is created. The rules are called on every action.

### 3.8 `advertise(app, port, source, path="/", name=None)`

Registers a `_whiteboard._tcp` service on the local network with `source` and `path` in its TXT record. With Source set to `@<source>`, the iPad shell connects only to this service and opens `path`. Call it before `web.run_app`; `port` is the port actually listened on. A failed registration is only logged.

## 4. Front-end interface

### 4.1 `createInkPad(container, options)`

Creates a pad in `container` and returns it. The pad fills the container and follows its size, so the container needs a height.

| Option | Type | Description |
| --- | --- | --- |
| `board` | string | Required. `^[A-Za-z0-9_-]{1,64}$`. |
| `space` | string | The space name, default the empty string. |
| `create` | object | Used to create the board when it does not exist: `{name, canvas, background, layers, data}`, in the format of section 5.1. Without it, a missing board raises the `error` event (`reason: "board"`). Ignored when the board exists. |
| `readonly` | boolean | Open read-only. |
| `tool` | object | Initial tool: `{tool, color, width, eraserMode}`. `tool` is `pen`, `marker`, `highlighter` or `eraser`; `color` is `#rrggbb`; `width` is 0.5 to 96; `eraserMode` is `object` or `pixel`. |
| `fingerDraw` | boolean | Whether fingers write. Default false: only Apple Pencil and the mouse write, fingers pan and zoom. |
| `transport` | see 4.4 | Default: `/ws` on the server that served the page. |
| `storage` | string | Prefix for local storage names, default `inksync:<space>`. Different projects on the same origin should use different prefixes. |
| `report` | string | URL that page errors are reported to (POST JSON). Default: no reporting. |
| `undoLimit` | number | Undo steps, default 200. |
| `initial` | object | Initial content `{meta, strokes}` when `transport` is `"local"`. |

### 4.2 The returned object

| Member | Description |
| --- | --- |
| `open(board, {create, readonly})` | Switches to another board on the same connection and clears undo history. Writing not yet delivered to the previous board is still delivered to that board. Returns a Promise that resolves when the board is loaded; on failure it rejects and raises `error`. |
| `setTool(tool)` | Switches the tool, in the format of the `tool` option. Only the given fields are replaced (for example, `setTool({ tool: "eraser" })` keeps the color and width). |
| `undo()`, `redo()` | Undo and redo this device's changes on the current board. |
| `clear()` | Clears the current board (undoable). |
| `fit()` | A `fixed` canvas shows the whole canvas; other canvases show all ink. |
| `zoom(factor)` | Zooms around the center. |
| `setMeta(patch)` | Edits `name`, `background`, `layers`, `data` by the rules of section 5.2; requires `caps.meta`. |
| `exportPNG({layers = false, scale = 1})` | Returns a Promise of a PNG data URL. Extent: the whole canvas for `fixed`, otherwise the ink bounds plus a margin. With `layers: true` the image layers are included. |
| `snapshot()` | `{meta, strokes}`, a copy of the current content. |
| `load({meta, strokes})` | Replaces the content and clears undo history. Only for `"local"`. |
| `on(event, listener)` | Subscribes to an event; returns a function that unsubscribes. |
| `destroy()` | Writes the local cache, disconnects and removes the pad. |

Read-only properties:

| Property | Description |
| --- | --- |
| `board` | Metadata of the current board. |
| `status` | `"online"`, `"syncing"`, `"offline"` or `"local"`. |
| `tool` | The current tool. |
| `caps` | `{write, clear, meta, unlock}`, booleans. |
| `locked` | null, or why the board is read-only: `{reason, …}` (a damaged file and similar). |
| `shell` | `{active, version, bridge}`: whether the page runs in the iPad shell, and the shell version. |
| `version` | The SDK version. |
| `info` | The object from the server's `info` hook. |

### 4.3 Events

| Event | Payload | When |
| --- | --- | --- |
| `status` | status string | The connection status changes. |
| `history` | `{undo, redo}` | Undo or redo becomes available or unavailable. |
| `meta` | metadata | A board is loaded, or its metadata changes (on this device or another). |
| `change` | `{board}` | The ink of the current board changes (on this device or another). |
| `op` | `{board, op}` | This device sends an operation. |
| `strokestart`, `strokeend` | stroke | A stroke on this device starts or ends. |
| `locked` | `{board, locked, unlock}` | A board becomes read-only or stops being read-only. When `caps.unlock` is true, `unlock()` asks the server to allow editing. |
| `caps` | `caps` | Permissions change. |
| `deleted` | `{board}` | The current board was deleted. |
| `error` | `{reason, detail}` | The server refused: `board` (missing and no `create`), `create` (invalid `create`), `denied` (refused by the rules), `rate` (creating too fast), `space`, `version`. |
| `rejected` | `{board, op, reason}` | An operation already shown on this device was rejected by the server (`denied`, `locked`, `deleted`, `invalid`). The pad then reloads the server's content. |
| `interrupted` | `{count}` | Strokes were repeatedly interrupted by the system. |
| `outdated` | `{sdk, server}` | The server was upgraded while the page still runs the old front-end files. The host decides when to reload the page. |
| `shell` | the `shell` property | The shell handshake completed. |
| `message` | message object | An extension message from the server. |

### 4.4 `transport`

| Value | Behavior |
| --- | --- |
| omitted | WebSocket to `/ws` on the server that served the page. |
| `{url}` | WebSocket to the given address, for example `ws://mac.local:8900/ws`. |
| `"local"` | No server; `status` is `"local"`. Content comes from `initial`, then the local cache, then an empty board; the host saves through the `op` event or `snapshot()`. |

### 4.5 Page behavior

- A page may hold several pads. Each receives only strokes that start inside its own area, and the same holds for samples from the shell.
- A pad intercepts touch gestures only inside its own area; inputs, buttons and scrolling elsewhere on the page work as usual. The shell turns off the system's handwriting text input, so page inputs take keyboard input.
- Writing done offline is kept in IndexedDB and delivered after reconnecting. There is one database per storage prefix, caching the content of at most 200 boards; beyond that the least recently used are removed, except boards with undelivered writing. View positions (zoom and pan) are kept in `localStorage` under `<storage>views`, also for at most 200 boards.
- The SDK installs a single global object on the page, `window.whiteboardShell`, which the shell calls.

## 5. Data format

### 5.1 Metadata

```jsonc
{
  "id": "u42-9709-s23-12-q3",
  "name": "9709 s23 P12 Q3",
  "created": 1790000000.0,                // seconds, maintained by the server
  "updated": 1790000100.0,                // seconds, maintained by the server
  "canvas": {"mode": "fixed", "width": 800, "height": 1400},
  "background": {"pattern": "blank", "paper": "#ffffff"},
  "layers": [{"src": "/static/q/9709-s23-12-q3.png", "x": 0, "y": 0, "width": 800}],
  "data": {"paper": "9709_s23_12", "question": 3}
}
```

| Field | Rules |
| --- | --- |
| `name` | At most 64 characters. |
| `canvas` | `{"mode":"infinite"}` (default); `{"mode":"column","width":W}`: fixed width, unbounded downward; `{"mode":"fixed","width":W,"height":H}`: fixed width and height. W and H are 1 to 100000. The writable range is `0 ≤ x ≤ W`, and `0 ≤ y` (`column`) or `0 ≤ y ≤ H` (`fixed`). Cannot be changed after creation. |
| `background` | `pattern`: `blank`, `grid`, `lines`, `dots`, default `grid`; `paper`: paper color `#rrggbb`. |
| `layers` | At most 1000 items, drawn in order. Each is `{src, x, y, width, height?, z?, sheet?}` in board coordinates (at zoom 1, one unit is one CSS pixel). Without `height`, the image's aspect ratio is used. `z`: `below` (default, under the ink) or `above` (over the ink). `sheet: true` draws the layer as a sheet of paper with a shadow, and the background pattern is not drawn. `src` must pass `Policy.allow_src` and may contain `{w}`, which the front end replaces with one of 640, 1024, 1600, 2400 (by the pixel width needed for display), so the server can cache images per bucket. |
| `data` | A JSON object of at most 16 KB when serialized, checked by `Policy.validate_data`. The server does not interpret it. |

### 5.2 Editing metadata

- Editable: `name`, `background`, `layers`, `data`. Not editable: `id`, `created`, `updated`, `canvas`.
- `data` is merged by key: keys present are replaced, keys whose value is null are deleted, other keys are unchanged.
- `layers` and `background` are replaced as a whole.
- The front end's `setMeta` requires `caps.meta`; a patch touching a key in `protected_data_keys` is rejected as a whole. The server's `hub.edit_meta` is subject to neither.

### 5.3 Strokes

```jsonc
{"id": "k3m9x0a1b2c4-4f2a-17", "tool": "pen", "color": "#1b1b1f", "w": 3.0,
 "p": [120.5, 40.25, 0.03, 121.0, 41.0, 0.04], "n": 42, "dev": "ipad",
 "m": [[5, 120, 40, 180, 40]], "cut": 1}
```

| Field | Description |
| --- | --- |
| `p` | A flat array `[x, y, pressure, …]` in board coordinates; pressure is 0 to 1; at most 20000 points. |
| `tool` | `pen`, `marker`, `highlighter`. |
| `w` | Line width, 0.5 to 96. |
| `n` | Stacking order, drawn from low to high. |
| `m` | Optional, parts removed by the pixel eraser: `[[radius, x0, y0, x1, y1, …], …]`. |
| `cut` | Optional. 1 means the start is a cut, 2 the end, 3 both. |

The stroke outline algorithm is in the whiteboard project's `stroke.js` (front end) and `whiteboard/freehand.py` (Python); server-side rendering can follow them.

## 6. iPad shell

- The user sets Source to `@qb` on the shell's page in the iOS Settings app, or opens `whiteboard-shell://open?source=qb`. The shell then connects only to services whose `source` is `qb` and opens their `path`.
- After each page load the shell requests `GET /ipad/version` to check for shell updates. The qb server need not provide this URL: when the request fails, the shell shows nothing.
- The shell hands Apple Pencil samples to every pad on the page; hosts need no code for it.

## 7. Limits

| Item | Limit |
| --- | --- |
| Board id | `^[A-Za-z0-9_-]{1,64}$` |
| Space name | `^[a-z0-9-]{0,32}$` |
| One WebSocket message | 8 MB |
| Points per stroke | 20000 (the front end splits longer strokes) |
| Strokes per operation | 2000 |
| `data` | 16 KB |
| `layers` | 1000 items |
| Creation rate | Set by `Policy.create_limit` |
| Number of boards | No limit |

## 8. Tests

```bash
pip install ./packages/inksync pytest
cd packages/inksync && python -m pytest tests -q
```

The tests put only `packages/inksync` on the import path, which confirms that inksync does not depend on the whiteboard app.

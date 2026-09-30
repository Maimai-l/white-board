English | [简体中文](embed.zh-CN.md)

# Embedding the Handwriting Pad

Other web apps can embed a handwriting pad that syncs with the Mac. An exercise page, for example, can show a question image and an answer field and give each question its own board. The Mac stores the boards and shows them in the board chooser.

```js
import { createInkPad } from "/sdk/inkpad.js";

const pad = createInkPad(document.querySelector("#answer-pad"), {
  app: "qb",
  board: "qb-9709-s23-12-q3",
  name: "9709 s23 P12 Q3",
  folder: "刷题",
  underlay: { src: "/apps/qb/img/9709-s23-12-q3.png", width: 800 },
});

pad.on("history", ({ undo, redo }) => { /* enable or disable buttons */ });
pad.setTool({ ...pad.tool, tool: "eraser" });
pad.undo();
```

## Installing an app

An app is a folder of static files in the `apps/` folder of the storage directory. The whiteboard server serves it at `/apps/<name>/`.

```
<storage directory>/apps/
  qb/
    index.html
    img/…
```

| Rule | Value |
| --- | --- |
| Folder name | `^[a-z0-9-]{1,32}$`. Other names are ignored. |
| Entry page | `index.html`. A folder without it is not listed as an app. |
| Paths | Only files inside the app's folder are served; `..` and symbolic links that leave the folder return 404. |
| Caching | Every response has `Cache-Control: no-cache`, so the iPad loads changes after a reload. |

`examples/apps/demo/` in this repository is a complete example: a question image, a pad, tool buttons and an answer field. Copy it into `apps/` and open `http://<computer name>.local:8848/apps/demo/` on the iPad.

> **Note**
> Serve the page from the whiteboard server. The page and the pad then share an origin, so no cross-origin setup is needed, and the iPad shell delivers Apple Pencil samples at about 240 per second to the pad, as it does in the whiteboard.

## iPad home

On the Mac, board settings show “iPad 首页” (iPad home) when at least one app is installed. With an app selected, the iPad opens that app instead of the whiteboard, both in the iPad shell and from the Home Screen icon. The Mac window is not affected.

| Address | Result |
| --- | --- |
| `/` on the iPad | Redirects to `/apps/<name>/`. |
| `/?home=whiteboard` | Opens the whiteboard without redirecting. Use this address for a link back to the whiteboard. |
| `/` on the Mac | Opens the whiteboard. |

The setting is stored as `ipad_home` in the configuration file. If the app is removed, the iPad opens the whiteboard again.

## `createInkPad(container, options)`

Creates a pad inside `container` and returns it. The pad fills the container and follows its size, so give the container a height.

| Option | Type | Description |
| --- | --- | --- |
| `app` | string | App name, `^[a-z0-9-]{1,32}$`. Required unless `transport` is `"local"`. |
| `board` | string | Required. Board ID, `^[A-Za-z0-9_-]{1,64}$`. The same ID is the same board on every device. |
| `name` | string | Name of a new board. |
| `kind` | string | `board` (default, infinite board) or `note` (note board) for a new board. |
| `folder` | string | Folder of a new board. Created if missing. |
| `underlay` | object | `{src, width}`: an image under the ink, such as the question. `src` must be under `/apps/`. The image's top-left corner is at the board origin; `width` is in board units and the height follows the image's aspect ratio. |
| `tool` | object | Initial tool: `{tool, color, width, eraserMode}`. |
| `fingerDraw` | boolean | Whether a finger draws. Default: only Apple Pencil draws; fingers pan and zoom. |
| `role` | string | `ipad` or `mac`. Default: detected from the device. |
| `transport` | string, object or function | How the pad syncs; see [Transports](#transports). Default: the server that served the page. |
| `initial` | object | `{meta, strokes}`: initial content when `transport` is `"local"`. |

`name`, `kind`, `folder` and `underlay` apply only when the board is created. The board is created the first time any device opens it; see [Pinned connections](protocol.md#pinned-connections).

With an `underlay`, the pad first opens with the image filling its width. After that, each device remembers its own view of each board.

## Methods

| Method | Description |
| --- | --- |
| `setTool(tool)` | Changes the tool. `tool.tool` is `pen`, `marker`, `highlighter` or `eraser`; `eraserMode` is `object` or `pixel`. |
| `undo()` / `redo()` | Undoes or redoes this device's last change. |
| `clear()` | Clears the board. Boards created by an app can be cleared without the “清空白板” (Clear board) permission. |
| `fit()` | Shows all ink. |
| `zoom(factor)` | Zooms around the center. |
| `exportPNG()` | Returns the ink as a PNG data URL, without the underlay. |
| `on(event, listener)` | Subscribes to an event; returns a function that unsubscribes. |
| `destroy()` | Saves to the local cache, disconnects and removes the pad from the page. |
| `snapshot()` | Returns the current content as `{meta, strokes}`. |
| `load(board)` | Replaces the content with `{meta, strokes}` and clears undo. Intended for `"local"` pads. |

`pad.tool`, `pad.state`, `pad.locked` and `pad.net.status` can be read. Other properties are internal.

## Events

| Event | Detail | When |
| --- | --- | --- |
| `status` | `"online"`, `"syncing"`, `"offline"` or `"local"` | The connection state changes. A `"local"` pad reports `"local"` once. |
| `history` | `{undo, redo}` | Undo or redo becomes available or unavailable. |
| `meta` | Board metadata | The board is loaded or its settings change. |
| `op` | The operation | This device sends an operation (`add`, `remove`, `restore`, `mask`, `clear`, `meta`; see [Operations](protocol.md#operations)). Sent with every transport. |
| `change` | — | The ink changes on this device. |
| `strokestart` / `strokeend` | The stroke | A stroke starts or ends on this device. |
| `locked` | `{board, locked, unlock}` | The board opens read-only, or read-only ends. `unlock()` asks the server to allow editing (needs the “管理白板” (Manage boards) permission). |
| `interrupted` | `{count}` | The system interrupted strokes three times in a row, usually because of Scribble. |
| `deleted` | `{board}` | The board was deleted on the Mac. |
| `error` | `{reason}` | The server refused the connection, for example because of an invalid `board` or `app`. |

## Transports

The `transport` option decides where the pad sends its operations.

| Value | Behavior |
| --- | --- |
| Omitted | WebSocket to `/ws` on the server that served the page. |
| `"local"` | No server. The status is `"local"` and nothing is sent. Content comes from `initial`, else the local cache, else an empty board. Save it from the `op` event or `snapshot()`. |
| `{url}` | WebSocket to the given address, for example `ws://mac.local:8848/ws`. A page served over https cannot connect to a `ws://` address. |
| Function | Custom transport. Called with `{clientId, role, pin, onMessage, onStatus}`; returns an object with the members below. |

A custom transport implements the same members as the built-in WebSocket transport (`net.js`):

| Member | Description |
| --- | --- |
| `connect()` | Starts syncing. Deliver server messages to `onMessage` in the format of [protocol.md](protocol.md), starting with `init` or `sync`, and state changes to `onStatus`. |
| `send(msg)` | Sends a control message such as `unlock`. Returns `false` if it could not be sent. |
| `sendLive(msg)` | Sends live ink. Losing it is harmless. |
| `sendOp(op)` | Sends an operation and returns an ID. Keep it in `outbox` until the server acknowledges it; report the acknowledgement as `onMessage({t: "op", op, mine: true})`. |
| `restoreOutbox(items)` | Puts operations from the previous session back into `outbox` and sends them. |
| `close()` | Stops syncing. |
| `outbox`, `boardId`, `lastSeq`, `epoch`, `status` | State the pad reads and writes. |

## Behavior

- **Offline.** Strokes written offline are kept in IndexedDB and sent after reconnecting, as in the whiteboard. Each board has its own cache and outbox.
- **Several pads.** A page can contain several pads, each with its own board. Each pad takes only the strokes that start inside it, including samples from the iPad shell.
- **Page input.** The pad blocks touch gestures only inside its own area. Text fields, buttons and scrolling elsewhere on the page work normally.
- **Mac view.** Boards created by an app appear in the board chooser, in the given folder. The Mac shows the underlay as well.
- **Permissions.** A device that can only write can create and open boards that an app created. Opening one of the user's own boards requires the “管理白板” (Manage boards) permission.

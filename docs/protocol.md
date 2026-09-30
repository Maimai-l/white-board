English | [简体中文](protocol.zh-CN.md)

# Sync Protocol

Clients and the server synchronize boards over one WebSocket at `/ws` that carries JSON text messages; a few HTTP endpoints serve pages, thumbnails and document boards.

The client draws locally first and then sends the operation. The server numbers, forwards and saves operations and never blocks local drawing.

| Property | Value |
| --- | --- |
| WebSocket path | `/ws` |
| Message format | JSON text frames; each message is an object with a type field `t`. Binary frames and non-object JSON are ignored. |
| Maximum message size | 8 MB (`server.MAX_WS_MESSAGE`) |
| Server heartbeat | WebSocket ping every 20 s |
| Default port | 8848; if taken, the next free port is used for that run |

## Channels

Stroke data travels on two channels: live points while a stroke is being drawn, and operations that change the board.

| Channel | Message | Saved | Purpose |
| --- | --- | --- | --- |
| Live ink | `live` | No | Sample points during a stroke, so other devices see the stroke as it is drawn |
| Operations | `op` | Yes | Finished strokes, erasing, clearing, undo, board settings |

Lost `live` messages need no recovery: the `op` sent when the stroke ends carries the complete point list.

## Connection

### Handshake

The first message on a connection must be `hello`; the server ignores every other message until it arrives.

| `hello` field | Type | Description |
| --- | --- | --- |
| `client` | string | Client ID, `^[A-Za-z0-9_-]{4,64}$`. Otherwise the server assigns a new ID. The web client stores its ID in `localStorage` key `whiteboard.client`. |
| `role` | string | `mac` or `ipad`. Any other value is treated as `mac`. Controls the interface layout only. |
| `board` | string or null | Board ID the client last showed. |
| `since` | integer | Last `seq` the client received. |
| `epoch` | string or null | Last `epoch` the client received. |
| `pin` | object | Optional. Pins the connection to one board; see [Pinned connections](#pinned-connections). |

The server answers with `init` or `sync`; see [Sequence numbers, epochs and reconnection](#sequence-numbers-epochs-and-reconnection). A second `hello` on the same connection is ignored.

### Pinned connections

A connection either follows the current board or is pinned to one board. Without `pin` in `hello`, it follows: when any device switches boards, it switches too. With `pin`, it stays on the named board. Other apps that embed a handwriting pad use pinned connections, for example one board per exercise.

| `pin` field | Type | Description |
| --- | --- | --- |
| `board` | string | Board ID, `^[A-Za-z0-9_-]{1,64}$`. |
| `app` | string | Name of the app, `^[a-z0-9-]{1,32}$`. Recorded in the board's `meta.app` when the board is created. |
| `name` | string | Optional. Name of a new board. |
| `kind` | string | Optional. `board` or `note` for a new board; default `board`. |
| `folder` | string | Optional. Folder of a new board; the folder is created if it does not exist. |

```jsonc
{"t":"hello","role":"ipad","client":"k3m9x0a1b2c4","board":null,"since":0,"epoch":null,
 "pin":{"board":"qb-9709-s23-12-q3","app":"qb","name":"9709 s23 P12 Q3","folder":"刷题"}}
```

| Rule | Behavior |
| --- | --- |
| Board does not exist | The server creates it with the given `app`, `name`, `kind` and `folder`. The current board does not change. |
| Board exists and has `meta.app` | Any device may pin to it. |
| Board exists without `meta.app` (a user's board) | Requires the `manage` permission, as switching boards does. |
| Invalid `pin`, or permission missing | The server sends `{"t":"error","reason":"pin"}` and closes the connection. |
| Operations and `live` | Apply to the pinned board and reach every connection showing that board: other connections pinned to it, and following connections when it is the current board. |
| `switch` and `boards` | Not sent to pinned connections. |
| The pinned board is deleted | The server sends `{"t":"deleted","board":"<id>"}`. Later operations are acknowledged without `op` and not applied. |
| `unlock` | Applies to the pinned board. |

### Client keepalive and reconnection

The web client (`net.js`) detects dead connections itself, because mobile browsers often suspend a socket without closing it.

| Setting | Value |
| --- | --- |
| `ping` interval | 10 s |
| Connection considered dead | No `pong` for 12 s; the client closes the socket and reconnects |
| Reconnect delays | 400, 800, 1500, 3000, 5000, 8000 ms (the last value repeats) |
| Reconnect triggers | Socket closed, browser `online` event, window `focus`, page becomes visible |

### Outbox

Every operation goes into an outbox and stays there until its `ack` arrives.

- `cid` has the form `<client id>-<time base36>-<counter base36>`.
- The outbox is saved in the browser cache (IndexedDB) and restored after a page reload.
- After each `init`, `sync` or `switch`, the client resends every operation still in the outbox, in order.
- After a snapshot, the client applies the pending operations to the local board again.

## Client-to-server messages

| `t` | Fields | Permission | Server response |
| --- | --- | --- | --- |
| `hello` | `client`, `role`, `board`, `since`, `epoch` | — | `init` or `sync` to the sender |
| `op` | `cid`, `op` | Depends on the operation, see [Operations](#operations) | `ack` to the sender; `op` to all other clients |
| `live` | `id`, `phase`, and phase fields | — | Forwarded to all other clients with `src` added |
| `ping` | `ts` | — | `pong` with the same `ts` |
| `sel` | `board` | `manage` | `switch` to all clients |
| `newboard` | `kind`, `folder` (optional) | `manage` | `switch` to all clients |
| `delboard` | `board` | `manage` | `switch` to all clients |
| `rename` | `board`, `name` | `manage` | `boards` to all clients |
| `folder` | `board`, `folder` | `manage` | `boards` to all clients |
| `order` | `ids` | `manage` | `boards` to all clients |
| `newfolder` | `name` | `manage` | `boards` to all clients |
| `delfolder` | `name` | `manage` | `boards` to all clients |
| `renamefolder` | `name`, `to` | `manage` | `boards` to all clients |
| `unlock` | — | `manage` | `switch` to all clients |

A message without the required permission, or one that changes nothing, gets no response. The exception is `op`, which is always acknowledged.

```jsonc
{"t":"hello","client":"k3m9x0a1b2c4","role":"ipad","board":"49b773c7c7c2","since":42,"epoch":"a1b2c3d4e5f6"}
{"t":"op","cid":"k3m9x0a1b2c4-m1x2y3-7","op":{"op":"clear"}}
{"t":"live","id":"c3f1-17","phase":"b","tool":"pen","color":"#1b1b1f","w":3}
{"t":"live","id":"c3f1-17","phase":"m","p":[120.5,40.25,0.03, 121.0,41.0,0.04]}
{"t":"live","id":"c3f1-17","phase":"e"}
{"t":"ping","ts":1730000000000}
{"t":"sel","board":"49b773c7c7c2"}
{"t":"newboard","kind":"note","folder":"数学"}
{"t":"delboard","board":"49b773c7c7c2"}
{"t":"rename","board":"49b773c7c7c2","name":"线性代数"}
{"t":"folder","board":"49b773c7c7c2","folder":"数学"}
{"t":"order","ids":["49b773c7c7c2","0d4be1a2f9c3"]}
{"t":"newfolder","name":"数学"}
{"t":"delfolder","name":"数学"}
{"t":"renamefolder","name":"数学","to":"线性代数"}
{"t":"unlock"}
```

### Live ink

`live` messages describe one stroke in progress, identified by the stroke `id`.

| `phase` | Fields | Meaning |
| --- | --- | --- |
| `b` | `tool`, `color`, `w` | Stroke begins |
| `m` | `p`: `[x, y, pressure, ...]` | New sample points, appended |
| `e` | `p` (optional) | Stroke ends. Receivers keep the live stroke for 5 s or until the matching `add` arrives. |
| `x` | — | Stroke cancelled; receivers remove it |

The server does not validate or store `live` messages.

### Board management

Board management messages change the board list or the current board.

| `t` | Behavior |
| --- | --- |
| `sel` | Saves all boards, then makes `board` the current board. Ignored if the board does not exist or is already current. A read-only board is read from disk again. |
| `newboard` | Creates a board of `kind` `board` or `note` (any other value, including `doc`, creates `board`) and makes it current. If `folder` names an existing folder, the board is placed in it. Document boards are created with `POST /api/doc`. |
| `delboard` | Deletes the board file, its thumbnail and its document original. If no board remains, a new empty board is created. |
| `rename` | Renames any board, not only the current one. Does not change `updated`. |
| `folder` | Moves a board into a folder; an empty string moves it out. A new name is added to the folder list. Does not change `updated`. |
| `order` | `ids` is the order the sender sees in the current view (the top level or one folder). The listed boards swap positions among the slots they already occupy; other boards do not move. Needs at least 2 known IDs. |
| `newfolder` | Creates an empty folder. Ignored if the name is empty or exists. |
| `delfolder` | Removes the folder and moves its boards out of it. No board is deleted. |
| `renamefolder` | Renames the folder and updates `meta.folder` on its boards. Ignored if `to` is empty, equal to `name`, or already exists, or if `name` does not exist. |

Folders are identified by name; see [format.md](format.md#folders).

## Server-to-client messages

| `t` | Recipients | Fields |
| --- | --- | --- |
| `init` | Sender of `hello` | `role`, `client`, `info`, `board`, `strokes`, `seq`, `epoch`, `locked`, `boards`, `folders` |
| `sync` | Sender of `hello` | `role`, `client`, `info`, `board`, `ops`, `seq`, `epoch`, `locked`, `boards`, `folders` |
| `switch` | All following clients | `board`, `strokes`, `seq`, `epoch`, `locked`, `boards`, `folders` |
| `boards` | All following clients | `boards`, `folders`, `board` |
| `op` | Clients showing the same board, except the sender | `op` (with `seq`), `src`, `board` |
| `ack` | Sender of `op` | `cid`, `seq`, `op` (only if accepted) |
| `live` | Clients showing the same board, except the sender | The original `live` fields plus `src` |
| `pong` | Sender of `ping` | `ts` |
| `error` | Sender of `hello` with an invalid `pin` | `reason` = `pin` |
| `deleted` | Clients pinned to a deleted board | `board` |

```jsonc
{"t":"init","role":"ipad","client":"k3m9x0a1b2c4","info":{...},"board":{...},"strokes":[...],
 "seq":42,"epoch":"a1b2c3d4e5f6","locked":null,"boards":[...],"folders":["数学"]}
{"t":"sync","role":"ipad","client":"k3m9x0a1b2c4","info":{...},"board":{...},"ops":[...],
 "seq":45,"epoch":"a1b2c3d4e5f6","locked":null,"boards":[...],"folders":["数学"]}
{"t":"switch","board":{...},"strokes":[...],"seq":0,"epoch":"f6e5d4c3b2a1","locked":null,
 "boards":[...],"folders":["数学"]}
{"t":"boards","boards":[...],"folders":["数学"],"board":{...}}
{"t":"op","op":{"op":"remove","ids":["c3f1-17"],"seq":43},"src":"k3m9x0a1b2c4","board":"49b773c7c7c2"}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-7","seq":43,"op":{"op":"remove","ids":["c3f1-17"],"seq":43}}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-8","seq":43}
{"t":"live","id":"c3f1-17","phase":"m","p":[...],"src":"k3m9x0a1b2c4"}
{"t":"pong","ts":1730000000000}
```

### Snapshot fields

| Field | Type | Description |
| --- | --- | --- |
| `board` | object | `meta` of the current board. See [format.md](format.md#meta-fields). |
| `strokes` | array | All strokes of the current board, ascending `n`, points as flat arrays. |
| `ops` | array | Operations with `seq` greater than `since`, in order. |
| `seq` | integer | Current `seq` of the board. |
| `epoch` | string | Epoch of the board in memory. |
| `locked` | object or null | `null` for a normal board; otherwise the read-only reason. See [Read-only boards](#read-only-boards). |
| `boards` | array | Metadata of all boards, in display order. |
| `folders` | array | Folder list, including empty folders. |
| `role` | string | Role the server recorded for this connection. |
| `client` | string | Client ID the server recorded for this connection. |
| `info` | object | Server information, see below. |

`boards` is sent when only the board list changed, for example after a rename, so clients do not reload strokes.

| `info` field | Description |
| --- | --- |
| `version` | Application version |
| `hostname` | `<name>.local` host name |
| `port` | Listening port |
| `urls` | Candidate addresses: `http://<host>.local:<port>/` and, if known, `http://<LAN IP>:<port>/` |
| `data_dir` | Storage directory |

## Operations

An operation is the `op` object inside an `op` message; every accepted operation changes the current board and receives a `seq`.

| `op` | Fields | Permission | Server behavior | Broadcast as |
| --- | --- | --- | --- | --- |
| `add` | `strokes` | — | Validates each stroke, ignores IDs that already exist, assigns a new `n` to each stroke. At most 2000 strokes per operation. | `add` |
| `restore` | `strokes` | — | Same as `add`, but keeps the `n` given in each stroke. | `add` |
| `remove` | `ids` | — | Removes the listed strokes; unknown IDs are ignored. At most 5000 IDs. | `remove` |
| `mask` | `masks`: `[{id, m}]` | — | Replaces each stroke's mask with `m`; an empty `m` removes the mask. Unknown IDs and unchanged masks are ignored. At most 2000 entries. | `mask`, changed entries only |
| `clear` | — | `clear` | Removes all strokes. Ignored on an empty board. | `clear` |
| `meta` | `meta` | `settings` | Accepts only `background` and `name`. | `meta` with the full resulting `meta` |

An operation that is invalid, lacks permission, or changes nothing is not recorded and gets an `ack` without `op`.

```jsonc
{"op":"add","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                        "p":[120.5,40.25,0.03, 121.0,41.0,0.04],"dev":"ipad"}]}
{"op":"restore","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                            "p":[...],"n":42,"dev":"ipad"}]}
{"op":"remove","ids":["c3f1-17"]}
{"op":"mask","masks":[{"id":"c3f1-17","m":[[5,120,40,180,40]]}]}
{"op":"clear"}
{"op":"meta","meta":{"background":"grid","name":"线性代数"}}
```

Stroke fields and limits: [format.md](format.md#stroke-fields). Mask format: [format.md](format.md#masks-m).

### Operation rules

- **Stacking order.** The server assigns `n` in increasing order; clients draw in ascending `n`. `restore` keeps the original `n`, so undoing an erase restores the previous stacking.
- **Undo.** Undo and redo run on the client. The client applies the change locally at once and sends the equivalent `remove`, `restore` or `mask`. Undo therefore works offline. Undoing `clear` sends `restore`.
- **Duplicates.** `add` and `restore` ignore stroke IDs that already exist and still send `ack`, so resending after a reconnect is safe.
- **Invalid data.** Invalid values are dropped or clamped (see [format.md](format.md#stroke-fields)). One invalid message never closes the connection.
- **Long strokes.** A stroke over 20000 points is dropped as a whole, not truncated. The client splits a longer stroke at commit (`stroke.js` `splitLongStroke`, `MAX_STROKE_POINTS` = 20000): adjacent pieces share one sample point, all ends are round, no `cut` is set, the pieces are sent in one `add` and undone as one step.
- **Full masks.** `mask` carries the complete current mask of each stroke, not a delta. A full mask is correct after reconnection and under any delivery order.
- **Erase batching.** During an erase drag the client collects operations and sends them once per animation frame, in the order `remove`, `restore`, `mask`.

## Acknowledgements

The server answers every `op` message with exactly one `ack` to the sender.

| Case | `ack` |
| --- | --- |
| Accepted | `{"t":"ack","cid":...,"seq":<new seq>,"op":<normalized op with seq>}` |
| Invalid, duplicate, no change, no permission, board read-only | `{"t":"ack","cid":...,"seq":<current seq>}` |
| Server error while handling | `{"t":"ack","cid":...,"seq":<current seq or 0>}` |

On `ack` the client removes `cid` from the outbox and applies the returned `op` (`net.js` `_ack`), so the sender's board matches the server's normalized result.

> **Warning**
> The client skips its own `mask` operations when they return in an `ack` (`inkpad.js` `applyOp`, `context.mine`). The local mask may already have grown since the operation was sent; applying the older full mask would undo the newer erasing. For this reason the server limit `MAX_MASK_SEGMENTS` (1024) must accept every mask the client can produce (`MASK_LIMIT` = 400); `tests/test_models.py` checks this.

## Sequence numbers, epochs and reconnection

Every accepted operation receives the next `seq` of its board, and the server keeps the latest 4000 operations (`hub.OPS_HISTORY`) for catch-up.

The client records the last `seq` and `epoch` it received and sends them in `hello` as `since` and `epoch`.

| Condition | Response |
| --- | --- |
| `board` equals the current board, `epoch` matches, `since` is an integer ≥ 0, and the history covers every operation after `since` | `sync` with the missing operations (empty if none) |
| Any other case: different board, different `epoch`, missing `since`, or history no longer covers `since` | `init` with the full board |

`epoch` is generated each time a board is loaded into server memory. `seq` restarts at 0 after a server restart; without `epoch`, a client with an older, larger `seq` would treat the board as unchanged. A changed `epoch` makes the client receive a full snapshot.

The client updates its last `seq` from `seq` in `init`, `sync` and `switch`, from `op.seq` in `op`, and from `seq` in `ack`.

## Read-only boards

A board whose file was not fully read is sent with a non-null `locked`, and the server rejects all operations on it.

```jsonc
"locked": {"reason": "partial", "dropped": 3}
```

| `reason` | Extra field |
| --- | --- |
| `unreadable` | `detail` |
| `corrupt` | `detail` |
| `partial` | `dropped` |
| `newer` | `version` |

Meaning of each reason: [format.md](format.md#file-version-and-incomplete-reads).

- Every `op` on the board, including `meta`, receives an `ack` without `op` and does not change the board.
- The client stops accepting input and shows a notice once per board.
- `rename` and `folder` still work; they rewrite only `meta` in the file.
- `unlock` applies to the current board and needs `manage`. The server first copies the file to `backups/locked/`; if the copy fails, the board stays read-only and no message is sent. On success the server sends `switch` with `locked` = `null` to all clients. The board ID and `epoch` do not change, so clients keep their view and undo history.

## Error handling

An unexpected error while handling a message is logged and the connection stays open.

If the message was an `op`, the server still sends an `ack`. The client resends unacknowledged operations after every reconnect, so closing the connection on a failing operation would cause an endless cycle of reconnects and resends.

## Permissions and device roles

Permissions decide what a connection or request may do; the role only decides the interface layout.

### Permissions

| Permission | Interface label | Allows |
| --- | --- | --- |
| `manage` | “管理白板” (Manage boards) | `sel`, `newboard`, `delboard`, `rename`, `folder`, `order`, `newfolder`, `delfolder`, `renamefolder`, `unlock`; `GET /api/info`, `GET /api/boards`, `GET`/`POST /api/thumb/{board}`, `POST /api/doc` |
| `settings` | “设置白板” (Board settings) | `meta` operation |
| `clear` | “清空白板” (Clear board) | `clear` operation |
| `export` | “导出白板” (Export board) | `GET /api/export/{board}` |

Writing on the current board (`add`, `restore`, `remove`, `mask`, `live`) needs no permission.

### Granting permissions

Permissions are derived from the TCP peer address, not from `role` or `?role=`, which the client chooses freely.

| Peer | Permissions |
| --- | --- |
| Loopback address, or the Mac's own LAN address | All four |
| Any other device | Only those enabled on the Mac in “白板设置” (Board settings); none by default |
| Address unknown | None |

- The setting is stored in `config.json` as `remote_permissions`.
- A WebSocket connection's permissions are fixed when it opens. A changed setting applies to new connections and page loads.
- The page receives its permission list in `data-perms` on `<html>`. The client uses it only to hide entries; the server enforces permissions.
- Checking for updates and choosing the storage directory use the local pywebview interface and are not available to other devices.

### Device roles

The role (`mac` or `ipad`) selects the interface layout.

| Step | Rule |
| --- | --- |
| 1. URL parameter | `?role=mac` or `?role=ipad` has the highest priority. The Mac window (pywebview) uses `?role=mac`. |
| 2. Touch check | `navigator.maxTouchPoints > 1` selects `ipad` (iPadOS Safari reports a Mac user agent by default). |
| 3. Server hint | The server writes `data-role` into the page: `ipad` if the User-Agent contains `iPad`, `iPhone` or `iPod`, otherwise `mac`. |

## HTTP endpoints

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/` | — | Application page with `data-role`, `data-perms` and `data-build` |
| GET | `/ws` | — | WebSocket |
| GET | `/static/...` | — | Front-end files, `Cache-Control: no-cache` |
| GET | `/profile.mobileconfig?host=<host>` | — | iPad configuration profile (Web Clip) |
| GET | `/icon.png` | — | 180 px app icon |
| GET | `/ipad?host=<host>` | — | iPad shell install page, see [ipad-shell.md](ipad-shell.md) |
| GET | `/ipad/version` | — | Version of the iPad shell bundled with this Mac |
| GET | `/ipad/Whiteboard.ipa` | — | iPad shell package; 404 if not bundled |
| GET | `/api/info` | `manage` | `build`, `hostname`, `port`, `urls`, `data_dir`, `current`, `clients` (`id`, `role`, `since`) |
| GET | `/api/boards` | `manage` | `boards`, `folders`, `current` |
| GET | `/api/thumb/{board}` | `manage` | Thumbnail PNG. Without an uploaded thumbnail: the first page of a document board, otherwise a blank PNG. |
| POST | `/api/thumb/{board}` | `manage` | Upload a thumbnail. Body: PNG, at most 512 KB. |
| POST | `/api/debug` | — | Client diagnostics written to the server log. Body: JSON object; the first 20 keys, up to 1000 characters. |
| POST | `/api/recording` | — | Save an input recording to `recordings/`. Body: JSON object with an `events` array, at most 32 MB. See [recording.md](recording.md). |
| POST | `/api/doc?name=<file name>&folder=<folder>` | `manage` | Create a document board from a PDF or image. Body: the file, at most 256 MB. |
| GET | `/api/doc/{board}/{page}?w=<width>` | — | Rendered page image |
| GET | `/api/export/{board}` | `export` | Document board with ink merged into the original |

A missing permission returns `403`.

### Endpoint details

| Endpoint | Details |
| --- | --- |
| `POST /api/thumb/{board}` | `404` for an unknown board; `400` if the body is not PNG. |
| `POST /api/doc` | The file name can also be sent in the `X-Filename` header. `folder` is optional and applies only if the folder exists. The body is read in 64 KB chunks until the end (`StreamReader.read(n)` returns at most `n` bytes). `413` over the limit, `400` for unsupported or unreadable files. Response: `{"board": <meta>}`. The server then sends `switch` to all clients. |
| `GET /api/doc/{board}/{page}` | `w` defaults to 1200 and is clamped to 160–2600. PDF pages and opaque images return JPEG (quality 82); images with transparency return PNG. `ETag` is `"<board>-<page>-<width>"`; a matching `If-None-Match` returns `304`. `Cache-Control: private, max-age=31536000, immutable`, because the original never changes. `404` if the board is not a document board, the original is missing, or the page is out of range. |
| `GET /api/export/{board}` | Document boards only (`404` otherwise). `Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename*=UTF-8''<name>`, `Cache-Control: no-store`. File name: `<original name>-批注<ext>`. |
| `POST /api/recording` | Saved as `recordings/<YYYYmmdd-HHMMSS>[-<name>].json`; `name` keeps letters, digits, `-` and `_`, up to 40 characters. Response: `{"ok": true, "path": ..., "events": <count>}`. |

- At most 2 pages render at the same time (`server.RENDER_LIMIT`). All pdfium calls are serialized by one lock (`docs._PDFIUM_LOCK`), because pdfium is not thread-safe and concurrent calls crash the process.
- The page triggers downloads with an `<a download>` link, never by assigning `location.href`. In the iPad shell (WKWebView), assigning `location.href` navigates away and closes the WebSocket; see [ipad-shell.md](ipad-shell.md).

## mDNS and Bonjour

iPads reach the Mac at `<host name>.local`; the application can also register two DNS-SD services.

| Service | Purpose | Default | Implementation | Command-line options |
| --- | --- | --- | --- | --- |
| `.local` host name | Address of the Mac | Always | Published by macOS mDNSResponder; the application does not take part | — |
| `_http._tcp` (name “Whiteboard”, TXT `path=/`) | Generic service discovery | Off on macOS, on elsewhere | zeroconf asynchronous API (`netinfo.MDNSAdvertiser`) | `--mdns` forces on, `--no-mdns` forces off |
| `_whiteboard._tcp` | iPad shell finds the Mac | On on macOS, off elsewhere | macOS: `DNSServiceRegister` through the system mDNSResponder; elsewhere: zeroconf (`netinfo.BonjourService`) | `--no-bonjour` turns off |

`_whiteboard._tcp` TXT record:

| Key | Value |
| --- | --- |
| `host` | `<host name>.local` |
| `port` | Actual listening port |
| `version` | Application version |
| `name` | Computer name |

Registration rules:

- Registration runs as a background task after the HTTP server is ready, with a 5 s timeout (`netinfo.REGISTER_TIMEOUT`). Any failure is logged and does not affect the server.
- zeroconf must be used through its asynchronous API. The synchronous API blocks the asyncio event loop, raises `EventLoopBlocked` and delays server startup until it times out.
- zeroconf is not used on macOS, because it would start a second mDNS responder beside the system one.

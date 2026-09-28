# Whiteboard

A shared handwriting whiteboard for a Mac and an iPad on the same local network.
Run the app on the Mac, open the icon on the iPad home screen, and both screens
write on the same board with every stroke appearing on the other in real time.

Everything stays on the LAN. There is no account, no cloud service and no
outbound connection other than the update check.

```
┌──────────── Mac ────────────┐            ┌─────── iPad ───────┐
│ pywebview window (role=mac) │            │ Home screen icon   │
│   boards / background       │  WebSocket │   Full-screen page │
│   zoom / export PNG         │◀──────────▶│   Apple Pencil     │
│ aiohttp server + mDNS       │    LAN     │                    │
│ boards stored as vectors    │            │ IndexedDB cache    │
└─────────────────────────────┘            └────────────────────┘
```

Chinese version of this document: [README.zh-CN.md](README.zh-CN.md).
The design notes under `docs/` are in Chinese.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installing on the Mac](#installing-on-the-mac)
- [Connecting an iPad](#connecting-an-ipad)
- [Native iPad shell (optional)](#native-ipad-shell-optional)
- [Writing](#writing)
- [Boards, folders and documents](#boards-folders-and-documents)
- [Permissions for other devices](#permissions-for-other-devices)
- [Stored data](#stored-data)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Non-goals](#non-goals)

## Features

- **Real-time sync over the LAN.** One Mac serves the page and stores the
  boards; any device on the network opens it in a browser and writes.
- **Vector ink.** Strokes are stored as points, not images — a 500-point stroke
  is usually under 2 KB. Outlines come from
  [perfect-freehand](https://github.com/steveruizok/perfect-freehand).
- **Apple Pencil first.** Pressure and tilt drive the pen width, the palm is
  ignored while the Pencil is down, and fingers pan and zoom unless finger
  drawing is switched on.
- **Two erasers.** One deletes whole strokes; the other cuts a stroke into the
  pieces that survive, still as vectors.
- **Three kinds of board**, including annotating a PDF or an image and
  exporting it with the ink merged back in.
- **Works while disconnected.** Strokes queue locally (and in IndexedDB) and are
  replayed in order once the connection returns.
- **Everything but writing is opt-in.** Any device on the network can draw;
  managing boards, changing settings, clearing and exporting are each granted
  separately from the Mac.

## Requirements

- A Mac for the server. The packaged app declares macOS 11 as its minimum and
  is published for Apple silicon only; see [Building](#building) for Intel.
- An iPad with Safari. Apple Pencil is optional but is what the input layer is
  tuned for.
- Both devices on the same network, with mDNS/Bonjour not blocked by the router.
- From source: Python 3.10 or later (releases are built with 3.12).

## Installing on the Mac

Download the archive for your architecture from the repository's Releases page
(`arm64` for Apple silicon, `x86_64` for Intel), unpack it, and drag
**Whiteboard.app** into `/Applications`.

The first launch raises two system prompts, both one-off:

- **"Cannot verify developer."** The app is ad-hoc signed. Right-click the app,
  choose **Open**, then **Open** again; afterwards double-clicking works.
- **"Whiteboard would like to find and connect to devices on your local
  network."** This must be allowed, or the iPad cannot reach the Mac. (Required
  since macOS 15; the app ships the usage description.)

### Updates

The app checks GitHub Releases once at startup and offers three choices when a
newer version exists: **skip this version** (remembered, newer ones still
prompt), **install on quit** (downloaded in the background, swapped in silently
when you close the window), or **install and restart now**.

"Download updates automatically" is **off** by default, so the check only
fetches version information. Turning it on pre-downloads the package at startup;
a manual check never downloads.

The downloaded package is unpacked with `ditto` and verified before it replaces
anything: a `.app` contains hundreds of symlinks and executable files, and
dropping any of them makes macOS refuse to open the bundle. The executable and
the code signature are checked before the swap and again after it; if the check
fails, the previous version is put back and reopened.

The About panel (关于, last icon in the top-right corner) shows the version and
links to the changelog and the log file, with 检查更新 (check for updates) at the
bottom. The result
is written above the button, including the version when already current and the
reason when the check fails.

### Running from source

```bash
git clone https://github.com/Maimai-l/white-board.git
cd white-board
```

On a Mac, double-click **start-whiteboard.command**: the first run creates the
environment and installs the dependencies, and later runs open the window
directly. **update.command** runs `git pull`.

macOS may refuse to open the scripts ("unidentified developer"); right-click →
**Open** → **Open**, or run `xattr -dr com.apple.quarantine <this folder>` once.

By hand:

```bash
pip install -r requirements.txt
python run.py                 # opens the Mac window and serves on the LAN
```

| Option | Effect |
| --- | --- |
| `--headless` | Serve only, no window (reach it from a browser) |
| `--port 9000` | Change the port (taken ports roll over to the next one) |
| `--data-dir DIR` | Change where boards are stored |
| `--mdns` | Also register an `_http._tcp` service (macOS leaves this to the system) |
| `--no-bonjour` | Do not register the `_whiteboard._tcp` service used by the iPad shell |
| `--version` | Print the version |

The default port is 8848. The **iPad** button in the window's top-right corner
shows the address the iPad should open, of the form
`http://your-mac-name.local:8848/`.

### Building

```bash
git tag v1.1.0 && git push origin v1.1.0     # builds and publishes a Release
```

The "打包 macOS 应用" workflow can also be run manually from the Actions page,
which produces build artifacts without publishing a Release. Locally:

```bash
pip install pyinstaller
python packaging/make_icns.py packaging/whiteboard.icns
WHITEBOARD_VERSION=1.1.0 pyinstaller --noconfirm packaging/whiteboard.spec
```

Application data stays in `~/Library/Application Support/Whiteboard` and is
shared with source runs, so switching between them keeps the boards. The
packaged app logs to `~/Library/Logs/Whiteboard.log`, as it has no terminal.

## Connecting an iPad

1. In the Mac window, click the iPad icon in the top-right corner (连接 iPad),
   then the download button on the card to get `whiteboard.mobileconfig`. Safari on the iPad
   can also fetch it directly from
   `http://your-mac-name.local:8848/profile.mobileconfig`.
2. On the iPad, open the profile → **Settings** → **Profile Downloaded** →
   **Install**. The profile is unsigned, so iPadOS says so; confirm.
3. A **Whiteboard** icon appears on the home screen and opens the board full
   screen, without Safari's address bar.

The profile refers to the `.local` mDNS host name, so it survives the Mac
changing its IP address. The name is published by the system's own Bonjour
responder; `--mdns` only adds an extra `_http._tcp` service for discovery tools
and is harmless if it fails.

## Native iPad shell (optional)

Safari hands the page about 60 new Pencil positions per second, with integer
coordinates. A native app receives about 240, with fractional coordinates and
predicted samples. The shell is a small native app wrapping a `WKWebView`: it
loads the same page from the Mac and forwards the UIKit Pencil samples to it.
All whiteboard logic stays in the page, so updating the Mac updates the shell's
behaviour the next time it opens. Design and acceptance criteria:
[docs/ipad-shell.md](docs/ipad-shell.md).

The shell is installed through TrollStore, so the iPad must run a version
TrollStore supports (iPadOS 14.0 beta 2 through 16.6.1, 16.7 RC, 17.0). Without
TrollStore, keep using the profile above; the features are the same.

1. The 连接 iPad card in the Mac window shows the install page address,
   `http://your-mac-name.local:8848/ipad`. Open it in Safari on the iPad.
2. Tap **安装白板外壳** to install through TrollStore (enable URL schemes in
   TrollStore's settings first).
3. Open the shell. With one Mac on the network it connects directly; with
   several it lists them; if the router blocks Bonjour, return to the install
   page and tap **打开外壳**.

When the Mac is updated, the shell offers to update itself on the next launch
and hands the package to TrollStore. To connect to a different Mac, open the
board settings (白板设置) and use 换一台 Mac, or turn on 重新查找 Mac in the iOS
Settings app under Whiteboard. Either way the shell lists what it finds, even if
that is a single Mac, so you can confirm which one to join.

The Mac registers `_whiteboard._tcp` through the system mDNS responder, which is
how the shell finds it; `--no-bonjour` turns that off, leaving only the install
page as a way in.

Exports on the iPad are caught by the shell as downloads and handed to the
system share sheet, so they can be saved to Files or sent on.

## Writing

### iPad

The default toolbar is the PencilKit tool picker. Managing boards, board
settings and export sit in the tinted group in the top-right corner; clearing
the board is under the picker's ⋯ menu; the connection dot in the top-left
corner is outside the toolbar (triple-tap it for the diagnostics panel).

- **Only the Apple Pencil draws by default.** Fingers pan and zoom, and a palm
  resting on the screen leaves nothing behind.
- Without a Pencil, switch on finger drawing (hand icon): one finger draws, two
  pan and zoom, and the stroke just started is withdrawn when a second finger
  lands. The choice is remembered on the device.
- The pen varies its width with pressure and tilt; the marker and highlighter
  are uniform, and the highlighter is translucent. The pressure curve is fitted
  to the range an iPad actually reports (a whole line of handwriting lands
  between 0 and 0.13); see [docs/format.md](docs/format.md).
- **Palm rejection** covers the whole time the Pencil is down plus 0.5 s after
  it lifts. Movement caused by a palm that touched down first is undone the
  moment the Pencil lands, so the view does not jump.
- The page suppresses Safari's selection, lookup and long-press gestures, which
  otherwise steal a stroke when you write quickly.

The picker is the implementation from
[static/vendor](whiteboard/web/static/vendor/README.md), with these additions:

- Each of the four tools keeps its own colour and width.
- Tapping the tool in use opens the width panel; the rainbow swatch opens the
  full colour picker.
- Dragging the grip docks the picker to **any of the four edges** (the original
  offers three; the top edge reuses the bottom layout with panels opening
  downwards). Both the drag and the release were rewritten to match iPadOS:
  a dwell of 250 ms inside an edge zone turns the pill into a bar, a flick is
  carried past the release point by inertia, and animations can be interrupted
  at any point. The tuning constants live in `TUNING` at the top of
  `static/js/pkpicker.js`.
- Dropped in a corner it shrinks to a circle showing the current tool, and
  expands again when the Pencil or cursor comes near.
- Undo and redo are wired to the board's own history stack.
- ⋯ holds auto-minimise, finger drawing and clear board.

On touch devices the picker is the only toolbar; there is no way back to the
plain one. The canvas, sync, zoom, notes and document boards are the
whiteboard's own — the vendor canvas, ruler and lasso are not connected, and
opacity is not wired up (the stroke format has no field for it).

### Erasers

Both erasers are selected in the eraser panel and share one setting between the
toolbars. Neither has a manual width.

| Eraser | Behaviour | Width |
| --- | --- | --- |
| Object (default) | Deletes each stroke it touches, whole | Fixed at the pen tip, diameter 6 |
| Pixel | Cuts the swept section out, leaving both ends | Follows the angle of the barrel |

The pixel eraser is at its tip above 25°, at its widest (diameter 45) below 15°,
and interpolated in between (23° → 10, 21° → 20, 19° → 31, 17° → 41). A normal
writing grip is well above 25°, so it stays at the tip until you deliberately
lay the Pencil down. A mouse or finger has no tilt and gets the middle step.

The angle is read from `tiltX`/`tiltY` (Pointer Events Level 2) and converted
with the formula in the specification's appendix; `altitudeAngle` is consulted
only when both are zero. Reading `altitudeAngle` alone does not work: the
specification requires π/2 — perfectly upright — when a device cannot report
tilt, so browsers without support look like a pen that never tilts.

Cut strokes stay vectors: a stroke is replaced by the pieces that survive, so
sync, undo and PDF export need no format change, and the pieces keep the
original stacking order. One drag is one undo step. A stroke wider than the
eraser is cut through rather than nibbled — the whole cross-section goes at
once. Measurements and the reasoning are in [docs/eraser.md](docs/eraser.md).

Erasing costs depend on the area swept, not on how many strokes the board
holds: strokes are indexed on a 256-unit grid, only dirty rectangles are
repainted, and the operations produced during one drag are batched per frame.
On a 3000-stroke board one sweep measured 438 ms → 4.4 ms (object) and
465 ms → 13 ms (pixel); at 6000 strokes, 854 ms → 28 ms.

### Mac

The Mac uses the plain toolbar: pen, marker, highlighter, eraser, the finger
drawing switch, the toolbar position, colour and width, undo, redo and clear
board. It docks to the top or the bottom edge, and the choice is remembered on
the device (the bottom edge is out of reach when you hold an iPad to write, so
that button moves the bar up). Around it are the top-right group (boards, board
settings, export PNG, iPad) and the bottom-right group (zoom in, fit, zoom out).

The interface follows the system controls: frosted glass, the system blue
accent, and a connection dot in the traffic-light colours (green connected,
yellow syncing, red offline). Background blur is switched off while a stroke is
in progress, since the canvas underneath changes every frame.

The iPad tool picker is never frosted: it sits directly over the writing area,
and the per-stroke blur switch would otherwise make it flicker between frosted
and solid several times per character.

- Left button writes; middle button, right button or held space pans; the wheel
  scrolls; ⌘/Ctrl + wheel zooms.
- **Trackpad pinch zoom** is handled on both paths, because the browsers differ:
  Chrome and Firefox send `ctrl + wheel`, WebKit (Safari and the `WKWebView` in
  the packaged app) sends the non-standard `gesturestart`/`gesturechange`/
  `gestureend`. Without the second path, pinching does nothing in the `.app`.
  On the wheel path a single `deltaY` is clamped to 25: a trackpad reports a few
  pixels at a time while a mouse wheel notch reports 100.
- Shortcuts: `⌘Z` undo, `⌘⇧Z` / `⌘Y` redo, `⌘0` fit, `⌘+` / `⌘-` zoom.
- Board settings (白板设置) hold the background (blank, grid, lines, dots), the
  storage directory and the permissions for other devices.
- Deleting a board and clearing a board both ask first, with the confirming
  button in the warning colour.

## Boards, folders and documents

A board's kind is chosen when it is created and does not change afterwards:

| Kind | Extent | Initial view |
| --- | --- | --- |
| Board | Infinite in all four directions | Fit to content on the Mac, 1:1 on the iPad |
| Note | One page wide (1000 units), infinite downwards | Page width, at the top |
| Document (beta) | Fixed, from a PDF or an image | Page width, first page |

Notes do not accept ink outside the paper — dragging at the edge turns the page
instead, and you cannot scroll past the first page. Page turns carry inertia
(above roughly 350 px/s), and zoom snaps when a page nearly fills the width.
Boards have no bounds at all, with zoom limited to 0.05×–8×. The middle button
in the bottom-right corner returns to the content.

### The board chooser

The first icon in the top-right corner (白板) opens the chooser: thumbnails
filling the screen, each marked with its kind, and a **+** tile for a new board
or folder.

- The name and the time of the last stroke sit under each thumbnail. Times are
  relative: today shows the time of day, this year omits the year.
- Click a name to edit it; Enter or clicking elsewhere commits, Escape cancels,
  and emptying it returns to the default name. Boards created from a dropped PDF
  or image are named after the file.
- The search box filters on the name, the default name, the folder and the
  document's original filename. Escape clears the search without closing the
  chooser.
- Any board can be renamed without switching to it; only the list is broadcast,
  not the board itself.

**Folders** are one level deep and appear as cards in the same grid; opening one
shows only the boards inside it.

- To file a board, use the folder icon next to its name and pick an existing
  folder or type a new name (which creates it).
- A folder's name is its identity: renaming it rewrites the name recorded on
  every board inside, and a name already in use is rejected.
- Boards created inside a folder — including dropped PDFs and images — stay in
  it.
- Deleting a folder only removes the folder; its boards move back out.
- Reopening the chooser lands on the level holding the current board.
- Search always covers every board and shows a flat list.

### Writing on a PDF or an image (beta)

Drop a PDF or an image **onto the Mac window**, or pick the document tile in the
new-board dialog, to create a document board: the pages are laid out on the
canvas, ready to be written on, and the iPad follows the switch.

- The original file is not modified, only copied into `docs/` in the storage
  directory. Ink remains vector data in the `.wbz` file.
- Ink cannot leave the document: no strokes outside the pages, and no scrolling
  past the first or last page.
- Page bitmaps are rendered on the Mac and sent to the iPad, per zoom step, for
  the pages currently visible.
- The third icon in the top-right corner exports: a PDF from a PDF, an image
  from an image. **Ink is appended as vectors and the existing content is not
  re-encoded**, so the file grows by roughly the size of the ink — a few hundred
  bytes per stroke, a few hundred KB for a heavily annotated handout. On the
  iPad the shell catches the file and opens the system share sheet.
- PDF and png/jpg/gif/bmp/webp/tiff are supported; encrypted PDFs are not.
- Ink in the gap between two pages is assigned to the nearer page on export, and
  anything outside the page is clipped.

## Permissions for other devices

Anyone on the network can open the address and write — that is the point. Every
other action has to be granted, one at a time, from the board settings (白板设置)
on the Mac. Nothing is granted by default.

| Permission | Allows |
| --- | --- |
| Manage boards | Switch, create, delete, rename, file into folders, create from a dropped PDF |
| Board settings | Background texture |
| Clear board | Wipe a whole board at once |
| Export board | Take a whole board, original file included |

- The decision is made from the TCP peer address (loopback, or one of this
  machine's own LAN addresses), not from the User-Agent or the `role` in the
  WebSocket handshake — a client can put anything it likes in those. A
  WebSocket's permissions are fixed when the connection is established.
- The server enforces them: `/api/info`, `/api/boards`, `/api/thumb` and
  uploads to `/api/doc` need *manage boards*, `/api/export` needs *export
  board*, background changes need *board settings*, and clearing needs *clear
  board*. What writing needs — the page, the WebSocket, page bitmaps, the
  profile, diagnostics — is always allowed.
- The page carries a `data-perms` attribute and draws each entry only where the
  permission allows it: on the Mac in the top-right group, on a touch device in
  the tinted group in the same corner, and clearing the board in the tool
  picker's ⋯ menu. That is cosmetic — the enforcement is on the server.
- **Update checks and choosing the storage directory are outside this system.**
  They go through the local pywebview bridge, which other devices cannot reach,
  so About and iPad appear only in that window.

## Stored data

- Default directory: `~/Library/Application Support/Whiteboard/boards-data`,
  changeable in the interface.
- `boards/<id>.wbz` — one file per board. Points are quantised, delta-coded and
  varint-packed, then the whole file is zlib-compressed; a 500-point stroke is
  usually under 2 KB. Format: [docs/format.md](docs/format.md).
- `index.json` — the board list and the folder names. Deleting it rebuilds from
  the `.wbz` files; empty folders cannot be recovered that way, since nothing
  refers to them, while folders holding boards can.
- `thumbs/<id>.png` — chooser thumbnails only. Document boards have none; their
  first page is used instead.
- `docs/<id>.<ext>` — the document board's original file, read-only, deleted
  with the board.
- The server saves changes every 3 seconds, and once more when the window closes
  or the process is interrupted.

### Disconnection and restarts

- Strokes drawn while offline queue locally and in IndexedDB, so a page reload
  does not lose them, and are replayed in order on reconnect. The server
  deduplicates by stroke id.
- A reconnecting client sends its `epoch` and sequence number. A restarted
  server has a new `epoch` and replies with the whole board, so the iPad is
  never left showing stale or blank content.
- Resizing or rotating repaints everything; panning and zooming repaint only the
  strokes in view.
- PNG export and thumbnails frame the content with a margin; an empty board
  exports one screen of blank paper.

## Troubleshooting

Triple-tap the connection dot in the top-left corner (or append `?debug=1` to
the address) to open the diagnostics panel: frame rate and longest frame gap,
render time, sample rate, strokes started and strokes interrupted by the system,
event intervals and disk write times.

While diagnostics are open, stutter reports are also sent to the Mac's terminal;
page errors are sent whether or not they are open. So when the iPad stutters,
read the `[诊断]` lines in the Mac terminal:

- long *最长帧*, short *事件间隔* → something is blocking the main thread;
- normal *最长帧*, long *事件间隔* or a rising *中断* → a system gesture is
  taking the events.

A window in the background stops `requestAnimationFrame` entirely; those frame
gaps are excluded from the reports.

### Loupe on the iPad, or strokes disappearing

Everything the page can do is done: selection and long-press menus are
suppressed, touch events are cancelled in the capture phase (the same fix as
[excalidraw#4705](https://github.com/excalidraw/excalidraw/pull/4705)), and the
selection is cleared when a stroke starts.

One source of interference is out of reach of any page: **Scribble**. It watches
the Apple Pencil at the system level and takes the ink when it decides you are
writing text, which looks like a stroke vanishing or a selection UI appearing —
lifting and landing again quickly is the easiest way to trigger it.

    Settings → Apple Pencil → Scribble → off

After three consecutive interruptions the app raises a notice explaining this.
**Settings → Accessibility → Zoom**, triggered by a three-finger double tap, has
the same effect and is equally out of reach.

## Development

```
whiteboard/
  server.py     aiohttp routes and the WebSocket protocol
  hub.py        operation log, broadcast, autosave
  store.py      board persistence (zlib + compact point encoding)
  codec.py      quantisation, delta coding and varint packing for points
  models.py     data models and validation of anything from the network
  profile.py    .mobileconfig and icon generation (PNG written in pure Python)
  netinfo.py    .local host name, LAN addresses, mDNS, Bonjour for the shell
  ipadshell.py  the shell's install page, version endpoint and IPA download
  runner.py     runs the server in a background thread
  app.py        pywebview window and native file dialogs
  web/          front end (native ES modules, no build step)
    static/js/  stroke geometry, rendering, input, networking, cache, interface
ipad/           the iPad shell (Swift, project generated by XcodeGen)
```

Further reading (in Chinese): the wire protocol in
[docs/protocol.md](docs/protocol.md), the file format in
[docs/format.md](docs/format.md), how the pixel eraser's numbers were measured
in [docs/eraser.md](docs/eraser.md), and the design of the native shell in
[docs/ipad-shell.md](docs/ipad-shell.md).

Problems that only a real Pencil can produce — pressure, tilt, coalesced samples
within a frame, the timing around lift-off — can be recorded on the iPad and
replayed on a development machine: triple-tap the connection dot and use
**录制输入** in the bottom-left cell. See [docs/recording.md](docs/recording.md).

```bash
python -m pytest tests -q                                  # everything
python -m pytest tests --ignore=tests/test_browser.py -q    # skip the browser tests
```

The end-to-end tests open two real Chromium pages (a Mac one and an iPad one)
and sync between them, covering drawing, undo, erasing, clearing, Pencil
exclusivity, switching boards, reconnecting with a backlog, folders and PNG
export. They skip themselves if Playwright or Chromium is missing.

## Non-goals

- **No accounts or passwords.** The premise is a home or office network: if you
  can reach it, you can write on it.
- **Two devices.** More than two works as far as the protocol is concerned, but
  is not tested and not promised.
- **No replica of the iPad selection tool**, which would need artwork.
- `http://xxx.local` is not a secure context, so Service Workers are
  unavailable and the iPad cannot open the page while the server is down. That
  is separate from the server restarting while the page is open, which is
  handled.

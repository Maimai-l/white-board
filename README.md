# Whiteboard

A shared handwriting whiteboard for a Mac and an iPad on the same local network.

English | [简体中文](README.zh-CN.md)

## Overview

Whiteboard is a Mac app that serves a handwriting board to the local network. The Mac and an iPad open the same board, and every stroke appears on the other device in real time. All traffic stays on the LAN. There is no account and no cloud service; the only outbound connection is the update check against GitHub Releases.

```
┌──────────── Mac ────────────┐            ┌─────── iPad ───────┐
│ pywebview window (role=mac) │            │ Home screen icon   │
│   boards / background       │  WebSocket │   Full-screen page │
│   zoom / export PNG         │◀──────────▶│   Apple Pencil     │
│ aiohttp server + mDNS       │    LAN     │                    │
│ boards stored as vectors    │            │ IndexedDB cache    │
└─────────────────────────────┘            └────────────────────┘
```

## Features

- **Real-time sync over the LAN.** The Mac serves the page and stores the boards; any device on the network opens the page in a browser and writes.
- **Vector ink.** Strokes are stored as points, not images. A 500-point stroke is usually under 2 KB. Outlines are drawn with [perfect-freehand](https://github.com/steveruizok/perfect-freehand).
- **Apple Pencil input.** Pressure and tilt set the pen width, the palm is ignored while the Pencil is down, and fingers pan and zoom unless finger drawing is on.
- **Two erasers.** The object eraser deletes the stroke it touches; the pixel eraser removes only the swept area and keeps the ink as vectors.
- **Three board kinds**, including document boards for annotating a PDF or an image and exporting it with the ink.
- **Offline writing.** Strokes queue locally and in IndexedDB and are sent in order when the connection returns.
- **Permissions.** Any device on the network can write. Managing boards, changing settings, clearing and exporting are each granted separately on the Mac.

## Requirements

| Item | Requirement |
| --- | --- |
| Server | A Mac with macOS 11 or later. Releases are published for Apple silicon (`arm64`) only; for Intel, see [Building](#building). |
| iPad | Safari. Apple Pencil is optional; the input handling is tuned for it. |
| Network | Both devices on the same network; the router must not block mDNS/Bonjour. |
| Running from source | Python 3.10 or later (releases are built with 3.12). |

## Installation

### Download

1. Download `Whiteboard-<version>-macos-arm64.zip` from the repository's Releases page.
2. Unpack it and drag **Whiteboard.app** into `/Applications`.

The first launch shows two system prompts. Each appears once.

| Prompt | Action |
| --- | --- |
| "Cannot verify developer" | The app is ad-hoc signed. Right-click the app, choose **Open**, then **Open** again. Afterwards, double-clicking works. |
| "Whiteboard would like to find and connect to devices on your local network" | Allow it. Otherwise the iPad cannot reach the Mac. This prompt exists since macOS 15. |

### Updates

The app checks GitHub Releases once at startup. When a newer version exists, a dialog shows the version, the release notes and three choices:

| Choice | Result |
| --- | --- |
| Skip this version | The version is remembered and not offered again. Newer versions are still offered. |
| Install on quit | The package downloads in the background and replaces the app when the window closes. |
| Install and restart | The app is replaced and reopened immediately. |

- “以后自动下载更新” (Download updates automatically), in the dialog's bottom-left corner, is **off** by default. When off, the check fetches only version information, and the download starts when you choose to install; progress appears in the dialog.
- When the switch is on, a newer version found at startup is downloaded in the background before the dialog appears. A manual check never downloads.
- Prereleases (tags with a hyphen, such as `v1.0.0-rc.1`) are not offered: the check reads `releases/latest`.
- The package is unpacked with `ditto`. The executable and the code signature are checked before the replacement and the signature again after it. If a check fails, the previous version is restored and reopened.

The About panel (关于, the last icon in the top-right corner) shows the app icon, name, version and address, links to the changelog and the log file, and has 检查更新 (Check for updates) at the bottom. The result appears above the button: the current version when up to date, or the reason when the check fails (no connection, rate limit, no package for this Mac).

### Running from source

```bash
git clone https://github.com/Maimai-l/white-board.git
cd white-board
```

| Script | Effect |
| --- | --- |
| `start-whiteboard.command` | First run creates the environment and installs the dependencies; later runs open the window directly. |
| `browser-mode.command` | Runs `start-whiteboard.command --headless`: serves only, for access from a browser. |
| `update.command` | Runs `git pull --ff-only`; changed dependencies are installed on the next start. |

> **Note**
> If macOS refuses to open a script ("unidentified developer"), right-click it, choose **Open**, then **Open** again, or run `xattr -dr com.apple.quarantine <repository folder>` once.

To run it by hand:

```bash
pip install -r requirements.txt
python run.py                 # opens the Mac window and serves on the LAN
```

| Option | Effect |
| --- | --- |
| `--headless` | Serve only, without a window. |
| `--port 9000` | Listen on another port (default 8848). A taken port rolls over to the next one, up to 20 attempts. |
| `--data-dir DIR` | Store boards in another directory. |
| `--mdns` | Also register an `_http._tcp` service. On macOS this is left to the system by default. |
| `--no-mdns` | Do not register the `_http._tcp` service. |
| `--no-bonjour` | Do not register the `_whiteboard._tcp` service that the iPad shell uses. |
| `--debug` | Enable debug logging and the developer tools. |
| `--version` | Print the version. |

> **Note**
> `--port` and `--data-dir` apply to one run only and are not written to the configuration. To change the storage directory permanently, choose it in board settings. A port that rolls over because it is taken is also used for that run only; the next launch tries the configured port again.

The iPad button in the window's top-right corner shows the address for the iPad, in the form `http://your-mac-name.local:8848/`.

### Building

Pushing a tag builds and publishes a Release, including the iPad shell IPA of the same version:

```bash
git tag v1.1.0 && git push origin v1.1.0
```

The “打包 macOS 应用” (Build macOS app) workflow can also run manually from the Actions page; it produces build artifacts without publishing a Release. To build locally:

```bash
pip install pyinstaller
python packaging/make_icns.py packaging/whiteboard.icns
WHITEBOARD_VERSION=1.1.0 pyinstaller --noconfirm packaging/whiteboard.spec
```

- A local build includes the iPad shell only if `packaging/ipad/Whiteboard.ipa` exists; otherwise the install page points to the Release download.
- An Intel build needs a local build on an Intel Mac, or a `macos-13` / `x86_64` matrix entry in `.github/workflows/build-macos.yml`.
- Before turning a prerelease into a release, follow [docs/release-checklist.md](docs/release-checklist.md).

The packaged app and source runs share `~/Library/Application Support/Whiteboard`, so switching between them keeps the boards. The packaged app writes its log to `~/Library/Logs/Whiteboard.log`.

## Connecting an iPad

### Home screen profile

1. In the Mac window, click the iPad icon in the top-right corner (连接 iPad), then the download button on the card to get `whiteboard.mobileconfig`. Safari on the iPad can also open `http://your-mac-name.local:8848/profile.mobileconfig` directly.
2. On the iPad, open the profile, then **Settings** → **Profile Downloaded** → **Install**. The profile is unsigned; confirm the installation.
3. A **Whiteboard** icon appears on the home screen. It opens the board full screen, without Safari's address bar.

The profile uses the `.local` mDNS host name, so it keeps working when the Mac's IP address changes. macOS publishes that name through its own Bonjour responder; `--mdns` only adds an `_http._tcp` service for discovery tools, and a failure there does not affect use.

### iPad shell

The iPad shell is an optional native app that wraps a `WKWebView`. It loads the same page from the Mac and passes UIKit Pencil samples to it. All board logic stays in the page, so updating the Mac also updates the shell's behavior the next time it opens.

| Input path | New Pencil positions per second | Coordinates | Predicted samples |
| --- | --- | --- | --- |
| Safari | about 60 | integers | no |
| iPad shell | about 240 | fractional | yes |

The shell installs through TrollStore, so the iPad must run a version TrollStore supports: iPadOS 14.0 beta 2 through 16.6.1, 16.7 RC, or 17.0. Without TrollStore, use the home screen profile; the features are the same.

1. The 连接 iPad card in the Mac window shows the install page address, `http://your-mac-name.local:8848/ipad`. Open it in Safari on the iPad.
2. In TrollStore's settings, enable URL schemes. Then tap **安装白板外壳** (Install shell).
3. Open the shell. With one Mac on the network, it connects directly; with several, it lists them. If the router blocks Bonjour, return to the install page and tap **打开外壳** (Open shell).

- **Updates.** After the Mac is updated, the shell offers to update on its next launch and passes the package to TrollStore.
- **Another Mac.** Use 换一台 Mac (Switch Mac) in board settings, or turn on 重新查找 Mac (Find Mac again) under Whiteboard in the iPadOS Settings app. Either way the shell lists what it finds, even a single Mac, so you can confirm the choice.
- **Discovery.** The Mac registers `_whiteboard._tcp` through the system mDNS responder. `--no-bonjour` turns this off; the install page is then the only way in.
- **Exports.** The shell receives exported files as downloads and opens the system share sheet, so they can be saved to Files or sent.

Design and acceptance criteria: [docs/ipad-shell.md](docs/ipad-shell.md).

## Usage

### Toolbars

The iPad uses the **tool picker**; every other device uses the **classic toolbar**. A device counts as an iPad when its User-Agent contains `iPad`, when it reports `Macintosh` with more than one touch point (iPadOS Safari uses a desktop User-Agent), or when the page runs inside the iPad shell. There is no switch between the two.

| | Tool picker (iPad) | Classic toolbar (other devices) |
| --- | --- | --- |
| Tools | Pen, marker, highlighter, eraser; each keeps its own color and width | Pen, marker, highlighter, eraser |
| Other controls | Undo, redo; ⋯ menu with auto-minimize, finger drawing and clear board | Toolbar position, color, width, undo, redo, clear board; finger drawing on touch devices |
| Position | Docks to any of the four edges, or shrinks to a circle in a corner | Top or bottom edge, remembered on the device; panels open away from the edge |

Around the toolbar:

- **Top-right group:** boards, board settings (白板设置) and export. The Mac window also shows 连接 iPad and 关于. On touch devices the group uses tinted icons. Each entry appears only if the device has the matching permission.
- **Bottom-right group (Mac only):** zoom in, back to content, zoom out.
- **Connection dot (top left):** green connected, yellow syncing, red offline. Triple-tap it for the diagnostics panel.

The tool picker is the implementation in [static/vendor](whiteboard/web/static/vendor/README.md), with these additions:

- Tapping the tool in use opens the width panel. The rainbow swatch opens the full color picker (swatches, spectrum, sliders).
- Dragging the grip docks the picker to any of the four edges; the top edge uses the bottom layout with panels opening downward. An edge becomes the target after a dwell of 250 ms inside its zone. A flick continues by inertia past the release point; animations can be interrupted at any point. The constants are in `TUNING` at the top of `static/js/pkpicker.js`.
- Dropped in a corner, the picker shrinks to a circle showing the current tool and expands when the Pencil or the cursor comes near. A finger has no hover and taps the circle.
- Undo and redo use the board's own history; the buttons follow its state.
- The picker is hidden while the board chooser or the settings sheet is open.
- The vendor canvas, ruler and lasso are not used. Opacity is not available because the stroke format has no field for it.

The interface follows the system controls: frosted glass and the system blue accent. Background blur is turned off while a stroke is in progress. The tool picker is never frosted, because it sits over the writing area.

### Pencil, finger and palm

| Input | Behavior |
| --- | --- |
| Apple Pencil | Draws. By default only the Pencil draws. |
| Finger (default) | One or two fingers pan and zoom. |
| Finger (finger drawing on) | One finger draws; two fingers pan and zoom. A stroke just started is withdrawn when a second finger lands. The setting is remembered on the device. |
| Palm | Ignored while the Pencil is down and for 0.5 s after it lifts. Movement from a palm that landed before the Pencil is undone when the Pencil lands. |

- The pen varies its width with pressure and tilt. The marker and the highlighter have a uniform width; the highlighter is translucent.
- The pressure curve fits the range an iPad reports: a whole line of handwriting falls between 0 and 0.13. See [docs/format.md](docs/format.md).
- The page suppresses Safari's text selection, lookup and long-press gestures on the canvas, which otherwise take over fast strokes.
- Text fields (board names, search, folder names) accept typing normally.

### Erasers

Choose the eraser in the eraser panel; the choice is remembered on the device. Neither eraser has a manual width. Widths are in screen pixels, so erasing is finer when zoomed in.

| Eraser | Behavior | Diameter |
| --- | --- | --- |
| Object (default) | Deletes the stroke it touches. If the pixel eraser has already split a stroke into separate pieces, deletes only the touched piece. | 6, at any angle |
| Pixel | Removes the swept area and keeps the rest. | Set by the Pencil's altitude angle when the Pencil lands |

The pixel eraser's diameter follows `ERASER_CURVE` in `static/js/input-erase.js`, measured from native PencilKit. Values between the points are interpolated linearly.

| Altitude angle | 90° | 80° | 68°–37° | 35° | 32° | 28° | ≤ 25° |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Diameter (screen px) | 6 | 7.5 | 16.5 | 35 | 52 | 75 | 81 |

- The width is fixed when the Pencil lands and does not change during the stroke; the hover cursor still follows the tilt. A normal writing grip is about 50°, which gives 16.5. A mouse or a finger has no tilt and uses the 50° value.
- The angle is computed from `tiltX`/`tiltY` (Pointer Events Level 2) with the formula in the specification's appendix. `altitudeAngle` is used only when both are 0, because browsers without tilt support report π/2 (upright) for it.
- The pixel eraser records the swept area as a mask on the stroke (`m` in the file format). Rendering and PDF export cut the mask out of the outline, so the ink stays vector.
- When one stroke's mask exceeds a size limit even after thinning, the stroke is replaced by the pieces that remain, keeping its stacking order. One drag is one undo step.

Erasing cost depends on the swept area, not on the number of strokes: strokes are indexed on a 256-unit grid, only dirty rectangles are repainted, and the operations of one drag are sent once per frame. Dirty-rectangle repainting matches a full repaint pixel for pixel.

| Board size | Eraser | One sweep, before → after |
| --- | --- | --- |
| 3000 strokes | Object | 438 ms → 4.4 ms |
| 3000 strokes | Pixel | 465 ms → 13 ms |
| 6000 strokes | — | 854 ms → 28 ms |

Measurements and details: [docs/eraser.md](docs/eraser.md), [docs/format.md](docs/format.md).

### Mouse, trackpad and keyboard

| Input | Action |
| --- | --- |
| Left button | Write |
| Middle button, right button, or held Space | Pan |
| Wheel | Scroll |
| ⌘/Ctrl + wheel | Zoom |
| Trackpad pinch | Zoom |
| `⌘Z` | Undo |
| `⌘⇧Z` / `⌘Y` | Redo |
| `⌘0` | Fit |
| `⌘+` / `⌘-` | Zoom in / out |

- Pinch zoom works in both event models: Chrome and Firefox send `ctrl + wheel`; WebKit (Safari and the packaged app's `WKWebView`) sends `gesturestart`/`gesturechange`/`gestureend`. On the wheel path, one event's `deltaY` is clamped to 25, because a trackpad reports a few pixels per event and a mouse wheel notch reports 100.
- Shortcuts and Space-to-pan are ignored while a text field has focus, so `⌘Z` there undoes typing.
- Board settings (白板设置) hold the background (blank, grid, lines, dots), the storage directory and the permissions for other devices.
- Clearing a board (“清空白板？”) and deleting a board (“删除白板？”) ask for confirmation; the confirm button uses the warning color.

### Board kinds

The kind is chosen when a board is created and cannot change.

| Kind | Extent | Initial view |
| --- | --- | --- |
| Infinite board | Infinite in all four directions; zoom 0.05×–8× | Fit to content on the Mac, 1:1 on the iPad |
| Note board | One page wide (1000 units), infinite downward | Page width, at the top |
| Document board (beta) | Fixed, from a PDF or an image | Page width, first page |

- On a note board, ink cannot go outside the page; dragging at the edge turns the page, and the view cannot scroll past the first page.
- Page turns continue by inertia above about 350 px/s. When a page nearly fills the width, zoom snaps to page width on release.
- The middle button in the bottom-right group returns to the content.

### Board chooser

The first icon in the top-right group (白板) opens the board chooser: full-screen thumbnails, each marked with its kind, and a **+** tile that creates a board (choose the kind first) or a folder.

- **Names and dates.** Each thumbnail shows the name and the time of the last stroke. Today shows only the time; this year omits the year.
- **Default names.** Unnamed boards show a gray default name (白板, 笔记, 文档). Boards created from a PDF or an image are named after the file, without the extension.
- **Renaming.** Click a name to edit it. Enter or clicking elsewhere commits, Escape cancels, and an empty name returns to the default. Enter and Escape during IME composition are left to the input method. Any board can be renamed without switching to it; only the list is broadcast.
- **Search.** The search box matches the name, the default name, the folder and the document's original file name. Search covers every board and shows a flat list. Escape clears the search without closing the chooser.
- **Reopening.** The chooser opens at the level that holds the current board.

**Dragging** works the same with a finger, a Pencil, a mouse or a trackpad. The card lifts out of the grid and follows the pointer, and a gap shows where it will land.

| Pointer | Drag starts when |
| --- | --- |
| Mouse, trackpad | The pointer moves a few pixels. |
| Finger, Pencil | The pointer moves sideways (the list scrolls only vertically), or the card is held still for 220 ms. A vertical swipe scrolls. |

| Drop target | Result |
| --- | --- |
| Middle of a folder card | Moves the board into the folder. The folder's edges do not count. |
| Back button or folder bar (inside a folder) | Moves the board out of the folder. |
| Between two boards | Reorders. Other cards move aside only after the pointer rests in one place. |
| Near the top or bottom edge | Scrolls the list. |

- The order is stored in `index.json` on the Mac, so it is the same on every device and survives restarts. Folders are sorted by name and are not reordered by dragging.
- Dragging uses pointer events, not browser drag and drop, which iOS Safari does not implement. Timings and spring curves are at the top of `static/js/dragsort.js`.
- `static/lab/drag.html` is a standalone copy for tuning those values on an iPad. It is available only when running from source; the packaged app excludes `static/lab/`.

**Folders** are one level deep and appear as cards in the same grid. Opening a folder shows only its boards.

- To create a folder, choose the folder tile in the new-board dialog (top level only). The new folder's name field is selected for editing, as in Finder: type a name and press Return, or click elsewhere. Esc keeps the default name 「未命名文件夹」 (Untitled folder); a name already in use shows a message and stays in edit mode.
- To file a board, drag it onto the folder, or use the folder icon next to its name and pick a folder or type a new name, which creates the folder.
- A folder's name is its identity. Renaming a folder renames it on every board inside; a name already in use is rejected.
- Boards created inside a folder stay in that folder. A PDF or image dropped onto the window, or opened from Finder, goes into the folder the board chooser shows; with the chooser closed, it goes into the current board's folder.
- Deleting a folder does not delete its boards; they move out of it.

### Documents (PDF and images)

Drop a PDF or an image onto the Mac window, choose the document tile in the new-board dialog, or open the file with the app from Finder, to create a document board (beta). The pages are laid out on the canvas, and connected devices switch to the new board.

**Opening files from Finder.** The packaged app is listed under “打开方式” (Open With) for PDF, png, jpeg, gif, bmp, webp and tiff files as an alternative app, so it does not replace the default app. `.wbz` files belong to the app and open with a double-click.

| File | Result |
| --- | --- |
| PDF or image | A new document board, as when the file is dropped onto the window. |
| `.wbz` in the current storage directory | Switches to that board. |
| Other `.wbz` (for example from `backups/`) | Imported as a new board; the original file is not changed. A board that cannot be read completely opens read-only. A document board also needs its original in the `docs/` folder of the same storage directory. |

| Item | Detail |
| --- | --- |
| Formats | PDF, png, jpg/jpeg, gif, bmp, webp, tif/tiff |
| Limits | Up to 400 pages; uploads up to 256 MB |
| Not supported | Password-protected PDFs |
| Original file | Copied unchanged into `docs/` in the storage directory; never modified |
| Ink | Vector data in the board's `.wbz` file; cannot be placed outside the pages; the view stops at the first and last page |
| Page images | Rendered on the Mac per zoom step, only for visible pages |

The third icon in the top-right group exports the document: a PDF from a PDF, an image from an image (gif, bmp, webp and tiff export as PNG).

- Ink is appended as vectors and the original content is not re-encoded. The file grows by a few hundred bytes per stroke; a heavily annotated handout grows by a few hundred KB.
- Ink in the gap between two pages is assigned to the nearer page; ink outside a page is clipped.

## Permissions for other devices

Any device that opens the address can write. Every other action must be granted on the Mac in board settings (白板设置), one permission at a time. None is granted by default.

| Permission | Allows | Enforced at |
| --- | --- | --- |
| Manage boards (管理白板) | Switch, create, delete, rename, file into folders, create from a PDF or image | `/api/info`, `/api/boards`, `/api/thumb`, uploads to `/api/doc`, and the matching WebSocket operations |
| Board settings (设置白板) | Change the background | `meta` operations |
| Clear board (清空白板) | Erase a whole board at once | `clear` operations |
| Export board (导出白板) | Download a whole board, including the original file | `/api/export` |

- Permissions are decided from the TCP peer address. Loopback and the Mac's own LAN addresses have every permission. The User-Agent and the `role` in the WebSocket handshake are not used, because a client can set them freely. A WebSocket's permissions are fixed when the connection opens.
- Writing is always allowed: the page, the WebSocket, page images, the profile and diagnostics reports need no permission.
- The page receives the granted permissions in a `data-perms` attribute and shows only the allowed entries. This only affects the display; the server enforces the permissions.
- Update checks and the storage directory choice use the local pywebview bridge, which other devices cannot reach. 关于 and 连接 iPad therefore appear only in the Mac window.

## Data

### Storage layout

The default storage directory is `~/Library/Application Support/Whiteboard/boards-data`. Change it in board settings.

```
boards-data/
  boards/<id>.wbz        one file per board
  index.json             board list, order and folder names
  thumbs/<id>.png        chooser thumbnails
  docs/<id>.<ext>        original file of a document board
  backups/upgrade/       copies made before a new version first runs
  backups/locked/        copies made before editing a read-only board
```

| Path | Details |
| --- | --- |
| `boards/<id>.wbz` | Points are quantized, delta-coded and varint-packed; the file is zlib-compressed. Format: [docs/format.md](docs/format.md). |
| `index.json` | Rebuilt from the `.wbz` files if deleted. Folders that contain boards are recovered; empty folders are not. |
| `thumbs/<id>.png` | Uploaded by the Mac. Document boards have none; their first page is used. |
| `docs/<id>.<ext>` | Read-only; deleted together with the board. |

The server saves changes every 3 seconds, and once more when the window closes or the process is interrupted (Ctrl+C).

### Read-only boards

A board whose file cannot be read completely (damaged, partly unreadable, or written by a newer version) opens as a read-only board.

- The readable content is shown, and the file is never overwritten. The warning appears once per board each time it opens.
- A device with the manage boards permission can choose 仍然编辑（先备份原文件） (Edit anyway, back up first) after a warning. The original file is first copied to `backups/locked/`.

Details: [docs/format.md](docs/format.md).

### Backups

On the first launch after a version change, `boards/` and `index.json` are copied to `backups/upgrade/<time>_<old version>_to_<new version>/` before anything else is touched. The latest 5 copies are kept.

To check your boards before upgrading, run `python tools/check_boards.py`.

## Offline and reconnection

- Strokes drawn while offline queue locally and in IndexedDB, so a page reload does not lose them. They are sent in order on reconnect, and the server deduplicates by stroke id.
- A reconnecting client sends its epoch and sequence number. A restarted server has a new epoch and sends the whole board, so the iPad never keeps stale or blank content.
- Resizing or rotating repaints everything; panning and zooming repaint only the strokes in view.
- PNG export and thumbnails frame the content with a margin. An empty board exports one screen of blank page.

> **Note**
> `http://*.local` is not a secure context, so Service Workers are unavailable and the iPad cannot open the page while the server is not running. A server restart while the page is open is handled.

## Troubleshooting

### Diagnostics panel

Triple-tap the connection dot, or add `?debug=1` to the address, to open the diagnostics panel. It shows frame rate and longest frame gap, render time, sample rate, strokes started, strokes interrupted by the system, event intervals, disk write times, and the Pencil's raw tilt and altitude readings with the angle in use.

While the panel is open, stutter reports are also sent to the Mac's terminal. Page errors are sent in any case. Read the `[诊断]` lines in the Mac terminal or log:

| Pattern | Cause |
| --- | --- |
| Long 最长帧 (longest frame), short 事件间隔 (event interval) | Something blocks the main thread. |
| Normal 最长帧, long 事件间隔 or rising 中断 (interruptions) | A system gesture is taking the events. |

A page in the background pauses `requestAnimationFrame`; those gaps are excluded from the reports.

### Loupe appears or strokes disappear

The page already suppresses selection and long-press menus, cancels touch events in the capture phase (as in [excalidraw#4705](https://github.com/excalidraw/excalidraw/pull/4705)) and clears the selection when a stroke starts.

**Scribble** works at the system level and is outside the page's control. It takes Pencil input when it detects handwriting, which looks like a vanished stroke or a selection UI. Lifting and landing the Pencil again quickly triggers it most often. Turn it off:

```
Settings → Apple Pencil → Scribble → off
```

After three consecutive interruptions, the page shows a notice about Scribble. **Settings → Accessibility → Zoom**, triggered by a three-finger double tap, has the same effect and is also outside the page's control.

## Development

### Source tree

```
whiteboard/
  server.py     aiohttp routes and the WebSocket protocol
  hub.py        operation log, broadcast, autosave, read-only boards
  store.py      board persistence (zlib + compact point encoding)
  codec.py      quantization, delta coding and varint packing for points
  models.py     data models and validation of network input
  config.py     settings file; command-line values that apply to one run only
  backup.py     copies the boards before a new version first touches them
  docs.py       document boards: reading, rendering and exporting PDFs / images
  inkpdf.py     ink as PDF vector paths
  freehand.py   Python port of perfect-freehand, for export
  profile.py    .mobileconfig and icon generation (PNG written in pure Python)
  netinfo.py    .local host name, LAN addresses, mDNS, Bonjour for the shell
  ipadshell.py  the shell's install page, version endpoint and IPA download
  updater.py    update check, download, verification and replacement
  resources.py  paths and logging for source and packaged runs
  runner.py     runs the server in a background thread
  app.py        pywebview window and native file dialogs
  web/          front end (native ES modules, no build step)
    static/js/
      inkpad.js, app-eraser.js      handwriting pad: drawing, erasing, undo,
                                    sync, cache, view; no interface
      app.js                        whiteboard app on top of the pad: chooser,
                                    settings, import / export, updates
      input.js, input-erase.js,     pointer routing and drawing; the eraser;
        input-gesture.js,           pan / zoom / momentum; fallback for the
        shell-fallback.js           0.9.43 shell
      ui.js, ui-*.js                toolbar and tool picker; board chooser,
                                    dialogs, settings
      stroke.js, boardstate.js,     geometry, ordering and spatial index,
        renderer.js, net.js, ...    rendering, sync, cache, export
    static/lab/                     tuning pages (source runs only)
ipad/           the iPad shell (Swift, project generated by XcodeGen)
tools/          check_boards.py (check boards before upgrading) and research scripts
```

### Documentation

| Document | Content |
| --- | --- |
| [docs/protocol.md](docs/protocol.md) | WebSocket protocol |
| [docs/format.md](docs/format.md) | File format |
| [docs/eraser.md](docs/eraser.md) | Pixel eraser measurements |
| [docs/ipad-shell.md](docs/ipad-shell.md) | iPad shell design |
| [docs/recording.md](docs/recording.md) | Input recording |
| [docs/release-checklist.md](docs/release-checklist.md) | Release checklist |

Input recording captures problems that need a real Pencil (pressure, tilt, coalesced samples within a frame, timing around lift-off) on the iPad for replay on a development machine. Open the diagnostics panel and use 录制输入 (Record input) in the bottom-left cell.

### Tests

```bash
python -m pytest tests -q                                   # everything
python -m pytest tests --ignore=tests/test_browser.py -q    # skip the browser tests
node --test tests/js/*.test.mjs                             # front-end pure functions
WB_BROWSER=webkit python -m pytest tests/test_browser.py    # end-to-end on WebKit
```

- The end-to-end tests open two browser pages (the Mac and a touch device) and sync between them. They cover drawing, undo, erasing, clearing, Pencil exclusivity, switching boards, reconnecting with a backlog, folders, read-only boards and PNG export. They skip themselves if Playwright or the browser is missing.
- `tests/test_compat.py` checks that board files written by earlier versions still read back identically. Do not regenerate the fixture in `tests/fixtures/compat/` with newer code.

### CI

| Workflow | Runs |
| --- | --- |
| `tests.yml` | Python tests with Chromium, front-end unit tests with Node, a smoke subset on WebKit |
| `build-macos.yml` | Builds the iPad shell and the macOS app on `v*` tags (publishes a Release), on pull requests that touch the app or its packaging, and manually |
| `ipad-check.yml` | Compiles the iPad shell when `ipad/` changes |

## Non-goals

- **No accounts or passwords.** The premise is a home or office network: any device that can reach the Mac can write.
- **Two devices.** More than two devices work at the protocol level but are not tested or supported.
- **No copy of the iPad selection tool**, which would need artwork.

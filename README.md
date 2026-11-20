# Whiteboard

A shared handwriting whiteboard for a Mac and an iPad on the same local network.

English | [简体中文](README.zh-CN.md)

## Overview

Whiteboard is a Mac app that serves a handwriting board to the local network. The Mac and an iPad open the same board, and every stroke appears on the other device in real time. All traffic stays on the LAN. There is no account and no cloud service; the only outbound connections are the update checks against the project's GitHub Pages site and GitHub Releases.

```
┌──────────── Mac ────────────┐            ┌─────── iPad ───────┐
│ pywebview window (role=mac) │            │ Home screen icon   │
│   boards / background       │  WebSocket │   Full-screen page │
│   zoom / export PDF, PNG    │◀──────────▶│   Apple Pencil     │
│ aiohttp server + mDNS       │    LAN     │                    │
│ boards stored as vectors    │            │ IndexedDB cache    │
└─────────────────────────────┘            └────────────────────┘
```

## Features

- **Real-time sync over the LAN.** The Mac serves the page and stores the boards; any device on the network opens the page in a browser and writes.
- **Vector ink.** Strokes are stored as points, not images. A 500-point stroke is usually under 2 KB. Outlines are drawn with [perfect-freehand](https://github.com/steveruizok/perfect-freehand).
- **Apple Pencil input.** Pressure and tilt set the pen width, the palm is ignored while the Pencil is down, and fingers pan and zoom unless finger drawing is on.
- **Ruler on the iPad.** Turn it on in the tool picker, move and rotate it with two fingers, and draw straight lines along its edge with the Pencil. The ruler stays on the iPad; the lines sync.
- **Shape recognition.** With it turned on, a stroke whose pen stays still for half a second at the end becomes a straight line; one undo brings back the handwriting. It is off by default and turned on per device next to finger drawing.
- **Two erasers.** The object eraser deletes the stroke it touches; the pixel eraser removes only the swept area and keeps the ink as vectors.
- **Three board kinds**, including document boards for annotating a PDF or an image and exporting it with the ink.
- **Offline writing.** Strokes queue locally and in IndexedDB and are sent in order when the connection returns.
- **Permissions.** Any device on the network can write. Managing boards, changing settings, clearing and exporting are each granted separately on the Mac.
- **Sketch.** Write freely on the iPad, then copy a PNG from the sketch that pops up from the Edge toolbar on the Mac into Claude or other pages; it is discarded when closed and never stored. See [docs/sketch.md](docs/sketch.md).

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

Builds published from 1.3.0 on update through [Sparkle](https://sparkle-project.org). The app reads `appcast.xml` from the project's GitHub Pages site, downloads only the changed parts of the app when it updates from one of the three previous builds, and checks the EdDSA signature of every download. Sparkle's own window shows the new version and its release notes; 检查更新 (Check for updates) in the About panel opens it.

| Channel | Published | Checked |
| --- | --- | --- |
| `stable` | for each `v*` tag without a hyphen | once a day |
| `alpha` | after every push to `dev` that passes all CI jobs, and for prerelease tags such as `v1.3.0-rc.1` | every hour; downloaded automatically and installed when the app quits |

- A build remembers its channel in the configuration file (`update_channel`) on first launch, so an alpha install stays on alpha after a stable release. Set `update_channel` to `stable` to leave the alpha channel.
- The first install of a build comes from the Pages site (its index page links the newest build of each channel) or from the Release.
- How the feed, the delta updates and the signing work: [docs/design/ci-and-updates.zh-CN.md](docs/design/ci-and-updates.zh-CN.md) (Chinese).

1.2.0 and earlier have their own updater: it reads `releases/latest` on GitHub and offers 1.3.0 in its update dialog. From 1.3.0 on, the app has no updater other than Sparkle. A build without Sparkle (running from source, or a local build without the public key) does not update itself; update it with `git pull` or install a new download.

The About panel (关于, the last icon in the top-right corner) shows the app icon, name, version and address, links to the changelog and the log file, and has 检查更新 (Check for updates) at the bottom. In a packaged build the button opens Sparkle's window, and the line above the button says so. When running from source, the line says to update with `git pull`; in a packaged build without a working Sparkle, it says that the build cannot update automatically, writes the reason to the log file, and new versions are downloaded from the changelog link.

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
- A local build has no Sparkle unless you run `packaging/fetch_sparkle.sh` and `packaging/embed_sparkle.sh dist/Whiteboard.app`, then `packaging/sign_app.sh dist/Whiteboard.app`; without `SPARKLE_FEED_URL` and `SPARKLE_PUBLIC_ED_KEY` it does not update itself.
- An Intel build needs a local build on an Intel Mac, or a `macos-13` / `x86_64` runner for the `package` job in `.github/workflows/ci.yml`.
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
| Other controls | Undo, redo, ruler; ⋯ menu with auto-minimize, finger drawing and clear board | Toolbar position, color, width, undo, redo, clear board; finger drawing on touch devices |
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
- The vendor canvas, ruler and lasso are not used. The vendor's ruler button is kept and turns on the board's own ruler, which has the same look as the vendor ruler (see [Ruler](#ruler)). Opacity is not available because the stroke format has no field for it.

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

### Ruler

The ruler button in the tool picker shows or hides a ruler on the iPad, like the ruler in the iPad's own apps. The ruler exists only on that iPad: it is neither synced nor shown on the Mac, and it is hidden again when the page reloads. Lines drawn along it sync like any other stroke.

| Input | Behavior |
| --- | --- |
| One finger on the ruler | Moves the ruler. |
| Two fingers on the ruler | Move and rotate the ruler. While the ruler turns, the angle to the horizontal (0° to 90°) is shown on it and fades out shortly after it stops; the angle snaps to multiples of 45° within 2°. |
| Fingers that start off the ruler | Pan and zoom the board as usual. If the first finger is on the ruler and the second lands off it, both pan and zoom the board. |
| Pencil starting on the ruler or within 24 pt of an edge | Draws along that edge: every point of the stroke lies on the edge line. The distance is measured on the screen, so it does not change with the zoom. |
| Pencil starting farther away | Draws as usual. |

The ruler stays in place on the screen when the board is panned or zoomed, and it holds still while a stroke along it is in progress. With finger drawing on, a finger stroke near an edge snaps the same way.

### Erasers

Choose the eraser in the eraser panel; the choice is remembered on the device. Neither eraser has a manual width. Widths are in screen pixels, so erasing is finer when zoomed in.

| Eraser | Behavior | Diameter |
| --- | --- | --- |
| Object (default) | Deletes the stroke it touches. If the pixel eraser has already split a stroke into separate pieces, deletes only the touched piece. | 6, at any angle |
| Pixel | Removes the swept area and keeps the rest. | Set by the Pencil's altitude angle when the Pencil lands |

The pixel eraser's diameter follows `ERASER_CURVE` in `packages/inksync/inksync/web/input-erase.js`, measured from native PencilKit. Values between the points are interpolated linearly.

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

- The order is stored in `space.json` on the Mac, so it is the same on every device and survives restarts. Folders are sorted by name and are not reordered by dragging.
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

The export dialog (see [Export](#export)) uses the pages of the original. A PDF keeps the original pages with the ink appended; an image becomes a one-page PDF or a PNG at the chosen resolution.

- In a PDF, ink is appended as vectors and the original content is not re-encoded. The file grows by a few hundred bytes per stroke; a heavily annotated handout grows by a few hundred KB.
- Ink in the gap between two pages is assigned to the nearer page; ink outside a page is clipped.

### Export

The export icon in the top-right group (导出) opens the export dialog. It needs the Export board permission.

| Part | Behaviour |
| --- | --- |
| Thumbnails | Thumbnails of every page on the left. Tap a page to select or deselect it; 全选 selects all, 当前页 selects the page in the middle of the view. |
| 格式 (Format) | PDF, PNG, or WBZ. With PNG, one selected page gives one PNG file and several give a ZIP with one PNG per page, named in page order. WBZ is the board file: it downloads the board's `.wbz` file unchanged; opening it in the app (double-click on the Mac) opens the same board. The export button follows the format: 导出 PDF, 导出 PNG or 导出 WBZ. |
| 范围 (Range, infinite and note boards) | 分页 exports pages; 整块 exports the whole board without pagination as one PDF page or one PNG, the size of all the ink plus a margin. |
| 页面 (Pages) | 全部, or the pages typed in the field, such as `1-5, 8, 11-13` (separated by `,` `、` `;` or spaces). The field and the thumbnails follow each other. A page number that does not exist shows a message under the field and disables the export button. |
| 清晰度 (Resolution, PNG only) | 标准 150 dpi, 高 300 dpi (default), 最高 600 dpi. The line below shows the pixel size, lowered as the server does above 40 million pixels. |
| 纸张 (Paper) | 跟随内容 (follow content, the default): each page's paper is its frame plus a margin, so the writing keeps its size. A4 or Letter: portrait sheets, with each frame scaled to fit. |
| 分页 (Pagination) | Number of pages (自动分成 N 页, or 手动分成 N 页 after adjusting), and 调整 to adjust them. |

- One board unit prints as 1 pt, so a page shows the ink at the size seen at 100% zoom. PDF pages are vector and stay sharp at any zoom; PNG pages are rendered at the chosen resolution, however far the board is spread.
- Automatic pages: an infinite board is read from top to bottom. Each page is as wide as the writing in that part of the board and is cut between lines where possible. A note board is cut by paper length, between lines where possible. A document board uses the pages of its original.
- 调整 shows the page frames on the board: drag a frame to move it, drag a corner to resize it (with A4 or Letter the paper's proportions are kept), and use the delete button to remove it. Drag on the board outside the frames with one finger, the Pencil or the mouse to draw a new frame; with A4 or Letter it has the paper's proportions, portrait or landscape by the shape of the drag. 清空 removes all frames, 添加一页 adds a page in the middle of the view, 自动分页 restores the automatic pages, and 完成 returns to the dialog. Two fingers, the scroll wheel and the trackpad still pan and zoom the board; nothing is drawn while adjusting.
- In the Mac window the file is saved through the save dialog; elsewhere it is downloaded (the iPad shell opens the share sheet).

## Permissions for other devices

Any device that opens the address can write. Every other action must be granted on the Mac in board settings (白板设置), one permission at a time. None is granted by default.

| Permission | Allows | Enforced at |
| --- | --- | --- |
| Manage boards (管理白板) | Switch, create, delete, rename, file into folders, create from a PDF or image | `/api/info`, `/api/boards`, `/api/thumb`, uploads to `/api/doc`, and the matching WebSocket operations |
| Board settings (设置白板) | Change the background | `meta` operations |
| Clear board (清空白板) | Erase a whole board at once | `clear` operations |
| Export board (导出白板) | Download a whole board, including the original file | `/api/export` |

- Permissions are decided from the TCP peer address. Loopback and the Mac's own LAN addresses have every permission. The User-Agent and the `role` in the WebSocket handshake are not used, because a client can set them freely. A WebSocket's permissions are fixed when the connection opens.
- Every request's source is checked too: requests from other web pages in a browser (carrying another site's `Origin`), and requests whose `Host` is not an IP address, `localhost`, `*.local` or this machine's host name, get 403. Otherwise any web page open on the Mac could read or delete boards with this machine's permissions. To open the whiteboard under another name (for example a VPN domain), add it to `allowed_hosts` in the config file (`~/Library/Application Support/Whiteboard/config.json`), for example `"allowed_hosts": ["mac.example.ts.net"]`, then restart the app. Details: [docs/protocol.md](docs/protocol.md#request-sources).
- Writing is always allowed: the page, the WebSocket, page images, the profile and diagnostics reports need no permission.
- The page receives the granted permissions in a `data-perms` attribute and shows only the allowed entries. This only affects the display; the server enforces the permissions.
- Update checks and the storage directory choice use the local pywebview bridge, which other devices cannot reach. 关于 and 连接 iPad therefore appear only in the Mac window.

## Data

### Storage layout

The default storage directory is `~/Library/Application Support/Whiteboard/boards-data`. Change it in board settings.

```
boards-data/
  boards/<id>.wbz        one file per board
  index.sqlite           index of the board list (rebuildable)
  space.json             order, folder names and the current board
  spaces/<app name>/     boards of installed apps, same layout
  apps/<app name>/       installed apps (see docs/embed.md)
  thumbs/<id>.png        chooser thumbnails
  docs/<id>.<ext>        original file of a document board
  backups/upgrade/       copies made before a new version first runs
  backups/locked/        copies made before editing a read-only board
```

| Path | Details |
| --- | --- |
| `boards/<id>.wbz` | Points are quantized, delta-coded and varint-packed; the file is zlib-compressed. Format: [docs/format.md](docs/format.md). |
| `index.sqlite` | Rebuilt from the `.wbz` files at the next start if deleted. At startup files are checked against the index by modification time, so files changed or added while the app was closed are picked up. |
| `space.json` | Order, folder names and the current board. If deleted, the order falls back to last update; folders that contain boards are recovered, empty folders are not. |
| `spaces/<app name>/` | Kept when the app (`apps/<app name>/`) is removed, and back when an app of the same name is installed again. |
| `thumbs/<id>.png` | Uploaded by the Mac. Document boards have none; their first page is used. |
| `docs/<id>.<ext>` | Read-only; deleted together with the board. |

The server saves changes every 3 seconds, and once more when the window closes or the process is interrupted (Ctrl+C).

### Read-only boards

A board whose file cannot be read completely (damaged, partly unreadable, or written by a newer version) opens as a read-only board.

- The readable content is shown, and the file is never overwritten. The warning appears once per board each time it opens.
- A device with the manage boards permission can choose 仍然编辑 (Edit anyway) after a warning. The original file is first copied to `backups/locked/`.

Details: [docs/format.md](docs/format.md).

### Backups

On the first launch after a version change, `boards/`, `index.sqlite`, `space.json` and `spaces/` (whichever exist) are copied to `backups/upgrade/<time>_<old version>_to_<new version>/` before anything else is touched. The latest 5 copies are kept.

When upgrading from 1.0.x to 1.1 the storage directory must be converted first (see [docs/format.md](docs/format.md#reading-10x-files)); if the backup fails then, the app does not start and shows the reason. Opening a directory converted by 2.0 with 1.0.x is not supported; to go back to 1.0.x, use the copy in `backups/upgrade/`.

To check your boards before upgrading, run `python tools/check_boards.py`.

## Offline and reconnection

- Strokes drawn while offline queue locally and in IndexedDB, so a page reload does not lose them. They are sent in order on reconnect, and the server deduplicates by stroke id.
- A reconnecting client sends its epoch and sequence number. A restarted server has a new epoch and sends the whole board, so the iPad never keeps stale or blank content.
- Resizing or rotating repaints everything; panning and zooming repaint only the strokes in view.
- Thumbnails frame the content with a margin.

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
packages/inksync/   sync component, installable on its own (interface in its README)
  inksync/
    server.py   WebSocket protocol v2; mount(), Spaces, serve_sdk()
    hub.py      runtime of one space: operations, broadcast, saving, unloading, events
    storage.py  board files, the SQLite index, space.json
    policy.py   Principal, Policy, creation rate limits
    codec.py    quantization, delta coding and varint packing for points
    models.py   metadata v2, validation of strokes and operations
    netinfo.py  .local host name, LAN addresses, Bonjour registration
    web/        the pad's front end (native ES modules, no build step)
      inkpad.js                    public entry point createInkPad
      pad.js, eraser.js            drawing, erasing, undo, sync, cache, view; no interface
      input.js, input-erase.js,    pointer routing and drawing; the eraser;
        input-gesture.js,          pan / zoom / momentum; fallback for the
        shell-fallback.js          0.9.43 shell
      ruler.js, input-ruler.js     ruler geometry; which fingers move the ruler,
                                   strokes along its edge
      stroke.js, boardstate.js,    geometry, ordering and spatial index,
        renderer.js, layers.js,    rendering, image layers,
        net.js, cache.js, ...      sync, local cache, export
whiteboard/
  server.py     aiohttp routes; mounts inksync at /ws, app spaces and permission rules
  hub.py, store.py   the whiteboard extension: following, folders, order, document
                boards, thumbnails, 1.0.x page compatibility and data migration
  config.py     settings file; command-line values that apply to one run only
  backup.py     copies the boards before a new version first touches them
  docs.py       document boards: reading, rendering and exporting PDFs / images
  inkpdf.py     ink as PDF vector paths
  inkraster.py  ink drawn into images (PNG export, image documents)
  pagination.py automatic page frames for export
  export.py     paged export: selected pages as PDF, a ZIP of PNG files or one PNG
  freehand.py   Python port of perfect-freehand, for export
  profile.py    .mobileconfig and icon generation (PNG written in pure Python)
  netinfo.py, codec.py   point to the modules of the same name in inksync (old import paths)
  ipadshell.py  the shell's install page, version endpoint and IPA download
  sparkle.py    Sparkle updates: loading the framework, update channel, Check for updates
  resources.py  paths and logging for source and packaged runs
  runner.py     runs the server in a background thread
  app.py        pywebview window and native file dialogs
  web/          front end (native ES modules, no build step)
    static/js/                      interface; the pad is loaded from /inksync/
      app.js                        whiteboard app on top of the pad: chooser,
                                    settings, import / export, updates
      boards.js                     board kind, folder and document in the interface
      ui.js, ui-*.js                toolbar and tool picker; board chooser,
                                    dialogs, settings
      ruler-view.js                 how the ruler looks (styles in css/app.css)
    static/lab/                     tuning pages (source runs only)
ipad/           the iPad shell (Swift, project generated by XcodeGen)
extensions/sketch/   Edge / Chrome extension (toolbar popup sketch); only embeds the /sketch page
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
| [docs/sketch.md](docs/sketch.md) | Sketch and the Edge extension |
| [docs/embed.md](docs/embed.md) | Hosting apps in the whiteboard app and embedding the pad |
| [packages/inksync/README.md](packages/inksync/README.md) | The inksync interface: storing and syncing boards on your own server |
| [docs/design/inksync-redesign.zh-CN.md](docs/design/inksync-redesign.zh-CN.md) | inksync 0.2 requirements and spec (Chinese) |

Input recording captures problems that need a real Pencil (pressure, tilt, coalesced samples within a frame, timing around lift-off) on the iPad for replay on a development machine. Open the diagnostics panel and use 录制输入 (Record input) in the bottom-left cell.

### Tests

Each CI job has a `make` target with the same name, so a local run uses the same commands as CI:

```bash
make deps      # test dependencies (from requirements-test.lock) and Chromium
make lock      # regenerate the lock files after editing a requirements*.txt (needs uv)
make fast      # checks + unit + js, no browser
make test      # checks, unit, js, inksync and e2e
make e2e PYTEST_ARGS="-k sync -x"
make webkit    # the WebKit smoke subset (WB_BROWSER=webkit)
make plan FILES="whiteboard/app.py docs/sketch.md"   # which CI jobs a change would run
```

- `make checks` runs ruff with the error-only rules (syntax errors, undefined names) the checks of `CHANGELOG.md` and commit messages, and checks that the dependency lock files are up to date.

- The end-to-end tests live in `tests/e2e/`, one file per area (sync, input, eraser, gallery, picker, …); shared fixtures are in `conftest.py`, shared helpers in `support.py`, and in-page helpers (`window.__t`) in `probe.js`. Pure calculations that need no page go in `tests/js/` instead.
- The end-to-end tests open two browser pages (the Mac and a touch device) and sync between them. They cover drawing, undo, erasing, clearing, Pencil exclusivity, switching boards, reconnecting with a backlog, folders, read-only boards and the export dialog. They skip themselves if Playwright or the browser is missing.
- `tests/test_compat.py` checks that board files written by earlier versions still read back identically. Do not regenerate the fixture in `tests/fixtures/compat/` with newer code.

### Dependencies

You edit the inputs; everything installs from the lock files generated from them (`tools/lock_deps.py`, `uv pip compile --universal --generate-hashes`, one lock for macOS arm64 and Linux x86_64):

| Input (edit this) | Lock (generated) | Installed by |
| --- | --- | --- |
| `requirements.txt` (the app) + `requirements-package.txt` (PyInstaller, cryptography) | `requirements.lock` | the `package` and `updates` jobs |
| `requirements-test.txt` | `requirements-test.lock` | `make deps`, every test job, the Playwright container |

Packages that appear in both locks have the same version, because the test lock is constrained by `requirements.lock`. Running from source (`start-whiteboard.command`) still installs `requirements.txt` directly.

To add or upgrade a dependency:

1. Edit the input file. To upgrade Playwright, change only its `playwright==` line in `requirements-test.txt`; the CI container image follows the version in the lock.
2. Run `make lock` (needs [uv](https://docs.astral.sh/uv/): `pip install uv` or `brew install uv`). Versions already locked stay put; `make lock LOCK_ARGS="--upgrade-package aiohttp"` upgrades one package, `LOCK_ARGS=--upgrade` all of them.
3. Commit the input and both locks together, in a commit of their own, and merge only when CI is green.

`tests/test_dependency_lock.py` (part of `make checks`) fails when an input changed but its lock was not regenerated. The `package` job writes the installed versions (`pip freeze`) into the run summary.

### CI

`.github/workflows/ci.yml` is the only workflow. Its `plan` job (`tools/ci_plan.py`) picks the jobs from the changed files, and the run summary lists each job with the file that pulled it in. The full rules are in [docs/design/ci-and-updates.zh-CN.md](docs/design/ci-and-updates.zh-CN.md) (Chinese).

| Change | Jobs |
| --- | --- |
| Documentation only | `checks` |
| App or inksync code | `checks`, `unit`, `e2e`, `webkit`, `package` (+ `js`, `inksync` where they apply) |
| `ipad/` | `checks`, `ipad` |
| CI itself, or an unknown path | all |
| Push to `dev`, or a `v*` tag | all, then publish an update (`alpha` or `stable`) |

- Pick jobs by hand with `[ci: e2e,webkit]` or `[ci: all]` in the commit message, or with the `jobs` input of a manual run.
- Other branches rehearse the update publishing at the end of the `package` job: a delta update is generated with a temporary key, its signature is checked, and it is applied to the previous build.
- Failed end-to-end tests upload a screenshot of every open page as the `test-results-*` artifact.

## Non-goals

- **No accounts or passwords.** The premise is a home or office network: any device that can reach the Mac can write.
- **Two devices.** More than two devices work at the protocol level but are not tested or supported.
- **No copy of the iPad selection tool**, which would need artwork.

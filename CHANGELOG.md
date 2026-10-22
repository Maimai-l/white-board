# Changelog

This file lists the notable changes to the board app and to inksync. The section of a version is used as its GitHub release notes and is shown in the in-app update dialog. Entries follow [docs/changelog-style.md](docs/changelog-style.md).

## 1.2.0 (unreleased)

### Added

- Sketch: Write on the iPad with Apple Pencil and copy the result on the Mac as a PNG with a transparent background. See docs/sketch.md.
- Sketch: A sketch is kept in memory only. Closing it discards the sketch and returns the iPad to the previous board.
- Sketch: Without the extension, the sketch is available at `http://127.0.0.1:8848/sketch` in any browser.
- Extension: An Edge and Chrome extension opens the sketch from the toolbar or with Alt+Shift+S. It is published with each release.
- inksync: Version 0.3.0 adds transient boards, a connection-closed event and PNG export with a transparent background.

### Changed

- Eraser: The pixel eraser width follows the Pencil angle during a stroke, as it did before 0.9.32.

### Fixed

- Tool picker: An area above the tool picker did not accept ink after a panel or the More menu had been opened and closed. Present since 0.9.26.
- Tool picker: Writing in that area could change hidden settings, such as switching the eraser from Object to Pixel mode.
- Export: Pixel-eraser masks on the second and later pages of a PDF appeared inverted. Present since 0.9.27.
- Sketch: Copying a sketch with ink spread over a large area produced an empty image.

## 1.1.1 (unreleased)

### Upgrade notes

- Settings: To open the board under an address other than an IP address, `*.local`, `localhost` or the Mac host name, add the address to `allowed_hosts` in the configuration file.

### Security

- Sync: Web pages open in a browser on the Mac could connect to the board server and read, change or delete boards. The server now rejects requests from other origins.
- inksync: Version 0.2.1 applies the same check in `mount` by default and adds the `allowed_origins` and `allowed_hosts` arguments and `guard_middleware`.

## 1.1.0 (2026-10-01)

The inksync sync component is redesigned so that other projects can use the handwriting pad on their own servers. The board app is used as before.

### Upgrade notes

- Storage: The first start converts the storage directory, creating `index.sqlite` and `space.json` and renaming `index.json` to `index.v1.json`.
- Storage: The data before conversion is backed up to `backups/upgrade/`. If the backup fails, the app does not start and shows the reason.
- Storage: Downgrading is not supported. To return to 1.0.x, restore the storage directory from `backups/upgrade/`.
- Boards: Boards created by apps in 1.0.x move into the space of each app (`spaces/<app name>/`) and no longer appear in the board list.
- iPad: The iPad shell does not need an update. Open 1.0.x pages continue to work and load the new version after a reload.
- inksync: Version 0.2.0 is not compatible with 0.1. Pages that embed the pad must be updated as described in docs/embed.md.

### Added

- Boards: The number of boards is not limited. The board list is served from a SQLite index, and start-up takes about 0.5 s with 20,000 boards.
- Boards: The board chooser lists each installed app. Its boards can be browsed page by page and viewed read-only with the Manage boards permission.
- Embedding: Embedded pads support infinite, fixed-width and fixed-size canvases, several image layers, app data and switching boards with `open`.
- inksync: Access rules are defined by the host through `Policy`, so boards can be separated per user. `examples/qb-server` is a complete example.
- inksync: The pad front end is distributed with inksync and served by `serve_sdk` at `/inksync/inkpad.js`. See packages/inksync/README.md.

### Changed

- Sync: Boards are saved and loaded on a background thread, so saving a large board does not delay sync. Boards not used for a while are released from memory.
- Storage: Browser caches are kept per project, with at most 200 boards each.
- Updates: After the server is upgraded, open pages reload when idle.

## 1.0.1 (2026-09-30)

### Added

- Mac: PDF and image files list the app under Open With in Finder, and `.wbz` files open with a double-click.
- Mac: A `.wbz` file from outside the storage directory, such as a backup, is imported as a new board.
- Boards: The name field of a new folder is selected for editing, as in Finder.
- Embedding: Other web apps can embed a pad that syncs with the Mac, with one board per page element and an optional underlay image. See docs/embed.md.
- Embedding: Apps are installed in `apps/` in the storage directory.
- Embedding: The embedded pad can run without a server (`transport: "local"`), sync to another address, or use a custom transport.
- Settings: Board settings on the Mac select the app that the iPad opens (iPad home app).
- iPad: The iPad shell connects to other services by name through the Source setting `@name` or `whiteboard-shell://open?source=name`. The shell must be reinstalled.
- inksync: The sync server is a separate package, `inksync` (`packages/inksync`), that mounts on any aiohttp application.
- inksync: Services register with `inksync.netinfo.advertise`.

### Changed

- Storage: Input recordings are limited to the newest 50 files and 256 MB in total.

### Fixed

- Settings: Permission changes on the Mac did not apply to connected devices until they reloaded.
- Boards: A PDF or image dropped onto the window while a folder was open in the board chooser was not placed in that folder.
- Export: Image export of a document board kept ink erased with the pixel eraser and rounded the ends of split strokes.

### Security

- Sync: Devices without the Manage boards permission received the full board list and the document pages of other boards.
- Sync: Requests forwarded by a proxy were treated as requests from the Mac. The Mac is now recognized on every network interface and on IPv6.
- inksync: Permissions are granted only to the local machine by default. Devices with write access can create boards only for installed apps, up to 5,000 per app.

## 1.0.0 (2026-09-29)

The first stable release. It adds no features and concentrates on data safety, connection stability and consistent behavior.

### Changed

- Storage: A board file that cannot be read completely opens read-only with an explanation, and the original file is not overwritten.
- Storage: On the Mac, Edit anyway unlocks such a board after copying the original file to `backups/locked/` in the storage directory.
- Storage: The first start after an upgrade or downgrade copies all boards to `backups/upgrade/`. The latest five backups are kept.
- Settings: The configuration file is written to a temporary file and then replaced. An unreadable configuration file is renamed and kept instead of being reset.
- Tool picker: The tool picker is used on the iPad only. The Mac window, desktop browsers and other touch devices use the classic toolbar.
- Boards: While a board is read-only, drawing pans the canvas, and clearing the board and changing the background are unavailable.
- Updates: A device running a prerelease is offered the release with the same version number.

### Fixed

- Storage: A board that failed to save during autosave prevented the remaining boards from being saved.
- Documents: Importing a PDF or image could modify board data at the same time as autosave.
- Documents: Importing a PDF or showing a document page failed intermittently with the error "Set changed size during iteration".
- Sync: A malformed message closed the connection, and the client could disconnect and reconnect repeatedly.
- Settings: A port chosen because the configured port was busy was saved, which invalidated the address stored on the iPad.
- Settings: The `--port` and `--data-dir` command-line options were saved. They now apply to the current run only.
- Settings: If a newly chosen storage directory could not be used, the server remained stopped.
- iPad: The search field, the board name field and the folder name fields in the board chooser did not accept text.
- Boards: In a text field, ⌘Z, ⌘Y, ⌘+, ⌘- and ⌘0 undid strokes or zoomed the board, and a space started panning the canvas.
- Boards: Pressing Return to choose an input method candidate in a folder name field submitted the unfinished name.
- Updates: The iPad shell prompted repeatedly for an update when the Mac ran a prerelease.
- Mac: Log lines were written twice in the packaged app.

### Removed

- Settings: The option to switch between the tool picker and the classic toolbar has been removed.
- Mac: The page for tuning drag behavior is no longer included in the app.

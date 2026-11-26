# Changelog

This file lists the notable changes to the board app and to inksync. The section of a version is used as its GitHub release notes and is shown in the in-app update dialog. Entries follow [docs/changelog-style.md](docs/changelog-style.md).

## 1.3.0 (unreleased)

### Upgrade notes

- Updates: Version 1.2.0 installs this version through its update dialog. Later updates are installed through the new update window.

### Added

- Library: The Library replaces the board picker. A sidebar lists Recent, All Boards, folders, apps and the Trash. Boards are shown as a grid or a list and sorted by date modified, by name or by hand.
- Library: Right-clicking a board on the Mac, or holding it on the iPad, opens a menu to open, rename, move, duplicate, export or trash it.
- Library: Several boards are selected with Command-click and moved or trashed together.
- Library: Boards are moved by dragging them onto a folder, onto All Boards or onto the Trash in the sidebar.
- Library: Several PDF and image files can be dropped at once. A file dropped on a folder in the sidebar is imported into that folder.
- Mac: The Connect iPad panel shows a QR code of the board address for the iPad camera, the connected devices and a link to install the iPad app.
- Mac: A message appears when an iPad connects or disconnects.
- Mac: Check for Updates… is in the app menu, below About.
- Mac: About is the standard macOS About window, with the full version and links to the changelog and the log file.
- Mac: The zoom control shows the zoom percentage. Clicking the percentage sets the zoom to 100%.
- Boards: A board can be duplicated. The copy has the same ink, background and folder and is placed after the original; a document board is copied with its original file.
- Export: The export button opens an export dialog that exports the selected pages as a PDF, one PNG file or a ZIP of PNG files.
- Export: Infinite boards are divided into pages automatically from top to bottom. Each page is as wide as the writing in that part of the board. Note boards are cut by paper length. See README.md.
- Export: Page frames can be moved, resized, added and deleted on the board before exporting.
- Export: The whole board can be exported without pagination as one PDF page or one PNG image.
- Export: The export dialog downloads the board file (`.wbz`). Opening it in the app shows the same board.
- Export: Pages to export can be typed as page numbers, such as `1-5, 8`. The field and the thumbnails follow each other.
- Export: Clear removes all page frames, and a drag on the board outside the frames draws a new one. Two fingers, the scroll wheel and the trackpad still move the board.
- Export: PNG pages are exported at 150, 300 or 600 dpi.
- Export: PDF pages of infinite and note boards contain vector ink, which stays sharp at any zoom.
- Ink: With Shape recognition on, a stroke becomes a straight line when the Pencil stays still for 0.5 s at its end. One undo brings back the handwritten stroke.
- Tool picker: Shape recognition is turned on in the More menu, next to Draw with finger. It is off by default and remembered per device.
- Settings: `update_channel` in the configuration file selects `alpha` test builds or `stable` releases.
- iPad: The Ruler button in the tool picker shows a ruler. Two fingers move and rotate it, and the ruler shows its angle while it turns.
- iPad: An Apple Pencil stroke started on the ruler or near its edge is drawn as a straight line along the edge. The line syncs; the ruler is not shown on the Mac.
- Documents: Importing a PDF of more than one page opens an import dialog with page thumbnails. All pages, a range such as `3-15` or the selected pages are imported.
- Documents: The import dialog can add a blank page after each imported page.
- Documents: A page scrubber on the right edge of a document board turns to any page by dragging or clicking, and shows the page number while dragging.
- Documents: Blank pages are inserted before or after a page from its menu: right-click on the Mac, hold a finger on touch devices.
- Documents: The More menu and the tool picker's More menu insert a blank page after the page in view. Blank pages show the board's background pattern.
- Documents: A blank page can be deleted with the ink on it. Pages of the original cannot be deleted. Exports include the blank pages.

- inksync: Hubs emit an `attached` event when a connection is registered after its handshake.
- inksync: `Hub.replace_board` replaces a board's metadata and strokes, and connections on the board reopen it with a new epoch.
- inksync: A `sheet` layer without `src` is drawn as a blank page with the board's background pattern.
- inksync: Pads emit a `view` event when the view moves or zooms.

### Changed

- Interface: On the Mac, a title bar shows the board name and its folder, with the Library, Connect iPad, Export and Board settings buttons.
- Mac: The top bar is the window's title bar, with the window buttons at its left end. Clicking it no longer moves the window down.
- Mac: Dragging an empty part of the top bar moves the window, and double-clicking it zooms the window.
- Interface: Board settings and Connect other devices open as panels below their buttons.
- Library: Settings at the bottom of the sidebar shows the connection, the permissions for other devices and the storage location without opening a board.
- Interface: All settings… at the bottom of the board settings panel opens the settings in the library.
- Interface: Labels that apply to every device on the network say other devices instead of iPad.
- Interface: The toolbar stays at the bottom of the window and contains the four tools, the color button, Undo, Redo and More.
- Interface: Clear is in the More menu. The colors are the system colors.
- Interface: Buttons, switches, links and cards change when the pointer is over them and when they are pressed.
- Interface: Menus, panels and dialogs grow from the button that opened them and fade out when they close. With Reduce motion turned on, they appear and disappear at once.
- Interface: Esc closes the export dialog and confirmation dialogs, like Cancel.
- Mac: The menu bar and the app name in it are in Chinese.
- iPad: The Library button is at the top left. The connection state is shown by the color of the connection button at the top right, which replaces the status dot.
- iPad: The connection panel shows the Mac's address without a colored dot. Switch Mac is only in the connection panel.
- iPad: The buttons at the top corners have no background while their panel is open and dim while pressed. Their panels line up with the right edge of the group.
- iPad: The diagnostics panel opens only after three quick taps on the connection button, not when the panel is opened and closed at a normal pace.
- Export: The export dialog lists the pages in a sidebar. Pages are exported as all pages, a typed range or the pages selected in the sidebar.
- Sketch: The sketch page shows the connection state as text, and its copy button is named Copy as image.
- Interface: Labels and messages use consistent wording without full-width brackets or sentence punctuation. Error messages are short, and the details are written to the log file.
- Boards: Deleting a board moves it to the Trash, where it can be restored or deleted permanently. Boards in the Trash for more than 30 days are deleted permanently.
- Mac: Failures that used to leave no trace, such as a board thumbnail that could not be uploaded or a port in the configuration file that is not a number, are written to the log file.
- Updates: The Mac app checks for updates with Sparkle and downloads only the changed parts of the app when updating from one of the three previous versions.
- Updates: Check for Updates… opens the update window, which shows the result and the release notes.
- Export: The paper follows the content by default: each page is its frame plus a margin, so the writing keeps its size. A4 and Letter pages are portrait.
- Export: Document boards are exported through the export dialog, where single pages of the original can be selected.
- Export: Exporting a document board with many strokes takes about one third of the previous time.

### Fixed

- Documents: An Apple Pencil touching the page scrubber turned pages. The Pencil now writes there, and only fingers and the mouse drag the scrubber.
- Mac: The window could not be moved while a dialog was open, so a dialog partly off screen could not be reached. The dimmed area behind a dialog now moves the window.
- iPad: After the browser lost track of a lifted finger, spreading two fingers zoomed out and dragging one finger zoomed. Such fingers are now forgotten when the next touch starts.
- Documents: Holding a finger on a page of a document board on a touch screen did not open the page menu. It now does.
- iPad: A tap on a board in the Library opened its menu instead of the board when the finger rested a little. A card is now picked up after half a second.
- Library: The Library stays open until the chosen board has arrived, instead of showing the previous board first.
- Updates: The iPad app never offered a newer shell from an alpha build, because alpha builds share one version number. It now offers one when the shell itself changed.
- iPad: The board settings button did nothing on a document board. It now opens the background of blank pages.
- Documents: A PDF of more than about 115 pages, such as a textbook, could not be imported, because the board's height was limited to 100,000 points.
- Documents: The pages of a PDF of more than 400 pages are chosen in the import dialog, instead of the pages after the 400th being left out without a word.
- Export: The PNG of a widely spread board was blurred, because its long edge was limited to 4096 px.
- Interface: Messages shown at the same time are listed one under another instead of covering each other. Importing a file shows one message, which turns into the error if the file cannot be opened.

## 1.2.0 (2026-10-22)

### Upgrade notes

- Settings: To open the board under an address other than an IP address, `*.local`, `localhost` or the Mac host name, add the address to `allowed_hosts` in the configuration file.

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

### Security

- Sync: Web pages open in a browser on the Mac could connect to the board server and read, change or delete boards. The server now rejects requests from other origins.
- inksync: `mount` applies the same check by default and accepts the `allowed_origins` and `allowed_hosts` arguments. `guard_middleware` is added.

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

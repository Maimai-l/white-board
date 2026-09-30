# Changelog

English | [简体中文](CHANGELOG.zh-CN.md)

One section per version, titled with the version number. When a release is published, CI uses the Chinese section of that version (`CHANGELOG.zh-CN.md`) as the release notes, which the in-app update dialog displays. A prerelease (for example `1.0.0-rc.1`) uses the section of its release (`1.0.0`).

> **Note**
> The update dialog displays content line by line, so every entry must fit on a single line.

## 1.0.1

### New

- Finder “打开方式” (Open With): PDF and image files list the app as an alternative; `.wbz` files open with a double-click. A `.wbz` from outside the storage directory, such as a backup, is imported as a new board.
- A new folder's name field is selected for editing right away, as in Finder.
- Other web apps can embed a handwriting pad that syncs with the Mac, one board per page element, with an optional underlay image. Apps are installed in `apps/` in the storage directory. See docs/embed.md.
- Board settings on the Mac can choose which app the iPad opens (“iPad 首页”).
- The sync server is a separate package, `inksync` (`packages/inksync`), that mounts on any aiohttp app.
- The iPad shell can connect to other services by name: set “来源” (Source) to `@name` in its Settings page, or open `whiteboard-shell://open?source=name`. Services register with `inksync.netinfo.advertise`. Requires reinstalling the shell.
- The embedded pad can run without a server (`transport: "local"`), sync to another address, or use a custom transport.

### Fixes

- A PDF or image dropped onto the window while a folder is open in the board chooser now goes into that folder.
- Image export of a document board now removes ink erased with the pixel eraser and keeps flat ends on split strokes, as PDF export already did.

## 1.0.0

The first stable release. It adds no new features; it focuses on data safety, connection stability and consistent behaviour.

### Data safety

- A board file that cannot be read completely (damaged, with undecodable strokes, or written by a newer version) now opens read-only with an explanation, and the original file is never overwritten. On the Mac you can choose "仍然编辑" (Edit anyway); the original is first copied to `backups/locked/` in the storage directory.
- The first launch after an upgrade or downgrade copies all boards to `backups/upgrade/` in the storage directory; the latest five backups are kept.
- During autosave, a board that fails to save no longer blocks the others; it is retried on the next round.
- The configuration file is written to a temporary file and then replaced, so an interrupted write never leaves a partial file. An unreadable configuration file is renamed and kept instead of being overwritten with defaults.
- If a newly chosen storage directory cannot be used, the app returns to the previous directory instead of leaving the server stopped.
- Importing a PDF or image no longer modifies board data at the same time as autosave.
- Fixed an intermittent "Set changed size during iteration" error when importing a PDF or displaying a document page (seen as a failed import or a blank page).

### Connection

- A malformed message no longer closes the connection. Previously a client could disconnect and reconnect repeatedly, and reloading the page did not help.
- A port chosen because the configured one was busy is used for the current run only and is no longer saved, so the address stored on the iPad stays valid. The command-line options `--port` and `--data-dir` also apply to the current run only.

### Interface

- The tool picker is used on the iPad only; the Mac window, desktop browsers and other touch devices use the classic toolbar. The switch between the two has been removed.
- While a board is read-only, drawing pans the canvas instead, and clearing the board and changing the background are unavailable, so strokes no longer appear and then vanish.
- Fixed the search field, board name field and folder name fields in the board chooser not accepting text on the iPad.
- While typing in a text field, ⌘Z, ⌘Y, ⌘+, ⌘- and ⌘0 act on the field only and no longer undo strokes or zoom the board; a space typed in a field no longer starts panning the canvas.
- When renaming or creating a folder with an input method such as Pinyin, pressing Return to pick a candidate no longer submits the unfinished name.

### Updates

- A device running a prerelease is offered the release with the same version number.
- The iPad shell no longer prompts repeatedly for an update when the Mac runs a prerelease.

### Other

- Log lines are no longer written twice in the packaged app.
- The development page for tuning drag behaviour is no longer included in the app package.
- Documentation brought in line with the code, and new tests added: storage compatibility fixtures, front-end unit tests, WebKit smoke tests, and interface tests that tap and type on the iPad page.

English | [简体中文](release-checklist.zh-CN.md)

# Release Checklist

Run this checklist on real devices before turning a prerelease into a release.

CI runs only Chromium and WebKit on Linux. Apple Pencil input, iPadOS Safari, the iPad shell, and the packaged `.app` can be verified only on real devices.

## 0. Before upgrading

- [ ] On the Mac, run `python tools/check_boards.py`. The last line of the output must be “全部白板都能完整读出。” (All boards can be read completely.)

> **Warning**
> If any board would open as a read-only board, do not upgrade. Send the output to the developer first.

## 1. Mac

### Upgrade

- [ ] Install the prerelease and open it for the first time. The storage directory contains `backups/upgrade/<time>_<old version>_to_<new version>/` with `boards/` and `index.json` inside.
- [ ] “关于” (About) shows the new version number, including the `-rc` suffix.
- [ ] All existing boards are present, with the same content, folders, and order as before the upgrade. Opening several of them shows no read-only notice.

### Drawing and editing

- [ ] Drawing with the mouse, undo, and redo work. “清空白板” (Clear board) asks for confirmation before clearing.
- [ ] The object eraser and the pixel eraser both erase. Undo restores the erased strokes.
- [ ] Create an infinite board and a note board. Drag a PDF into the window to create a document board, write on the PDF, and export it.

### Board chooser and settings

- [ ] In the board chooser, renaming, moving a board into a folder, drag-to-reorder, and search work.
- [ ] Change the storage directory in settings, then change it back. All boards are present.
- [ ] Close and reopen the window. Recently written content is present.
- [ ] Each line in the log file `~/Library/Logs/Whiteboard.log` appears only once.

### Open With

CI cannot check these items: they depend on the packaged app's Info.plist and on Finder.

- [ ] In Finder, “打开方式” (Open With) for a PDF and a JPEG lists the app; the default app for those files is unchanged.
- [ ] With the app closed, opening a PDF with it launches the app and creates a document board.
- [ ] With the board chooser showing a folder, opening an image with the app puts the new board in that folder.
- [ ] Double-clicking a `.wbz` from `backups/upgrade/` imports it as a new board, and the file in `backups/` is unchanged.

## 2. iPad

Run this section twice: once in the iPad shell and once from the Home Screen icon.

### Shell and toolbar

- [ ] Shell: after launch, the “有新版本” (New version available) prompt does not reappear repeatedly. When the shell needs an update, the installed shell has the same version number as the Mac app.
- [ ] The toolbar is the tool picker, including on an iPad where the tool picker was previously turned off.

### Text input

CI browsers do not reproduce iPadOS problems where a text field does not accept typing. Check these items only on a real device.

- [ ] Board chooser: the search field accepts typing in Chinese and English and filters the results. Tapping a board name starts renaming. The text fields for creating and renaming a folder accept typing.
- [ ] While a board rename is in progress on the iPad, rename another board on the Mac. The iPad keyboard stays open and the typed text is kept.
- [ ] iOS does not autocorrect or capitalize the first letter while renaming.
- [ ] In the new-folder dialog, the keyboard does not cover the text field. After the keyboard closes, the layout and the tool picker position are correct.

### Home Screen icon

- [ ] When opened from the Home Screen icon, PNG export saves the file.
- [ ] When importing a document, choosing an image from “照片” (Photos), including HEIC, gives the expected result. Unsupported formats show a message.

### Drawing

- [ ] Apple Pencil: pressure and tilt apply, fast strokes do not break, stroke ends have no blob, and a palm resting on the screen leaves no mark (palm rejection).
- [ ] One finger pans and two fingers zoom. With finger drawing turned on, one finger draws.
- [ ] Pixel eraser: the eraser is narrow with the Pencil upright and wider with the Pencil tilted flat. The width stays constant within one drag.

### Sync

- [ ] With both devices open, strokes drawn on the iPad appear on the Mac immediately, and strokes drawn on the Mac appear on the iPad immediately.
- [ ] Turn off Wi-Fi, draw several strokes, and reconnect. The strokes sync without duplicates.
- [ ] Quit and reopen the app on the Mac. The iPad reconnects automatically and no content is lost.

## 3. Read-only board (optional)

This section verifies the read-only notice itself.

- [ ] Copy a board's `.wbz` file as a backup. Then change a few bytes of the original file in a text editor.
- [ ] Open the board. Both devices show the read-only notice, and drawing pans the canvas instead.
- [ ] On the Mac, choose “仍然编辑” (Edit anyway). A copy of the original file appears in `backups/locked/`, and both devices can continue drawing.
- [ ] Restore the file from the backup.

## 4. Publish the release

When every item passes, push a tag without a suffix (for example `v1.0.0`) on the `stable` branch.

```bash
git tag v1.0.0 && git push origin v1.0.0
```

| Tag | Published as | Offered by the in-app updater |
| --- | --- | --- |
| `v1.0.0` | Release | Yes |
| `v1.0.0-rc.1` (contains a hyphen) | Prerelease | No |

- CI (`.github/workflows/build-macos.yml`) builds the Mac app and the iPad shell, then publishes the `.zip` and the `.ipa` to the GitHub Release.
- The in-app updater reads `releases/latest`, which excludes prereleases. Existing users receive the update only after the release is published.
- The release notes are the section of `CHANGELOG.zh-CN.md` for that version (`packaging/release_notes.py`). The update dialog shows these notes. A prerelease without its own section uses the section of the corresponding release version. If no section exists, GitHub generates the notes.

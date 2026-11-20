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

- [ ] Install the prerelease and open it for the first time. The storage directory contains `backups/upgrade/<time>_<old version>_to_<new version>/` with `boards/` inside, plus whichever of `index.json` (when upgrading from 1.0.x), `index.sqlite`, `space.json` and `spaces/` exist.
- [ ] When upgrading from 1.0.x: the storage directory has `index.sqlite` and `space.json`, and `index.json` is renamed to `index.v1.json`; boards created by apps in 1.0.x are in `spaces/<app name>/` and open read-only from that app's entry in the board chooser.
- [ ] “关于” (About) shows the new version number, including the `-rc` suffix.
- [ ] All existing boards are present, with the same content, folders, and order as before the upgrade. Opening several of them shows no read-only notice.

### Drawing and editing

- [ ] Drawing with the mouse, undo, and redo work. “清空白板” (Clear board) asks for confirmation before clearing.
- [ ] The object eraser and the pixel eraser both erase. Undo restores the erased strokes.
- [ ] Create an infinite board and a note board. Drag a PDF into the window to create a document board, write on the PDF, and export it.
- [ ] Export (导出): export a PDF and a PNG with two pages selected. The save dialog suggests a name ending in `.pdf` or `.zip`, and the saved files open in Preview. If not, send the log file (关于 → 日志文件) and the suggested name.
- [ ] Export → 调整: drag a page frame with the trackpad and resize it by a corner; panning with two fingers moves the board, not the frame. If a frame does not follow, send a screen recording.
- [ ] Export → WBZ: the save dialog suggests `<board name>.wbz`. Save it outside the storage directory, then double-click the saved file in Finder: the app opens it as a new board with the same content. If the name is wrong or the board does not open, send the log file and the saved `.wbz`.

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

### Updates (Sparkle)

CI checks that Sparkle loads in the packaged app and that delta updates apply. Installing an update needs a real Mac.

- [ ] Install the newest alpha build from the GitHub Pages site. About shows a version ending in `-alpha.<build time>`.
- [ ] Merge any change into `dev` and wait for CI to pass. Within an hour the update downloads without a prompt; after quitting and reopening the app, About shows the new build.
- [ ] 检查更新 (Check for updates) opens the Sparkle window and reports the newest version.
- [ ] Upgrading from 1.2.0: the previous update dialog offers this release; after installing it, 检查更新 (Check for updates) opens the Sparkle window.

If an item fails, send the lines of `~/Library/Logs/Whiteboard.log` that start with `Sparkle`, and the output of `/Applications/Whiteboard.app/Contents/MacOS/Whiteboard --check-updater`.

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

### Embedded pad

Copy `examples/apps/demo/` into `apps/` in the storage directory first.

- [ ] In the iPad shell, open `/apps/demo/`. The diagnostics panel is not available there, but fast Pencil strokes are as smooth as in the whiteboard.
- [ ] The answer field accepts typing, and the tool buttons work.
- [ ] Strokes written in the demo appear on the Mac after opening “示例 第 1 题” in the board chooser, with the question image under them.
- [ ] Set “iPad 首页” (iPad home) to `demo` on the Mac. Reopening the shell shows the demo; its “白板” link returns to the whiteboard. Set it back to “白板”.

### Home Screen icon

- [ ] Export (导出): a single PNG, a ZIP of PNG files and a PDF can each be saved (Safari downloads; the shell opens the share sheet). If not, send a screenshot of what appears instead.
- [ ] Export → 调整: a page frame can be dragged and resized by a corner with a finger, and two fingers on the board still pan and zoom. After 清空, dragging on the board with one finger and with the Pencil (also in the shell) each draws a new frame and no ink. If the board pans instead or ink appears, send a screen recording.
- [ ] When importing a document, choosing an image from “照片” (Photos), including HEIC, gives the expected result. Unsupported formats show a message.

### Drawing

- [ ] Apple Pencil: pressure and tilt apply, fast strokes do not break, stroke ends have no blob, and a palm resting on the screen leaves no mark (palm rejection).
- [ ] One finger pans and two fingers zoom. With finger drawing turned on, one finger draws.
- [ ] Shape recognition (“形状识别” in the tool picker's More menu): draw a slightly curved line with the Pencil and keep the tip still. After about half a second, with the Pencil still down, the line snaps straight on both devices; moving the Pencil then moves the end. One undo brings back the handwriting on both devices. A short hesitation, handwriting that ends with a pause (a letter, a curve), and the switch turned off leave the stroke as written. On failure, send an input recording of the stroke (docs/recording.md) and say whether it snapped too early, too late or not at all.
- [ ] Pixel eraser: the eraser is narrow with the Pencil upright and wider with the Pencil tilted flat. Tilting the Pencil during one drag changes the width gradually, without lifting.
- [ ] Ruler: tap the ruler button in the tool picker. Two fingers on the ruler move and rotate it while the board stays still, and the angle follows the fingers. Two fingers away from the ruler still pan and zoom the board.
- [ ] Ruler: hold the ruler with one hand and draw along its edge with the Pencil. The line is straight and lies on the edge, also when the board is zoomed in; strokes started a few centimetres away from the ruler are not pulled to it. The Mac shows the line but no ruler.

If a ruler item fails, send a screen recording of the iPad and say whether the page ran in the iPad shell or from the Home Screen icon.

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
| `v1.0.0` | Release | Yes: Sparkle `stable` channel, and the previous updater through `releases/latest` |
| `v1.0.0-rc.1` (contains a hyphen) | Prerelease | Only to Macs on the `alpha` channel |

- CI (`.github/workflows/ci.yml`) runs every job, publishes the Sparkle update to GitHub Pages, then publishes the `.zip` and the `.ipa` to the GitHub Release.
- Builds with Sparkle read `appcast.xml` on GitHub Pages. Builds without it (1.2.0 and earlier) read `releases/latest`, which excludes prereleases.
- The release notes are the section of `CHANGELOG.md` for that version (`packaging/release_notes.py`). The update dialog shows these notes. A prerelease without its own section uses the section of the corresponding release version. If no section exists, GitHub generates the notes.

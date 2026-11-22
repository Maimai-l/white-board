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
- [ ] When upgrading from 1.0.x: the storage directory has `index.sqlite` and `space.json`, and `index.json` is renamed to `index.v1.json`; boards created by apps in 1.0.x are in `spaces/<app name>/` and open read-only from that app's row under “应用” (Apps) in the library sidebar.
- [ ] “关于白板” (About; menu bar “白板” → “关于白板”) shows the new version number, including the `-rc` suffix.
- [ ] All existing boards are present, with the same content, folders, and order as before the upgrade. Opening several of them shows no read-only notice.

### Drawing and editing

- [ ] Drawing with the mouse, undo, and redo work. “清空白板” (Clear board) asks for confirmation before clearing.
- [ ] The object eraser and the pixel eraser both erase. Undo restores the erased strokes.
- [ ] Create an infinite board and a note board. Drag a PDF into the window to create a document board, write on the PDF, and export it.
- [ ] Export (导出): export a PDF and a PNG with two pages selected. The save dialog suggests a name ending in `.pdf` or `.zip`, and the saved files open in Preview. If not, send the log file (白板 menu → 关于白板 → 日志文件) and the suggested name.
- [ ] Export → 调整: drag a page frame with the trackpad and resize it by a corner; panning with two fingers moves the board, not the frame. If a frame does not follow, send a screen recording.
- [ ] Export → WBZ: the save dialog suggests `<board name>.wbz`. Save it outside the storage directory, then double-click the saved file in Finder: the app opens it as a new board with the same content. If the name is wrong or the board does not open, send the log file and the saved `.wbz`.

### Window

- [ ] The title bar shows the board name on the left, with its folder under the name.
- [ ] Zoom: clicking “放大” (Zoom in) and “缩小” (Zoom out), and pinching on the trackpad, change the percentage at the bottom right; clicking the percentage returns to `100%`. If the percentage does not change or does not match the zoom, send a screenshot.
- [ ] Menus and panels: “更多” (More), “颜色与粗细” (Color and width), “白板设置” (Board settings) and “连接 iPad” (Connect iPad) each close when clicking anywhere outside them, and the click also reaches what was under it. If one stays open, send a screenshot and say which one.
- [ ] Connect iPad: open “连接 iPad” (Connect iPad) and scan the QR code in the panel with the iPad's own Camera app. Safari opens this board, at the address shown in the panel. After “在 iPad 上安装白板” (Install on iPad), scanning again opens the shell install page. If the code does not scan or the address is wrong, send a screenshot of the panel and the address the iPad opened.
- [ ] When the iPad opens the board, the Mac window shows “iPad 已连接” (iPad connected), the “连接 iPad” icon turns blue, and the panel lists the iPad under “已连接” (Connected). After the page is closed on the iPad (or its Wi-Fi is turned off), “iPad 已断开” (iPad disconnected) appears and the icon returns to its normal color. If a message is missing or appears twice, or the icon does not change color, send the log file and say how the iPad was disconnected.
- [ ] Clicking “连接 iPad” three times opens the diagnostics panel; three more clicks close it.
- [ ] Pointer and motion: moving the pointer over toolbar buttons, title bar buttons, library cards and dialog buttons gives each a gray fill or a slight lift; menus and panels grow from their button and fade out when closed, without flicker or a jump at the start or end. Esc closes the export dialog. If something has no reaction, flickers or jumps, send a screen recording.

### Library and settings

- [ ] Right-clicking a board shows a menu with “打开” (Open), “重命名” (Rename), “移到文件夹” (Move to folder), “复制” (Duplicate), “导出…” (Export) and “移到回收站” (Move to Trash). Right-clicking the library background shows “新建白板” (New board), “新建文件夹” (New folder), “导入 PDF 或图片…” (Import PDF or image), “以图标显示” (as icons) and “以列表显示” (as list). The menu opens at the pointer, and no system or web view context menu appears. If not, send a screenshot.
- [ ] In the library in the Mac window (not a browser), ⌘N opens the two new-board choices, ⇧⌘N adds a folder in the sidebar with its name in edit mode, and ⌘I opens the file chooser. If a shortcut does nothing or triggers something else, say which shortcut and what happened.
- [ ] Dragging a board onto a folder in the sidebar moves it into the folder; onto “全部白板” (All boards) moves it out of its folder; onto “回收站” (Trash) moves it to the trash. With two boards selected by ⌘-click, dragging one moves both. If a board cannot be dragged or lands elsewhere, send a screen recording.
- [ ] With the sort set to “手动排列” (Manual), dragging cards in “全部白板” reorders them, and the order is kept after quitting and reopening the app. With the sort set to “修改时间” (Date modified), dragging a card does not change the order.
- [ ] Trash: “恢复” (Restore) on a board in the trash puts it back into its folder. “永久删除” (Delete permanently) on another, after confirmation, removes its `.wbz` from `boards/` in the storage directory. “清空回收站” (Empty trash) asks for confirmation first. If the result differs, send the log file and a screenshot of the trash.
- [ ] Duplicate: “复制” (Duplicate) on an infinite board and on a document board each adds “<name> 副本” right after the original; it opens with the same content, and the document board's copy shows the PDF pages. If the result differs, send a screenshot and the log file.
- [ ] Renaming boards and folders, creating folders, and search work.
- [ ] In “白板设置” (Board settings) → “存储位置” (Storage), use “更改…” (Change) to switch the storage directory, then switch it back. All boards are present.
- [ ] Close and reopen the window. Recently written content is present.
- [ ] Each line in the log file `~/Library/Logs/Whiteboard.log` appears only once.

### Open With

CI cannot check these items: they depend on the packaged app's Info.plist and on Finder.

- [ ] In Finder, “打开方式” (Open With) for a PDF and a JPEG lists the app; the default app for those files is unchanged.
- [ ] With the app closed, opening a PDF with it launches the app and creates a document board.
- [ ] With the library showing a folder, opening an image with the app puts the new board in that folder.
- [ ] Double-clicking a `.wbz` from `backups/upgrade/` imports it as a new board, and the file in `backups/` is unchanged.

### Updates (Sparkle)

CI checks that Sparkle loads in the packaged app and that delta updates apply. Installing an update needs a real Mac.

- [ ] Install the newest alpha build from the GitHub Pages site. “关于白板” (About) shows a version ending in `-alpha.<build time>`.
- [ ] Merge any change into `dev` and wait for CI to pass. Within an hour the update downloads without a prompt; after quitting and reopening the app, “关于白板” (About) shows the new build.
- [ ] 检查更新 (Check for updates) opens the Sparkle window and reports the newest version.
- [ ] Menu bar: the app name reads 白板 and every menu is in Chinese (关于白板, 检查更新…, 服务, 隐藏白板, 隐藏其他, 全部显示, 退出白板; in the Edit menu 剪切, 拷贝, 粘贴, 全选), with no space before the app name. 检查更新… (Check for Updates…) opens the Sparkle window; 关于白板 (About) opens the standard macOS About window in Chinese: the icon, 白板, “版本 1.3.0 (alpha.…)” and the links 更新日志 and 日志文件; 更新日志 opens the releases page in the browser and 日志文件 opens the log in Console. If an item is still in English, the version line lacks the build, or a link does nothing, send a screenshot of the menu or the About window.
- [ ] Upgrading from 1.2.0: the previous update dialog offers this release; after installing it, 检查更新 (Check for updates) opens the Sparkle window.

If an item fails, send the lines of `~/Library/Logs/Whiteboard.log` that start with `Sparkle`, and the output of `/Applications/Whiteboard.app/Contents/MacOS/Whiteboard --check-updater`.

## 2. iPad

Run this section twice: once in the iPad shell and once from the Home Screen icon.

### Shell and toolbar

- [ ] Shell: after launch, the “有新版本” (New version available) prompt does not reappear repeatedly. When the shell needs an update, the installed shell has the same version number as the Mac app.
- [ ] The toolbar is the tool picker, including on an iPad where the tool picker was previously turned off.

### Library and chrome

- [ ] “白板库” (Library) at the top left opens the library; tapping a board opens it.
- [ ] In the library, long-pressing a board without moving and then releasing shows its menu (“打开”, “重命名”, “移到文件夹”, “复制”, “导出…”, “移到回收站”). The board does not open, and no magnifier, text selection or system menu appears. Long-pressing and then moving lifts the board, which can be dropped on a folder or “回收站” (Trash) in the sidebar. Long-pressing a folder in the sidebar shows “重命名”, “在此新建白板” (New board here) and “删除文件夹” (Delete folder). If the menu is missing, the board opens, or a system menu appears, send a screen recording.
- [ ] The sidebar button at the top left of the library hides the sidebar, and a second tap shows it. In portrait the sidebar starts hidden and hides again after a row is chosen. If the sidebar covers the content or a button cannot be tapped, send one screenshot in landscape and one in portrait.
- [ ] After tapping a button, menu item or card and lifting the finger, it does not stay highlighted as if still pressed.
- [ ] Connection status at the top right: blue while connected to the Mac, and a tap shows “已连接” (Connected) with the Mac's address, with no colored dot. After quitting the app on the Mac, the icon turns gray and a tap shows “未连接” (Not connected); it recovers when the app reopens. The shell also shows a small “换一台 Mac” (Switch Mac) button, and board settings do not show it a second time. The buttons in the corner have no circle behind them while their panel is open and dim while pressed. Opening, closing and reopening the panel at a normal pace does not open the diagnostics panel; three quick taps do. If the diagnostics panel opens by accident, say how fast the taps were.

### Text input

CI browsers do not reproduce iPadOS problems where a text field does not accept typing. Check these items only on a real device.

- [ ] Library: the search field accepts typing in Chinese and English and filters the results. Long-pressing a board and choosing “重命名” (Rename) lets you type a name. The text fields for creating a folder (“+” in the sidebar) and renaming one accept typing.
- [ ] While a board rename is in progress on the iPad, rename another board on the Mac. The iPad keyboard stays open and the typed text is kept.
- [ ] iOS does not autocorrect or capitalize the first letter while renaming.
- [ ] When creating a folder in the sidebar, the keyboard does not cover the name field. After the keyboard closes, the layout and the tool picker position are correct.

### Embedded pad

Copy `examples/apps/demo/` into `apps/` in the storage directory first.

- [ ] In the iPad shell, open `/apps/demo/`. The diagnostics panel is not available there, but fast Pencil strokes are as smooth as in the whiteboard.
- [ ] The answer field accepts typing, and the tool buttons work.
- [ ] Strokes written in the demo appear on the Mac after opening “示例 第 1 题” under “应用” (Apps) in the library sidebar, with the question image under them.
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

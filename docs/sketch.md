English | [简体中文](sketch.zh-CN.md)

# Sketch

Write freely with Apple Pencil on the iPad, then copy what you wrote as a PNG image from the sketch that pops up from the browser toolbar on the Mac, and paste it into another web page, for example Claude's message box. Sketches are not stored: after the sketch page closes, its content is discarded and the iPad returns to the board it was on.

```
iPad shell (Apple Pencil)
      │  whiteboard sync
Whiteboard app on the Mac (127.0.0.1:8848)
      │  page /sketch
Toolbar popup sketch (extension extensions/sketch) ── copy a PNG ──> other web pages
```

## Using it

1. Run the whiteboard app on the Mac and open the whiteboard on the iPad as usual (iPad shell or Home Screen icon).
2. In Edge, press Alt+Shift+S or click the extension's toolbar icon. The sketch pops up (like any extension's popup), and the iPad and the whiteboard window on the Mac switch to a blank sketch board.
3. Write on the iPad; the popup shows it live. It always zooms to show all ink, so ink written anywhere on the iPad is visible (never zoomed in beyond 1×).
4. Click Copy (or press ⌘C), then press ⌘V in Claude's message box.
5. Click Clear for the next one. The popup closes when you click elsewhere; about 3 seconds later the sketch board is discarded and the iPad and the Mac window return to their previous board.

The popup has only two buttons, Clear and Copy; the dot at the bottom left is the connection status (green when connected). No port setting is needed: the popup tries 8848 to 8857 and remembers the one it found.

The extension is optional: opening `http://127.0.0.1:8848/sketch` in any browser works the same.

| Behavior | Details |
| --- | --- |
| Export | A PNG with a transparent background, covering the ink bounds plus a 64-unit margin, at 2× resolution, at most 4096 pixels per side. |
| Storage | The sketch board exists only in the whiteboard server's memory: nothing is written to the storage directory, no thumbnail is made, and it is not in the board list. The iPad and the browser do not cache it either. After a server restart there is no sketch board. |
| Reloading the page | Reconnecting within 3 seconds keeps the sketch. |
| Choosing another board on the Mac during a sketch | The iPad follows to that board and does not return to the sketch board; the sketch in the popup is unaffected. |
| The iPad reconnects | While the sketch is going on, the iPad returns to the sketch board; after it ended, the iPad opens its previous board. Ink written on the sketch board while offline is discarded and never lands on another board. |
| Permission | Starting a sketch requires the Manage boards permission (it makes the iPad switch boards). This machine always has it. |

## Installing the extension (Edge)

1. Download `Whiteboard-sketch-extension-<version>.zip` from the Release (for a manual build, from the build's `Whiteboard-sketch-extension` artifact) and unzip it; when running from source, use `extensions/sketch/` in the repository.
2. Open `edge://extensions` in Edge and turn on Developer mode.
3. Click Load unpacked and choose the unzipped `sketch` folder.
4. To change the shortcut, open `edge://extensions/shortcuts`.

Chrome works the same way at `chrome://extensions`.


## Implementation

| Part | Location |
| --- | --- |
| Temporary boards | inksync `Hub.create_transient` / `drop_transient`: in memory only, never written, never indexed, never unloaded. Snapshots carry `transient: true`, so front ends do not cache them. |
| Sketch session | `start_sketch` / `end_sketch` in `whiteboard/hub.py`: following connections switch to the sketch board `sketch`; it ends `SKETCH_GRACE` (3 seconds) after the sketch page's connection closes. The current-board record never changes. |
| HTTP | `GET /sketch` (page), `POST /api/sketch` (start or continue), `DELETE /api/sketch` (end); see [protocol.md](protocol.md#http-endpoints). |
| Page | `whiteboard/web/sketch.html`, `whiteboard/web/static/js/sketch.js`. After every ink change and window resize the view zooms to all ink (`fit({maxScale: 1})`). |
| Extension | `extensions/sketch/`: a Manifest V3 toolbar popup (`popup.html`) that only embeds the app's `/sketch` page and contains no pad code, so it needs no update when the app is upgraded. The `key` in the manifest fixes the extension's id. The embedded page is same-origin with the whiteboard server, so the [request source check](protocol.md#request-sources) does not get in the way. |
| Tests | `tests/test_sketch.py` (protocol, concurrency, disconnection, permissions, no files left), the sketch cases in `tests/test_browser.py` (following, copying a transparent PNG, the view following the ink, going offline in a real browser), `tests/test_extension.py` (the extension in Chromium: embedding, copying, finding the port) |

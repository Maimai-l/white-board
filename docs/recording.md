English | [简体中文](recording.zh-CN.md)

# Input Recording

Input recording captures raw Apple Pencil input on the iPad, together with the board state, and replays it on a development machine. Use it for problems that only a real pen produces: pressure that changes along a stroke, a constantly changing tilt, the same position delivered twice, and the timing of the pen lift.

## Record input

Recording is available whenever the diagnostics panel is open.

1. Tap the connection status dot in the top-left corner three times, with no more than 1.2 s between taps. The diagnostics panel opens, and a “录制输入” (Record input) cell appears in the bottom-left corner.
2. Tap “录制输入”. The cell shows the number of recorded events (“N 条”).
3. Perform the actions that show the problem.
4. Tap “停止录制” (Stop recording). The cell shows “已存到 <path>” (Saved to) or “已下载” (Downloaded).

In a desktop browser, add `?debug=1` to the URL to open the diagnostics panel.

> **Note**
> Closing the diagnostics panel while recording stops the recording and saves it.

> **Note**
> The first line of the diagnostics panel shows the running build: the version number for the packaged app, or `branch@short-commit` when running from source. Include this line in screenshots and bug reports.

The entry point is on screen because the iPad opens the board as a Web Clip installed by a configuration profile, which has no address bar for query parameters.

## Saved files

The recording is sent to the Mac and saved in the storage directory. If the upload fails, for example when the page was opened from another origin, the browser downloads the file instead.

| Destination | Path |
| --- | --- |
| Server | `<storage directory>/recordings/<YYYYMMDD-HHMMSS>[-<name>].json` |
| Browser download (fallback) | `recording-<milliseconds since epoch>.json` |

The timestamp is the Mac's local time. `<name>` is the recording's `name` field reduced to letters, digits, `-` and `_`, at most 40 characters; recordings started from the panel have no name.

### `POST /api/recording`

The endpoint stores one recording as a file.

| Item | Value |
| --- | --- |
| Request body | JSON object with an `events` array |
| Maximum size | 32 MiB (`MAX_RECORDING_BYTES`) |
| Permission | None required |
| Response | `{"ok": true, "path": "<file path>", "events": <event count>}` |
| Errors | `400` if the body is not a JSON object with an `events` array; `413` if it exceeds the size limit |
| Server log | `[录制] <path>，<N> 条事件` |

The body is written to disk unchanged.

## Replay a recording

`whiteboard.recorder.replay(data, options)` restores the board to its state at the start of the recording and feeds the recorded events back to the input controller.

```js
await whiteboard.recorder.replay(data)                   // original timing
await whiteboard.recorder.replay(data, { wait: false })  // no delays
await whiteboard.recorder.replay(data, { speed: 4 })     // four times faster
```

| Option | Default | Effect |
| --- | --- | --- |
| `wait` | `true` | Waits between events according to their `t` values. |
| `speed` | `1` | Divides the waiting time. Ignored when `wait` is `false`. |

Replay performs these steps:

1. Resets the board to `meta` and `before`, and sets the viewport to `viewport`.
2. Sets finger drawing to `fingerDraw`.
3. Switches the input source to the iPad shell when `source` is `shell`, and to the browser otherwise.
4. Sets the stroke id prefix and counter to `ids`, so that new strokes receive the same ids as in the recording.
5. Feeds each event: applies its `tool` and `viewport` if present, re-reads the canvas position, then calls the input controller (`onDown`, `onMove`, `onUp`, or `receiveShell` for shell batches).
6. Restores the original input source and id prefix. The id counter does not move backward.

The return value is the list of strokes on the board. Compare it with `after` to check that the replay reproduces the recording.

Events are passed as plain objects rather than synthetic `PointerEvent`s, because the `PointerEvent` constructor does not accept `altitudeAngle` or `azimuthAngle` and cannot provide `getCoalescedEvents()`.

### Replay on a development machine

The following Playwright snippet replays a saved file in a page that has the board open.

```python
import json

data = json.load(open("recordings/20260924-203011.json"))
result = page.evaluate(
    "async ([d]) => whiteboard.recorder.replay(d, { wait: false })", [data]
)
assert result == data["after"]
```

Tests that use replay:

| Test | Checks |
| --- | --- |
| `tests/test_browser.py::test_recorder_replays_an_erase_exactly` | A recorded pixel erase replays to exactly `after`. |
| `tests/test_browser.py::test_recorder_panel_rides_the_diagnostics_toggle` | The recording cell appears with the diagnostics panel. |
| `tests/test_shell_input.py::test_shell_recording_replays_exactly` | A recording made in the iPad shell replays to exactly `after`. |

### Limits of replay

Replay reproduces the path from input to strokes and masks; it does not reproduce the network.

> **Warning**
> Replay writes `before` directly into local state, so the server does not know those strokes. It discards the `mask` operations sent during replay, and no acknowledgements return. Problems caused by acknowledgements, such as an echoed mask overwriting newer local state, do not appear in replay.

If the replay result equals `after`, the problem lies between input and mask, and replay can be used to investigate it. If it differs, the difference comes from a part that replay does not model, such as the network path. For example, one recording erased 32 capsule chains with 237 points on the device, while replay produced 7 chains with 298 points; the 61 missing points were overwritten by acknowledgements. See [eraser.md](eraser.md#synchronization).

Replaying the same recording repeatedly produces byte-identical results, except for the random ids of new strokes.

## File format

A recording is a JSON object. Pointer event fields are stored with their original values and are not rounded, so that replayed strokes match `after` exactly.

### Top-level fields

| Field | Type | Content |
| --- | --- | --- |
| `schema` | number | Format version, currently `1` |
| `name` | string | Recording name; empty when started from the panel |
| `at` | string | Start time, ISO 8601 |
| `agent` | string | `navigator.userAgent` |
| `build` | string | Running build, as on the first line of the diagnostics panel |
| `role` | string | Client role |
| `dpr` | number | `devicePixelRatio` |
| `stage` | array | Canvas `[left, top, width, height]` in CSS px, rounded to 0.01 |
| `viewport` | object | `{scale, x, y}` at the start |
| `fingerDraw` | boolean | Whether finger drawing is on |
| `source` | string | `browser` or `shell` |
| `shell` | object or null | `{version, bridge}` when `source` is `shell` |
| `ids` | object | `{prefix, counter}` for new stroke ids |
| `before` | array | All strokes on the board at the start |
| `meta` | object | Board metadata at the start |
| `events` | array | Recorded events, see below |
| `after` | array | All strokes on the board at the end |
| `viewportAtEnd` | object | `{scale, x, y}` at the end |

### Pointer events

Pointer events are captured on the board stage (`#stage`) in the capture phase, before the input controller receives them. The recorded types are `pointerdown`, `pointermove`, `pointerup` and `pointercancel`.

| Field | Source | Present |
| --- | --- | --- |
| `type` | Event type | Always |
| `t` | Milliseconds since the start, rounded to 0.01 | Always |
| `id` | `pointerId` | Always |
| `pt` | `pointerType` | Always |
| `btn` | `button` | Always |
| `btns` | `buttons` | Always |
| `x`, `y` | `clientX`, `clientY` | Always |
| `p` | `pressure` | Always |
| `tx`, `ty` | `tiltX`, `tiltY` | When non-zero |
| `alt` | `altitudeAngle` (radians) | When provided |
| `az` | `azimuthAngle` (radians) | When provided |
| `tw` | `twist` | When non-zero |
| `primary` | `false` for a non-primary pointer | When not primary |
| `tool` | Current tool settings | On `pointerdown` |
| `viewport` | `{scale, x, y}` | When the viewport changed since the previous event |
| `c` | Samples from `getCoalescedEvents()`, each with `x`, `y`, `p`, `tx`, `ty`, `alt`, `az`, `tw` | On `pointermove`, when there is more than one sample |

The viewport is recorded on every change, not only at pen-down, so that strokes after a pan or zoom replay at the correct world position.

### Shell batches

In the iPad shell, Apple Pencil input arrives from the shell instead of from pointer events. Each batch is recorded unchanged.

```json
{"type": "shell", "t": 1234.56, "batch": { ... }}
```

| Field | Content | Present |
| --- | --- | --- |
| `type` | `"shell"` | Always |
| `t` | Milliseconds since the start, rounded to 0.01 | Always |
| `batch` | The object defined in [ipad-shell.md](ipad-shell.md) section 5.1, with all fields and values unchanged | Always |
| `tool` | Current tool settings | When the batch contains a sample with `ph` = `down` |
| `viewport` | `{scale, x, y}` | When the viewport changed |

Safari's own pen events are also recorded during this time. When a shell recording is replayed, the input source is switched to the shell, so these events do not draw, as on the device.

## Pen sample rate

The diagnostics panel shows the pen sample rate; read it before tuning input processing.

```
帧 60fps  最长 18ms
笔事件 122/s  新位置 64/s  合并 1
```

| Value | Meaning |
| --- | --- |
| `帧` (Frames) | Frame rate and longest frame interval |
| `笔事件` (Pen events) | Pen pointer events per second |
| `新位置` (New positions) | Pen events per second whose position differs from the previous event |
| `合并` (Coalesced) | Number of samples returned by `getCoalescedEvents()` for the latest drawing move event |

The effective rate is the number of new positions. The remaining events repeat the previous position, and most of them also repeat its pressure and tilt, so they carry no new information.

Measured on iPad (eight recordings and panel readings):

| Measurement | Result |
| --- | --- |
| Pen events during a stroke | About 120–125 per second |
| New positions | About 64 per second, about one per frame |
| Repeated events identical in every field | 80%–100% of the events without a new position also have the same pressure and tilt (100% in the latest recording) |
| Effect of board size | None: 1 stroke and 692 strokes give the same rate |
| Coordinate resolution | 100% of `clientX` / `clientY` values are integers (1 CSS px) |
| `getCoalescedEvents()` | Never returned more than one sample; no recorded event has a `c` field |
| `pointerrawupdate` | Not supported by Safari |

- MDN lists `getCoalescedEvents()` as limited availability, and discussion on the Apple Developer Forums (last post November 2023) states that Mobile Safari does not implement it.
- At a writing speed of 1.6 px/ms, consecutive new positions are 26 screen px apart, while the pen line is 13 px wide.
- Smoothing is therefore tuned for 60 Hz input. The `streamline` option of `perfect-freehand` smooths pen positions when the outline is built, so the input layer stores pen positions without additional smoothing.

The following settings do not change the number of new positions (about 64 per second):

| Setting | Result |
| --- | --- |
| `{ desynchronized: true }` on the live canvas | No change in rate; causes flicker while writing, because `drawLive` clears and redraws the live stroke each frame. Not used. |
| Settings → Apple Pencil → Scribble off | No change |
| Settings → Apps → Safari → Advanced → Feature Flags → “Prefer Page Rendering Updates near 60fps” off, then force-quit and reopen | No change. The board runs as a Web Clip, and it is not documented whether this Safari flag applies to it. |
| Low Power Mode off | No change |

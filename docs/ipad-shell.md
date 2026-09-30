English | [简体中文](ipad-shell.zh-CN.md)

# iPad Shell

The iPad shell is an optional native iPad app. It loads the board page from the Mac in a WKWebView and forwards UIKit Apple Pencil samples to that page.

This document specifies phase 1 of the shell: its features, the interface between the shell and the web page, the build and distribution process, and the acceptance criteria. For phase 2 it lists only the start condition and the basic requirements.

## 1. Goals

On iPad, the Apple Pencil input that the web page receives has the same quality as the input of a native app. Other devices continue to use the browser, with no change in features.

### 1.1 Input quality in Safari and in UIKit

Safari delivers fewer, coarser Pencil samples than UIKit on the same iPad.

| Property | Safari (web page) | Native (UIKit) |
|---|---|---|
| Samples per second | about 120 | 240 to 244 |
| New (non-repeated) positions per second | about 60 | 147 to 240 |
| Coordinate precision | integer CSS pixels | fractional, from `preciseLocation(in:)` |
| Timestamp precision | integer milliseconds | `UITouch.timestamp`, seconds, fractional |
| Predicted samples | none | `predictedTouches(for:)` |

Data sources: Safari figures come from the board recording `20260927-181756.json`; native figures come from four InkProbe sessions (`20260924-*.zip` in the repository root). Both were taken on the same iPad.

On the same Safari input, the smoothing algorithms of Google Ink, ink-stroke-modeler, Xournal++, Krita and the current implementation produce no visible difference, either in still images or in replay. Handwriting quality is therefore limited by the input data, not by the smoothing algorithm.

### 1.2 Principles

The shell provides input and platform services only; it contains no board logic. Stroke geometry, synchronization, erasing and the user interface all stay in the web page.

| Consequence | Detail |
|---|---|
| Safari stays fully functional | Safari access keeps every feature; only the input quality is lower. |
| Most changes need no reinstall | Everything except the shell's native code ships with the Mac app update. The shell loads the new web code on its next launch. |

## 2. Prerequisites

The shell is installed with TrollStore and runs on a fixed range of iPadOS versions.

| Item | Requirement |
|---|---|
| Installation | Unsigned IPA installed with TrollStore. No Apple Developer account is needed. |
| Supported iPadOS (TrollStore) | 14.0 beta 2 to 16.6.1, 16.7 RC (20H18), and 17.0. Other 16.7.x releases and 17.0.1 or later are not supported. |
| Target iPad | Runs Safari 15.6, which is within the supported range. |
| Deployment target | iPadOS 15.0, the same as InkProbe (`ipad/project.yml`). |
| Device family | iPad only (`TARGETED_DEVICE_FAMILY = 2`). |

> **Warning**
> Do not upgrade the system on the target iPad. After an upgrade outside the supported range, new versions of the shell cannot be installed.

## 3. Architecture

The shell, the web page, the Mac server and CI each have one responsibility.

| Part | Responsibility |
|---|---|
| Shell (Swift, `ipad/`) | Loads the board page from the Mac in a WKWebView; captures Pencil touches and forwards them to the page; suppresses system gestures that interfere with writing; checks for and installs shell updates. |
| Web page | Uses the samples forwarded by the shell as the Pencil input source; all other behavior is unchanged. |
| Mac server | Registers the Bonjour service; serves the install page, the shell version information and the IPA. |
| GitHub Actions | Builds the IPA and bundles it into the Mac app. |

Finger input does not pass through the shell. The web page handles it with pointer events, and the existing logic for panning, zooming, palm rejection and the finger drawing switch is unchanged.

The shell project is generated with XcodeGen, in the same way as InkProbe:

```sh
python3 ipad/make_icon.py        # generates the icon (the repository stores no binary files)
cd ipad && xcodegen generate
```

## 4. Shell features

### 4.1 Connecting to the Mac and loading the page

The shell finds the Mac without any manual entry of host name or port. It tries the following methods in order.

| Step | Method | Behavior |
|---|---|---|
| 1 | Saved address | If an address is saved on the device, the shell loads it directly. If loading fails (for example, the Mac's port moved to the next free port), the shell continues with step 2. |
| 2 | Bonjour discovery | The shell browses for `_whiteboard._tcp` with `NWBrowser` (registration: section 8.4). One Mac found: the shell waits 1 s for further Macs, then connects. Several Macs found: the shell lists their names for the user to choose. No Mac within 5 s: the shell shows the instructions for step 3. |
| 3 | One-tap link | The shell registers the URL scheme `whiteboard-shell`. On the install page (section 8.2) in Safari, “打开外壳” (Open shell) opens `whiteboard-shell://connect?host=<host>.local&port=<port>`. The shell saves the address and connects. This step covers networks where the router blocks Bonjour. |

The shell saves an address after the page finishes loading and uses it on the next launch. The host name accepts only letters, digits, `.` and `-`; the port must be 1 to 65535.

There are two ways to switch to another Mac:

| Entry point | Behavior |
|---|---|
| iOS Settings app, shell page, switch “重新查找 Mac” (Find Mac again) | On the next launch or return to the foreground, the shell forgets the saved address, resets the switch and starts Bonjour discovery. |
| Board page, “白板设置” (Board settings), group “Mac”, button “换一台 Mac” (Switch Mac) | The page sends `rediscover` to the shell (section 5.2). |

When the user asks to switch, the shell lists the Macs found even if there is only one, so that it does not reconnect to the same Mac automatically. While the board page is still loaded, the list also has a “取消” (Cancel) button.

The Settings app page of the shell also shows “版本” (Version) and “当前 Mac” (Current Mac; “未连接” when no Mac is saved).

#### Choosing a service by name

Besides the whiteboard, other projects can register the same Bonjour service (section 8.4), for example an exercise app named `qb`. Each service carries a name in the TXT field `source`; the whiteboard's name is `whiteboard`.

| Entry point | Behavior |
|---|---|
| iOS Settings app, shell page, field “来源” (Source) | `@qb` or `qb`: the shell connects only to services named `qb`. Empty: any service, as before. A saved address with another name is not used; on return to the foreground, the shell searches again if the current service has another name. |
| `whiteboard-shell://open?source=qb` | Sets “来源” to `@qb`, forgets the saved address and searches. |

| Services found | Result |
|---|---|
| One with the wanted name | Connects after 1 s, as above. |
| Several with the wanted name | Lists them. |
| None with the wanted name after 5 s, but others found | Lists all services found, so that the user can pick one. |
| None at all | Shows the step 3 instructions. |

Names follow `^[a-z0-9-]{1,32}$`; a leading `@` is ignored. Services from other projects are listed as `@<name> · <computer name>（<host>:<port>）`.

Page loading:

- The page URL is `http://<host>.local:<port><path>`, where `path` comes from the TXT record and defaults to `/?role=ipad`. `role=ipad` makes the whiteboard server return the iPad interface (`detect_role` in `packages/inksync/inksync/ws.py`, used by `whiteboard/server.py`). The host and port come from the TXT record of the Bonjour service (section 8.4).
- Each load ignores the local cache and times out after 8 s.
- When loading fails, the shell shows the error and two buttons, “重试” (Retry) and “重新查找 Mac” (Find Mac again).
- When a new navigation starts, the shell stops sending samples until the page completes the handshake again (section 5.2).
- When the web content process terminates, the shell reloads the page.
- Links with a scheme other than `http`, `https`, `about`, `blob` or `data` open in the system. `window.open` opens the URL in Safari.
- On iPadOS 16.4 or later, the WKWebView is inspectable (`isInspectable = true`).

`Info.plist` contains:

| Key | Value | Purpose |
|---|---|---|
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `true` | Allows loading http addresses on the local network. |
| `NSLocalNetworkUsageDescription` | description text | Required for local network access since iPadOS 14. |
| `NSBonjourServices` | `["_whiteboard._tcp"]` | Required for Bonjour browsing since iPadOS 14. |
| `CFBundleURLTypes` | scheme `whiteboard-shell` | One-tap link (step 3). |
| `LSApplicationQueriesSchemes` | `["apple-magnifier"]` | TrollStore install URL (section 8.3). |
| `UIRequiresFullScreen`, `UIStatusBarHidden` | `true` | Full-screen app without status bar. |

The WKWebView fills the screen; the page does not scroll or zoom:

```swift
scrollView.isScrollEnabled = false
scrollView.bounces = false
scrollView.minimumZoomScale = 1
scrollView.maximumZoomScale = 1
scrollView.contentInsetAdjustmentBehavior = .never
```

### 4.1.1 Exporting files

The shell handles export requests as downloads and hands the file to the system share sheet.

By default, WKWebView treats a link with a `download` attribute, or a `data:` or export URL assigned to `location.href`, as a navigation. The navigation replaces the board page, the WebSocket closes, and no file is saved. The shell therefore applies these rules:

| Condition | Decision |
|---|---|
| `navigationAction.shouldPerformDownload` is true | `.download` |
| Response header `Content-Disposition` contains `attachment` (document board export) | `.download` |
| `navigationResponse.canShowMIMEType` is false | `.download` |

`WKDownloadDelegate` saves each file in its own temporary directory `export-<UUID>`, so files with the same name do not overwrite each other. An empty suggested file name becomes `whiteboard`. After the download finishes, the shell presents `UIActivityViewController`, where the user saves the file to Files or shares it. A failed download shows the alert “导出失败” (Export failed).

Requirements for the web page: every download uses a link with a `download` attribute (`downloadURL` in `exporter.js`). PNG export converts the `data:` URL to a `blob:` URL before downloading.

### 4.2 Capturing Pencil input

The shell reads Pencil touches in the window's `sendEvent(_:)`, before any gesture recognizer sees them.

> **Note**
> A gesture recognizer attached to the WKWebView does not receive complete strokes: after the page calls `preventDefault`, WebKit's internal recognizers make it fail. See section 12, Q2.

Components (`ipad/Whiteboard/PencilCapture.swift`):

| Component | Role |
|---|---|
| `ShellWindow` (`UIWindow` subclass) | Overrides `sendEvent(_:)` and passes every event to `PencilTracker` before calling `super`. |
| `PencilTracker` | Reads Pencil touches, encodes the samples (section 5.1) and emits one batch per event. |
| `EstimateRecognizer` | Receives `touchesEstimatedPropertiesUpdated` only. Attached to the WKWebView's superview, not to the WKWebView. |

Sample capture rules:

- Only touches with `type == .pencil` are read.
- A touch is tracked only if it begins on the WKWebView or one of its subviews. Touches on the status overlay or alerts are ignored.
- Touch IDs are assigned by the shell, start at 1 and increase by 1 per touch.
- Real samples come from `event.coalescedTouches(for:)`:

  | Touch phase | Phases emitted |
  |---|---|
  | `began` | first sample `down`, the rest `move` |
  | `moved` | all samples `move` |
  | `ended` / `cancelled` | last sample `up` / `cancel`, the rest `move` |
  | `stationary` | no samples |

- Predicted samples come from `event.predictedTouches(for:)`. The most recent prediction is sent with every batch, including batches that carry only updates; it becomes `null` when no Pencil touch is active.
- Coordinates use `preciseLocation(in: webView)`; azimuth uses `azimuthAngle(in: webView)`. Non-finite numbers are sent as `0`.
- The fields of each sample are listed in section 5.1.
- One batch is sent per touch event and per estimate update callback. There is no fixed send interval. Empty batches are not sent.
- Batches are sent only while the handshake is active (section 5.2).

`EstimateRecognizer` settings:

| Setting | Value |
|---|---|
| `allowedTouchTypes` | `[.pencil]` |
| `cancelsTouchesInView`, `delaysTouchesBegan`, `delaysTouchesEnded` | `false` |
| `shouldRecognizeSimultaneouslyWith` | `true` for every recognizer |
| `shouldRequireFailureOf`, `canPrevent`, `canBePrevented` | `false` |
| State | Never recognizes; fails when all its touches end or are cancelled. |

If `EstimateRecognizer` fails early, only the force correction is lost; writing is not affected. The InkProbe file `TouchLogger.swift` implements the same reading logic.

### 4.3 Suppressing system gestures

The shell turns off system behaviors that interfere with writing, in addition to the web page's own measures.

The web page keeps its existing measures (`touch-action: none`, `preventDefault` on `touchstart`, and so on). The shell adds:

| Behavior | Measure |
|---|---|
| Scribble (handwriting to text in editable areas, iPadOS 14 and later) | `UIScribbleInteraction` on the WKWebView; `scribbleInteraction(_:shouldBeginAt:)` returns `false`. |
| Long-press menu and link preview | `allowsLinkPreview = false` |
| Data detectors | `dataDetectorTypes = []` |
| Back/forward swipe | `allowsBackForwardNavigationGestures = false` |
| Screen-edge system gestures | `preferredScreenEdgesDeferringSystemGestures = .all`: an edge swipe must be repeated before the system acts on it. |
| Status bar and home indicator | Hidden. |

Whether these measures are sufficient is checked with the items in section 9.2 (section 12, Q5).

### 4.4 Checking for and installing shell updates

See section 8.

## 5. Interface between the shell and the web page

The shell and the page exchange sample batches and handshake messages through `evaluateJavaScript` and a WebKit message handler.

### 5.1 Sample batches from the shell

The shell sends one batch with:

```js
window.whiteboardShell && window.whiteboardShell.receive(<JSON>)
```

Batch structure:

```json
{
  "bridge": 1,
  "samples": [
    {"id": 3, "ph": "move", "k": "real", "t": 5321.0412, "x": 412.53, "y": 688.07,
     "f": 0.3333, "fmax": 4.1667, "alt": 0.9531, "az": 2.2104, "est": ["force"], "ui": 15173}
  ],
  "pred": {"id": 3, "samples": [{"t": 5321.0495, "x": 414.10, "y": 689.52, "f": 0.34, "fmax": 4.1667, "alt": 0.95, "az": 2.21}]},
  "updates": [{"ui": 15170, "f": 0.3712, "alt": 0.9520, "az": 2.2110}]
}
```

| Field | Meaning |
|---|---|
| `bridge` | Interface version (section 6). |
| `id` | Touch ID. Constant from pen down to pen up; assigned by the shell, starting at 1. |
| `ph` | `down`, `move`, `up` or `cancel`. |
| `k` | Always `real` (real sample). Predicted samples are in `pred`, never in `samples`. |
| `t` | `UITouch.timestamp`, in seconds. |
| `x`, `y` | `preciseLocation(in: webView)`, in points. With page zoom 1 and no scrolling, equal to the page's `clientX`, `clientY`. |
| `f`, `fmax` | `force`, `maximumPossibleForce`. |
| `alt` | `altitudeAngle`, in radians. 0 means the pen lies flat on the screen; π/2 means the pen is vertical. |
| `az` | `azimuthAngle(in: webView)`, in radians. |
| `est` | Names from `estimatedPropertiesExpectingUpdates`: any of `force`, `azimuth`, `altitude`, `location`. May be empty. |
| `ui` | `estimationUpdateIndex`; `null` when `est` is empty. |
| `pred` | Predicted samples for this batch, as `{id, samples}`; each sample has `t`, `x`, `y`, `f`, `fmax`, `alt`, `az`. Each batch replaces the previous prediction completely. `null` when there is no prediction. |
| `updates` | Estimated property updates received for this batch, each `{ui, f, alt, az}`. `ui` matches a previously sent sample. |

> **Note**
> The web page itself can create batches in the same format when a 0.9.43 shell stops delivering samples (`shell-fallback.js`, section 12, Q2). Such batches carry `fallback: true` or `orphan: true`, and their samples use `k: "safari"`. The shell never sends these fields. They appear in recordings.

### 5.2 Messages from the web page to the shell

The page sends messages with `window.webkit.messageHandlers.whiteboard.postMessage(<object>)`.

| Message | When | Shell action |
|---|---|---|
| `{"type": "hello", "bridge": [min, max]}` | On page startup (`connectShell` in `shell.js`) | Replies with the handshake result. |
| `{"type": "rediscover"}` | The user taps “换一台 Mac” (Switch Mac) in “白板设置” (Board settings) | Searches again and lists the Macs on the network (section 4.1). No reply. |

The shell replies to `hello` with:

```js
window.whiteboardShell && window.whiteboardShell.hello({"shellVersion": "1.3.0", "bridge": 1, "active": true})
```

| `active` | Behavior |
|---|---|
| `true` | The shell starts sending samples. |
| `false` | The shell sends no samples; the page keeps using Safari's pointer events. |

The page switches to the shell source only if `active` is `true` and the reported `bridge` is within its own range. It then sets `document.documentElement.dataset.shell` to `active` (otherwise `inactive`). Outside the shell, `window.webkit.messageHandlers.whiteboard` does not exist and the page does nothing.

### 5.3 Time

The page uses `t` only for time differences between samples of the same stroke and never compares `t` with `performance.now()`.

Logic that depends on the current time (for example `PALM_GRACE`, which rejects palm touches for 500 ms after pen up) uses `performance.now()` at the moment the batch is received.

## 6. Interface version negotiation

The shell and the page agree on an integer interface version, `bridge`, during the handshake.

| Rule | Detail |
|---|---|
| Version number | `bridge` is an integer, currently `1` (`PencilTracker.bridge`). It increases by 1 only for an incompatible change to the interface format. |
| Shell version | Equal to the Mac version; both come from the same git tag. |
| Supported range | The page declares `[min, max]`: `SHELL_BRIDGE = [1, 1]` in `input.js`. `BRIDGE` in `whiteboard/ipadshell.py` must be equal (`tests/test_ipadshell.py::test_bridge_range_matches_the_web_page`); `/ipad/version` reports it. |
| In range | The shell replies `active: true`. |
| Out of range | The shell replies `active: false`, shows the alert “外壳版本与白板版本不兼容，请更新外壳” (Shell and board versions are incompatible; update the shell) once per run, and runs the update check (section 8.3). |
| Compatibility window | When the page raises `max`, it keeps support for the previous version until the next release. A shell that is one release behind the Mac therefore continues to work. |

## 7. Web page changes

### 7.1 Input source

`input.js` converts shell samples into pointer-like events, which then follow the same path as Safari's pointer events (`onDown`, `onMove`, `onUp`, `addSample`, `startErase`, and so on).

Conversion (`shellEvent` in `input.js`):

| Event property | Value |
|---|---|
| `clientX`, `clientY` | `x`, `y` |
| `pressure` | `f / fmax`, clamped to 0–1; 0 when `fmax` is not positive |
| `tiltX`, `tiltY` | 0 (`penAltitude` then reads `altitudeAngle`) |
| `altitudeAngle` | `alt`; π/2 when missing |
| `azimuthAngle` | `az` |
| `timeStamp` | `t × 1000` (milliseconds, fractional) |
| `pointerId` | `1000000 + id` (kept apart from browser pointer IDs) |
| `pointerType` | `pen` |
| `fromShell` | `true` |

Rules:

- While the shell is `active`, Safari's `pointerType === "pen"` events on `#stage` do not draw. They still update the palm rejection timer, provide the reference for the coordinate deviation (section 7.4), and drive the fallback for 0.9.43 shells (section 12, Q2). Finger and mouse events are unchanged.
- On `down`, the page checks the point with `document.elementFromPoint`. If the element is not inside `#stage`, the whole stroke is ignored and the control handles its own click events. The Pencil can therefore tap toolbar and tool picker buttons.
- Predicted samples are drawn on the live layer only. They are never stored in the stroke or sent to other devices.
- In the shell the page is treated as an iPad: the tool picker is shown, and the “白板设置” (Board settings) button is always present, even without the settings permission, because it contains “换一台 Mac” (Switch Mac).

### 7.2 Pressure

Shell pressure is `f / fmax`, without conversion.

In the four InkProbe sessions, the median of `force / maximumPossibleForce` is 0.017 to 0.084 and the 95th percentile is 0.129 to 0.398. Safari recordings report pressure between 0.007 and 0.125. The two are of the same magnitude, so Safari's pressure is assumed to be `force / maximumPossibleForce` (section 12, Q1).

The four sessions are eraser tests, not normal writing, so the assumption requires verification: write the same content in InkProbe and in the board (Safari) on the same iPad and compare the pressure distributions.

| Result | Action |
|---|---|
| Assumption holds | No conversion. The stroke format and the pressure curve (`PEN_KNEE`, `PEN_FLOOR`, `PEN_GAMMA` in `stroke.js`) stay unchanged. |
| Assumption fails | The input layer converts `f / fmax` to Safari's range before storing it. The stroke format stays unchanged; the conversion is derived from the comparison. |

Estimated property updates (phase 1):

- An update is applied only if its sample belongs to the stroke that is still being written. The page recomputes the point's pressure from the new `f` and `alt`, with the same tilt adjustment and smoothing as `addSample`.
- Updates that arrive after the stroke is committed are discarded and counted in the diagnostics line “抬笔后丢弃” (Discarded after lift).
- The table of pending updates is cleared when it exceeds 4096 entries.

Statistics from the four InkProbe sessions (80 Pencil strokes; 19,139 samples expecting updates, all of which received an update):

| Session | Updates | Arriving after pen up | Share | Update delay, median / 95th percentile | Force change of late updates (median `Δf / fmax`) |
|---|---|---|---|---|---|
| test1-calibr | 3862 | 128 | 3.3% | 25.4 / 39.7 ms | −0.029 |
| test2-calibr | 3388 | 203 | 6.0% | 26.0 / 39.6 ms | −0.020 |
| test3-drag | 7191 | 101 | 1.4% | 25.3 / 39.0 ms | −0.090 |
| test4-contrast | 4698 | 43 | 0.9% | 26.1 / 41.3 ms | −0.016 |

In total, 475 updates (2.5%) arrive after pen up. In every stroke they belong to the last 6 or so samples (about 25 ms), and all of them reduce the force. Discarding them leaves the last few points of a stroke 0.02 to 0.09 (in `f / fmax`) too high. `trimSettledTail` in the input layer usually removes the settled end points of a stroke, so the remaining effect is a slightly thicker stroke end.

### 7.3 Recording

Recordings store shell batches unchanged, so that a replay reproduces the stroke exactly.

| Item | Format |
|---|---|
| Top-level `source` | `browser` or `shell` |
| Top-level `shell` | `{version, bridge}` when `source` is `shell`; otherwise `null` |
| Shell batch entry | `{"type": "shell", "t": ..., "batch": {...}}`; `batch` is the object from section 5.1 with all fields and original values, without rounding |

Safari pen events recorded alongside do not draw during replay. Replaying a shell recording passes each batch to the input source layer (`receiveShell`); the result must equal the recording's `after`. Tests: `tests/test_shell_input.py::test_shell_recording_replays_exactly`, following `tests/test_browser.py::test_recorder_replays_an_erase_exactly`. Recording format: [recording.md](recording.md).

### 7.4 Diagnostics panel

The diagnostics panel (`perf.js`) shows the input source and, in the shell, the shell's sample rates and deviation counters.

| Line | Content | Shown |
|---|---|---|
| `输入来源 <source>  外壳 <version>  bridge <n>` | Input source (`browser` or `shell`), shell version and `bridge` | Always; version part only when known |
| `外壳 <n>/s  新位置 <n>/s` | Shell samples per second and new positions per second, computed like `penHz` and `penMoveHz` | Shell version known or source `shell` |
| `坐标偏差 <d>px（对上 <n> 个）  力度更新 <n>  抬笔后丢弃 <n>` | Coordinate deviation and matched samples; applied estimate updates; updates discarded after lift | Same |
| `外壳断笔 <n>  Safari 补点 <n>` | Strokes closed by the page because the shell sent no `up`; samples filled in from Safari events. Both are 0 in normal operation. | Same |

Coordinate deviation compares shell samples with Safari pen events of the same stroke; Safari pen events keep arriving while the shell is `active`:

- The two time bases are aligned at pen down, because the first sample on both sides is the same `UITouch`.
- For each Safari event, the nearest shell sample within ±25 ms (`SHELL_MATCH_MS`) is found. The deviation is the largest `|Δx|` or `|Δy|` over the stroke.
- At most 512 samples per stroke are kept for the comparison (`SHELL_TRACE`).
- Rounding alone accounts for at most 0.5 px; a larger value indicates misaligned coordinate systems.

Samples filled in from Safari events are not counted in the shell sample rate.

## 8. Build, distribution and updates

### 8.1 Building the IPA in CI

`.github/workflows/build-macos.yml` builds the IPA, bundles it into the Mac app and publishes both.

| Job | Runner | Steps |
|---|---|---|
| `ipad` | `macos-26` | Determine version; `brew install xcodegen ldid`; `python3 ipad/make_icon.py`; `xcodegen generate`; `xcodebuild -sdk iphoneos -configuration Release` without code signing; `ldid -S` pseudo-signing; zip `Payload/` into `Whiteboard-<version>-ipad.ipa`; upload artifact `Whiteboard-ipad`. |
| `build` | `macos-14` (arm64), needs `ipad` | Download the IPA to `packaging/ipad/Whiteboard.ipa`; PyInstaller (`packaging/whiteboard.spec`) places it in the app resources at `whiteboard/ipad/Whiteboard.ipa`; smoke test with `packaging/smoke_ipad.py`. |
| `release` | `ubuntu-latest`, needs `ipad` and `build`, tags only | Publishes the Mac zip and the IPA in the GitHub Release. The IPA is also a separate asset for the first installation. Tags containing `-` are published as prereleases. |

Version numbers:

| Value | Source |
|---|---|
| Version | Tag `v1.2.3` → `1.2.3`; manual runs use the `version` input (default `0.0.0-dev`); other runs use `0.0.0-dev`. |
| `CFBundleShortVersionString` | The numeric part of the version: `1.0.0-rc.1` → `1.0.0`, `0.0.0-dev` → `0.0.0`. Passed as `MARKETING_VERSION`. |
| `CFBundleVersion` | `github.run_number` |

Triggers of `build-macos.yml`: tags `v*` (build and release); pull requests that change `whiteboard/**`, `packaging/**`, `ipad/**`, `run.py`, `requirements.txt` or the workflow (build only); `workflow_dispatch` (build only, no release).

`.github/workflows/ipad-check.yml` compiles the shell without producing an IPA. It runs on pushes to any branch that change `ipad/**` or the workflow, and on pull requests that change `ipad/**`.

A local PyInstaller build without `packaging/ipad/Whiteboard.ipa` still succeeds; the app then contains no shell and the install page links to the Release.

### 8.2 First installation

The install page `GET /ipad` lets an iPad install the shell with TrollStore and pass the Mac's address to it. It requires no permission.

| Element | Content |
|---|---|
| Header | Version and `<host>:<port>` |
| “安装白板外壳” (Install shell) | Link to `apple-magnifier://install?url=http://<host>.local:<port>/ipad/Whiteboard.ipa`, installed by TrollStore. A note says to enable URL Scheme in TrollStore settings if nothing happens. |
| No bundled IPA (running from source) | Instead of the button: “这个版本没有附带外壳，请从 Release 下载” (This version has no bundled shell; download it from the Release) and a link to `https://github.com/<repo>/releases`. |
| “打开外壳” (Open shell) | Link to `whiteboard-shell://connect?host=<host>.local&port=<port>`, which passes this Mac's address to the shell (section 4.1, step 3). |

The host is the `host` query parameter if present, otherwise `netinfo.local_hostname()`, the same value as for `/profile.mobileconfig`. The port is the server's actual port.

The iPad reaches the install page in the same way as the configuration profile: the “连接 iPad” (Connect iPad) card in the Mac window shows `外壳安装页 <url>ipad` next to the profile download button. The configuration profile remains available for iPads without TrollStore.

### 8.3 Updates

The shell compares its own version with the IPA bundled in the Mac app and offers to install a newer one.

Server endpoints (no permission required, `Cache-Control: no-store`):

| Endpoint | Response |
|---|---|
| `GET /ipad/version` | `{"version": "1.3.0", "ipa": true, "bridge": [1, 1]}`. `ipa` is `false` when running from source. |
| `GET /ipad/Whiteboard.ipa` | The bundled IPA (`application/octet-stream`, `Content-Disposition: attachment`). 404 “这个版本没有附带外壳” when there is none. |

Update check:

- The shell requests `/ipad/version` after each page load, on each return to the foreground while a page is loaded, and after an incompatible handshake. The request ignores the cache and times out after 5 s.
- If `ipa` is `true` and `version` is newer than the shell's version, the shell shows “有新版本 X，是否更新？” (New version X available. Update?) with the buttons “更新” (Update) and “以后再说” (Later).
- “更新” opens `apple-magnifier://install?url=http://<host>.local:<port>/ipad/Whiteboard.ipa`; TrollStore downloads and installs the IPA. This URL install interface exists since TrollStore 1.3 and requires URL Scheme to be enabled in TrollStore settings.
- “以后再说” suppresses the prompt for the rest of the current run.

Version comparison (`ShellVersion` in `MacAddress.swift`):

| Rule | Example |
|---|---|
| A leading `v` or `V` is removed. | `v1.2.0` equals `1.2.0` |
| Everything from the first `-` is ignored (prerelease suffix). | `1.0.0-rc.1` equals `1.0.0`; no prompt |
| Dot-separated parts are compared as numbers; non-digit characters after the digits of a part are ignored; missing parts count as 0. | `1.10.0` is newer than `1.9.3`; `1.2` equals `1.2.0` |

> **Note**
> The shell's own version (`CFBundleShortVersionString`) contains digits only. Ignoring the prerelease suffix prevents a prerelease Mac app from offering its own shell version as an update on every launch.

Most changes need no shell update: the shell loads the page from the Mac, so the iPad uses the new web code on its next launch. The shell needs an update only when its native code or the `bridge` version changes.

### 8.4 Bonjour registration on the Mac

The server registers the `_whiteboard._tcp` service so that the shell can find the Mac without manual input.

| Item | Behavior |
|---|---|
| Default | Registered on macOS only (`netinfo.bonjour_default()`). `--no-bonjour` disables it. |
| Timing | Registered in a background task after the port is fixed; deregistered when the server stops. |
| macOS | `DNSServiceRegister` from libSystem through `ctypes`, handled by the system mDNSResponder. The callback is NULL, so no run loop is needed and registration works in `--headless` mode. Registration times out after 5 s. |
| Other platforms | zeroconf (`MDNSAdvertiser`). |
| Service name | The `name` field below. |
| Failure | Logged only; the server starts normally. The shell can connect through the install page instead. |

TXT record fields:

| Field | Value |
|---|---|
| `host` | The Mac's `.local` host name, equal to `netinfo.local_hostname()` |
| `port` | The port the server actually listens on (after moving to the next free port, the new value) |
| `version` | Mac app version |
| `name` | The computer name shown to users (`scutil --get ComputerName`; falls back to the host name without `.local`) |
| `source` | Service name used to choose a service (section 4.1): `whiteboard` for the whiteboard app. Absent in versions before 1.1.0; the shell treats that as `whiteboard`. |
| `path` | Page the shell opens after connecting: `/?role=ipad` for the whiteboard app. |

Other projects register the same service with `inksync.netinfo.advertise(app, port, source, path)` (see packages/inksync/README.md). It uses the same code as the whiteboard: `DNSServiceRegister` on macOS and zeroconf elsewhere.

> **Warning**
> On macOS, registration must go through the system mDNSResponder. Do not start a second mDNS responder. The zeroconf-based `MDNSAdvertiser` listens on the mDNS port itself and is therefore off by default on macOS (`netinfo.mdns_default()`).

The existing `_http._tcp` registration (`--mdns`, `--no-mdns`) is unchanged.

Verification on the Mac, in both window mode and `--headless` mode:

```sh
dns-sd -B _whiteboard._tcp                    # lists this Mac
dns-sd -L "<service name>" _whiteboard._tcp   # TXT record contains host, port, version, name, source, path
```

On the shell side, `MacDiscovery` browses with `NWBrowser` (`.bonjourWithTXTRecord`, peer-to-peer off) and takes host, port, `source` and `path` directly from the TXT record. If `name` is missing or empty, it uses the service name. Results are sorted by name.

## 9. Phase 1 acceptance criteria

### 9.1 Input data

| ID | Criterion | Check |
|---|---|---|
| A1 | New positions per second from the shell are at least 95% of the InkProbe value on the same iPad. | Diagnostics panel; write the same content in the shell and in InkProbe. |
| A2 | Shell sample coordinates are fractional. | Shell recording |
| A3 | Coordinate deviation between shell samples and Safari pen events is at most 1 CSS pixel. | Diagnostics line “坐标偏差” (Coordinate deviation) |
| A4 | Replaying a shell recording reproduces `after` exactly. | `tests/test_shell_input.py::test_shell_recording_replays_exactly` |

### 9.2 No regressions

Each item in the shell must behave as in Safari:

- Writing with pen, marker and highlighter.
- Object eraser and pixel eraser, including the pixel eraser diameter that follows the tilt.
- Finger pan and zoom, palm rejection, and the finger drawing switch.
- Tapping toolbar and tool picker buttons with the Pencil.
- No long-press menu, text selection or Scribble recognition during fast writing.
- Reconnection after a disconnect, and resynchronization after offline writing.

### 9.3 Browser version unaffected

- Without the shell, Safari behaves as before.
- All existing tests in `tests/` pass.

### 9.4 Connection

- With one Mac running the board on the local network, the shell opens the board on first launch without user action.
- With two Macs, the shell lists both; choosing one opens its board.
- After the Mac's port changes (for example, 8848 is in use and the server moves to 8849), the shell connects to the new port on its next launch.
- On a network that blocks Bonjour (simulated with `--no-bonjour` on the Mac), the “打开外壳” (Open shell) button on the install page opens the board without any text input.

### 9.5 Build, installation and updates

- After a tag is pushed, the Release contains the Mac zip and the iPad IPA, and the Mac app contains the IPA of the same version.
- Opening the install page in Safari on the iPad and tapping “安装白板外壳” (Install shell) installs the shell through TrollStore.
- When the Mac version is newer than the shell, the shell offers an update on launch; after confirmation, TrollStore installs it, and the reopened shell shows the new version.

## 10. Phase 2: native rendering of the active stroke

### 10.1 Start condition

Phase 2 starts only if, after phase 1, ink in the shell still visibly lags behind the pen.

Measurement: record writing in the shell and in InkProbe with 240 fps slow-motion video and compare the number of frames between the pen tip and the end of the ink.

### 10.2 Basic requirements

- A transparent native layer above the WKWebView draws only the stroke being written, including its prediction. On pen up, the stroke is handed to the web page for final rendering and the native layer is cleared.
- The native layer must produce the same outline as `stroke.js` in the web page; otherwise the ink shifts on pen up. This requires porting perfect-freehand and the outline computation of `stroke.js` to Swift, with point-by-point comparison. `whiteboard/freehand.py` and `tests/test_docs.py` already use this method to keep the Python port identical to the original.
- Detailed phase 2 requirements are written when phase 2 starts.

## 11. Out of scope

- Pencil double-tap and squeeze for switching tools (`UIPencilInteraction`).
- Pencil hover (requires iPadOS 16.1 or later and an iPad and Pencil that support hover).
- App Store or TestFlight distribution.
- Drawing strokes with PencilKit. Strokes must look the same on all devices, so only the web page draws them.

## 12. Questions requiring device verification

| ID | Question | Method | Impact and status |
|---|---|---|---|
| Q1 | Is Safari's pressure equal to `force / maximumPossibleForce`? | See section 7.2. | Decides whether shell pressure needs conversion. Open. |
| Q2 | Does a gesture recognizer on the WKWebView receive all Pencil touches without affecting the page's pointer events? | Print the sample counts on both sides in a shell prototype. | **Answered: no.** See below. |
| Q3 | How long does each `evaluateJavaScript` call take, and does one call per frame drop frames? | Frame interval in the diagnostics panel; the shell logs the call duration. | If too slow, send fewer batches or use another transport. Open. The shell logs the count, average and maximum duration every 5 s (`EvalStats`); read them in Console.app on the Mac. |
| Q4 | Can TrollStore download the IPA from a local http address? | Install once from the install page (section 8.2). | If not: first installation downloads the IPA in Safari and opens it with TrollStore; updates download the IPA in the shell and pass it to TrollStore through the share sheet. Open. |
| Q5 | Do the measures in section 4.3 fully prevent Scribble and the long-press menu? | Write fast, following section 9.2. | If not, another method is needed. Open. |
| Q6 | Does the installed shell keep working after a system upgrade? | Not verified by this document; read the TrollStore documentation before deciding to upgrade the iPad. | Decides whether the iPad can be upgraded. |
| Q7 | Does `NSNetService` registration need an extra run loop in headless mode? | Check with the method in section 8.4 under `--headless`. | Resolved in the implementation: registration uses `DNSServiceRegister` with a NULL callback and needs no run loop. Verification on a Mac per section 8.4 is still required. |

Q2 findings and measures:

- In the 0.9.43 recording `20260927-211825`, all three strokes received samples only for about 30 ms after pen down, followed by no `move` and no `up`. After the page calls `preventDefault`, WebKit's internal recognizer that defers other gestures makes recognizers inside the WKWebView fail.
- The shell now reads samples in the window's `sendEvent` (section 4.2). Only estimated property updates still use a gesture recognizer, attached to the WKWebView's superview.
- For 0.9.43 shells, which need to be reinstalled, the web page fills the gaps with Safari pen events (`whiteboard/web/static/js/shell-fallback.js`). The filled strokes have Safari's precision but are complete:

  | Situation | Page action |
  |---|---|
  | The shell sent no sample for a stroke 50 ms (`SHELL_STALL_MS`) after Safari's pen down | The page starts the stroke from Safari's pen events. |
  | A shell stroke received no real sample for more than 50 ms while Safari pen events continue | The page completes the stroke with Safari's pen events. |
  | No shell `up` within 150 ms (`SHELL_ORPHAN_MS`) after Safari's pen up | The page closes the stroke. |
  | A tap during which the shell sent nothing | After 150 ms the page creates the dot from Safari's down and up. |

  Batches created this way go through `receiveShell`, so they are recorded and replay identically. A current shell never triggers this code; it can be removed together with its call sites in `input.js` when all iPads run a current shell.

## 13. Phase 1 implementation map

| Part | Location |
|---|---|
| Shell (section 4) | `ipad/Whiteboard/`: `AppDelegate.swift` (window, URL scheme, foreground), `PencilCapture.swift` (4.2, 5.1), `ShellViewController.swift` (4.1, 4.1.1, 4.3, 5.2, 8.3), `StatusView.swift` (search, list, error screens), `MacDiscovery.swift` (Bonjour browsing, update check), `MacAddress.swift` (address, saved settings, version comparison), `Settings.bundle` (“重新查找 Mac”, version, current Mac), `Info.plist`; `ipad/project.yml`, `ipad/make_icon.py` |
| Web page (section 7) | `input.js` shell input section (input source, coordinate deviation, estimate updates, prediction), `shell-fallback.js` (fallback for 0.9.43 shells), `shell.js` (handshake), `recorder.js` (7.3), `perf.js` (7.4), `ui.js` (tool picker, settings button in the shell), `ui-settings.js` (“Mac” group, install page address on the Connect iPad card), `exporter.js` (4.1.1) |
| Mac server (section 8) | `whiteboard/ipadshell.py` (`/ipad`, `/ipad/version`, `/ipad/Whiteboard.ipa`), routes in `whiteboard/server.py`, `netinfo.BonjourService` (8.4), `runner.py`, `run.py` (`--no-bonjour`) |
| Build (section 8.1) | `ipad` job in `.github/workflows/build-macos.yml`; `.github/workflows/ipad-check.yml`; `packaging/whiteboard.spec`; `packaging/smoke_ipad.py` (checks that the packaged app serves the IPA and the install page) |
| Tests | `tests/test_shell_input.py` (web page, including A2 and A4), `tests/test_ipadshell.py` (install page, version endpoint, Bonjour registration, bridge range, download handling) |

Checks that require a real device: A1 and A3 in section 9.1, all of section 9.2, section 9.4, installation and updates in section 9.5, and Q1 to Q5 and Q7 in section 12.

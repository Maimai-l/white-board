English | [简体中文](format.zh-CN.md)

# Board File Format (.wbz)

A `.wbz` file stores one board. Its content is zlib-compressed UTF-8 JSON; each stroke's point list is additionally encoded in a compact binary format.

Board content is always vector data. Thumbnails and exported images are separate files and are never read back as content.

This document describes file format 2 (inksync 2.0 and later). Files in format 1 (1.x) are still read; see [Reading 1.x files](#reading-1x-files).

## Storage directory

A storage directory holds one space (inksync's `FileStorage`). The whiteboard app's storage directory is the user space; the spaces of installed apps are under its `spaces/`.

```
<storage directory>/
├── boards/<board id>.wbz        one file per board: the only source of metadata and strokes
├── index.sqlite                 index: each board's metadata, rebuildable from the board files at any time
├── space.json                   per-space data: folder list, order, current board (not rebuildable)
├── thumbs/<board id>.png        thumbnails for the board chooser (not content)
├── docs/<board id>.<ext>        originals of document boards (PDF or image), never modified
├── spaces/<app name>/           spaces of installed apps, with the same boards/, index.sqlite, space.json
├── apps/<app name>/             static files of installed apps; see embed.md
├── backups/upgrade/<...>/       data copied after a version change; see [Backups](#backups)
├── backups/locked/<id>-<time>.wbz   read-only board files copied before unlocking
├── recordings/<time>[-<name>].json  input recordings; see recording.md
└── index.v1.json                the 1.x index, renamed after migration and no longer read or written
```

| Platform | Default storage directory |
| --- | --- |
| macOS | `~/Library/Application Support/Whiteboard/boards-data` |
| Windows | `%APPDATA%\Whiteboard\boards-data` |
| Linux | `$XDG_DATA_HOME/whiteboard/boards-data` (default `~/.local/share/whiteboard/boards-data`) |

The storage directory can be changed in the Mac's board settings, or set for one run with `run.py --data-dir`.

`.wbz` files and `space.json` are written atomically: the data goes to a temporary file in the same directory, is flushed with `fsync`, and then replaces the target.

## Index (`index.sqlite`)

The index holds each board's metadata, so board lists can be shown without decompressing every `.wbz` file.

| Item | Description |
| --- | --- |
| Table | `boards(id TEXT PRIMARY KEY, meta TEXT, mtime REAL)`: the metadata as JSON and the board file's modification time. |
| Reading | Loaded into memory at startup; queries then read memory only. |
| Writing | Row by row (SQLite WAL mode), in order, by one dedicated thread. |
| Check at startup | The set of file names in `boards/` and each file's modification time are compared with the index: new files and files whose time differs are read again, rows whose file is gone are deleted. If the process exits before the index is written, the next start catches up this way. |
| Missing or damaged | Rebuilt from all board files. |

The index can be deleted at any time; it is rebuilt at the next start without losing any content.

## Per-space data (`space.json`)

`space.json` holds small data that cannot be rebuilt from the board files. It is written on every change and is limited to 1 MB. The whiteboard app's user space uses these keys:

| Key | Description |
| --- | --- |
| `folders` | Folder names, including empty folders. |
| `order` | A list of board IDs: the order in the board chooser. Boards not in the list come first, newest `created` first. |
| `current` | The ID of the current board. |

### Folders

Folders have one level and cannot be nested. A folder is identified only by its name; there is no separate folder ID.

- A board records its folder's name in `meta.data.folder`. A board outside any folder has no such key.
- `space.json` keeps the folder list in `folders` as well, because an empty folder has no board to record it.
- A name that appears in a board's `data.folder` but not in `folders` is added to `folders` when the storage directory is opened.
- Renaming a folder rewrites `data.folder` of every board in it; deleting a folder only removes the name from the list, and its boards move out of the folder. No board is deleted.
- Folder names are trimmed and are at most 64 characters.

## File structure

A `.wbz` file contains the result of `zlib.compress(json_utf8, 6)` applied to the following object.

```jsonc
{
  "v": 2,
  "meta": {
    "id": "49b773c7c7c2",
    "name": "",
    "created": 1758000000.0,
    "updated": 1758000123.4,
    "canvas": {"mode": "infinite"},
    "background": {"pattern": "grid"},
    "layers": [],
    "data": {"folder": "Math"}
  },
  "strokes": [
    {
      "id": "c3f1a2-7k-1f",
      "tool": "pen",
      "color": "#1b1b1f",
      "w": 3.0,
      "n": 42,
      "dev": "ipad",
      "cut": 2,
      "m": [[5, 120, 40, 180, 40]],
      "p": "A6ABwAKABAiZ1QG2ev8="
    }
  ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `v` | integer | File format version, currently 2. See [File version and incomplete reads](#file-version-and-incomplete-reads). |
| `meta` | object | Board metadata. See [meta fields](#meta-fields). |
| `strokes` | array | Strokes. See [Stroke fields](#stroke-fields). |

### meta fields

`meta` describes the board itself. The server normalizes it with `inksync.models.sanitize_meta` on every read and write; unknown fields are dropped.

| Field | Type | Values and limits |
| --- | --- | --- |
| `id` | string | `^[A-Za-z0-9_-]{1,64}$`, unique within the space. New boards use 12 hexadecimal characters. The file name is `<id>.wbz`. |
| `name` | string | Trimmed, at most 64 characters. An empty string means “unnamed”; the interface shows a default name. |
| `created` | number | Creation time, Unix seconds. |
| `updated` | number | Time of the last accepted operation, Unix seconds. Renaming and filing do not change it. |
| `canvas` | object | `{"mode":"infinite"}` (extends in four directions); `{"mode":"column","width":W}` (fixed width, extends downward; the whiteboard app's note boards use 1000); `{"mode":"fixed","width":W,"height":H}` (fixed size). W and H are 1 to 100000. Set at creation and cannot change. Invalid values become `infinite`. |
| `background` | object | `pattern`: `blank`, `grid`, `lines` or `dots` (`grid` if invalid); `paper`: optional paper color `#rrggbb`. |
| `layers` | array | Image layers, at most 1000, drawn in order. Each is `{src, x, y, width, height?, z?, sheet?}`: `src` is a same-origin path (starting with a single `/`, without `..`, `\` or a scheme), at most 512 characters, and may contain `{w}` (replaced by 640, 1024, 1600 or 2400 according to the displayed width); `x`, `y`, `width`, `height` are in board coordinates; without `height` the image's aspect ratio is used; `z: "above"` draws the layer above the ink; `sheet: true` draws it as a page with a shadow. If any item is invalid, the whole array is treated as empty. |
| `data` | object | The host's own fields, at most 16 KB as JSON. The whiteboard app uses `folder` (folder) and `doc` (document board original; see [Document boards](#document-boards)); boards created by an app in 1.0.1 also have `app`. |

### Stroke fields

A stroke is one continuous pen, marker or highlighter line; memory, WebSocket and disk use the same field names.

| Field | Type | Values and limits |
| --- | --- | --- |
| `id` | string | Generated by the client, globally unique. `^[A-Za-z0-9_.:-]{1,64}$`. |
| `tool` | string | `pen`, `marker`, `highlighter`. Invalid values become `pen`. |
| `color` | string | `#rrggbb`. Invalid values become `#1b1b1f`. |
| `w` | number | Width in world units, clamped to 0.5–96. Default 3. |
| `n` | integer | Stacking order, 0 ≤ `n` < 2^40. Strokes are drawn in ascending `n`. Assigned by the server. If missing in a file, the stroke's array index is used. |
| `dev` | string | Device that drew the stroke, at most 16 characters. |
| `cut` | integer | Optional. Cut ends, 1–3. Omitted when 0. See [Cut ends](#cut-ends-cut). |
| `m` | array | Optional. Eraser mask. Omitted when empty. See [Masks](#masks-m). |
| `p` | string | Points. In the file: base64 of the [point encoding](#point-encoding-p). In memory and on the WebSocket: flat array `[x, y, pressure, ...]`. |

| `p` limit | Value |
| --- | --- |
| Values per point | 3 (`x`, `y`, pressure) |
| Minimum points | 1 |
| Maximum points | 20000 (`models.MAX_POINTS_PER_STROKE`) |
| Allowed values | Finite numbers only (no NaN, no infinity, no booleans) |

A stroke that violates a `p` limit is discarded as a whole; it is not truncated.

## Stroke outline

Each stroke is rendered as one filled closed outline generated by perfect-freehand from the stroke's points, width and pressure.

| Target | Implementation |
| --- | --- |
| Screen | `packages/inksync/inksync/web/vendor/perfect-freehand.js` (perfect-freehand 1.2.3, unmodified ESM build), called from `stroke.js` |
| Export | `whiteboard/freehand.py`, a Python port used by `/api/export/{board_id}` on the server |

Both implementations must produce identical outline points. `tests/test_browser.py::test_python_outline_matches_perfect_freehand` compares them point by point with tolerance 1e-9 on recorded strokes.

> **Warning**
> Run `test_python_outline_matches_perfect_freehand` after any change to the vendored perfect-freehand file or to `freehand.py`.

### Tool properties

| Tool | Opacity | Width multiplier | Width follows pressure |
| --- | --- | --- | --- |
| `pen` | 1 | 1 | Yes |
| `marker` | 1 | 2.6 | No |
| `highlighter` | 0.3 | 6 | No |

The width multiplier is applied when the stroke starts; the stored `w` already includes it.

### Pressure and width

The radius at each point comes from the project's own pressure curve, not from perfect-freehand's `thinning`.

```text
half   = max(0.3, w / 2)
force  = p · (1 + KNEE) / (p + KNEE)                        p clamped to 0–1
radius = half · (FLOOR + (1 − FLOOR) · force^GAMMA)          pen
radius = half                                                marker, highlighter
```

| Constant | Value | Defined in |
| --- | --- | --- |
| `PEN_KNEE` | 0.05 | `stroke.js`, `inkpdf.py` |
| `PEN_FLOOR` | 0.2 | `stroke.js`, `inkpdf.py` |
| `PEN_GAMMA` | 1.2 | `stroke.js`, `inkpdf.py` |

The curve is shaped for iPad Safari, which reports pressure in roughly 0.003–0.13 rather than the full 0–1 range. Mouse and finger input have no pressure reading; the input layer converts speed into a pressure value (`input.js` `pressureFor`, `stroke.js` `pressureForFactor`).

### Outline parameters

| Parameter | Value | Description |
| --- | --- | --- |
| `size` | `max(w, 0.6)` | Stroke width |
| pressure passed in | `radius / w` | With `thinning` = 1 the perfect-freehand radius reduces to `size × pressure`, which equals `radius`. |
| `thinning` | 1 | Hands width control to the pressure curve above. |
| `smoothing` | 0.5 | Minimum spacing between outline points is `(size × smoothing)²`. |
| `streamline` | 0.5 | Smoothing of input positions. |
| `simulatePressure` | false | Speed is already folded into the pressure value. |
| `start.cap` / `end.cap` | from `cut` | A cut end has no round cap. See [Cut ends](#cut-ends-cut). |
| start noise (`START_NOISE`) | 1 world unit | Length discarded at the start of the stroke. |
| coordinate scale (`INK_SCALE`) | 10 | Coordinates and lengths are multiplied by 10 before the call and divided by 10 afterwards. |

`getStrokePoints` and `getStrokeOutlinePoints` are called separately instead of `getStroke`, so the start-noise length (1 world unit) is independent of the stroke width.

`INK_SCALE` reduces the effect of perfect-freehand's fixed `END_NOISE_THRESHOLD = 3` to 0.3 world units, so the end of a stroke keeps its outline points and does not narrow before the end cap.

> **Note**
> `START_NOISE`, `INK_SCALE`, `OUTLINE_SMOOTHING` and `OUTLINE_STREAMLINE` must have the same values in `stroke.js` and `inkpdf.py`.

### Input filtering

The input layer drops samples that would distort the stroke end before the points reach the outline.

| Rule | Value | Code |
| --- | --- | --- |
| Minimum distance between samples | 1.2 screen pixels **and** 12 % of the stroke half-width | `input.js` `MIN_STEP_PX`, `MIN_STEP_RATIO` |
| Trailing samples removed at pen lift | Samples within 30 % of the half-width from the last point | `input.js` `TAIL_SETTLE`, `trimSettledTail` |

### Path construction

The outline points form one closed path of quadratic Bézier curves through the midpoints of adjacent points, with each point as the control point.

This is the `Q` + `T` path from the perfect-freehand README; segment *i* uses outline point *i* as its control point, so `stroke.js` calls `quadraticCurveTo` directly (`quadPath`).

PDF has no quadratic curves. `inkpdf.py` converts each one to an exact cubic:

```text
C1 = P0 + 2/3 · (Q − P0)
C2 = P2 + 2/3 · (Q − P2)
```

Each stroke is filled once with the nonzero rule. A single fill keeps overlapping parts of a semi-transparent highlighter stroke at uniform opacity.

## Cut ends (`cut`)

`cut` marks which ends of a stroke were produced by cutting, so those ends are drawn flat instead of with a round cap.

| `cut` | Start | End |
| --- | --- | --- |
| absent / 0 | round | round |
| 1 | flat | round |
| 2 | round | flat |
| 3 | flat | flat |

A round cap at a cut end would refill part of the erased gap.

| Renderer | Honors `cut` |
| --- | --- |
| Screen (`stroke.js` `strokeOutline`) | Yes |
| PDF export (`inkpdf.py` `outline_path`) | Yes |
| Image export (`docs.py` `export_image`) | No, always round caps |

Strokes split by `splitLongStroke` (see [protocol.md](protocol.md#operation-rules)) do not set `cut`.

## Masks (`m`)

A mask records the capsules swept by the pixel eraser over a stroke; renderers clip them out of the outline and the stroke's points stay unchanged.

### Mask format

```jsonc
"m": [
  [r, x0, y0, x1, y1, ...],   // one chain: radius, then two or more points
  ...
]
```

| Element | Description |
| --- | --- |
| chain | One eraser sweep: a polyline of capsules with radius `r`. |
| `r` | Capsule radius in world units, > 0. |
| `x0, y0, x1, y1, ...` | Polyline points, at least 2. |
| segments in a chain | `max(1, (len − 3) / 2)` |

Validation (`models.sanitize_mask`):

- A chain must be an array of odd length ≥ 5 with `r` > 0.
- A chain with a non-number value is dropped.
- Chains past the segment limit are dropped, and the chain that crosses the limit is truncated.

### Recording sweeps

The pixel eraser always adds to the mask; it never cuts strokes directly (`stroke.js` `eraseKind` returns only `"bite"` or `null`).

- Consecutive segments of one drag are appended to the same chain when the radius matches within 1 % and the new segment starts at the chain's last point (`addMask`).
- A chain that lies entirely inside the new capsule is removed.
- A stroke appears broken when the mask covers its full width; the data is still one stroke with one ID.

### Mask rendering

Renderers do not subtract capsules from the ink, because even-odd subtraction of overlapping capsules cancels in the overlap.

| Target | Method |
| --- | --- |
| Screen | Redraws the background inside the union of the capsules. |
| PDF | Splits the capsules into groups with no overlap inside a group, then applies one `W* n` clip per group. The masked gap stays vector. |

Details: [eraser.md](eraser.md).

### Mask size limit

The number of capsule segments per stroke is limited because clipping cost grows with it.

| Limit | Value | Where | Action when exceeded |
| --- | --- | --- | --- |
| `MASK_LIMIT` | 400 segments | `stroke.js` | Simplify the mask (RDP, tolerance `r / 6`). If still over the limit, convert the mask into cuts (`app-eraser.js` `bakeMask`). |
| `MAX_MASK_SEGMENTS` | 1024 segments | `models.py` | Server drops or truncates chains past the limit. |

Measured full-screen redraw time for one stroke: 2.9 ms with 400 segments, 15 ms with 1000, 57 ms with 2000.

`bakeMask` replaces the stroke with the pieces left after cutting along every capsule, clears the mask and sends a `remove` plus a `restore`. The pieces keep the original `n`. Cuts cannot represent a partly removed edge, so thin slivers along the edge are removed in this step.

> **Warning**
> `MAX_MASK_SEGMENTS` must be at least `MASK_LIMIT`, and both count total segments per stroke. If the server truncates a mask the client considers valid, erased ink reappears on other devices. `tests/test_models.py` checks this.

### Mask synchronization

The `mask` operation carries each stroke's complete current mask, not a delta. A full mask is correct regardless of reconnection or delivery order. See [protocol.md](protocol.md#operations).

## Point encoding (`p`)

On disk, the flat point array `[x, y, pressure, ...]` is encoded as bytes and stored as a base64 string.

| Step | Rule |
| --- | --- |
| 1. Quantize coordinates | `round(value × 8)` (1/8 world unit, `QUANT = 8`) |
| 2. Delta | Each point stores `x − prev_x` and `y − prev_y`. The first point's previous value is (0, 0). |
| 3. Zigzag varint | Each delta is zigzag-mapped `(v << 1) ^ (v >> 63)` and written as an unsigned LEB128 varint (7 bits per byte, high bit = continuation). |
| 4. Pressure | Clamped to 0–1, stored as one byte `round(p × 255)`. |
| 5. base64 | The byte string is base64-encoded into the JSON `p` field. |
| 6. zlib | The whole JSON document is zlib-compressed (level 6). |

Byte layout:

```text
uvarint  count
repeat count times:
  svarint  dx      (quantized)
  svarint  dy      (quantized)
  uint8    pressure
```

Decoding reverses the steps: `x = Σdx / 8`, `y = Σdy / 8`, `pressure = byte / 255`. A varint longer than 63 bits, a read past the end, or a missing pressure byte is an error.

A stroke of 500 points is usually under 2 KB. The code is in `packages/inksync/inksync/codec.py`; `encode_points` / `decode_points` (and the `_b64` variants) are inverse functions covered by round-trip tests.

## File version and incomplete reads

`v` is the file format version, currently 2 (`inksync.storage.FILE_VERSION`). It changes only when the file format changes, not when the application version changes. A file without `v` is read as version 1.

`FileStorage.read_file` reports a board as not fully readable in the following cases:

| `reason` | Condition | Extra field |
| --- | --- | --- |
| `unreadable` | The file cannot be opened (permissions, external disk not mounted). May be temporary. | `detail`: error text |
| `corrupt` | The file opens, but decompression or JSON parsing fails, or the top level is not an object. No stroke is read. | `detail`: error text |
| `partial` | Most of the file is read, but some strokes cannot be decoded or are invalid. | `dropped`: number of strokes |
| `newer` | `v` is greater than `FILE_VERSION` or is not an integer. The file was written by a newer version. | `version`: the value of `v` |

When `newer` applies, `partial` is not reported. A missing `.wbz` file is not an error; the board opens empty.

### Read-only boards

A board that is not fully readable opens as a read-only board.

- Readable content is shown.
- The server rejects all operations on it, including `meta` (see [protocol.md](protocol.md#read-only-boards)).
- Autosave does not write the file, so the partial content never overwrites the original.
- Renaming and moving to a folder rewrite only `meta` in the file; the rest of the file, including fields this version does not recognize, is kept. If the file cannot be read, the change fails.
- 1.x reads a file with `v` = 2 as `newer` and opens it read-only, but 1.x renaming and filing rewrite `meta` in such files and lose `canvas`, `layers` and `data`. Downgrading from 2.0 to 1.x is therefore not supported; restore from `backups/upgrade/` if needed.
- Selecting the board again reads the file again.
- A board in the index whose file cannot be read stays in the board list and opens read-only.

### Unlocking

A device with the “管理白板” (Manage boards) permission can choose to edit a read-only board anyway.

1. The server copies the file to `backups/locked/<id>-<YYYYmmdd-HHMMSS>.wbz` (with `-1`, `-2`, … appended if that name exists).
2. If the copy fails, the board stays read-only.
3. Otherwise the board becomes editable and is saved normally. Strokes that could not be read and fields this version does not recognize are lost at the next save.

## Backups

The application writes two kinds of backups inside the storage directory.

| Directory | When | Content | Retention |
| --- | --- | --- | --- |
| `backups/upgrade/<time>.<ns>_<old>_to_<new>/` | First start after a version change (upgrade or downgrade), before any board is opened | `boards/`, `index.json`, `index.sqlite`, `space.json` and `spaces/` (whichever exist) | Latest 5 |
| `backups/locked/` | Before a read-only board is unlocked | The original `.wbz` file | Not pruned |

- `docs/` is not backed up: the application never modifies the originals.
- `thumbs/` is not backed up: thumbnails are regenerated.
- No upgrade backup is taken when `boards/` is empty.
- An upgrade backup is first written to `.partial-<name>` and renamed when complete.
- If the upgrade backup fails, startup continues and the backup is retried at the next start. Exception: a storage directory still in the 1.x format (with `index.json` and without `index.sqlite`) must be converted first; then a failed backup stops the start and the reason is shown.

## Reading 1.x files

When 2.0 reads a file with `v` = 1, it converts the metadata to the 2.0 fields in memory; the file is written back with `v: 2` at the next save or metadata change. Strokes are unchanged.

| 1.x field | 2.0 |
| --- | --- |
| `kind: "board"` | `canvas: {"mode": "infinite"}` |
| `kind: "note"` | `canvas: {"mode": "column", "width": 1000}` |
| `kind: "doc"` | `canvas: {"mode": "fixed", …}` sized to the pages laid out, and one `sheet` image layer per page |
| `background: "grid"` etc. | `background: {"pattern": "grid"}` |
| `underlay: {src, width}` | the first item of `layers` |
| `folder`, `app`, `doc` | moved into `data` |

The first time 2.0 opens a 1.x storage directory, it backs up the data (see [Backups](#backups)), creates `index.sqlite`, moves the folder list, order and current board from `index.json` into `space.json`, moves boards with `app` into `spaces/<app name>/boards/`, and finally renames `index.json` to `index.v1.json`.

## Reading a file

A `.wbz` file can be read with the Python standard library and `inksync.codec`.

```python
import json, zlib
from inksync.codec import decode_points_b64

payload = json.loads(zlib.decompress(open("boards/xxx.wbz", "rb").read()))
for stroke in payload["strokes"]:
    points = decode_points_b64(stroke["p"])   # [x, y, pressure, ...]
    print(stroke["tool"], stroke["color"], len(points) // 3, "points")
```

PNG export of ordinary boards happens in the browser (export button in the interface). A `.wbz` file contains only vector data.

## Document boards

A document board (beta) is created from a PDF or image; the ink is stored in the `.wbz` file and the original is kept unchanged in `docs/`. A document board's metadata:

- `canvas` is `fixed`, sized to the pages laid out;
- one `sheet` image layer per page, with `src` `/api/doc/<board id>/<page>?w={w}`;
- the original's information in `data.doc` (below), and `background.pattern` `blank`.

### data.doc fields

```jsonc
"doc": {
  "type": "pdf",
  "name": "讲义.pdf",
  "ext": ".pdf",
  "pages": [[595.28, 841.89], [595.28, 841.89]]
}
```

| Field | Type | Values and limits |
| --- | --- | --- |
| `type` | string | `pdf` or `image` |
| `name` | string | Original file name, at most 128 characters. Used only for the export file name. |
| `ext` | string | Original extension, lowercase, `^\.[a-z0-9]{1,7}$`. |
| `pages` | array | `[width, height]` per page, rounded to 0.01. Each value 0 < v < 10^6. At most 400 pages. PDF in pt, images in pixels. Sizes already include `/Rotate`. |

| Supported original | Extensions |
| --- | --- |
| PDF | `.pdf` |
| Image | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`, `.tif`, `.tiff` |

A new document board uses the file name without extension as `name`, `background` = `blank`, and stores the original as `docs/<board id><ext>`.

### Page layout

Pages are placed top to bottom in world coordinates, each centered on the widest page, with a gap of 24 world units.

| Constant | Value | Code |
| --- | --- | --- |
| `PAGE_GAP` | 24 | `whiteboard/docs.py` (export), `doc_layers` in `whiteboard/models.py` (page image layers) |

> **Warning**
> The two gaps must match. If they differ, exported ink lands on the wrong page. The front end no longer lays out pages itself; it draws the image layers where they are.

### PDF export

PDF export appends the ink to each page as a new content stream and leaves the original content stream objects unchanged.

| Step | Rule |
| --- | --- |
| Page assignment | By the center of the stroke's bounding box. Strokes in the gap between pages go to the nearest page. |
| Simplification | Ramer–Douglas–Peucker, tolerance `w × 0.05` clamped to 0.35–1.5 pt. |
| Outline | Same outline as the screen, with `cut` and masks. One fill per stroke. |
| Coordinates | Rounded to 1/4 pt integers and scaled back with `cm`. |
| Opacity | `/ExtGState` entries `/WBa<percent>` with `ca` and `CA`. |
| Page contents | `/Contents` becomes an array: `q`, original streams, `Q`, ink stream. |
| Compression | Ink stream compressed with Flate (zlib level 9). |
| Rewrite | Each export writes a new file from the original with `PdfWriter(clone_from=...)`, which keeps only reachable objects. |

Measured size: 300–450 bytes per stroke. Exporting the same board repeatedly gives the same size, because the original is never modified. Code: `whiteboard/inkpdf.py`, `whiteboard/docs.py`.

`inkpdf.page_matrix` returns the `cm` matrix from display coordinates (top-left origin, y down) to PDF user space for each `/Rotate` value (0, 90, 180, 270), including the CropBox offset; `page_geometry` reads the inherited `/Rotate` and page box.

### Image export

Image export draws the ink onto the original at full resolution.

| Rule | Value |
| --- | --- |
| Simplification | Same as PDF export. |
| Rasterization | Each stroke is drawn only within its bounding box at 3× supersampling (`SUPERSAMPLE`), then downsampled. If the supersampled tile exceeds 64,000,000 pixels, the stroke is drawn without supersampling. |
| JPEG output | Reuses the original quantization tables and chroma subsampling. Without tables, quality 90. |
| Other formats | `.png` stays PNG. `.gif`, `.bmp`, `.webp`, `.tif`, `.tiff` export as PNG. |
| `cut` and masks | Applied as in PDF export: `cut` ends are flat, and each stroke's mask capsules are removed from that stroke only. |

The export file name is `<original name without extension>-批注<ext>`.

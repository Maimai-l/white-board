English | [简体中文](eraser.zh-CN.md)

# Pixel Eraser

This document records how the pixel eraser parameters were measured from native iPadOS PencilKit, the values the implementation uses, and how erased areas are stored, synchronized, rendered and exported.

## Summary

The pixel eraser follows these rules, all derived from native PencilKit measurements.

| Rule | Value | Code |
| --- | --- | --- |
| Diameter depends only on the altitude angle | 6 to 81 screen px, see [Diameter curve](#diameter-curve) | `input-erase.js` `ERASER_CURVE` |
| Pressure has no effect | Pressure 0.15 to 0.51 at the same angle gives the same diameter | `input-erase.js` `eraserRadius` |
| Size is constant on screen | World radius = screen radius / `viewport.scale`, at every zoom level | `input-erase.js` `worldRadius` |
| Width is fixed for the whole stroke | Taken once at pen-down | `input-erase.js` `startErase` |
| Mouse, finger, pen without tilt data | Diameter at 50° (16.5 px) | `input-erase.js` `eraserRadius` |
| Object eraser | Fixed 6 px diameter (`ERASER_TIP`) | `input-erase.js` |
| Erased area is stored as a mask | Capsule chains in the stroke's `m` field | `app-eraser.js` `erasePixels` |

## Data sources

All native data was collected with [InkProbe](https://github.com/Maimai-l/InkProbe), which lets PencilKit draw and erase normally while recording every raw touch in the same gesture.

| Session | Content | Zoom |
| --- | --- | --- |
| Isolated dabs | 21 separate eraser taps on solid ink, one altitude angle per row | 1 |
| a | Five eraser strokes over a solid area painted with the marker, altitude 37°–43° | 1 |
| c | Two eraser strokes over a solid marker area | 0.25 |
| test1 | Calibration | 1 |
| test2 | Calibration | 2 |
| test3 | Drag | 1 |
| bench1 | Mixed | 1–3 |
| bench2 | One eraser stroke that PencilKit did not record | 0.25 |
| Native eraser strokes | 92 eraser strokes, used for per-stroke altitude statistics | — |

## Measurement method

Two quantities are measured, and they agree only where the eraser contact shape is circular.

| Quantity | Method | Used for |
| --- | --- | --- |
| Equivalent diameter | Area of each isolated hole in `PKStroke.mask`, converted to the diameter of a circle of equal area, matched to the `altitudeAngle` of that tap in `input.json` | Isolated dabs |
| Perpendicular width | Width of the hole measured perpendicular to the eraser path at each sample; median per stroke | Drag strokes |

All 21 isolated dabs were matched to their taps, with center offsets under 3 pt. Drawing-coordinate measurements are multiplied by the zoom at the time to obtain screen size.

## Measured data

Each table lists native measurements as (altitude angle, diameter in screen units). The same data is used by `tests/test_browser.py::test_eraser_width_follows_the_native_curve`.

### Isolated dabs (equivalent diameter)

| Angle | Diameter | Angle | Diameter | Angle | Diameter |
| --- | --- | --- | --- | --- | --- |
| 83.3° | 6.9 | 49.8° | 17.6 | 31.8° | 54.1 |
| 82.0° | 6.3 | 49.7° | 18.2 | 28.5° | 72.2 |
| 81.1° | 7.2 | 49.7° | 15.9 | 27.3° | 78.7 |
| 80.0° | 9.0 | 35.0° | 35.2 | 20.3° | 80.7 |
| 79.1° | 8.1 | 34.4° | 40.7 | 14.8° | 82.1 |
| 50.3° | 16.5 | 33.7° | 45.0 | 12.6° | 81.2 |
| 50.1° | 17.7 | 32.5° | 50.5 | 12.1° | 80.3 |

### Drag strokes (perpendicular width)

| Angle | Width | Angle | Width | Angle | Width |
| --- | --- | --- | --- | --- | --- |
| 67.8° | 16.5 | 50.7° | 15.5 | 40.9° (a) | 15.5 |
| 64.8° | 16.5 | 45.3° | 16.5 | 39.6° (a) | 15.5 |
| 58.8° | 15.5 | 44.9° | 18.0 | 38.9° (a) | 16.0 |
| 57.7° | 15.5 | 44.0° | 16.5 | 38.4° (a) | 16.0 |
| 54.4° | 16.0 | 43.0° (a) | 15.5 | 10.4° (c, zoom 0.25) | 80.5 |
| 53.4° | 14.5 | | | | |
| 52.6° | 16.0 | | | | |

Session c measured 322 drawing units at zoom 0.25, which is 80.5 screen units.

## Diameter curve

`ERASER_CURVE` maps the altitude angle to the eraser diameter in screen pixels. Values between points are linearly interpolated; angles above 90° use 6 and angles below 0° use 81.

| Angle | Diameter (px) | Basis |
| --- | --- | --- |
| 90° | 6 | Pen tip |
| 80° | 7.5 | Isolated dabs, 79°–83° |
| 68° | 16.5 | Drag width |
| 37° | 16.5 | Drag width, session a |
| 35° | 35 | Isolated dabs |
| 32° | 52 | Isolated dabs |
| 28° | 75 | Isolated dabs |
| 25° | 81 | Isolated dabs, saturation |
| 0° | 81 | Saturation |

- The diameter starts to grow at 80°, stays at about 16.5 between 68° and 37°, rises steeply below 37°, and saturates at about 81 below 25°.
- A typical writing grip is about 50°, where the diameter is 16.5.
- The curve is monotonic: a flatter pen never gives a narrower eraser.

> **Note**
> The step from 16.5 at 37° to 35 at 35° joins the two measurement methods. At 35° the two values suggest a contact shape of about 16.5 × 75 (an ellipse elongated along the pen), so the equivalent diameter is larger than the perpendicular width in the 25°–44° range. No drag samples exist between 37° and 25°, so this is the least certain part of the curve.

A straight line from (37°, 16.5) to (25°, 81) was evaluated in place of the isolated-dab points and rejected, because it lowered IoU (test1 0.931 → 0.862, bench1 0.871 → 0.830).

## Zoom

The eraser has a constant size on screen, so zooming in erases a thinner line in drawing coordinates. Hit testing and the cursor ring both use `screen radius / viewport.scale`, in both zoom directions.

| Angle | Zoom 1 (drawing units) | Zoom 2.02 (drawing units × zoom) |
| --- | --- | --- |
| ~80° | 6.3–9.0 | 6.9–12.2 |
| ~50° | 15.9–18.2 | 16.9–22.8 |
| ~25° | 80.3–82.1 | 80.4–85.4 |

For zoom-out, session c (zoom 0.25, 10.4°) gives 80.5 screen units against 81 from the curve. There is no lower bound on `scale` in `worldRadius`.

## Width within a stroke

The eraser width is set once at pen-down and does not change for the rest of the stroke, which matches native behavior.

| Native statistic (92 eraser strokes) | Value |
| --- | --- |
| Strokes that report a single altitude value | 65% |
| Strokes with an in-stroke range of at most 2.36° | 90% |
| Median in-stroke range | 0° |
| Longest initial plateau observed | 648 identical readings in a 1450-sample stroke |

- iPadOS Safari reports whole-degree `tiltX` / `tiltY` and no `altitudeAngle`. While writing, the angle varies between about 33° and 47°, across the steep part of the curve. A width that follows the angle per sample would vary by up to 2.7× within one stroke.
- The hover cursor ring still follows the current tilt, because it is a preview.

Fixing the width per stroke does not reduce accuracy:

| Session | IoU, width per sample | IoU, width per stroke |
| --- | --- | --- |
| test1 | 0.934 | 0.931 |
| test2 | 0.885 | 0.878 |
| test3 | 0.902 | 0.901 |
| bench1 | 0.871 | 0.871 |

## Pixel comparison

`tools/compare_native_eraser.py` rasterizes the native erased area and the whiteboard's erased area onto the same image and reports IoU, over-erase and under-erase.

```sh
python3 tools/compare_native_eraser.py <unzipped InkProbe sessions directory>
```

For each session it prints the pixel counts and writes `<name>-diff.png` next to the sessions: gray = erased by both, red = erased only by native (under-erase), blue = erased only by the whiteboard (over-erase).

### Method

| Step | Rule |
| --- | --- |
| Ink region | Rebuilt from the control points (`points`) and per-point width at scale 2. `interpolatedPoints` cannot be used, because its `rangeIndex` covers only the segments that survive the mask. |
| Native erased area | Ink region minus the ink still visible in `final/render-transparent@2x.png` (alpha > 32). Counting holes in the mask misses strokes that PencilKit split, and holes can extend outside the ink. |
| Whiteboard erased area | Capsules along the `coalesced` eraser samples, diameter from `ERASER_CURVE` at the first sample, divided by the zoom at `viewportAtBegin`, intersected with the ink region. |
| Eraser corridor | Both sides are limited to the union of capsules with 3× the eraser radius along the eraser path. |
| Percentages | Over-erase and under-erase are relative to the union. |

> **Note**
> The corridor removes reconstruction error at stroke reversals. The marker tip is a 50 × 100 flat shape, but the ink region is rebuilt with a circle of radius 50, which adds a half-disc at each reversal. In session a this accounted for 47% of the native total and lowered IoU from 0.91 to 0.50 before the corridor was applied. On sessions without reversals the corridor removes 0–8 px.

The rebuilt ink region is about 13% thinner overall than the exported transparent image.

### Results

Results are measured inside the ink region and the eraser corridor.

| Session | Zoom | IoU | Over-erase | Under-erase |
| --- | --- | --- | --- | --- |
| a | 1 | 0.912 | 7.4% | 1.4% |
| c | 0.25 | 0.940 | 2.2% | 3.8% |
| test1 | 1 | 0.931 | 6.5% | 0.1% |
| test2 | 2 | 0.878 | 11.5% | 0% |
| test3 | 1 | 0.901 | 9.8% | 0% |
| bench1 | 1–3 | 0.871 | 11.6% | 1.2% |
| bench2 | 0.25 | 0.003 | 99.5% | 0.2% |

Over-erase and under-erase percentages were measured with the per-sample width; IoU for test1–bench1 is the per-stroke value.

### Known anomalies

| Session | Observation |
| --- | --- |
| a | The remaining difference is at the start of two eraser strokes: native erases about one eraser diameter further back, which indicates that PencilKit starts erasing before the first coalesced sample (predicted touches). |
| test2 | The mask of the second stroke contains two large negative-area regions (177 × 131 and 160 × 90) where the eraser path length is at most 7 pt; no matching input or undo exists. |
| bench2 | The eraser stroke had no effect in PencilKit: 2361 samples, 200 of them on visible ink, and none of the 20 strokes has a mask or `maskedPathRanges`. The IoU of 0.003 reflects this missing native data, not a geometry error. Session c at the same zoom erases fully, so this is not a zoom rule. |

## Mask storage

The pixel eraser never splits a stroke directly; it appends the swept capsules to the stroke's mask (`m` field). The format is described in [format.md](format.md).

```json
"m": [[radius, x0, y0, x1, y1, ...], ...]
```

| Rule | Value | Code |
| --- | --- | --- |
| A new segment extends the last chain | When it starts at the chain's end point and its radius is within 1% of the chain's radius | `stroke.js` `SWEEP_RADIUS_TOLERANCE`, `addMask` |
| Covered segments are dropped | Chains fully inside the new capsule are removed | `stroke.js` `addMask` |
| Client limit per stroke | `MASK_LIMIT` = 400 capsule segments | `stroke.js` |
| Thinning when over the limit | Ramer–Douglas–Peucker with tolerance `r / 6`, the same as PDF export (`inkpdf.py` `_capsules`) | `stroke.js` `simplifyMask` |
| Baking when still over the limit | The mask is converted into cuts and the stroke is replaced by the remaining pieces (`remove` + `restore`); pieces keep the original stacking number `n` | `app-eraser.js` `bakeMask` |
| Server limit per stroke | `MAX_MASK_SEGMENTS` = 1024 segments, counted the same way as the client | `models.py` |

- Native PencilKit also uses masks: when the eraser cuts through a stroke, PencilKit splits it into separate strokes, each with its own mask. In the whiteboard a stroke that looks cut in two remains one stroke with one id until it is baked or touched by the object eraser.
- An earlier design split a stroke whenever the eraser radius was at least the stroke's half-width. It was removed because pressure changes the half-width along the stroke, so one drag switched between splitting and masking.
- Baking replaces masked edges with flat cut ends and removes thin slivers along the edge, which is visible. Thinning first avoids it in normal use.
- `tests/test_models.py` checks that `MAX_MASK_SEGMENTS >= MASK_LIMIT`. A server limit counted differently would truncate masks that are valid on the client.

Full-screen redraw time grows quadratically with the number of mask segments on one stroke, which is why the limit is reduced by thinning rather than raised:

| Segments | 100 | 400 | 1000 | 2000 | 4000 | 8000 |
| --- | --- | --- | --- | --- | --- | --- |
| Full redraw | 0.6 ms | 2.9 ms | 15 ms | 57 ms | 229 ms | 911 ms |

For the same back-and-forth input, thinning raises the number of samples before baking from about 400 to about 2500 (from over 3 s to about 20 s at 120 Hz).

## Input sampling

The eraser processes the same samples as drawing.

| Event | Handling |
| --- | --- |
| `pointerdown` | Sets the width for the stroke and erases at the down position. |
| `pointermove` | Erases along every sample from `getCoalescedEvents()`, or the event itself if there is only one. Each step sweeps the segment from the previous sample, not a single point. |
| `pointerup` | Sweeps the last segment to the up position, unless it equals the last sample. |
| `pointercancel` | Does not sweep a final segment, because the position does not reflect a user lift. |

## Synchronization

Mask changes are sent once per animation frame as `{"op": "mask", "masks": [{id, m}]}`, with the stroke's full current mask rather than a delta. See [protocol.md](protocol.md).

- A client ignores `mask` operations that echo its own changes (`context.mine` in `pad.js` `applyOp`). The local mask is already current, and by the time the acknowledgement arrives it usually contains newer segments.
- The server rewrites a mask only when it exceeds `MAX_MASK_SEGMENTS`, which is above the client limit.
- Very long strokes are split into several strokes at commit time, because the server drops point lists longer than `MAX_POINTS_PER_STROKE` (20000). See [protocol.md](protocol.md).

> **Note**
> Applying the echoed mask would overwrite newer local segments and break the chain into short pieces. In one drag of 120 samples (one chain, 121 points expected) this produced 60 chains with a 0 ms acknowledgement delay, and 24 chains with 52 points with a 60 ms delay. Input replay does not exercise this path; see [recording.md](recording.md).

## Rendering on screen

The screen renderer paints the background again over the union of mask capsules instead of subtracting capsules from the ink (`renderer.js` `paintStrokes`).

- Subtraction cannot express a union of overlapping capsules. With `even-odd`, overlaps at shared chain end points count twice and remain unerased; with `nonzero`, doubly covered areas have winding −2 and also remain.
- Filling the union is exact: all capsule subpaths use the same winding direction, and `nonzero` fills their union.
- Order: a stroke's mask covers that stroke and the strokes below it. Before drawing an unmasked stroke that overlaps the pending area, the renderer repaints the background, so later strokes are never covered.
- Consecutive masked strokes are collected and repainted in one batch.
- The repainted background is the board background: paper and pattern, the note page, or the document page. PNG export uses the same function; with a transparent background it clears the area instead.

## PDF export

PDF export clips each masked stroke with several even-odd clip paths applied in sequence (`inkpdf.py` `mask_clips`).

1. Thin each chain with tolerance `r / 6` and flatten it into capsules.
2. Split the capsules into groups whose members do not overlap.
3. For each group, emit a clip path of the stroke's bounding box minus the group's capsules, followed by `W* n`.
4. Fill the stroke inside `q` / `Q`.

The intersection of the complements equals the complement of the union, so the result is the stroke outside all capsules. Usually two or three groups are needed, and the gaps remain vector paths. Each stroke is clipped by its own mask only.

## Object eraser on masked strokes

The object eraser deletes only the piece it touches, even when that piece belongs to a masked stroke (`app-eraser.js` `splitBitten`).

1. For each touched stroke that has a mask, bake the mask into separate strokes with `bakeMask`.
2. Hit-test again against the new pieces.
3. Delete only the pieces the eraser touches.

| Case | Behavior |
| --- | --- |
| Stroke passed near but not touched | Not split. |
| Thin slivers along the edge | Removed by the split; the piece is deleted immediately afterward. |
| Mask does not cut the stroke through | The split produces one piece; the result equals deleting the whole stroke. |
| Undo | Recorded as one `split`: undo restores the original stroke and removes all pieces. Pieces created and deleted within the same drag are not recorded. |

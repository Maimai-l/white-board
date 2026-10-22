# InkProbe sessions

Native PencilKit recordings made with [InkProbe](https://github.com/Maimai-l/InkProbe) on 2026-09-24, on the same iPad as the board recordings. They are the source of the native figures in `docs/eraser.md` (pixel eraser width curve) and `docs/ipad-shell.md` (pressure, estimated-property updates, latency).

This branch holds data only. It shares no history with `stable` or `dev` and is not part of any release.

All four were recorded on an iPad6,7 with iPadOS 15.6, on an infinite canvas at zoom 1, with Pencil-only drawing.

| File | Session name | Samples | Sequences | Strokes at the end |
| --- | --- | --- | --- | --- |
| `20260924-112006_test1-calibr.zip` | test1-calibr | 6,397 | 22 | 1 |
| `20260924-112204_test2-calibr.zip` | test2-calibr | 5,210 | 34 | 2 |
| `20260924-112359_test3-drag.zip` | test3-drag | 11,540 | 17 | 5 |
| `20260924-112626_test4-contrast.zip` | test4-contrast | 7,947 | 7 | 1 |

Each archive contains `input.json` (every raw touch, including coalesced and predicted samples and estimated-property updates), `meta.json`, `steps/` and `final/` (the PencilKit drawing after each step and at the end).

To compare the board's eraser with these sessions, unzip them into one directory and run, from a checkout of `dev`:

```sh
python3 tools/compare_native_eraser.py <directory with the unzipped sessions>
```

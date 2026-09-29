English | [简体中文](README.zh-CN.md)

# vendor

This directory contains third-party code, stored unmodified. To change its behavior, wrap it in `static/js/`.

## pencilkit-picker.js

`pencilkit-picker.js` provides the tool picker used on the iPad. It is PencilBoard, a plain JavaScript reproduction of the iPadOS PencilKit toolbar, including a canvas.

| Item | Details |
| --- | --- |
| Source | `pencil-toolpicker.js`, provided by the user |
| Graphic assets | Converted from the `PencilKit.sketch` symbols of [WireFrameRate/PencilKitForSketch](https://github.com/WireFrameRate/PencilKitForSketch), as stated in the file header. They reproduce the Apple PencilKit interface. |
| Bundled library | [perfect-freehand](https://github.com/steveruizok/perfect-freehand) 1.2.3 (MIT, Copyright (c) 2021 Stephen Ruiz Ltd), the stroke outline library used by tldraw and Excalidraw |
| Modifications | None. The file is unmodified. |
| Size | About 470 KB, mostly base64 images |

> **Warning**
> The graphic assets imitate Apple interface artwork. Distributing them in the repository carries a copyright risk. Confirm the licensing before publishing the repository.

### Usage

- The board uses only the toolbar. `static/js/pkpicker.js` removes the canvas by subclassing.
- The file is loaded on demand. Only the iPad, where the tool picker is the toolbar, loads it with `import()`. Other devices do not download it.

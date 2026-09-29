[English](README.md) | 简体中文

# vendor

本目录存放第三方代码，按原样保存，不做修改。如需改变其行为，在 `static/js/` 中进行封装。

## pencilkit-picker.js

`pencilkit-picker.js` 提供 iPad 上使用的笔具盘。它是 PencilBoard，即 iPadOS PencilKit 工具栏的原生 JavaScript 复刻，包含画布。

| 项目 | 说明 |
| --- | --- |
| 来源 | 用户提供的 `pencil-toolpicker.js` |
| 图形资源 | 据文件头说明，由 [WireFrameRate/PencilKitForSketch](https://github.com/WireFrameRate/PencilKitForSketch) 的 `PencilKit.sketch` 符号转换而来，再现 Apple PencilKit 界面。 |
| 内含库 | [perfect-freehand](https://github.com/steveruizok/perfect-freehand) 1.2.3（MIT，Copyright (c) 2021 Stephen Ruiz Ltd），tldraw 与 Excalidraw 使用的笔迹轮廓库 |
| 修改 | 无，文件未做任何修改。 |
| 体积 | 约 470 KB，主要为 base64 图片 |

> **警告**
> 图形资源是对 Apple 界面素材的仿制，随仓库分发存在版权风险。公开发布仓库之前，请先确认授权情况。

### 使用方式

- 白板只使用其中的工具栏。`static/js/pkpicker.js` 通过继承移除了画布部分。
- 该文件按需加载：只有 iPad（笔具盘即 iPad 的工具栏）通过 `import()` 加载，其他设备不下载。

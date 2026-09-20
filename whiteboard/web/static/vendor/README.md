# vendor

这里放的是第三方代码，**原样存放，不做修改**。要改行为请在 `static/js/` 里包一层。

## pencilkit-picker.js

- 来源：用户提供的 `pencil-toolpicker.js`（PencilBoard，iPadOS PencilKit 工具栏的原生 JS 复刻，含画布）。
- 图形资源：文件头注明由 [WireFrameRate/PencilKitForSketch](https://github.com/WireFrameRate/PencilKitForSketch)
  的 `PencilKit.sketch` 符号转换而来，也就是对 Apple PencilKit 界面的再现。
  这部分是**苹果界面的仿制素材**，随仓库分发有版权风险，仓库公开发布前请自行确认。
- 内含 [perfect-freehand](https://github.com/steveruizok/perfect-freehand) 1.2.3
  （MIT，Copyright (c) 2021 Stephen Ruiz Ltd），tldraw 和 Excalidraw 用的就是它。
- 白板只用它的工具栏，画布那一半在 `static/js/pkpicker.js` 里通过继承摘掉了，
  这个文件本身一个字节都没改。
- 体积约 470 KB（大部分是 base64 图片），所以是**按需加载**：只有打开
  「笔具盘 beta」时才会 `import()` 它。

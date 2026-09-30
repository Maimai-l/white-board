[English](embed.md) | 简体中文

# 在白板应用中嵌入手写板

其他网页应用可以放在白板应用的存储目录中，由白板服务端提供，并在页面中嵌入与 Mac 同步的手写板。例如刷题页面可以显示题图和答案输入框，每道题一块白板。

本文说明由白板应用托管的应用。手写板的完整接口（选项、方法、事件、元数据格式）见 [inksync 接口说明](../packages/inksync/README.zh-CN.md)；不运行白板应用、在自己的服务端上保存白板的做法也见该文档。

```js
import { createInkPad } from "/inksync/inkpad.js";

const pad = createInkPad(document.querySelector("#answer-pad"), {
  space: "qb",                       // 应用名称
  board: "9709-s23-12-q3",
  create: {
    name: "9709 s23 P12 Q3",
    canvas: { mode: "fixed", width: 800, height: 1400 },
    background: { pattern: "blank" },
    layers: [{ src: "/apps/qb/img/9709-s23-12-q3.png", x: 0, y: 0, width: 800 }],
    data: { paper: "9709_s23_12", question: 3 },
  },
});

pad.on("history", ({ undo, redo }) => { /* 启用或停用按钮 */ });
pad.setTool({ tool: "eraser" });
await pad.open("9709-s23-12-q4", { create: { name: "9709 s23 P12 Q4" } });   // 换题
```

## 安装应用

应用是存储目录下 `apps/` 中的一个静态文件夹，白板服务端在 `/apps/<名称>/` 提供它。

```
<存储目录>/apps/
  qb/
    index.html
    img/…
```

| 规则 | 值 |
| --- | --- |
| 文件夹名称 | 须符合 `^[a-z0-9-]{1,32}$`，其他名称被忽略。 |
| 入口页面 | `index.html`。没有该文件的文件夹不算应用。 |
| 路径 | 只提供应用文件夹内的文件；通过 `..` 或符号链接离开该文件夹的请求返回 404。 |
| 缓存 | 每个响应都带 `Cache-Control: no-cache`，iPad 刷新后即加载修改后的文件。 |

本仓库的 `examples/apps/demo/` 是一个完整的示例：两道题各一块白板、题图、工具按钮和答案输入框。将它复制到 `apps/` 中，然后在 iPad 上打开 `http://你的电脑名.local:8848/apps/demo/`。

> **注意**
> 页面应由白板服务端提供。这样页面与手写板同源，不需要跨域设置；iPad 外壳也会像在白板中一样，以约 240 次/秒的频率把 Apple Pencil 采样点交给手写板。

## 应用的空间

每个已安装的应用有自己的空间，`space` 选项填应用名称。空间的白板保存在 `<存储目录>/spaces/<应用名>/` 中，与用户自己的白板分开：

| 项目 | 说明 |
| --- | --- |
| 白板 id | 只需在应用内唯一。不同应用的同名 id 是不同的白板。 |
| 新建 | 白板不存在时按 `create` 新建。不属于「管理白板」的设备每分钟最多新建 60 块，超过时收到 `error` 事件（`reason: "rate"`）。白板数量不设上限。 |
| 书写、清空 | 能书写的设备都可以。 |
| 修改元数据、解除只读 | 需要「管理白板」权限。 |
| 图片层 | `layers` 的 `src` 须为本服务端的路径（以 `/` 开头），通常位于 `/apps/<名称>/` 下。 |
| 删除应用 | 删除 `apps/<名称>/` 后，该空间不能再连接；`spaces/<名称>/` 中的白板保留，重新安装同名应用后恢复。 |

应用的白板 id 容易猜到，因此局域网内能书写的设备都能打开该应用的白板。需要按用户区分时，应由自己的服务端运行 inksync，并在 `Policy` 中判断，见 [examples/qb-server](../examples/qb-server/server.py)。

## 在 Mac 上查看

在有「管理白板」权限的设备上，白板选择界面为每个已安装的应用显示一项。点开后按页列出该应用的白板，点击一块白板以只读方式查看，包括图片层。应用的白板不出现在用户自己的白板列表中，iPad 也不跟随它们。

## iPad 首页

装有至少一个应用时，Mac 的白板设置中出现「iPad 首页」。选定一个应用后，iPad 打开时进入该应用而不是白板，iPad 外壳和主屏图标都是如此。Mac 窗口不受影响。

| 地址 | 结果 |
| --- | --- |
| iPad 上的 `/` | 跳转到 `/apps/<名称>/`。 |
| `/?home=whiteboard` | 打开白板，不跳转。应用中返回白板的链接使用这个地址。 |
| Mac 上的 `/` | 打开白板。 |

该设置保存在配置文件的 `ipad_home` 中。应用被删除后，iPad 重新打开白板。

## 从 1.x 升级

| 1.x | 2.0 |
| --- | --- |
| `import … from "/sdk/inkpad.js"` | `import … from "/inksync/inkpad.js"`。旧地址 `/sdk/inkpad.js` 跳转到新地址，但 1.x 的选项需要按下表修改。 |
| `app: "qb"` | `space: "qb"`。 |
| `name`、`kind`、`folder`、`underlay` 选项 | 放入 `create`：`name`；`canvas`；`layers`（`underlay: {src, width}` 对应 `layers: [{src, x: 0, y: 0, width}]`）。应用的白板没有文件夹，需要分组时写入 `data`。 |
| `pad.state`、`pad.net.status` | `pad.snapshot()`、`pad.status`。返回的对象只有接口说明中列出的成员。 |
| `setTool({...pad.tool, tool})` | `setTool({tool})`，未给出的字段保留。 |
| `locked` 事件的 `unlock()` | 不变，是否可用看 `pad.caps.unlock`。 |

1.x 中应用建立的白板在首次以 2.0 启动时移入各自应用的空间，白板 id 不变。

[English](embed.md) | 简体中文

# 嵌入手写板

其他网页应用可以嵌入一块与 Mac 同步的手写板。例如刷题页面可以显示题图和答案输入框，每道题一块白板。白板由 Mac 保存，并显示在白板选择界面中。

```js
import { createInkPad } from "/sdk/inkpad.js";

const pad = createInkPad(document.querySelector("#answer-pad"), {
  app: "qb",
  board: "qb-9709-s23-12-q3",
  name: "9709 s23 P12 Q3",
  folder: "刷题",
  underlay: { src: "/apps/qb/img/9709-s23-12-q3.png", width: 800 },
});

pad.on("history", ({ undo, redo }) => { /* 启用或停用按钮 */ });
pad.setTool({ ...pad.tool, tool: "eraser" });
pad.undo();
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

本仓库的 `examples/apps/demo/` 是一个完整的示例：题图、手写板、工具按钮和答案输入框。将它复制到 `apps/` 中，然后在 iPad 上打开 `http://你的电脑名.local:8848/apps/demo/`。

> **注意**
> 页面应由白板服务端提供。这样页面与手写板同源，不需要跨域设置；iPad 外壳也会像在白板中一样，以约 240 次/秒的频率把 Apple Pencil 采样点交给手写板。

## iPad 首页

装有至少一个应用时，Mac 的白板设置中出现「iPad 首页」。选定一个应用后，iPad 打开时进入该应用而不是白板，iPad 外壳和主屏图标都是如此。Mac 窗口不受影响。

| 地址 | 结果 |
| --- | --- |
| iPad 上的 `/` | 跳转到 `/apps/<名称>/`。 |
| `/?home=whiteboard` | 打开白板，不跳转。应用中返回白板的链接使用这个地址。 |
| Mac 上的 `/` | 打开白板。 |

该设置保存在配置文件的 `ipad_home` 中。应用被删除后，iPad 重新打开白板。

## `createInkPad(container, options)`

在 `container` 中创建手写板并返回。手写板铺满容器并跟随容器尺寸变化，因此容器需要有高度。

| 选项 | 类型 | 说明 |
| --- | --- | --- |
| `app` | 字符串 | 应用名称，须符合 `^[a-z0-9-]{1,32}$`。`transport` 为 `"local"` 时可以省略，其他情况必填。 |
| `board` | 字符串 | 必填。白板 id，须符合 `^[A-Za-z0-9_-]{1,64}$`。同一 id 在所有设备上是同一块白板。 |
| `name` | 字符串 | 新建白板的名称。 |
| `kind` | 字符串 | 新建白板的类型：`board`（默认，大白板）或 `note`（笔记）。 |
| `folder` | 字符串 | 新建白板所在的文件夹，不存在时自动建立。 |
| `underlay` | 对象 | `{src, width}`：笔迹下方的底图，例如题图。`src` 必须位于 `/apps/` 下。图片左上角位于白板原点；`width` 以白板坐标为单位，高度按图片比例计算。 |
| `tool` | 对象 | 初始工具：`{tool, color, width, eraserMode}`。 |
| `fingerDraw` | 布尔值 | 手指是否书写。默认只有 Apple Pencil 书写，手指用于平移和缩放。 |
| `role` | 字符串 | `ipad` 或 `mac`，默认按设备判断。 |
| `transport` | 字符串、对象或函数 | 同步方式，见[传输](#传输)。默认连接提供该页面的服务端。 |
| `initial` | 对象 | `{meta, strokes}`：`transport` 为 `"local"` 时的初始内容。 |

`name`、`kind`、`folder` 和 `underlay` 只在新建白板时生效。任一设备第一次打开某块白板时即新建，见[固定白板的连接](protocol.zh-CN.md#固定白板的连接)。

设有 `underlay` 时，手写板第一次打开时底图铺满宽度。此后每台设备分别记住每块白板的视图。

## 方法

| 方法 | 说明 |
| --- | --- |
| `setTool(tool)` | 切换工具。`tool.tool` 为 `pen`、`marker`、`highlighter` 或 `eraser`；`eraserMode` 为 `object` 或 `pixel`。 |
| `undo()` / `redo()` | 撤销或重做本设备最近的修改。 |
| `clear()` | 清空白板。应用建立的白板不需要「清空白板」权限。 |
| `fit()` | 显示全部笔迹。 |
| `zoom(factor)` | 以中心为基准缩放。 |
| `exportPNG()` | 以 PNG data URL 返回笔迹，不含底图。 |
| `on(event, listener)` | 订阅事件，返回取消订阅的函数。 |
| `destroy()` | 写入本地缓存、断开连接，并从页面中移除手写板。 |
| `snapshot()` | 以 `{meta, strokes}` 返回当前内容。 |
| `load(board)` | 以 `{meta, strokes}` 替换当前内容，并清空撤销记录。用于 `"local"` 手写板。 |

可以读取 `pad.tool`、`pad.state`、`pad.locked` 和 `pad.net.status`。其余属性属于内部实现。

## 事件

| 事件 | 内容 | 触发时机 |
| --- | --- | --- |
| `status` | `"online"`、`"syncing"`、`"offline"` 或 `"local"` | 连接状态变化。`"local"` 手写板只报告一次 `"local"`。 |
| `history` | `{undo, redo}` | 撤销或重做变为可用或不可用。 |
| `meta` | 白板元数据 | 白板载入或设置变化。 |
| `op` | 该操作 | 本设备发出一个操作（`add`、`remove`、`restore`、`mask`、`clear`、`meta`，见[操作](protocol.zh-CN.md#操作)）。任何传输方式下都会触发。 |
| `change` | —— | 本设备上的笔迹发生变化。 |
| `strokestart` / `strokeend` | 该笔画 | 本设备上一笔开始或结束。 |
| `locked` | `{board, locked, unlock}` | 白板以只读方式打开，或解除只读。`unlock()` 请求服务端允许编辑（需要「管理白板」权限）。 |
| `interrupted` | `{count}` | 笔画连续三次被系统打断，通常是「随手写」造成的。 |
| `deleted` | `{board}` | 白板在 Mac 上被删除。 |
| `error` | `{reason}` | 服务端拒绝了连接，例如 `board` 或 `app` 不合法。 |

## 传输

`transport` 选项决定手写板把操作发往何处。

| 取值 | 行为 |
| --- | --- |
| 省略 | 通过 WebSocket 连接提供该页面的服务端的 `/ws`。 |
| `"local"` | 不连接服务器。状态为 `"local"`，不发送任何内容。内容依次取自 `initial`、本地缓存，都没有时为空白板。由宿主通过 `op` 事件或 `snapshot()` 保存。 |
| `{url}` | 通过 WebSocket 连接指定地址，例如 `ws://mac.local:8848/ws`。通过 https 提供的页面不能连接 `ws://` 地址。 |
| 函数 | 自定义传输。调用时传入 `{clientId, role, pin, onMessage, onStatus}`，返回实现下列成员的对象。 |

要让其他项目自己的服务端保存和同步白板、不运行白板应用，在该服务端上用 [inksync](../packages/inksync/README.zh-CN.md) 挂载协议，并由该服务端提供页面，或者让 `{url}` 指向它。

自定义传输实现与内置 WebSocket 传输（`net.js`）相同的成员：

| 成员 | 说明 |
| --- | --- |
| `connect()` | 开始同步。把服务端消息按 [protocol.zh-CN.md](protocol.zh-CN.md) 的格式交给 `onMessage`，第一条为 `init` 或 `sync`；状态变化交给 `onStatus`。 |
| `send(msg)` | 发送控制消息，例如 `unlock`。无法发送时返回 `false`。 |
| `sendLive(msg)` | 发送实时笔迹，丢失无妨。 |
| `sendOp(op)` | 发送操作并返回一个 id。收到服务端回执之前保留在 `outbox` 中；回执以 `onMessage({t: "op", op, mine: true})` 的形式报告。 |
| `restoreOutbox(items)` | 把上次未发出的操作放回 `outbox` 并发送。 |
| `close()` | 停止同步。 |
| `outbox`、`boardId`、`lastSeq`、`epoch`、`status` | 手写板读写的状态。 |

## 行为

- **离线。** 离线时写的笔画保存在 IndexedDB 中，重连后发送，与白板相同。每块白板有各自的缓存和待发队列。
- **多块手写板。** 一个页面可以有多块手写板，每块对应自己的白板。每块只接收起点位于自身区域内的笔画，iPad 外壳的采样点也是如此。
- **页面输入。** 手写板只在自身区域内拦截触摸手势。页面其他位置的输入框、按钮和滚动照常工作。
- **在 Mac 上查看。** 应用建立的白板显示在白板选择界面的指定文件夹中。Mac 同样显示底图。
- **权限。** 只能书写的设备可以新建和打开应用建立的白板。打开用户自己的白板需要「管理白板」权限。

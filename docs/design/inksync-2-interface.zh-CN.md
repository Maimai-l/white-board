# inksync 2.0 接口

本文是 inksync 2.0 对外接口的约定，供使用 inksync 的项目（例如刷题项目 qb）在 2.0 实现期间并行开发。接口在 inksync 2.0.0 发布时生效。实现期间如有变动，会更新本文并在第 9 节记录。

设计依据见 [inksync-2.zh-CN.md](inksync-2.zh-CN.md)。

## 1. 组成

| 部分 | 内容 |
| --- | --- |
| 服务端 | Python 包 `inksync`：保存白板、同步、权限规则、Bonjour 注册。挂载到使用者自己的 aiohttp 应用上。 |
| 前端 | 随 Python 包分发的 ES 模块，由服务端提供，入口 `inkpad.js`，导出 `createInkPad`。 |
| iPad 外壳 | 白板项目的原生应用。按服务名找到使用者的服务端，打开其页面，把 Apple Pencil 采样（约 240 次/秒）交给页面中的手写板。使用者不需要修改外壳。 |

安装：

```bash
pip install "inksync[discovery] @ git+https://github.com/Maimai-l/white-board.git@v2.0.0#subdirectory=packages/inksync"
```

需要 Python 3.10 或更高版本、aiohttp 3.9 或更高版本。`[discovery]` 在 macOS 以外的系统上注册 Bonjour 服务时需要。

## 2. 最小服务端

```python
from pathlib import Path
from aiohttp import web
from inksync import DefaultPolicy, FileStorage, Hub, Principal, mount, serve_sdk
from inksync.netinfo import advertise, is_local_request

PORT = 8900
ROOT = Path(__file__).parent


def authenticate(request: web.Request) -> Principal:
    return Principal(
        id=request.cookies.get("qb_user"),          # 使用者自己的身份，可以为 None
        local=is_local_request(request),
        address=request.remote or "",
    )


class QbPolicy(DefaultPolicy):
    def can_open(self, who, board_id, meta):
        return who.local or (who.id is not None and board_id.startswith(f"u{who.id}-"))

    def can_create(self, who, board_id, spec):
        return self.can_open(who, board_id, None)


app = web.Application()
hub = Hub(FileStorage(ROOT / "data" / "ink", index_fields=("paper", "question")), policy=QbPolicy())
mount(app, hub, path="/ws", authenticate=authenticate)
serve_sdk(app, prefix="/inksync/")
app.router.add_static("/static/", ROOT / "static")
advertise(app, port=PORT, source="qb", path="/")      # iPad 外壳中「来源」填 @qb
web.run_app(app, port=PORT)
```

页面：

```html
<div id="pad" style="height: 70vh"></div>
<script type="module">
  import { createInkPad } from "/inksync/inkpad.js";

  const pad = createInkPad(document.getElementById("pad"), {
    board: "u42-9709-s23-12-q3",
    create: {
      name: "9709 s23 P12 Q3",
      canvas: { mode: "fixed", width: 800, height: 1400 },
      background: { pattern: "blank" },
      layers: [{ src: "/static/q/9709-s23-12-q3.png", x: 0, y: 0, width: 800 }],
      data: { paper: "9709_s23_12", question: 3 },
    },
  });
  pad.on("status", (s) => console.log("同步状态", s));
</script>
```

## 3. 服务端接口

### 3.1 导出的名称

```python
from inksync import (
    FileStorage, Hub, Spaces,              # 存储、单个空间、多个空间
    Policy, DefaultPolicy, Principal,      # 规则与身份
    mount, serve_sdk,                      # 挂载同步接口、提供前端文件
    __version__,
)
from inksync.netinfo import advertise, is_local_request
```

其他模块和名称属于内部实现。

### 3.2 `mount(app, target, path="/ws", authenticate=None, info=None)`

| 参数 | 说明 |
| --- | --- |
| `target` | 一个 `Hub`（单个空间，空间名为空串），或一个 `Spaces`（多个空间）。 |
| `authenticate` | `request -> Principal`。连接建立时调用一次。默认：`id` 为 None，`local` 按 `is_local_request` 判断。 |
| `info` | `request -> dict`，随快照发给前端（前端的 `info` 属性）。默认为空对象。 |

`mount` 负责：启动时开始自动保存；关闭时断开所有连接；清理时等待保存完成。

### 3.3 `serve_sdk(app, prefix="/inksync/")`

在 `prefix` 下提供前端文件，入口为 `<prefix>inkpad.js`。所有响应带 `Cache-Control: no-cache`。前端版本与服务端相同。

### 3.4 `FileStorage(root, index_fields=())`

| 参数 | 说明 |
| --- | --- |
| `root` | 存储目录。其中 `boards/<id>.wbz` 是白板文件，`index.sqlite` 是索引（可随时重建），`space.json` 是空间级数据。 |
| `index_fields` | 需要列入索引的 `data` 字段名，可用点号取嵌套字段。只有索引中的字段能用于 `hub.list_boards` 的结果。 |

### 3.5 `Hub(storage, policy=None, autosave=3.0, idle_unload=120.0)`

一个空间的运行时。所有方法在事件循环线程中调用。

| 方法 | 说明 |
| --- | --- |
| `hub.on(event, callback)` | `"created"`、`"saved"`、`"deleted"`：`callback(board_id, meta)`。`"changed"`：`callback(board_id, meta, op)`，每个被接受的操作一次。回调在事件循环线程中调用，不能阻塞。返回取消订阅的函数。 |
| `hub.board_meta(board_id)` | 元数据，不存在时为 None。 |
| `hub.list_boards(offset=0, limit=100, prefix="", order="updated")` | 索引行列表：`id`、`name`、`created`、`updated`、`canvas`（只含 `mode`）以及 `index_fields` 中的字段（放在 `data` 下）。`order` 为 `updated`、`created` 或 `name`，均为从新到旧或按名称升序。 |
| `hub.count(prefix="")` | 白板数。 |
| `await hub.strokes(board_id)` | 当前全部笔画的副本，按层叠顺序排列，格式见 5.3 节。白板不存在时为 None。 |
| `await hub.create_board(board_id, spec)` | 由服务端新建白板，`spec` 与前端 `create` 相同。已存在时抛出 `ValueError`。 |
| `await hub.edit_meta(board_id, patch)` | 由服务端修改元数据，规则见 5.2 节；不受 `Policy` 和 `protected_data_keys` 限制。已连接的前端立即收到新的元数据。 |
| `await hub.delete_board(board_id)` | 删除白板。显示它的前端收到 `deleted` 事件。 |
| `hub.connections()` | 当前连接的只读列表，每项有 `id`、`principal`、`board`、`readonly`、`device`。 |
| `await hub.refresh()` | 规则的判断依据变化后调用：重新计算每个连接的 `caps` 并通知前端。 |

### 3.6 `Spaces(factory)`

多个空间时使用。`factory(name) -> Hub | None`：第一次请求某个空间时调用，返回 None 表示空间不存在。`Spaces.get(name)` 返回已创建的 Hub；`await Spaces.close(name)` 关闭一个空间。没有连接超过 10 分钟的空间自动关闭。

### 3.7 `Principal` 与 `Policy`

```python
@dataclass(frozen=True)
class Principal:
    id: str | None
    local: bool
    address: str
    attrs: Mapping[str, Any] = field(default_factory=dict)
```

`Policy` 的方法。`DefaultPolicy` 实现了全部方法，使用者继承并覆盖需要的部分：

| 方法 | `DefaultPolicy` |
| --- | --- |
| `can_open(who, board_id, meta) -> bool` | True |
| `can_create(who, board_id, spec) -> bool` | True |
| `can_write(who, meta) -> bool` | True |
| `can_clear(who, meta) -> bool` | True |
| `can_edit_meta(who, meta, patch) -> bool` | `who.local` |
| `can_unlock(who, meta) -> bool` | `who.local` |
| `create_limit(who) -> tuple[int, float] \| None` | 本机 None（不限）；其他 `(60, 60.0)`：60 秒内最多新建 60 块。按 `who.id` 计数，为 None 时按 `who.address`。 |
| `validate_data(data) -> dict \| None` | 原样返回。返回 None 表示拒绝。 |
| `protected_data_keys: frozenset[str]` | 空。列出的 `data` 键只能由 `hub.edit_meta` 修改。 |
| `allow_src(src) -> bool` | 以 `/` 开头、不含 `..`、`\`、`//` 开头和 `:` 的路径。 |

`meta` 在新建前为 None。规则在每次动作时调用。

### 3.8 `advertise(app, port, source, path="/", name=None)`

在局域网注册 `_whiteboard._tcp` 服务，TXT 记录带 `source` 和 `path`。iPad 外壳「来源」设为 `@<source>` 后只连接这个服务，并打开 `path`。在 `web.run_app` 之前调用；`port` 为实际监听的端口。注册失败只记录日志。

## 4. 前端接口

### 4.1 `createInkPad(container, options)`

在 `container` 中创建手写板并返回。手写板铺满容器并跟随容器尺寸变化，容器需要有高度。

| 选项 | 类型 | 说明 |
| --- | --- | --- |
| `board` | 字符串 | 必填。`^[A-Za-z0-9_-]{1,64}$`。 |
| `space` | 字符串 | 空间名称，默认空串。 |
| `create` | 对象 | 白板不存在时用于新建：`{name, canvas, background, layers, data}`，格式见 5.1 节。省略时，白板不存在会触发 `error` 事件（`reason: "board"`）。白板已存在时忽略。 |
| `readonly` | 布尔值 | 只读打开。 |
| `tool` | 对象 | 初始工具：`{tool, color, width, eraserMode}`。`tool` 为 `pen`、`marker`、`highlighter`、`eraser`；`color` 为 `#rrggbb`；`width` 为 0.5 到 96；`eraserMode` 为 `object` 或 `pixel`。 |
| `fingerDraw` | 布尔值 | 手指是否书写，默认 false（只有 Apple Pencil 和鼠标书写，手指平移缩放）。 |
| `transport` | 见 4.4 节 | 默认连接提供页面的服务端的 `/ws`。 |
| `storage` | 字符串 | 本地存储名称前缀，默认 `inksync:<space>`。同一来源下的不同项目应使用不同的前缀。 |
| `report` | 字符串 | 页面报错的上报地址（POST JSON）。默认不上报。 |
| `undoLimit` | 数字 | 撤销步数，默认 200。 |
| `initial` | 对象 | `transport` 为 `"local"` 时的初始内容 `{meta, strokes}`。 |

### 4.2 返回的对象

| 成员 | 说明 |
| --- | --- |
| `open(board, {create, readonly})` | 在同一连接上切换到另一块白板，撤销记录清空。上一块白板尚未送达的书写会继续送达上一块白板。返回 Promise，白板载入后完成；失败时拒绝并触发 `error`。 |
| `setTool(tool)` | 切换工具，格式同 `tool` 选项。 |
| `undo()`、`redo()` | 撤销、重做本设备在当前白板上的修改。 |
| `clear()` | 清空当前白板（可撤销）。 |
| `fit()` | `fixed` 画布显示整个画布；其他显示全部笔迹。 |
| `zoom(factor)` | 以中心为基准缩放。 |
| `setMeta(patch)` | 修改 `name`、`background`、`layers`、`data`，规则见 5.2 节；需要 `caps.meta`。 |
| `exportPNG({layers = false, scale = 1})` | 返回 Promise，结果为 PNG data URL。范围：`fixed` 为整个画布，其他为笔迹外框加边距。`layers` 为 true 时包含图片层。 |
| `snapshot()` | `{meta, strokes}`，当前内容的副本。 |
| `load({meta, strokes})` | 替换内容并清空撤销记录。只用于 `"local"`。 |
| `on(event, listener)` | 订阅事件，返回取消订阅的函数。 |
| `destroy()` | 写入本地缓存、断开连接、移除手写板。 |

只读属性：

| 属性 | 说明 |
| --- | --- |
| `board` | 当前白板的元数据。 |
| `status` | `"online"`、`"syncing"`、`"offline"` 或 `"local"`。 |
| `tool` | 当前工具。 |
| `caps` | `{write, clear, meta, unlock}`，布尔值。 |
| `locked` | null，或白板只读的原因 `{reason, …}`（文件损坏等）。 |
| `shell` | `{active, version, bridge}`：是否在 iPad 外壳中、外壳版本。 |
| `version` | SDK 版本。 |
| `info` | 服务端 `info` 钩子提供的对象。 |

### 4.3 事件

| 事件 | 内容 | 时机 |
| --- | --- | --- |
| `status` | 状态字符串 | 连接状态变化。 |
| `history` | `{undo, redo}` | 撤销、重做变为可用或不可用。 |
| `meta` | 元数据 | 白板载入，或元数据变化（本机或其他设备）。 |
| `change` | `{board}` | 当前白板的笔迹变化（本机或其他设备）。 |
| `op` | `{board, op}` | 本机发出一个操作。 |
| `strokestart`、`strokeend` | 笔画 | 本机一笔开始、结束。 |
| `locked` | `{board, locked}` | 白板进入或解除只读。 |
| `caps` | `caps` | 权限变化。 |
| `deleted` | `{board}` | 当前白板被删除。 |
| `error` | `{reason, detail}` | 服务端拒绝：`board`（不存在且没有 `create`）、`create`（`create` 不合法）、`denied`（规则拒绝）、`rate`（新建过于频繁）、`space`、`version`。 |
| `rejected` | `{board, op, reason}` | 已在本机显示的操作被服务端拒绝（`denied`、`locked`、`deleted`、`invalid`）。之后手写板重新载入服务端的内容。 |
| `interrupted` | `{count}` | 笔画连续被系统打断。 |
| `outdated` | `{sdk, server}` | 服务端已升级，页面仍在使用旧的前端文件。宿主决定何时重新载入页面。 |
| `shell` | `shell` 属性 | 外壳握手完成。 |
| `message` | 消息对象 | 服务端发来的扩展消息。 |

### 4.4 `transport`

| 取值 | 行为 |
| --- | --- |
| 省略 | WebSocket 连接提供页面的服务端的 `/ws`。 |
| `{url}` | WebSocket 连接指定地址，例如 `ws://mac.local:8900/ws`。 |
| `"local"` | 不连接服务器，`status` 为 `"local"`。内容依次取自 `initial`、本地缓存、空白板；宿主通过 `op` 事件或 `snapshot()` 保存。 |

### 4.5 页面行为

- 一个页面可以有多块手写板，每块只接收起点位于自身区域内的笔画，外壳的采样也是如此。
- 手写板只在自身区域内拦截触摸手势；页面其他位置的输入框、按钮和滚动照常工作。外壳关闭了系统的手写文字输入，页面输入框用键盘输入。
- 离线时的书写保存在 IndexedDB，重连后送达。每个存储前缀最多缓存 200 块白板的内容，超出时删除最久未用的；有未送达书写的白板不删除。
- SDK 在页面上只安装一个全局对象 `window.whiteboardShell`（外壳调用它）。

## 5. 数据格式

### 5.1 元数据

```jsonc
{
  "id": "u42-9709-s23-12-q3",
  "name": "9709 s23 P12 Q3",
  "created": 1790000000.0,                // 秒，服务端维护
  "updated": 1790000100.0,                // 秒，服务端维护
  "canvas": {"mode": "fixed", "width": 800, "height": 1400},
  "background": {"pattern": "blank", "paper": "#ffffff"},
  "layers": [{"src": "/static/q/9709-s23-12-q3.png", "x": 0, "y": 0, "width": 800}],
  "data": {"paper": "9709_s23_12", "question": 3}
}
```

| 字段 | 规则 |
| --- | --- |
| `name` | 最长 64 个字符。 |
| `canvas` | `{"mode":"infinite"}`（默认）；`{"mode":"column","width":W}`：宽度固定，向下无限；`{"mode":"fixed","width":W,"height":H}`：宽高固定。W、H 为 1 到 100000。可书写范围为 `0 ≤ x ≤ W`，以及 `0 ≤ y`（`column`）或 `0 ≤ y ≤ H`（`fixed`）。新建后不可修改。 |
| `background` | `pattern`：`blank`、`grid`、`lines`、`dots`，默认 `grid`；`paper`：纸张颜色 `#rrggbb`。 |
| `layers` | 最多 1000 项，按顺序绘制。每项 `{src, x, y, width, height?, z?, sheet?}`。坐标为白板坐标（缩放为 1 时 1 单位 = 1 CSS 像素）。`height` 省略时按图片比例。`z`：`below`（默认，笔迹下方）或 `above`（笔迹上方）。`sheet: true`：画成带阴影的一张纸，此时不画背景图案。`src` 须通过 `Policy.allow_src`，可以包含 `{w}`：前端替换为 640、1024、1600、2400 之一（按显示所需的像素宽度），服务端可按档位缓存图片。 |
| `data` | JSON 对象，序列化后不超过 16 KB，经 `Policy.validate_data` 校验。服务端不解释其内容。 |

### 5.2 修改元数据

- 可修改：`name`、`background`、`layers`、`data`。不可修改：`id`、`created`、`updated`、`canvas`。
- `data` 按键合并：出现的键被替换，值为 null 的键被删除，未出现的键不变。
- `layers`、`background` 整体替换。
- 前端的 `setMeta` 需要 `caps.meta`；`patch` 中出现 `protected_data_keys` 的键时整个修改被拒绝。服务端的 `hub.edit_meta` 不受这两项限制。

### 5.3 笔画

```jsonc
{"id": "k3m9x0a1b2c4-4f2a-17", "tool": "pen", "color": "#1b1b1f", "w": 3.0,
 "p": [120.5, 40.25, 0.03, 121.0, 41.0, 0.04], "n": 42, "dev": "ipad",
 "m": [[5, 120, 40, 180, 40]], "cut": 1}
```

| 字段 | 说明 |
| --- | --- |
| `p` | 扁平数组 `[x, y, 压感, …]`，白板坐标，压感 0 到 1，最多 20000 个点。 |
| `tool` | `pen`、`marker`、`highlighter`。 |
| `w` | 线宽，0.5 到 96。 |
| `n` | 层叠顺序，从小到大绘制。 |
| `m` | 可选，像素橡皮擦去的部分：`[[半径, x0, y0, x1, y1, …], …]`。 |
| `cut` | 可选，1 表示起点是切口，2 表示终点是切口，3 表示两端都是。 |

笔画的轮廓算法见白板项目的 `stroke.js`（前端）与 `whiteboard/freehand.py`（Python），服务端渲染时可以参考。

## 6. iPad 外壳

- 用户在 iOS「设置」App 的外壳页面把「来源」设为 `@qb`，或打开 `whiteboard-shell://open?source=qb`。外壳只连接 `source` 为 `qb` 的服务，并打开其 `path`。
- 外壳在每次页面加载后请求 `GET /ipad/version` 检查外壳更新。qb 的服务端可以不提供这个地址：请求失败时外壳不做任何提示。
- Apple Pencil 采样由外壳交给页面中的全部手写板，使用者不需要任何代码。

## 7. 限制

| 项目 | 上限 |
| --- | --- |
| 白板 id | `^[A-Za-z0-9_-]{1,64}$` |
| 空间名称 | `^[a-z0-9-]{0,32}$` |
| 单条 WebSocket 消息 | 8 MB |
| 每笔点数 | 20000（前端自动切分更长的笔画） |
| 每个操作的笔画数 | 2000 |
| `data` | 16 KB |
| `layers` | 1000 项 |
| 新建速率 | 由 `Policy.create_limit` 决定 |
| 白板数量 | 不设上限 |

## 8. 并行开发建议

- 2.0 发布前，页面可以用 `transport: "local"` 开发界面：接口与联网时相同，只是不同步。
- 身份：建议用 Cookie 或页面地址中的令牌，在 `authenticate` 中解析。白板 id 中包含用户标识，并在 `can_open`、`can_create` 中核对，可以防止用户打开他人的白板。
- 批改：用 `hub.on("changed")` 或 `hub.on("saved")` 得知答案变化，用 `hub.strokes()` 读取笔画。

## 9. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-30 | 初版。 |

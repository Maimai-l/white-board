# inksync 0.2 需求与规格

状态：已在 inksync 0.2.0 中实现。实现与本文的差别见第 12 节。对外接口的正式说明见 [packages/inksync/README.zh-CN.md](../../packages/inksync/README.zh-CN.md)（另有英文版）。

本文说明手写同步组件（服务端 inksync 与前端手写板）重新划分边界的需求和规格。白板应用和刷题项目（qb）都是这个组件的使用者。

第二版根据对照代码的审查做了修改，修改记录见第 11 节。

## 1. 背景

inksync（包版本 0.1.0，随白板应用 1.0.1 发布）是从白板应用中按文件抽取出来的。抽取时以「行为不变」为约束，白板应用自己的决定随代码一起进入了包中。用刷题项目核对时发现以下问题（括号内为代码位置）：

| 编号 | 问题 | 后果 |
| --- | --- | --- |
| P1 | 元数据字段固定（`models.sanitize_meta` 只保留固定字段；`store.edit_meta` 保留白板文件中的其他顶层内容，但元数据本身的其他字段被丢弃） | 使用者不能在白板上记录自己的信息，例如题号。 |
| P2 | 画布只有「无限」「笔记」「文档」三种（`KINDS`），宽高不可指定 | 不能规定作答区域的大小。 |
| P3 | 底图只能有一张，地址必须在 `/apps/` 下（`UNDERLAY_SRC_RE`） | 使用者的静态文件布局受白板应用的路由约束。 |
| P4 | 所有白板在同一目录、同一份索引中，完整列表发给客户端；索引每次改动整份重写（`store._write_index`）；查找逐条扫描（`get_meta`） | 白板数量增长后变慢。应用白板与用户白板混在一起，为防止无权限设备不停新建白板，加入了每个应用 5000 块的上限（`MAX_APP_BOARDS`）。 |
| P5 | 存储初始化时必须至少有一块白板，并且有「当前白板」（`store._load_index`、`Hub.__init__`） | 使用者的存储中会出现一块不需要的空白板。 |
| P6 | 白板管理消息（`sel`、`newboard`、`rename`、文件夹、排序等）和「跟随当前白板」写在核心协议和 `Hub` 中 | 使用者不需要这些功能，却不能关闭，也不能加入自己的消息。 |
| P7 | 文档板（PDF 页面）写在核心模型中（`KINDS` 中的 `doc`、`sanitize_doc`） | 核心包含白板应用专有的数据结构。 |
| P8 | 权限固定为四个名称；`pin_policy` 拿不到请求或用户身份 | 使用者无法按用户限制可打开的白板。 |
| P9 | 自动保存在事件循环线程中同步编码和写盘（`Hub.save_all`）；1 万笔约 2 秒 | 保存期间所有连接的同步暂停。 |
| P10 | 载入内存的白板在服务运行期间不会释放（`Hub._boards`） | 打开过的白板越多，占用内存越多。 |
| P11 | 协议握手没有版本号；页面不会在服务端升级后重新载入 | 服务端升级后，iPad 上已打开的旧页面继续用旧脚本与新服务端通信。 |
| P12 | 手写板核心向白板应用的接口发请求：`report.js` 和 `perf.js` 请求 `/api/debug`，`docpages.js` 请求 `/api/doc`（`/api/thumb`、`/api/recording` 只由白板应用自己的模块请求） | 在其他服务端上这些请求返回 404。 |
| P13 | 前端本地存储名称固定为 `whiteboard`；视图位置按白板写入 `localStorage` 且从不清理；每块白板的离线缓存从不清理；「手指书写」设置全局共享；待发队列不区分白板（`net.js` 的 `outbox`、`cache.js` 的 `pending`） | 同一来源下的多个使用者互相影响；白板数量多时 `localStorage` 会写满；无法在一条连接上安全地切换白板。 |
| P14 | 前端没有独立分发；嵌入接口直接返回内部类 `InkPad` | 使用者只能复制目录，升级时无法核对版本；内部属性被当作接口使用。 |
| P15 | 前端向宿主页面写入全局状态（`<html data-shell>`） | 影响宿主页面。 |
| P16 | inksync 的测试放在白板应用的 `tests/` 中；白板应用没有使用包自己的 `mount` | 包没有被独立验证过。 |

一处更正：此前提到「笔画工具种类固定」是问题。复核后，工具种类必须固定：每种工具的绘制算法要在所有客户端和导出中一致，使用者自定义新的绘制算法会使其他客户端无法显示。使用者需要的「红色批改笔」一类工具，用现有工具加颜色和粗细的预设即可实现，属于界面层，不需要协议支持。

## 2. 需求

### 2.1 使用者

| 使用者 | 使用方式 |
| --- | --- |
| 白板应用 | Mac 端完整界面，iPad 跟随 Mac 当前白板；白板列表、文件夹、文档板、缩略图、导出。 |
| 在白板应用中安装的应用（`apps/`） | 由白板应用提供页面和同步，每个应用的白板与用户白板分开保存。 |
| 独立服务端（例如 qb） | 自己的 aiohttp 服务端挂载 inksync，自己的页面嵌入手写板，不运行白板应用。 |

截至本文，没有任何已安装的应用，也没有任何带 `app` 字段的白板（已向用户确认）。1.0.1 的嵌入接口因此不需要兼容。

### 2.2 功能需求

| 编号 | 需求 |
| --- | --- |
| F1 | 使用者可以为每块白板声明画布：无限、固定宽度向下延伸、固定宽高。 |
| F2 | 使用者可以为每块白板声明背景样式、纸张颜色，以及任意数量的图片层（位置和尺寸以白板坐标给出，可以在笔迹下方或上方）。图片地址是否允许由使用者决定。 |
| F3 | 使用者可以在白板上保存自己的字段（`data`），服务端原样保存和转发，由使用者校验；使用者可以指定只允许服务端修改的字段。 |
| F4 | 每个服务可以有多个互相独立的空间（space）。每个空间有自己的存储、索引和规则；白板 id 只在空间内唯一。 |
| F5 | 核心协议只包含同步本身：握手、打开白板、操作、实时笔迹、回执、心跳、只读状态、错误。其他消息由扩展注册。 |
| F6 | 使用者通过钩子决定：请求对应的身份；谁可以打开、新建、书写、清空、修改元数据、解除只读；新建白板的速率。 |
| F7 | 一条连接在同一时间显示一块白板，可以在不断开连接的情况下切换到另一块白板；切换前未送达的操作仍然送到它们所属的白板。 |
| F8 | 可以以只读方式打开白板（查看，不书写）。 |
| F9 | 服务端提供事件：白板新建、内容变化、保存、删除；并提供服务端读取笔画、修改元数据、导入白板文件的接口。 |
| F10 | 前端作为带版本号的包，随 Python 包一起分发，由服务端在固定路径提供。 |
| F11 | 前端的接口是一个明确列出的对象，内部类不对外。 |
| F12 | 前端的本地存储按空间区分，视图位置和离线缓存有上限并自动清理；有未送达操作的白板不被清理。 |
| F13 | 服务端升级后，前端能得知并通知宿主；白板应用据此重新载入页面。 |
| F14 | 前端不访问白板应用专有的接口；除 6.4 节列出的外壳接口外，不修改宿主页面的全局状态。报错上报地址可配置，默认关闭。 |
| F15 | iPad 外壳的接口（网页与外壳之间的消息、Bonjour 记录、`/ipad/version`）写成文档，作为稳定接口。 |
| F16 | 操作因权限不足被拒绝时，前端通知宿主并交出被拒绝的操作，不静默丢弃。 |

### 2.3 非功能需求

| 编号 | 需求 | 验收方式 |
| --- | --- | --- |
| N1 | 保存不阻塞同步：事件循环线程中只复制，编码和写盘在工作线程中进行。 | 1 万笔的白板保存期间，其他白板的操作回执延迟不超过 50 ms（测量；codec 是纯 Python，受 GIL 影响，达不到时改为进程池编码）。 |
| N2 | 按 id 查找白板为 O(1)；修改一块白板不重写整份索引。 | 2 万块白板时，新建一块白板不超过 20 ms，启动不超过 1 s。 |
| N3 | 长时间未使用的白板从内存中释放。 | 打开 1000 块白板后，内存中的白板数回落。 |
| N4 | inksync 不引用白板应用的任何模块，可以在只安装 inksync 的环境中独立测试。 | CI 在只安装 inksync 的虚拟环境中运行它自己的测试，并检查没有引用 `whiteboard`。 |
| N5 | 公开接口在 `__all__` 和前端入口中列出。inksync 自己的测试和 qb 示例只经公开接口使用 inksync。 | 同上。白板应用在同一仓库内，可以使用内部模块，但 inksync 修改内部模块时不需要为外部使用者保持兼容。 |
| N6 | 白板应用的用户功能与 1.0.1 相同；已有数据无需用户操作即可继续使用。唯一的变化见 7.4 节（应用白板不再出现在用户白板列表中）。 | 现有测试全部通过；用 1.0.1 的存储目录启动新版本的测试。 |
| N7 | 笔画格式和操作格式不变，1.0.1 页面留下的离线待发队列在升级后仍然送达。 | 用 1.0.1 页面留下的 IndexedDB 数据测试。 |
| N8 | 不丢失已被回执的操作：保存与新操作并发、白板删除与保存并发、进程退出时，都不丢失、不复活。 | 并发测试（第 8 节 A5）。 |

### 2.4 不做的事

- 不内置用户系统。身份由使用者的钩子提供。
- 不改变笔画的几何与绘制算法，不增加新的工具种类（见第 1 节的更正）。
- 不做多人同时编辑同一笔画的冲突合并；现有「笔画整条增删、遮罩整份替换」的模型不变。
- 不做服务端识别手写文字。
- 服务端把白板渲染成图片（批改用）列为后续工作，不在 1.1 范围内。1.1 提供读取笔画的接口，使用者可以自行渲染。
- 不支持从 1.1 降级到 1.0.x（见 4.6 节）。

## 3. 总体结构

```
inksync（Python 包，同时分发前端）
├── core      操作的校验与应用、序号与纪元、操作历史（无输入输出）
├── storage   存储接口 Storage；默认实现 FileStorage（.wbz、内存索引与 SQLite、space.json）
├── hub       一个空间的运行时：连接、内存中的白板、广播、保存、释放、事件、扩展消息
├── spaces    多个空间的注册与生命周期
├── policy    身份 Principal 与规则 Policy，默认实现 DefaultPolicy
├── server    WebSocket 协议、mount、前端文件路由 serve_sdk
├── netinfo   本机判断、Bonjour 注册 advertise（沿用）
└── web/      前端 SDK（ES 模块），入口 inkpad.js

白板应用（使用者之一）
├── 扩展 whiteboard.boards   跟随当前白板、白板列表、文件夹、排序、新建与删除、1.0.x 页面兼容
├── 扩展 whiteboard.docs     文档板：导入、页面图片、导出、页面读取权限
├── 空间：""（用户白板）和每个已安装应用一个空间
└── 界面 app.js / ui*.js     在 SDK 的内部类上构建
```

依赖方向只能是白板应用依赖 inksync。

## 4. 服务端规格

### 4.1 空间与挂载

单个空间：

```python
from inksync import FileStorage, Hub, mount, serve_sdk

hub = Hub(FileStorage("data/qb"), policy=QbPolicy())
mount(app, hub, path="/ws")
serve_sdk(app, prefix="/inksync/")
```

多个空间：

```python
spaces = Spaces(factory=lambda name: Hub(FileStorage(root / name), policy=…) if name in installed() else None)
mount(app, spaces, path="/ws")
```

| 规则 | 说明 |
| --- | --- |
| 空间名称 | `^[a-z0-9-]{0,32}$`；空串为默认空间。单空间挂载只接受空串。 |
| 创建 | `Spaces.get(name)` 第一次被请求时调用 `factory`，返回 None 表示该空间不存在，回复 `{"t":"error","reason":"space"}` 并关闭连接。新建的 Hub 立即开始自动保存。运行中安装的应用因此无需重启即可使用。 |
| 关闭 | 没有连接、没有内存中的白板超过 10 分钟的空间被关闭（保存完成后移除）。`Spaces.close(name)` 立即关闭一个空间（用于卸载应用）。 |
| 生命周期 | `mount` 接管启动、关闭（断开所有连接）和清理（等待所有保存完成）。白板应用也通过 `mount` 挂载，不再自行实现这三步。 |

### 4.2 身份与规则

```python
@dataclass(frozen=True)
class Principal:
    id: str | None          # 使用者定义的身份，例如用户 id；None 表示匿名
    local: bool             # 请求来自本机（netinfo.is_local_request）
    address: str            # 对端地址，用于匿名身份的速率限制
    attrs: Mapping[str, Any] = {}

authenticate: Callable[[web.Request], Principal]     # mount 参数，默认只判断是否本机，id 为 None
```

`Policy` 的方法（每个方法都有默认实现，使用者按需覆盖）：

| 方法 | 默认（`DefaultPolicy`） |
| --- | --- |
| `can_open(who, board_id, meta)` | 允许。 |
| `can_create(who, board_id, spec)` | 允许。 |
| `can_write(who, meta)` | 允许。 |
| `can_clear(who, meta)` | 允许。 |
| `can_edit_meta(who, meta, patch)` | 仅本机。 |
| `can_unlock(who, meta)` | 仅本机。 |
| `create_limit(who)` | 返回 `(块数, 秒数)` 或 None（不限）。本机不限；其他为 `(60, 60)`，按 `who.id`（为 None 时按 `who.address`）计数。超出时拒绝新建（`reason: "rate"`）。 |
| `validate_data(data)` | 原样接受（仍受 4.4 节的大小限制）。 |
| `protected_data_keys` | 空集合。列出的 `data` 键只能由服务端接口修改，客户端的 `meta` 操作中出现这些键时整个操作被拒绝。 |
| `allow_src(src)` | 以 `/` 开头的同源路径，不含 `..`、`\` 和协议名。 |
| `caps(who, meta)` | 由以上方法得出 `{write, clear, meta, unlock}`，随快照发给客户端。 |

- `can_clear` 默认允许：能书写的连接本来就能用 `remove` 删除全部笔画，单独限制 `clear` 只影响界面入口。白板应用保留「清空白板」权限作为界面上的防误操作。
- 规则在每次动作时调用，不缓存。`hub.refresh()` 为所有连接重新计算 `caps`，有变化时发送 `caps` 消息。
- 连接建立时计算一次 `Principal` 并保存在连接上；使用者更改身份判断方式后调用 `hub.reauthenticate()`（按保存的请求重新计算）。
- 白板应用把自己的四项权限（manage、settings、clear、export）映射到这些方法上，并通过扩展消息把权限名单发给自己的界面。

### 4.3 Hub

```python
hub = Hub(storage, policy=None, autosave=3.0, idle_unload=120.0)

hub.on("created" | "saved" | "deleted", callback)   # callback(board_id, meta)
hub.on("changed", callback)              # callback(board_id, meta, op)，每个被接受的操作一次
hub.register("sel", handler)             # 扩展消息，见 5.5 节
hub.set_hello(handler)                   # 扩展决定没有指定白板或要求跟随的 hello 打开哪块白板，见 5.2 节
hub.set_encoder(encoder)                 # 按连接改写发出的消息，见 5.6 节

await hub.open(conn, board_id, reply="init")   # 把连接移到另一块白板并发送快照
await hub.strokes(board_id)              # 当前笔画（已载入的取内存，否则从存储读），按 n 排序的副本
await hub.edit_meta(board_id, patch)     # 服务端修改元数据：经过内存中的白板，记入操作并广播
await hub.create_board(board_id, spec) / await hub.delete_board(board_id)
await hub.import_file(path) -> meta      # 导入存储目录之外的 .wbz（新 id）
hub.board_meta(board_id)                 # 索引中的元数据
hub.connections()                        # 当前连接，只读；连接有 id、principal、device、board、readonly
await hub.refresh() / await hub.reauthenticate()
```

白板在内存中的状态（`BoardRuntime`）：笔画、元数据、`seq`、`epoch`、操作历史（最近 4000 条）、改动计数 `version`、最后使用时间。

| 行为 | 规格 |
| --- | --- |
| 载入 | 第一次被打开时在工作线程中从存储读入。同一块白板同时只有一个载入任务，其他请求等待同一个结果。 |
| 保存 | 每 `autosave` 秒一轮。对每块 `version` 大于已保存版本的白板：在事件循环线程中记下 `version`，复制元数据字典和每个笔画字典（浅复制；笔画的点列在创建后不再修改，遮罩在 `mask` 操作中整体赋值或删除，均只改笔画字典本身）；在工作线程中编码、压缩、写入白板文件；完成后回到事件循环线程，把已保存版本设为记下的 `version`，并更新索引。写盘期间到达的操作使 `version` 继续增加，下一轮再保存。同一块白板同时最多一个保存任务。保存失败时已保存版本不变，下一轮重试。 |
| 删除 | 先把白板 id 加入已删除集合，等待该白板进行中的保存结束，再删除文件和索引行。工作线程写文件前检查已删除集合，命中则放弃写入。已删除白板上收到的操作只回执（不含 `op`），不应用，也不会重新载入这块白板。 |
| 释放 | 没有连接（只读连接也算连接）、已全部保存、没有进行中的载入或保存、最后使用超过 `idle_unload` 秒的白板，释放笔画和操作历史，只保留 `(epoch, seq)`。再次载入时沿用这对值，因此停留在 `seq` 的客户端得到空的 `sync`；落后的客户端因操作历史已释放而得到完整的 `init`。服务进程重启后 `epoch` 更新，与 1.0.x 相同。 |
| 关闭 | 停止接受新连接，等待进行中的保存完成，再保存所有仍有改动的白板。 |
| 事件回调 | 在事件循环线程中调用；回调抛出的异常只记录日志。 |

`Hub` 不再有「当前白板」和文件夹；这两项移到白板应用的扩展中（第 7 节）。

### 4.4 白板元数据

```jsonc
{
  "id": "q-9709-s23-12-3",
  "name": "9709 s23 P12 Q3",
  "created": 1790000000.0,
  "updated": 1790000100.0,
  "canvas": {"mode": "fixed", "width": 800, "height": 1200},
  "background": {"pattern": "blank", "paper": "#ffffff"},
  "layers": [{"src": "/static/q/9709-s23-12-3.png", "x": 0, "y": 0, "width": 800, "height": 520}],
  "data": {"paper": "9709_s23_12", "question": 3}
}
```

| 字段 | 规格 |
| --- | --- |
| `id` | `^[A-Za-z0-9_-]{1,64}$`，在空间内唯一，同时是文件名。 |
| `name` | 字符串，最长 64 个字符。 |
| `created`、`updated` | 服务端维护。 |
| `canvas` | `{"mode":"infinite"}`（默认）；`{"mode":"column","width":W}`：宽度固定，向下延伸；`{"mode":"fixed","width":W,"height":H}`：宽高固定。W、H 在 1 到 100000 之间。可书写范围：`column` 为 `0 ≤ x ≤ W, y ≥ 0`；`fixed` 为 `0 ≤ x ≤ W, 0 ≤ y ≤ H`。新建后不可修改（已有笔画的位置依赖它）。 |
| `background` | `pattern`：`blank`、`grid`、`lines`、`dots` 之一，默认 `grid`；`paper`：`#rrggbb`，默认与 1.0.x 相同的纸色。 |
| `layers` | 图片层数组，最多 1000 项，按顺序绘制。每项 `{src, x, y, width, height?, z?, sheet?}`。`height` 省略时按图片比例计算。`z` 为 `below`（默认，笔迹下方）或 `above`（笔迹上方，不接收输入）。`sheet` 为 true 时这一层画成一张带阴影的纸，层与层之间的空白处用画布外的颜色（与 1.0.x 文档板的页面相同），此时 `background.pattern` 不绘制。`src` 须通过 `Policy.allow_src`，最长 512 个字符；可以包含 `{w}`，见下文。 |
| `data` | JSON 对象，序列化后不超过 16 KB，经 `Policy.validate_data` 校验。 |

- 可修改字段：`name`、`background`、`layers`、`data`。客户端通过 `meta` 操作修改，经 `Policy.can_edit_meta` 许可；服务端通过 `hub.edit_meta` 修改。`canvas`、`id`、`created` 不可修改。
- `meta` 操作中的 `data` 按键合并：出现的键被替换，值为 `null` 的键被删除，未出现的键不变。`layers` 和 `background` 整体替换。
- 未知字段被丢弃。服务端不解释 `data` 的内容。
- 前端据 `canvas` 限制书写范围和视图：`column` 与 1.0.x 的笔记相同（接近页宽时吸附）；`fixed` 下落点在范围外的笔画不开始，视图不能移出范围，`fit()` 显示整个画布。

图片层的加载（前端）：

| 规则 | 说明 |
| --- | --- |
| 可见范围 | 只加载与视野相交的层，以及视野上下各 1 层；其余层释放图片。 |
| `{w}` | 替换为以下档位之一：640、1024、1600、2400，取不小于「显示宽度 × 设备像素比」的最小档位；视野外的层最多取 1024。档位固定，因此服务端可以长期缓存每个档位的图片。 |
| 没有 `{w}` | 按原图加载。 |

### 4.5 存储

```python
class Storage(Protocol):
    # 以下方法只在事件循环线程中调用
    def get_meta(self, board_id) -> dict | None
    def list(self, offset=0, limit=100, prefix="", order="updated") -> list[dict]   # 索引行
    def count(self, prefix="") -> int
    def create(self, meta) -> dict
    def update_index(self, meta, mtime) -> None
    def remove(self, board_id) -> bool
    kv: KeyValue                                   # 空间级的小数据，见下表

    # 以下方法在工作线程中调用，只读写白板文件，不访问索引
    def read_file(self, board_id) -> tuple[dict, list[dict], dict | None]   # meta, strokes, problem
    def write_file(self, meta, strokes) -> float   # 返回文件修改时间
    def delete_file(self, board_id) -> None
    def backup_file(self, board_id) -> Path | None
    def import_file(self, path, new_id) -> dict
```

`FileStorage(root, index_fields=(), convert_meta=None)`：

| 项目 | 规格 |
| --- | --- |
| 白板文件 | `boards/<id>.wbz`，格式见 4.6 节。白板文件是元数据和笔画的唯一来源。 |
| 索引 | 内存中保存全部索引行（按 id 的字典）。索引行包含 `id`、`name`、`created`、`updated`、`canvas.mode`、白板文件的修改时间，以及 `index_fields` 列出的 `data` 字段（可以用点号取嵌套字段，例如 `doc.name`）。 |
| 索引持久化 | `index.sqlite`（Python 标准库 sqlite3，WAL 模式），按行写入。由一个专用线程持有连接并按顺序执行写入；事件循环线程只提交写入请求。关闭时等待写入队列清空。 |
| 索引重建 | 索引缺失或损坏时从白板文件全部重建。启动时比较 `boards/` 中的文件名集合和每个文件的修改时间，只重新读取新增或修改时间不一致的文件。进程在索引写入前退出时，下次启动据此补齐。 |
| `kv` | 空间级的小数据（白板应用用于文件夹名单、排序、当前白板），保存在 `space.json`，不属于索引，重建索引时不受影响。每次修改同步写入（先写临时文件再替换），大小上限 1 MB。 |
| 空存储 | 允许没有任何白板。不存在「当前白板」。 |
| `list` 的排序 | `updated`、`created`、`name`，或使用者提供的 id 列表（列表中的在前，按列表顺序；其余按 `created` 从新到旧排在前面）。 |

### 4.6 文件格式与升级

`.wbz` 的 `v` 升到 2，笔画部分不变。

读取 `v` 为 1 的文件时，在内存中转换元数据：

| 1.0.x 字段 | 1.1 |
| --- | --- |
| `kind: board` | `canvas: {"mode":"infinite"}` |
| `kind: note` | `canvas: {"mode":"column","width":1000}` |
| `kind: doc` | `canvas: {"mode":"fixed", …}`，宽高取各页排列后的外框（与 `docs.layout` 相同的算法，页间距 24） |
| `background: "grid"` 等 | `background: {"pattern":"grid"}` |
| `underlay` | `layers` 的第一项 |
| `folder`、`app`、`doc` | 移入 `data` |

之后调用 `convert_meta` 钩子；白板应用在其中为文档板生成每页一个 `sheet` 图片层。文件在下次保存或修改元数据时以 `v: 2` 写回。

首次以 1.1 打开一个 1.0.x 存储目录：

1. 白板应用现有的升级备份（`backup.backup_if_upgraded`）把 `boards/` 和 `index.json` 复制到 `backups/upgrade/`。1.1 把这一步改为：备份失败时停止启动，并告诉用户原因（例如磁盘空间不足），不做任何转换。备份范围增加 `spaces/`、`space.json` 和 `index.sqlite`（以后的版本升级使用）。
2. 建立 `index.sqlite`。
3. `index.json` 中的文件夹名单、排序和当前白板迁入 `space.json`（白板应用的扩展通过 `storage.legacy_index` 读取）。
4. 带 `app` 字段的白板移入对应应用的空间 `spaces/<应用名>/boards/`（按 2.1 节，目前没有这样的白板；这一步保证规则完整）。
5. `index.json` 改名为 `index.v1.json`，1.1 不再读写它。

降级：1.0.x 读取 `v: 2` 的文件时以只读方式打开（`reason: "newer"`），但 1.0.x 的改名和移动文件夹仍会改写这些文件的元数据，丢失 `canvas`、`layers` 和 `data`。因此不支持降级；需要回到 1.0.x 时，从 `backups/upgrade/` 恢复。更新说明中写明这一点。

## 5. 协议规格（v2）

### 5.1 核心消息

客户端发往服务端：

| `t` | 字段 | 说明 |
| --- | --- | --- |
| `hello` | `v`（2）、`client`、`space`、`board`、`since`、`epoch`、`create`、`readonly`、`follow`、`device` | 第一条消息，见 5.2 节。`device` 只用于日志。 |
| `open` | `board`、`since`、`epoch`、`create`、`readonly` | 在同一连接上切换到另一块白板。 |
| `op` | `cid`、`op`、`board` | 与 1.0.x 相同，另加 `board`：操作所属的白板。省略时为连接当前的白板。 |
| `live` | 与 1.0.x 相同 | 作用于连接当前的白板；只读连接发送的被忽略。 |
| `ping` | `ts` | 与 1.0.x 相同。 |
| `unlock` | — | 经 `Policy.can_unlock` 许可。 |

服务端发往客户端：

| `t` | 字段 | 说明 |
| --- | --- | --- |
| `init` | `v`、`server`、`client`、`board`、`strokes`、`seq`、`epoch`、`locked`、`caps`、`readonly`、`info` | 完整快照。 |
| `sync` | 同上，以 `ops` 代替 `strokes` | 断线续传。 |
| `op` | 与 1.0.x 相同 | 发给显示该白板的其他连接。 |
| `ack` | `cid`、`seq`、`op`（被接受时）、`board`、`rejected`（被拒绝时的原因） | 见 5.3 节。 |
| `live`、`pong` | 与 1.0.x 相同 | |
| `locked` | `board`、`locked` | 白板进入或解除只读，发给显示该白板的所有连接。 |
| `caps` | `caps` | 权限变化。 |
| `deleted` | `board` | 所显示的白板被删除。 |
| `error` | `reason`，可选 `detail` | `version`、`space`、`board`、`create`、`denied`、`rate`。 |

- `server` 为 `{"name":"inksync","version":"0.2.0","protocol":2,"build":"…"}`。`info` 由使用者的 `info(request)` 钩子提供。
- 协议版本：`hello` 中 `v` 大于服务端支持的版本时，回复 `{"t":"error","reason":"version","supported":[2]}`。没有 `v` 的 `hello` 只在设置了 1.0.x 兼容的空间中接受（7.3 节），否则同样回复 `version` 错误。
- 操作的种类、字段与 1.0.x 相同（`add`、`restore`、`remove`、`mask`、`clear`、`meta`），只有 `meta` 操作的可修改字段按 4.4 节扩展。

### 5.2 打开白板

| `hello` / `open` 的内容 | 结果 |
| --- | --- |
| 有 `board`，没有 `follow` | 打开这块白板。已存在时经 `can_open` 许可；不存在且有 `create` 时，依次检查 `can_create`、`create_limit`、字段校验后新建；不存在且没有 `create` 时回复 `error`（`reason: "board"`）。已存在的白板不被 `create` 修改。 |
| 有 `follow: true` 或没有 `board` | 调用扩展设置的 `set_hello` 处理函数，由它决定打开哪块白板；`board`、`since`、`epoch` 作为续传依据交给它。没有设置处理函数时回复 `error`（`reason: "board"`）。 |
| 续传 | 打开的白板与 `board` 相同、`epoch` 相同、历史包含 `since` 之后的全部操作时回复 `sync`，否则回复 `init`。与 1.0.x 相同。 |
| `readonly: true` | 连接只读，`caps.write` 为 false。 |

白板应用的跟随连接发送 `follow: true` 和上次显示的白板，扩展打开当前白板；当前白板没有变化时客户端得到 `sync`。

### 5.3 回执与被拒绝的操作

| 情况 | `ack` |
| --- | --- |
| 已接受 | `{"t":"ack","cid":…,"board":…,"seq":…,"op":…}` |
| 重复或没有改动 | `{"t":"ack","cid":…,"board":…,"seq":…}`（与 1.0.x 相同） |
| 不合法 | 同上，另加 `"rejected":"invalid"` |
| 权限不足、连接只读 | 同上，另加 `"rejected":"denied"` |
| 白板只读（`locked`） | 同上，另加 `"rejected":"locked"` |
| 白板已删除 | 同上，另加 `"rejected":"deleted"` |

前端收到带 `rejected` 的回执时，把该操作从待发队列移除，触发 `rejected` 事件并附上操作，然后重新打开当前白板以取得服务端的内容，使本地显示与服务端一致。宿主可以在 `rejected` 事件中保存这些操作。

### 5.4 前端与服务端版本

- 前端 SDK 与服务端来自同一个包，版本号相同。`serve_sdk` 在提供的 `version.js` 中写入服务端的构建号，SDK 以此作为自身的构建号。前端收到的 `server.build` 与自身不同时触发 `outdated` 事件。
- 白板应用在收到 `outdated` 后，等到没有进行中的书写、缓存已写入，再重新载入页面；同一构建号只重新载入一次（记录在 `sessionStorage`），防止循环。

### 5.5 扩展消息

- `hub.register(name, handler)` 注册一种消息；`handler(conn, msg)` 在事件循环线程中调用。名称不能与核心消息相同。
- 未注册的消息被忽略。
- 服务端向客户端发送的扩展消息，前端以 `message` 事件交给宿主。

### 5.6 按连接改写消息

`hub.set_encoder(encoder)` 设置一个函数 `encoder(conn, msg) -> msg`，Hub 发给每个连接的每条消息（包括 `op`、`ack` 中携带的元数据）都先经过它。白板应用用它实现 1.0.x 页面兼容（7.3 节）和按权限过滤白板列表。

## 6. 前端规格

### 6.1 分发

- 前端文件放在 `packages/inksync/inksync/web/`，由 `serve_sdk(app, prefix)` 提供，入口为 `<prefix>inkpad.js`。所有文件带 `Cache-Control: no-cache`。
- 白板应用从同一位置加载这些模块。`/sdk/inkpad.js` 跳转到新入口（1.0.1 的嵌入接口没有使用者，见 2.1 节）。

### 6.2 `createInkPad(container, options)`

| 选项 | 说明 |
| --- | --- |
| `space` | 空间名称，默认空串。 |
| `board` | 必填。 |
| `create` | 白板不存在时新建所用的 `{name, canvas, background, layers, data}`。 |
| `readonly` | 只读打开。 |
| `tool` | 初始工具 `{tool, color, width, eraserMode}`。 |
| `fingerDraw` | 手指是否书写。只作用于这块手写板，不写入任何全局设置。 |
| `transport` | 省略、`"local"`、`{url}` 或函数，与 1.0.x 相同。 |
| `storage` | 本地存储名称前缀，默认为 `inksync:<space>`。 |
| `report` | 报错上报地址；默认不上报。 |
| `undoLimit` | 撤销步数，默认 200。 |
| `initial` | `transport` 为 `"local"` 时的初始内容。 |

返回的对象只有下列成员：

| 成员 | 说明 |
| --- | --- |
| `open(board, {create, readonly})` | 在同一连接上切换白板，撤销记录清空。原白板未送达的操作留在待发队列中，照常送达原白板。 |
| `setTool(tool)`、`undo()`、`redo()`、`clear()`、`fit()`、`zoom(factor)` | 与 1.0.x 相同。 |
| `setMeta(patch)` | 修改 `name`、`background`、`layers`、`data`（`data` 按 4.4 节合并）。 |
| `exportPNG({layers = false, scale = 1})` | 笔迹导出为 PNG data URL；`layers` 为 true 时包含图片层（图片须同源）。 |
| `snapshot()`、`load(board)` | 与 1.0.x 相同。 |
| `on(event, listener)` | 返回取消订阅的函数。 |
| `destroy()` | 写入缓存、断开、移除。 |
| 只读属性 | `board`（当前元数据）、`status`、`tool`、`caps`、`locked`、`shell`、`version`。 |

事件：`status`、`history`、`meta`、`change`、`op`、`strokestart`、`strokeend`、`locked`、`caps`、`deleted`、`error`、`rejected`、`interrupted`、`outdated`、`shell`、`message`。

### 6.3 本地存储与待发队列

| 项目 | 规格 |
| --- | --- |
| 待发队列 | 每项记录 `{cid, board, op}`。重连或收到快照后，按顺序重发全部项，每项带自己的 `board`。只在收到对应回执后移除。 |
| IndexedDB | 库名为 `storage` 选项的值。每块白板的缓存记录最后使用时间。 |
| 清理 | 每个库最多保留 200 块白板的缓存，超出时删除最久未用的；待发队列中有项目的白板不删除。 |
| 视图位置 | 存入同一个 IndexedDB 库，随白板缓存一起清理；不再写 `localStorage`。 |
| 客户端 id | 每个库一个设备 id，保存在 `localStorage` 的 `<storage>:client`；每块手写板的连接 id 为设备 id 加随机后缀（与 1.0.x 相同）。 |
| 1.0.x 数据迁移 | 由 SDK 执行（不依赖白板应用的页面）：第一次打开前缀为 `inksync:`（默认空间）的库时，读取 1.0.x 的 `whiteboard` 库，把白板缓存迁入，把 `pending` 中的操作标上 1.0.x 缓存的 `last` 白板（1.0.x 的待发操作总是发往连接当时所在的白板，即 `last`）后放入待发队列；然后删除 `localStorage` 中的 `whiteboard.view.*` 和 `whiteboard.fingerDraw`，最后删除 `whiteboard` 库。迁移只执行一次，失败时保留 1.0.x 数据并在下次重试。 |

### 6.4 iPad 外壳接口

以下内容作为稳定接口写入 `docs/ipad-shell`，外壳和网页任何一方修改都需要兼容处理：

| 接口 | 说明 |
| --- | --- |
| 网页 → 外壳 | `window.webkit.messageHandlers.whiteboard.postMessage({type, …})`。 |
| 外壳 → 网页 | `window.whiteboardShell.receive(batch)`、`window.whiteboardShell.hello(info)`。SDK 安装 `window.whiteboardShell`，这是 SDK 唯一的全局对象。 |
| 桥接版本 | `SHELL_BRIDGE`。 |
| Bonjour | 服务类型 `_whiteboard._tcp`，TXT 字段 `host`、`port`、`version`、`name`、`source`、`path`；由 `inksync.netinfo.advertise` 注册。 |
| `GET /ipad/version` | 外壳每次加载页面后请求。其他服务端可以不提供：外壳在请求失败或返回的内容不是预期格式时不做任何提示。 |

SDK 不再设置 `<html data-shell>`，改为提供 `shell` 属性和 `shell` 事件。白板应用的界面自行设置它需要的属性。

## 7. 白板应用作为使用者

### 7.1 扩展 `whiteboard.boards`

| 功能 | 实现 |
| --- | --- |
| 当前白板与跟随 | 扩展在 `space.json` 中保存当前白板 id，并记录跟随连接。`hello` 处理函数为跟随连接打开当前白板。`sel`、`newboard`、`delboard`、`unlock` 之后用 `hub.open(conn, id, reply="switch")` 移动所有跟随连接。 |
| 白板列表 | `boards` 消息由扩展发给跟随连接，内容与 1.0.1 相同（完整列表，界面据此搜索、按文件夹计数、按类型显示标签）。用户空间只包含用户自己建立的白板，数量与 1.0.1 相同；应用白板在各自的空间中，不进入这份列表，因此不需要分页。可见范围与 1.0.1 相同：没有 manage 权限的连接只收到自己所在的那一块，文件夹名单为空（经 `set_encoder` 过滤）。 |
| 类型标签 | 索引行包含 `canvas.mode`；`index_fields` 包含 `folder` 和 `doc.name`，界面据此显示类型和文档名。 |
| 文件夹 | 白板所在的文件夹存 `data.folder`；文件夹名单（含空文件夹）存 `space.json`。`folder` 在 `protected_data_keys` 中，只能通过扩展消息 `folder`（需要 manage）修改，与 1.0.1 相同。 |
| 排序 | 白板 id 的列表，存 `space.json`，拖动排序只改这一项。`list` 按这份列表排序（4.5 节）。迁移时取自 1.0.x `index.json` 的顺序。 |
| 权限 | 四项权限映射到 `Policy`；`perms` 作为扩展消息发送。 |
| 缩略图 | 保持现有 HTTP 接口，由 `hub.on("deleted")` 删除缩略图。 |
| 本地文件 | 「打开方式」导入 `.wbz` 使用 `hub.import_file`；判断文件是否属于本存储目录的逻辑保留在白板应用中。 |
| 服务器信息 | `/api/info` 中的客户端列表取自 `hub.connections()`（`device` 代替 1.0.x 的 `role`）。录制目录等仍由白板应用的配置提供。 |

### 7.2 扩展 `whiteboard.docs`

- 文档板的元数据为 `canvas: fixed`，每页一个 `sheet: true` 的图片层，`src` 为 `/api/doc/<id>/<页码>?w={w}`；原件信息存 `data.doc`，`doc` 在 `protected_data_keys` 中。
- 页面读取权限：请求的白板是某个跟随连接当前显示的白板，或请求方有 manage 权限时允许。与 1.0.1 相同，判断改由扩展提供（它保存当前白板）。
- 需要改为读取 `data.doc` / `canvas` 的现有代码：`server.py` 的 `_doc_thumb`、`handle_doc_page`、`handle_doc_export`，`store.py` 的 `_import_extras` 与 `register_doc`，`docs.bounds`，`ui-boards.js` 的类型标签，`exporter.js` 与 `app.js` 中按 `kind === "doc"` 的分支，`boardstate.js` 的 `doc`、`pages`、`limits`，`renderer.js` 的文档页绘制（改为通用的 `sheet` 图片层），`docpages.js`（改为通用的图片层加载，4.4 节）。导出使用 `hub.strokes`。

### 7.3 1.0.x 页面兼容

白板应用在 1.1 期间为用户空间开启 1.0.x 兼容：

- 接受没有 `v` 的 `hello`（不带 `pin`），视为 `follow: true`。
- 对这些连接，`set_encoder` 把发出的消息改写为 1.0.x 的形式：快照使用 `init`、`sync`、`switch`，带 `role`、`boards`、`folders`；所有消息（包括 `op`、`ack`）中的元数据转换回 1.0.x 字段（`kind`、`folder`、`doc`、`underlay`、字符串形式的 `background`）；`ack` 去掉 `board` 和 `rejected`；`locked` 消息改为 1.0.x 的 `switch`。
- 1.0.x 客户端发出的 `op` 没有 `board`，按连接当前的白板处理，与 1.0.x 相同。
- 带 `pin` 的 1.0.x `hello` 回复 `{"t":"error","reason":"pin"}`（没有使用者，见 2.1 节）。
- 2.1 移除该兼容。

### 7.4 已安装应用的空间

- 每个已安装应用一个空间，存储在 `<存储目录>/spaces/<应用名>/`。只有已安装的应用名能解析为空间；运行中安装的应用立即可用，卸载时关闭该空间（数据保留）。
- 应用白板的 `create` 由应用页面给出；白板应用的规则：没有 manage 权限的设备可以打开和新建（受速率限制），不能修改元数据。
- Mac 的白板选择界面中，每个应用空间显示为一个条目，点开后分页读取该空间的白板（`GET /api/spaces/<名称>/boards?offset=&limit=`，需要 manage），选中后以只读连接查看。
- 与 1.0.1 的差别：1.0.1 中应用白板出现在用户白板列表中，Mac 选中后 iPad 会跟随切换到它。1.1 中应用白板只在应用自己的页面中书写，Mac 只查看，iPad 不跟随。截至本文没有应用白板存在，这一变化不影响任何已有数据。

## 8. 验收

| 编号 | 内容 |
| --- | --- |
| A1 | `examples/qb-server/`：只使用 inksync 公开接口的最小服务端（固定画布、题图层、`data`、按 Cookie 中的用户 id 限制白板前缀的规则、`advertise`），以及一个页面。自动测试覆盖：新建、书写、同步到第二个客户端、`open` 切换白板（切换前的离线操作仍送达原白板）、离线后重连、只读打开、规则拒绝与 `rejected` 事件。 |
| A2 | inksync 的测试移到 `packages/inksync/tests/`，CI 在只安装 inksync 的虚拟环境中运行，并检查 inksync 没有引用 `whiteboard`。 |
| A3 | 白板应用现有的 Python、浏览器和 WebKit 测试全部通过。 |
| A4 | 用 1.0.1 的存储目录和 1.0.1 页面留下的 IndexedDB 数据启动 1.1：内容、文件夹（含空文件夹）、排序、当前白板、文档板（显示、导出）、待发队列均保留；备份失败时不启动转换。 |
| A5 | 并发测试：保存进行中持续写入，保存后不丢操作；保存进行中删除白板，白板不复活；释放后重新打开，停留在最新 `seq` 的客户端得到 `sync`；关闭时所有已回执的操作已写盘。 |
| A6 | 性能测试：第 2.3 节 N1 至 N3。 |
| A7 | 1.0.x 页面（1.0.1 的前端文件）连接 1.1 白板应用：书写、跟随切换、改名、文件夹、文档板显示均正常。 |

## 9. 实施顺序

每一步结束时测试全部通过，可以单独提交。

1. 服务端内部：`Storage` 拆分为索引部分和文件部分，内存索引与 SQLite、`space.json`；工作线程载入与保存（版本计数、删除标记）；白板释放；`Policy` 与 `Principal`。协议和元数据暂不改变。
2. 拆分：把跟随、白板列表、文件夹、排序、管理消息和文档板移到白板应用的扩展；`set_hello`、`set_encoder`、`register`；白板应用改用 `mount`；inksync 测试独立运行。
3. 元数据 v2 与文件 v2：`canvas`、`background`、`layers`、`data`，读取 v1 文件，升级步骤（4.6 节）；前端按 `canvas` 和 `layers` 绘制、加载和限制范围，文档板改为 `sheet` 图片层。
4. 协议 v2：`v`、`space`、`open`、`follow`、`create`、`readonly`、`caps`、`locked`、`op` 的 `board`、`rejected`，以及 1.0.x 页面兼容。
5. 前端：SDK 移入包、`serve_sdk`、接口对象、按白板的待发队列、本地存储前缀与清理、1.0.x 数据迁移、`outdated`、去掉白板应用专有的请求和全局状态。
6. 白板应用的应用空间和选择界面。
7. `examples/qb-server/` 与验收测试。
8. 文档（中英文）、更新说明（含不支持降级），发布 1.1.0。

## 10. 已做的取舍

| 问题 | 决定 | 原因 |
| --- | --- | --- |
| 一题一块白板，还是一块白板划分多个区域 | 一题一块白板 | 撤销、清空、导出、离线缓存都按白板计算，划分区域需要在白板内部重新实现这些功能。 |
| 工具种类是否可扩展 | 不可扩展 | 见第 1 节的更正。 |
| 索引用 JSON 还是 SQLite | 内存字典加 SQLite | JSON 每次修改都要重写整份；SQLite 在标准库中，按行更新。查询只读内存。白板文件是元数据和笔画的唯一来源，索引可以随时重建；不能重建的空间级数据单独存 `space.json`。 |
| 文件夹和排序是否属于核心 | 不属于 | 只有白板应用使用；核心提供 `kv` 和 `index_fields` 供扩展保存。 |
| 画布是否可修改 | 不可修改 | 已有笔画的位置以画布为前提。 |
| 释放白板后是否换纪元 | 不换，保留 `(epoch, seq)` | 换纪元会使每个闲置超过 2 分钟的客户端重新下载整块白板。 |
| 待发队列按连接还是按白板 | 按白板，`op` 带 `board` | 允许在一条连接上切换白板而不丢失或错投未送达的操作。 |
| 1.0.x 页面兼容保留多久 | 只在 1.1 | 1.1 起有 `outdated` 事件，之后的升级不再需要兼容层。 |
| 是否支持降级到 1.0.x | 不支持 | 1.0.x 会改写 1.1 文件的元数据；需要时从升级备份恢复。 |

## 11. 第二版修改记录

根据对照代码的审查：

| 审查意见 | 修改 |
| --- | --- |
| 工作线程保存时，清除改动标记会丢失保存期间到达的操作 | 改为版本计数（4.3 节「保存」）；元数据也复制。 |
| 保存进行中删除白板，白板文件会被重新写出并复活 | 删除标记与等待（4.3 节「删除」）。 |
| 待发队列不区分白板，`open` 会把 A 的操作发到 B | 待发队列按白板记录，`op` 带 `board`（5.1、6.3 节）；1.0.x 待发操作归入 `last` 白板。 |
| 空间级数据（空文件夹、排序、当前白板）放在可重建的索引中会丢失 | 改存 `space.json`，同步写入（4.5 节）。 |
| `Storage.save` 在工作线程中修改内存索引 | 存储接口拆分为只在事件循环线程调用的索引部分和工作线程调用的文件部分（4.5 节）。载入也移到工作线程。 |
| 释放后换纪元导致闲置客户端整块重下 | 保留 `(epoch, seq)`（4.3 节「释放」）。 |
| 文档板改为通用图片层时丢失分档加载、按视野释放和纸张样式 | 图片层加载规则与 `sheet`（4.4 节）；列出需要修改的现有代码（7.2 节）。 |
| `data` 中的文件夹、文档信息可被普通 `meta` 操作修改；合并方式未定义 | 按键合并；`protected_data_keys`（4.2、4.4 节）。 |
| Hub 缺少服务端读取笔画、修改元数据、导入文件的接口 | `hub.strokes`、`hub.edit_meta`、`hub.import_file`、`hub.connections`（4.3 节）。 |
| 白板列表是否分页、类型标签从哪里来 | 用户空间保持完整列表；索引行加 `canvas.mode` 和 `index_fields`（7.1 节）。 |
| 多空间的创建、关闭，运行中安装应用 | `Spaces`（4.1 节）；与 1.0.1 的行为差别写明（7.4 节）。 |
| 跟随连接重连时的续传；解除只读后没有通知 | `follow` 与 `set_hello`（5.2 节）；`locked` 消息（5.1 节）。 |
| 与现有升级备份不一致；备份范围；降级 | 合并到 `backup_if_upgraded`，失败时停止；不支持降级（4.6 节）。 |
| 1.0.x 页面兼容缺少对 `op`、`ack` 中元数据的改写 | `set_encoder`（5.6、7.3 节）。 |
| SDK 仍有全局对象；`/ipad/version`；`advertise` | 列为外壳接口的一部分（6.4 节）。 |
| 被拒绝的操作被静默丢弃 | `rejected` 回执与事件（5.3 节）。 |
| 背景只能选四种样式、图片层只能在笔迹下方 | `background.paper`、`layers[].z`（4.4 节）。 |
| 第 1 节若干表述不准确 | 已更正 P1、P4、P12、P16 与版本号。 |

以下审查意见未采纳：

| 审查意见 | 原因 |
| --- | --- |
| 为 1.0.1 的嵌入接口和带 `pin` 的连接保留兼容 | 没有使用者（2.1 节）。 |

## 12. 实现与规格的差别

| 规格 | 实现 | 原因 |
| --- | --- | --- |
| `FileStorage(root, index_fields=(), convert_meta=None)`；索引行只含部分字段（4.5 节） | `FileStorage(root, convert_meta=None)`；索引行保存完整元数据，`list_boards` 返回完整元数据 | 元数据最大约 16 KB 加图片层列表，2 万块白板时内存和启动时间仍在目标内（启动约 0.45 秒）；使用者不必事先声明字段，白板应用的类型标签和文档名直接取自元数据。 |
| 视图位置存入 IndexedDB，随白板缓存清理（6.5 节） | 存入 `localStorage` 的 `<存储前缀>views`，最多 200 块，按最后使用时间清理 | 视图在每次平移后写入，同步的 `localStorage` 比 IndexedDB 事务简单；上限相同，写满的问题（P13）同样解决。 |
| 版本变更备份失败时停止启动（4.6 节） | 只有需要从 1.0.x 转换时，备份失败才停止启动；其他版本变更备份失败时照常启动，下次再试 | 不做转换时数据不被改写，停止启动只会让用户无法使用。 |
| `locked` 事件 `{board, locked}` | `{board, locked, unlock}`，`caps.unlock` 为 true 时调用 `unlock()` | 与 1.0.1 相同，白板应用的「仍然编辑」使用它。 |
| 不提供 `/sdk/inkpad.js` | 白板应用把 `/sdk/inkpad.js` 跳转到 `/inksync/inkpad.js` | 旧地址给出明确的去处；1.0.x 的选项仍需按 docs/embed.zh-CN.md 修改。 |


# inksync 2.0 需求与规格

状态：草案，待确认。确认后再开始实现；实现完成时本文转为正式文档并补英文版。

本文说明手写同步组件（服务端 inksync 与前端手写板）重新划分边界的需求和规格。白板应用和刷题项目（qb）都是这个组件的使用者。

## 1. 背景

inksync 1.0.1 是从白板应用中按文件抽取出来的。抽取时以「行为不变」为约束，白板应用自己的决定随代码一起进入了包中。用刷题项目核对时发现以下问题（括号内为代码位置）：

| 编号 | 问题 | 后果 |
| --- | --- | --- |
| P1 | 元数据字段固定（`models.sanitize_meta` 丢弃其他字段） | 使用者不能在白板上记录自己的信息，例如题号。 |
| P2 | 画布只有「无限」「笔记」「文档」三种（`KINDS`），宽高不可指定 | 不能规定作答区域的大小。 |
| P3 | 底图只能有一张，地址必须在 `/apps/` 下（`UNDERLAY_SRC_RE`） | 使用者的静态文件布局受白板应用的路由约束。 |
| P4 | 所有白板在同一目录、同一份索引中，完整列表发给客户端；索引每次改动整份重写（`store._write_index`）；查找逐条扫描（`get_meta`） | 白板数量增长后变慢；为此加入了每个应用 5000 块的上限（`MAX_APP_BOARDS`）。 |
| P5 | 存储初始化时必须至少有一块白板，并且有「当前白板」（`store._load_index`、`Hub.__init__`） | 使用者的存储中会出现一块不需要的空白板。 |
| P6 | 白板管理消息（`sel`、`newboard`、`rename`、文件夹、排序等）和「跟随当前白板」写在核心协议和 `Hub` 中 | 使用者不需要这些功能，却不能关闭，也不能加入自己的消息。 |
| P7 | 文档板（PDF 页面）写在核心模型中（`KINDS` 中的 `doc`、`sanitize_doc`） | 核心包含白板应用专有的数据结构。 |
| P8 | 权限固定为四个名称；`pin_policy` 拿不到请求或用户身份 | 使用者无法按用户限制可打开的白板。 |
| P9 | 自动保存在事件循环线程中同步编码和写盘（`Hub.save_all`）；1 万笔约 2 秒 | 保存期间所有连接的同步暂停。 |
| P10 | 载入内存的白板在服务运行期间不会释放（`Hub._boards`） | 打开过的白板越多，占用内存越多。 |
| P11 | 协议握手没有版本号；页面不会在服务端升级后重新载入 | 服务端升级后，iPad 上已打开的旧页面继续用旧脚本与新服务端通信。 |
| P12 | 前端直接请求白板应用的接口（`/api/debug`、`/api/doc`、`/api/thumb`） | 在其他服务端上这些请求返回 404。 |
| P13 | 前端本地存储名称固定为 `whiteboard`；视图位置按白板写入 `localStorage` 且从不清理；每块白板的离线缓存从不清理；「手指书写」设置全局共享 | 同一来源下的多个使用者互相影响；白板数量多时 `localStorage` 会写满。 |
| P14 | 前端没有独立分发；嵌入接口直接返回内部类 `InkPad` | 使用者只能复制目录，升级时无法核对版本；内部属性被当作接口使用。 |
| P15 | 前端向宿主页面写入全局状态（`<html data-shell>`） | 影响宿主页面。 |
| P16 | inksync 的测试放在白板应用的 `tests/` 中，包版本号固定为 0.1.0；白板应用没有使用包自己的 `mount` | 包没有被独立验证过。 |

一处更正：此前提到「笔画工具种类固定」是问题。复核后，工具种类必须固定：每种工具的绘制算法要在所有客户端和导出中一致，使用者自定义新的绘制算法会使其他客户端无法显示。使用者需要的「红色批改笔」一类工具，用现有工具加颜色和粗细的预设即可实现，属于界面层，不需要协议支持。

## 2. 需求

### 2.1 使用者

| 使用者 | 使用方式 |
| --- | --- |
| 白板应用 | Mac 端完整界面，iPad 跟随 Mac 当前白板；白板列表、文件夹、文档板、缩略图、导出。 |
| 在白板应用中安装的应用（`apps/`） | 由白板应用提供页面和同步，每个应用的白板与用户白板分开保存。 |
| 独立服务端（例如 qb） | 自己的 aiohttp 服务端挂载 inksync，自己的页面嵌入手写板，不运行白板应用。 |

### 2.2 功能需求

| 编号 | 需求 |
| --- | --- |
| F1 | 使用者可以为每块白板声明画布：无限、固定宽度向下延伸、固定宽高。 |
| F2 | 使用者可以为每块白板声明背景样式，以及任意数量的图片层（位置和尺寸以白板坐标给出）。图片地址是否允许由使用者决定。 |
| F3 | 使用者可以在白板上保存自己的字段（`data`），服务端原样保存和转发，由使用者校验。 |
| F4 | 每个服务可以有多个互相独立的空间（space）。每个空间有自己的存储、索引和规则；白板 id 只在空间内唯一。 |
| F5 | 核心协议只包含同步本身：握手、打开白板、操作、实时笔迹、回执、心跳、错误。其他消息由扩展注册。 |
| F6 | 使用者通过钩子决定：请求对应的身份和权限；谁可以打开、新建、书写、清空、修改元数据、解除只读；新建白板的速率。 |
| F7 | 一条连接在同一时间对应一块白板，可以在不断开连接的情况下切换到另一块白板。 |
| F8 | 可以以只读方式打开白板（查看，不书写）。 |
| F9 | 服务端提供事件：白板新建、内容变化、保存、删除，供使用者接入自己的流程。 |
| F10 | 前端作为带版本号的包，随 Python 包一起分发，由服务端在固定路径提供。 |
| F11 | 前端的接口是一个明确列出的对象，内部类不对外。 |
| F12 | 前端的本地存储按空间区分，视图位置和离线缓存有上限并自动清理；有未发送操作的白板不被清理。 |
| F13 | 服务端升级后，前端能得知并通知宿主；白板应用据此重新载入页面。 |
| F14 | 前端不访问白板应用专有的接口，不修改宿主页面的全局状态。报错上报地址可配置，默认关闭。 |
| F15 | iPad 外壳的接口（网页与外壳之间的消息、Bonjour 记录）写成文档，作为稳定接口。 |

### 2.3 非功能需求

| 编号 | 需求 | 验收方式 |
| --- | --- | --- |
| N1 | 保存不阻塞同步：在事件循环线程中只复制笔画列表，编码和写盘在工作线程中进行。 | 1 万笔的白板保存期间，其他白板的操作回执延迟不超过 50 ms。 |
| N2 | 按 id 查找白板为 O(1) 或 O(log n)；修改一块白板不重写整份索引。 | 2 万块白板时，新建一块白板不超过 20 ms，启动不超过 1 s。 |
| N3 | 没有连接、没有未保存改动、超过 120 秒未使用的白板从内存中释放。 | 测试打开 1000 块白板后内存中的白板数回落。 |
| N4 | inksync 不引用白板应用的任何模块，可以在只安装 inksync 的环境中独立测试。 | CI 中在干净的虚拟环境安装 inksync 后运行它自己的测试。 |
| N5 | 公开接口在 `__all__` 和前端入口中列出；其余均为内部实现。 | 文档只描述公开接口；测试只经公开接口使用 inksync。 |
| N6 | 白板应用的用户功能与 1.0.1 相同；已有数据无需用户操作即可继续使用。 | 现有测试全部通过；用 1.0.1 的存储目录启动新版本的测试。 |
| N7 | 笔画格式和操作格式不变，离线待发队列在升级后仍然有效。 | 用 1.0.1 页面留下的 IndexedDB 待发队列测试。 |

### 2.4 不做的事

- 不内置用户系统。身份由使用者的钩子提供。
- 不改变笔画的几何与绘制算法，不增加新的工具种类（见第 1 节的更正）。
- 不做多人同时编辑同一笔画的冲突合并；现有「笔画整条增删、遮罩整份替换」的模型不变。
- 不做服务端识别手写文字。
- 服务端把白板渲染成图片（批改用）列为后续工作，不在 2.0 范围内。

## 3. 总体结构

```
inksync（Python 包，同时分发前端）
├── core      操作的校验与应用、序号与纪元、操作历史（无输入输出）
├── storage   存储接口 Storage；默认实现 FileStorage（.wbz + SQLite 索引）
├── hub       一个空间的运行时：连接、内存中的白板、广播、保存、释放、事件、扩展消息
├── policy    身份 Principal 与规则 Policy，默认实现 DefaultPolicy
├── server    WebSocket 协议、空间解析、mount、前端文件路由
└── web/      前端 SDK（ES 模块），入口 inkpad.js

白板应用（使用者之一）
├── 扩展 whiteboard.boards   跟随当前白板、白板列表、文件夹、排序、新建与删除、1.x 客户端兼容
├── 扩展 whiteboard.docs     文档板：导入、页面图片、导出
├── 空间：""（用户白板）和每个已安装应用一个空间
└── 界面 app.js / ui*.js     在 SDK 的内部类上构建（同一仓库内允许，不属于公开接口）
```

依赖方向只能是白板应用依赖 inksync。

## 4. 服务端规格

### 4.1 空间与挂载

```python
from inksync import FileStorage, Hub, mount, serve_sdk

hub = Hub(FileStorage("data/qb"), policy=QbPolicy())
mount(app, hub, path="/ws")             # 单个空间
serve_sdk(app, prefix="/inksync/")      # 前端文件
```

多个空间时传入解析函数：

```python
mount(app, spaces=lambda name, request: hubs.get(name), path="/ws")
```

| 规则 | 说明 |
| --- | --- |
| 空间名称 | `^[a-z0-9-]{0,32}$`；空串为默认空间。单空间挂载只接受空串或与 `Hub.name` 相同的名称。 |
| 解析失败 | 回复 `{"t":"error","reason":"space"}` 并关闭连接。 |
| 生命周期 | `mount` 接管启动（开始自动保存）、关闭（断开连接）和清理（等待保存完成）。白板应用也通过 `mount` 挂载，不再自行实现这三步。 |

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
| `allow_src(src)` | 以 `/` 开头的同源路径，不含 `..`、`\` 和协议名。 |
| `caps(who, meta)` | 由以上方法得出 `{write, clear, meta, unlock}`，随快照发给客户端。 |

`can_clear` 默认允许的原因：能书写的连接本来就能用 `remove` 删除全部笔画，单独限制 `clear` 只影响界面入口，不构成保护。白板应用保留「清空白板」权限作为界面上的防误操作。

- 规则在每次动作时调用，不缓存；`hub.refresh()` 为所有连接重新计算 `caps` 并在有变化时发送。
- 连接建立时计算一次 `Principal` 并保存在连接上；使用者更改身份判断方式后调用 `hub.reauthenticate()`。
- 白板应用把自己的四项权限（manage、settings、clear、export）映射到这些方法上，并通过扩展消息把权限名单发给自己的界面。

### 4.3 Hub

```python
hub = Hub(storage, policy=None, name="", autosave=3.0, idle_unload=120.0, default_board=None)
hub.on("created" | "saved" | "deleted", callback)   # callback(board_id, meta)
hub.on("changed", callback)              # callback(board_id, meta, op)，每个被接受的操作一次
hub.register("sel", handler)             # 扩展消息，见 5.4 节
hub.open(conn, board_id, reply="init")   # 把连接移到另一块白板并发送快照（扩展使用）
hub.board_meta(board_id) / hub.create_board(board_id, spec) / hub.delete_board(board_id)
hub.connections()                        # 当前连接，只读
await hub.refresh() / await hub.reauthenticate()
```

| 行为 | 规格 |
| --- | --- |
| 载入 | 白板在第一次被打开时从存储读入内存。 |
| 保存 | 每 `autosave` 秒一轮。事件循环线程中对每块有改动的白板复制笔画字典（浅复制；笔画的点列在创建后不再修改，遮罩以整体赋值方式替换），然后在工作线程中编码、压缩、写盘。同一块白板同一时间最多一个保存任务。保存失败保留改动标记，下一轮重试。 |
| 释放 | 没有连接、没有改动、没有进行中的保存、最后使用超过 `idle_unload` 秒的白板从内存中移除。再次打开时重新读入，并获得新的纪元（客户端据此收到完整快照）。 |
| 默认白板 | `hello` 没有 `board` 时调用 `default_board(conn)` 取得白板 id；没有设置该钩子时回复 `error`（`reason: "board"`）。白板应用用它实现「打开当前白板」。 |
| 关闭 | 等待进行中的保存完成，再对仍有改动的白板做最后一次保存。 |
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
  "background": "blank",
  "layers": [{"src": "/static/q/9709-s23-12-3.png", "x": 0, "y": 0, "width": 800, "height": 520}],
  "data": {"paper": "9709_s23_12", "question": 3}
}
```

| 字段 | 规格 |
| --- | --- |
| `id` | `^[A-Za-z0-9_-]{1,64}$`，在空间内唯一，同时是文件名。 |
| `name` | 字符串，最长 64 个字符。 |
| `created`、`updated` | 服务端维护。 |
| `canvas` | `{"mode":"infinite"}`（默认）；`{"mode":"column","width":W}`：宽度固定，向下延伸；`{"mode":"fixed","width":W,"height":H}`：宽高固定。W、H 在 1 到 100000 之间。新建后不可修改（已有笔画的位置依赖它）。 |
| `background` | `blank`、`grid`、`lines`、`dots` 之一。 |
| `layers` | 图片层数组，最多 1000 项，按顺序绘制在笔迹下方。每项 `{src, x, y, width, height?}`；`height` 省略时按图片比例计算。`src` 须通过 `Policy.allow_src`，最长 512 个字符；可以包含 `{w}`，前端按显示所需的像素宽度替换（用于按分辨率加载，例如文档页）。 |
| `data` | JSON 对象，序列化后不超过 16 KB，经 `Policy.validate_data` 校验。 |

- 可修改字段：`name`、`background`、`layers`、`data`，通过 `meta` 操作，经 `Policy.can_edit_meta` 许可。`canvas` 和 `id` 不可修改。
- 未知字段被丢弃。服务端不解释 `data` 的内容。
- 前端据 `canvas` 限制书写范围和视图：`column` 与 1.x 的笔记相同；`fixed` 下落点在范围外的笔画不开始，视图不能移出范围，`fit()` 显示整个画布。

### 4.5 存储

```python
class Storage(Protocol):
    def get_meta(self, board_id) -> dict | None
    def list(self, offset=0, limit=100, prefix="", order="updated") -> list[dict]   # 只含索引字段
    def count(self, prefix="") -> int
    def create(self, meta) -> dict
    def load(self, board_id) -> tuple[dict, list[dict], dict | None]   # meta, strokes, problem
    def save(self, meta, strokes) -> None          # 可在工作线程中调用
    def update_meta(self, meta) -> None
    def delete(self, board_id) -> bool
    def backup(self, board_id) -> Path | None      # 解除只读前备份
    kv: KeyValue                                   # 空间级的小数据，供扩展保存（例如文件夹名单）
```

`FileStorage(root, index_fields=())` 的规格：

| 项目 | 规格 |
| --- | --- |
| 白板文件 | `boards/<id>.wbz`，格式见 4.6 节。白板文件是唯一的数据来源。 |
| 索引 | 内存中保存全部索引行（按 id 的字典），`get_meta`、`list`、`count` 只读内存，不访问磁盘。索引行包含 `id`、`name`、`created`、`updated`、白板文件的修改时间，以及 `index_fields` 列出的 `data` 字段；完整元数据只在白板文件中。 |
| 索引持久化 | `index.sqlite`（Python 标准库 sqlite3，WAL 模式），按行写入，不重写整份。写入由一个专用线程按顺序执行，事件循环线程只提交写入请求。 |
| 索引重建 | 索引缺失或损坏时从白板文件全部重建。启动时比较 `boards/` 中的文件名集合和每个文件的修改时间，只重新读取新增或修改时间不一致的文件。进程在索引写入前退出时，下次启动据此补齐。 |
| 空存储 | 允许没有任何白板。不存在「当前白板」。 |
| 写入 | 先写临时文件再替换（沿用 `_atomic_write`）。 |

### 4.6 文件格式

`.wbz` 的 `v` 升到 2，笔画部分不变。

- 2.0 读取 `v` 为 1 的文件时，在内存中转换元数据：`kind: note` → `canvas: column 1000`；`kind: board` → `infinite`；`kind: doc` → `fixed`，宽高取各页排列后的外框（与 `docs.layout` 相同的算法，页间距 24）；`underlay` → `layers` 的第一项；`folder`、`app`、`doc` 移入 `data`。之后调用 `FileStorage(convert_meta=…)` 钩子，白板应用在其中为文档板生成页面图片层。
- 文件在下次保存或修改元数据时以 `v: 2` 写回。
- 首次以 2.0 打开一个 1.x 存储目录时：先把 `index.json` 和 `boards/` 复制到 `backups/pre-2.0/`，复制失败则停止并报告，不做任何转换；然后建立 `index.sqlite`；`index.json` 中的文件夹名单、排序和当前白板通过 `storage.legacy_index` 交给白板应用的扩展迁入 `storage.kv`。`index.json` 保留不删，2.0 不再读写它。
- 1.x 读取 `v: 2` 的文件时按现有规则以只读方式打开（`reason: "newer"`），不会覆盖。

## 5. 协议规格（v2）

### 5.1 核心消息

客户端发往服务端：

| `t` | 字段 | 说明 |
| --- | --- | --- |
| `hello` | `v`（2）、`client`、`space`、`board`、`since`、`epoch`、`create`（可选）、`readonly`（可选）、`device`（可选，仅用于日志） | 第一条消息。`board` 省略时由 `Hub.default_board` 决定。白板不存在且带 `create` 时按 `create` 新建，否则回复 `error`。 |
| `open` | `board`、`since`、`epoch`、`create`、`readonly` | 在同一连接上切换到另一块白板。 |
| `op` | `cid`、`op` | 与 1.x 相同。 |
| `live` | 与 1.x 相同 | 只读连接发送的被忽略。 |
| `ping` | `ts` | 与 1.x 相同。 |
| `unlock` | — | 经 `Policy.can_unlock` 许可。 |

服务端发往客户端：

| `t` | 字段 | 说明 |
| --- | --- | --- |
| `init` | `v`、`server`、`client`、`board`、`strokes`、`seq`、`epoch`、`locked`、`caps`、`info` | 完整快照。 |
| `sync` | 同上，以 `ops` 代替 `strokes` | 断线续传。 |
| `op`、`ack`、`live`、`pong` | 与 1.x 相同 | |
| `caps` | `caps` | 权限变化。 |
| `deleted` | `board` | 所在白板被删除。 |
| `error` | `reason`，可选 `detail` | `version`、`space`、`board`、`create`、`denied`、`rate`。 |

- `server` 为 `{"name":"inksync","version":"2.0.0","protocol":2,"build":"…"}`。`info` 由使用者的 `info(request)` 钩子提供。
- 协议版本：`hello` 中 `v` 大于服务端支持的版本时，回复 `{"t":"error","reason":"version","supported":[2]}`。
- 操作的种类、字段、回执规则与 1.x 相同（`add`、`restore`、`remove`、`mask`、`clear`、`meta`），只有 `meta` 操作的可修改字段按 4.4 节扩展。
- 只读连接发出的 `op` 都得到不含 `op` 的回执。

### 5.2 新建白板

`create` 字段：`{name, canvas, background, layers, data}`，均可省略。

| 情况 | 结果 |
| --- | --- |
| 白板已存在 | 忽略 `create`，经 `can_open` 许可后打开。已有白板的元数据不被 `create` 修改。 |
| 白板不存在、有 `create` | 依次检查 `can_create`、`create_limit`、字段校验，通过则新建。 |
| 白板不存在、没有 `create` | `error`，`reason: "board"`。 |

### 5.3 前端与服务端版本

- 前端 SDK 与服务端来自同一个包，版本号相同。`serve_sdk` 在提供的 `version.js` 中写入服务端的构建号，SDK 以此作为自身的构建号。前端收到的 `server.build` 与自身不同时触发 `outdated` 事件（页面仍是旧版本，服务端已经升级）。白板应用在没有进行中的书写、待发队列已写入缓存后重新载入页面；同一构建号只重新载入一次（记录在 `sessionStorage`），防止循环。
- 1.x 页面（`hello` 中没有 `v`）只在白板应用中兼容，由白板扩展处理，见 7.3 节。

### 5.4 扩展消息

- `hub.register(name, handler)` 注册一种消息；`handler(conn, msg)` 在事件循环线程中调用。名称不能与核心消息相同。
- 未注册的消息被忽略。
- 服务端向客户端发送的扩展消息，前端以 `message` 事件交给宿主。

## 6. 前端规格

### 6.1 分发

- 前端文件放在 `packages/inksync/inksync/web/`，由 `serve_sdk(app, prefix)` 提供，入口为 `<prefix>inkpad.js`。
- 白板应用从同一位置加载这些模块。`/sdk/inkpad.js` 保留为跳转。
- 所有前端文件带 `Cache-Control: no-cache`。

### 6.2 `createInkPad(container, options)`

| 选项 | 说明 |
| --- | --- |
| `space` | 空间名称，默认空串。 |
| `board` | 必填。 |
| `create` | 白板不存在时新建所用的 `{name, canvas, background, layers, data}`。 |
| `readonly` | 只读打开。 |
| `tool` | 初始工具 `{tool, color, width, eraserMode}`。 |
| `fingerDraw` | 手指是否书写。只作用于这块手写板，不写入任何全局设置。 |
| `transport` | 省略、`"local"`、`{url}` 或函数，与 1.x 相同。 |
| `storage` | 本地存储名称前缀，默认为 `inksync:<space>`。 |
| `report` | 报错上报地址；默认不上报。 |
| `undoLimit` | 撤销步数，默认 200。 |
| `initial` | `transport` 为 `"local"` 时的初始内容。 |

返回的对象只有下列成员：

| 成员 | 说明 |
| --- | --- |
| `open(board, {create, readonly})` | 在同一连接上切换白板，撤销记录清空。 |
| `setTool(tool)`、`undo()`、`redo()`、`clear()`、`fit()`、`zoom(factor)` | 与 1.x 相同。 |
| `setMeta(patch)` | 修改 `name`、`background`、`layers`、`data`。 |
| `exportPNG({layers = false, scale = 1})` | 笔迹导出为 PNG data URL；`layers` 为 true 时包含图片层（图片须同源）。 |
| `snapshot()`、`load(board)` | 与 1.x 相同。 |
| `on(event, listener)` | 返回取消订阅的函数。 |
| `destroy()` | 写入缓存、断开、移除。 |
| 只读属性 | `board`（当前元数据）、`status`、`tool`、`caps`、`locked`、`version`。 |

事件：`status`、`history`、`meta`、`change`、`op`、`strokestart`、`strokeend`、`locked`、`caps`、`deleted`、`error`、`interrupted`、`outdated`、`message`。

### 6.3 本地存储

| 项目 | 规格 |
| --- | --- |
| IndexedDB | 库名为 `storage` 选项的值。每块白板的缓存记录最后使用时间。 |
| 清理 | 每个库最多保留 200 块白板的缓存，超出时删除最久未用的；待发队列不为空的白板不删除。 |
| 视图位置 | 存入同一个 IndexedDB 库，随白板缓存一起清理；不再写 `localStorage`。 |
| 客户端 id | 每个库一个设备 id；每块手写板的连接 id 为设备 id 加随机后缀（与 1.x 相同）。 |
| 1.x 数据 | 白板应用首次以 2.0 启动时，把 1.x 的 `whiteboard` 库中的缓存和待发队列迁入新库，并删除 `localStorage` 中的 `whiteboard.view.*`。 |

### 6.4 iPad 外壳接口

以下内容作为稳定接口写入 `docs/ipad-shell`，外壳和网页任何一方修改都需要兼容处理：

- 网页调用 `window.webkit.messageHandlers.whiteboard.postMessage({type, …})`；外壳调用 `window.whiteboardShell.receive(batch)` 和 `window.whiteboardShell.hello(info)`；桥接版本范围 `SHELL_BRIDGE`。
- Bonjour 服务类型 `_whiteboard._tcp` 及 TXT 字段 `host`、`port`、`version`、`name`、`source`、`path`。

SDK 不再设置 `<html data-shell>`，改为在手写板上提供 `shell` 属性和 `shell` 事件。白板应用的界面自行设置它需要的属性。

## 7. 白板应用作为使用者

### 7.1 扩展 `whiteboard.boards`

| 功能 | 实现 |
| --- | --- |
| 当前白板与跟随 | 扩展保存当前白板 id（`storage.kv`）和跟随连接的集合。`sel`、`newboard`、`delboard` 之后用 `hub.open(conn, id, reply="switch")` 移动所有跟随连接。 |
| 白板列表 | `boards` 消息由扩展发送给跟随连接，列表来自 `storage.list`。可见范围与 1.0.1 相同：没有 manage 权限的连接只收到自己所在的那一块，文件夹名单为空。 |
| 文件夹 | 白板所在的文件夹存 `data.folder`，`index_fields` 包含 `folder`；文件夹名单（含空文件夹）存 `storage.kv`。 |
| 排序 | 排序是白板 id 的列表，存 `storage.kv`，拖动排序只改这一项，不改任何白板文件。不在列表中的白板按 `created` 从新到旧排在前面，与 1.0.1 中新白板出现在最前的行为一致。迁移时，排序列表取自 1.x `index.json` 中的白板顺序。 |
| 权限 | 四项权限映射到 `Policy`；`perms` 作为扩展消息发送。 |
| 缩略图 | 保持现有 HTTP 接口，由 `hub.on("deleted")` 删除缩略图。 |

### 7.2 扩展 `whiteboard.docs`

文档板的元数据为 `canvas: fixed`，每页一个图片层，`src` 为 `/api/doc/<id>/<页码>?w={w}`；原件信息存 `data.doc`。导出沿用现有实现。

### 7.3 1.x 页面兼容

白板应用在 2.0 期间接受没有 `v` 的 `hello`：扩展按 1.x 的消息形式应答（`init`、`sync`、`switch`、`boards`，含 `role`、`folders`），元数据转换回 1.x 的字段（`kind`、`folder`、`doc`、`underlay`），并把 1.x 的 `perms` 消息照旧发送。这使升级时仍打开着的 iPad 页面继续工作，直到页面重新载入。带 `pin` 的 1.x `hello`（1.0.1 的嵌入手写板）回复 `{"t":"error","reason":"pin"}`，因为应用白板已改为独立空间。2.1 移除该兼容。

### 7.4 已安装应用的空间

- 每个已安装应用一个空间，存储在 `<存储目录>/spaces/<应用名>/`。只有已安装的应用名能解析为空间。
- Mac 的白板选择界面中，每个应用空间显示为一个条目，点开后按页读取该空间的白板（`GET /api/spaces/<名称>/boards?offset=&limit=`）。
- 1.0.1 中带 `app` 字段的用户空间白板保持原位，作为普通白板显示。

## 8. 验收

| 编号 | 内容 |
| --- | --- |
| A1 | `examples/qb-server/`：只使用 inksync 公开接口的最小服务端（固定画布、题图层、`data`、按 Cookie 中的用户 id 限制白板前缀的规则），以及一个页面。自动测试覆盖：新建、书写、同步到第二个客户端、切换白板、离线后重连、只读打开、规则拒绝。 |
| A2 | inksync 的测试移到 `packages/inksync/tests/`，CI 在只安装 inksync 的虚拟环境中运行，并检查 inksync 没有引用 `whiteboard`。 |
| A3 | 白板应用现有的 Python、浏览器和 WebKit 测试全部通过。 |
| A4 | 用 1.0.1 的存储目录和 1.0.1 页面留下的 IndexedDB 数据启动 2.0，内容、文件夹、排序、文档板、待发队列均保留。 |
| A5 | 第 2.3 节 N1 至 N3 的性能测试。 |
| A6 | 1.x 页面连接 2.0 白板应用的测试（7.3 节）。 |

## 9. 实施顺序

每一步结束时测试全部通过，可以单独提交。

1. 服务端内部：`Storage` 接口与 SQLite 索引、工作线程保存、白板释放、`Policy` 与 `Principal`。协议和元数据暂不改变。
2. 拆分：把跟随、白板列表、文件夹、排序、管理消息和文档板移到白板应用的扩展；白板应用改用 `mount`；inksync 测试独立运行。
3. 元数据 v2 与文件 v2：`canvas`、`layers`、`data`，读取 v1 文件，前端按 `canvas` 和 `layers` 绘制和限制范围。
4. 协议 v2：`v`、`space`、`open`、`create`、`readonly`、`caps`、扩展消息，以及 1.x 页面兼容。
5. 前端：SDK 移入包、`serve_sdk`、接口对象、本地存储前缀与清理、`outdated`、去掉白板应用专有的请求和全局状态。
6. 白板应用的应用空间和选择界面。
7. `examples/qb-server/` 与验收测试。
8. 文档（中英文）、更新说明，发布 2.0.0。

## 10. 已做的取舍

| 问题 | 决定 | 原因 |
| --- | --- | --- |
| 一题一块白板，还是一块白板划分多个区域 | 一题一块白板 | 撤销、清空、导出、离线缓存都按白板计算，划分区域需要在白板内部重新实现这些功能。 |
| 工具种类是否可扩展 | 不可扩展 | 见第 1 节的更正。 |
| 索引用 JSON 还是 SQLite | SQLite | JSON 每次修改都要重写整份；SQLite 在标准库中，按行更新。白板文件仍是唯一的数据来源，索引可以随时重建。 |
| 文件夹和排序是否属于核心 | 不属于 | 只有白板应用使用；核心提供 `storage.kv` 和 `index_fields` 供扩展保存。 |
| 画布是否可修改 | 不可修改 | 已有笔画的位置以画布为前提。 |
| 1.x 页面兼容保留多久 | 只在 2.0 | 2.0 起有 `outdated` 事件，之后的升级不再需要兼容层。 |

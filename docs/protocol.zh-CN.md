[English](protocol.md) | 简体中文

# 同步协议

客户端与服务端通过 `/ws` 上的一条 WebSocket 同步白板，消息为 JSON 文本；另有若干 HTTP 接口提供页面、缩略图和文档板。本文描述协议版本 2（inksync 2.0 起）。

客户端先在本地绘制，再发送操作。服务端负责编号、转发和保存操作，不阻塞本地绘制。

协议分两层：

- **核心**（inksync）：握手、打开白板、操作、实时笔迹、回执、心跳、只读状态、错误。任何挂载了 inksync 的服务端都一样。
- **白板应用的扩展**：跟随当前白板、白板列表、文件夹、排序和权限名单，见[白板应用的扩展](#白板应用的扩展)。

| 项目 | 值 |
| --- | --- |
| 协议版本 | 2（`inksync.server.PROTOCOL`） |
| WebSocket 路径 | `/ws` |
| 消息格式 | JSON 文本帧；每条消息是一个带类型字段 `t` 的对象。二进制帧和非对象的 JSON 被忽略。 |
| 单条消息上限 | 8 MB（`inksync.server.MAX_WS_MESSAGE`） |
| 服务端心跳 | 每 20 s 发送一次 WebSocket ping |
| 默认端口 | 8848；被占用时本次运行改用下一个可用端口 |

## 通道

笔画数据通过两个通道传输：书写过程中的实时采样点，以及改变白板内容的操作。

| 通道 | 消息 | 是否保存 | 用途 |
| --- | --- | --- | --- |
| 实时笔迹 | `live` | 否 | 书写过程中的采样点，使其他设备在书写的同时看到笔画 |
| 操作 | `op` | 是 | 完成的笔画、擦除、清空、撤销、白板设置 |

`live` 消息丢失后无需补发：笔画结束时发送的 `op` 包含完整的点列。

## 连接

### 握手

连接上的第一条消息必须是 `hello`；收到 `hello` 之前，服务端忽略其他所有消息。

| `hello` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `v` | 整数 | 协议版本，2。 |
| `client` | 字符串 | 客户端 id，须符合 `^[A-Za-z0-9_-]{4,64}$`，否则服务端分配新的 id。 |
| `space` | 字符串 | 空间名称，`^[a-z0-9-]{0,32}$`，默认空串。白板应用中空串是用户自己的白板，已安装应用的名字各是一个空间。 |
| `board` | 字符串或 null | 要打开的白板 id，`^[A-Za-z0-9_-]{1,64}$`。跟随模式下是客户端上次显示的白板。 |
| `since` | 整数 | 客户端在这块白板上收到的最后一个 `seq`。 |
| `epoch` | 字符串或 null | 客户端在这块白板上收到的最后一个 `epoch`。 |
| `create` | 对象 | 可选。白板不存在时用于新建，见[打开白板](#打开白板)。 |
| `readonly` | 布尔值 | 可选。只读打开：不能书写，也不发送实时笔迹。 |
| `follow` | 布尔值 | 可选。由服务端决定打开哪块白板（白板应用的页面用它跟随当前白板）。 |
| `device` | 字符串 | 可选。设备类型，只用于服务端日志，例如 `mac`、`ipad`。 |

服务端以 `init` 或 `sync` 应答，见[序号、纪元与重连](#序号纪元与重连)。同一连接上的第二条 `hello` 被忽略。

| 握手失败 | 服务端的应答 |
| --- | --- |
| `v` 不是 2 | `{"t":"error","reason":"version","supported":[2]}`，然后关闭连接。没有 `v` 的 `hello` 是 1.x 页面，只在白板应用的用户空间中接受，见 [1.x 页面](#1x-页面)。 |
| 空间不存在 | `{"t":"error","reason":"space"}`，然后关闭连接。 |
| 打开白板失败 | `{"t":"error","reason":…}`，然后关闭连接。原因见下表。 |

客户端的身份（`Principal`）在连接建立时按请求确定，握手里的内容改变不了它。

### 打开白板

`hello` 和 `open` 用同样的规则打开白板。

| 请求 | 结果 |
| --- | --- |
| 有 `board`，没有 `follow` | 白板已存在：经规则 `can_open` 许可后打开；`create` 被忽略，已有白板的元数据不变。白板不存在且有 `create`：依次检查 `can_create`、新建速率（`create_limit`）和字段，通过后新建。白板不存在且没有 `create`：拒绝。 |
| 有 `follow: true` 或没有 `board` | 由服务端的扩展决定打开哪块白板（白板应用打开当前白板）。没有这样的扩展时拒绝。 |
| `readonly: true` | 连接只读，`caps.write` 为 false；它发出的操作都被拒绝，实时笔迹被忽略。 |

| `error` 的 `reason` | 含义 |
| --- | --- |
| `board` | 白板 id 不合法，或白板不存在且没有 `create`。 |
| `create` | `create` 不合法（画布、图片层或 `data` 不合规）。 |
| `denied` | 规则不允许打开或新建。 |
| `rate` | 新建白板过于频繁。 |

`create` 的字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 白板名称。 |
| `canvas` | 画布，见 [format.zh-CN.md](format.zh-CN.md#meta-字段)。 |
| `background` | 背景。 |
| `layers` | 图片层。 |
| `data` | 使用者自己的字段。 |

```jsonc
{"t":"hello","v":2,"client":"k3m9x0a1b2c4","space":"qb","board":"u42-q3","since":0,"epoch":null,
 "create":{"name":"第 3 题","canvas":{"mode":"fixed","width":800,"height":1400},
           "layers":[{"src":"/static/q3.png","x":0,"y":0,"width":800}],"data":{"question":3}}}
```

一条连接同一时间显示一块白板。`open` 在同一连接上切换到另一块白板，字段与 `hello` 中的 `board`、`since`、`epoch`、`create`、`readonly` 相同；成功时应答 `init` 或 `sync`，失败时应答带 `board` 的 `error`，连接不断开。

### 客户端保活与重连

网页客户端（`net.js`）自行检测失效的连接，因为移动端浏览器经常挂起套接字而不关闭它。

| 设置 | 值 |
| --- | --- |
| `ping` 间隔 | 10 s |
| 判定连接失效 | 12 s 内未收到 `pong`；客户端关闭套接字并重连 |
| 重连间隔 | 400、800、1500、3000、5000、8000 ms（之后一直使用最后一个值） |
| 触发重连的事件 | 套接字关闭、浏览器 `online` 事件、窗口 `focus`、页面变为可见 |

### 待发队列

每个操作都先放入待发队列，收到对应的 `ack` 之后才移除。

- 每项记录 `{cid, board, op}`：操作所属的白板。切换白板之后，之前那块白板上没送达的操作照样发往那块白板。
- `cid` 的格式为 `<客户端 id>-<时间的 36 进制>-<计数器的 36 进制>`。
- 待发队列保存在浏览器缓存（IndexedDB）中，页面重新载入后恢复。
- 每次收到 `init`、`sync` 或 `switch` 后，客户端按顺序重发待发队列中的全部操作，每项带着自己的 `board`。
- 收到快照后，客户端将属于这块白板的待发操作重新应用到本地白板上。

## 客户端发往服务端的消息

核心消息：

| `t` | 字段 | 服务端的应答 |
| --- | --- | --- |
| `hello` | 见[握手](#握手) | 向发送方发送 `init` 或 `sync` |
| `open` | `board`、`since`、`epoch`、`create`、`readonly` | 向发送方发送 `init`、`sync` 或 `error` |
| `op` | `cid`、`op`、`board`（省略时为连接当前的白板） | 向发送方发送 `ack`；操作被接受时向显示这块白板的其他连接发送 `op` |
| `live` | `id`、`phase` 及各阶段的字段 | 加上 `src` 后转发给显示同一块白板的其他连接 |
| `ping` | `ts` | 返回带相同 `ts` 的 `pong` |
| `unlock` | — | 经规则 `can_unlock` 许可后解除只读，向显示这块白板的连接发送 `locked` |

其他消息由服务端的扩展注册（白板应用的见[白板应用的扩展](#白板应用的扩展)）；没有注册的消息被忽略。

```jsonc
{"t":"hello","v":2,"client":"k3m9x0a1b2c4","follow":true,"board":"49b773c7c7c2","since":42,"epoch":"a1b2c3d4e5f6","device":"ipad"}
{"t":"open","board":"u42-q4","since":0,"epoch":null,"create":{"name":"第 4 题"}}
{"t":"op","cid":"k3m9x0a1b2c4-m1x2y3-7","board":"49b773c7c7c2","op":{"op":"clear"}}
{"t":"live","id":"c3f1-17","phase":"b","tool":"pen","color":"#1b1b1f","w":3}
{"t":"live","id":"c3f1-17","phase":"m","p":[120.5,40.25,0.03, 121.0,41.0,0.04]}
{"t":"live","id":"c3f1-17","phase":"e"}
{"t":"ping","ts":1730000000000}
{"t":"unlock"}
```

### 实时笔迹

`live` 消息描述一条正在书写的笔画，以笔画 `id` 标识。

| `phase` | 字段 | 含义 |
| --- | --- | --- |
| `b` | `tool`、`color`、`w` | 笔画开始 |
| `m` | `p`：`[x, y, 压感, ...]` | 新的采样点，追加到笔画末尾 |
| `e` | `p`（可选） | 笔画结束。接收方保留这条实时笔画 5 s，或直到对应的 `add` 到达。 |
| `x` | — | 笔画作废，接收方将其移除 |

服务端不校验也不保存 `live` 消息。

## 服务端发往客户端的消息

| `t` | 接收方 | 字段 |
| --- | --- | --- |
| `init` | `hello` 或 `open` 的发送方 | 见[快照字段](#快照字段)，含 `strokes` |
| `sync` | `hello` 或 `open` 的发送方 | 见[快照字段](#快照字段)，以 `ops` 代替 `strokes` |
| `switch` | 被服务端移到另一块白板的连接（白板应用：跟随的连接） | 与 `init` 相同 |
| `op` | 显示同一块白板的其他连接 | `op`（含 `seq`）、`src`、`board` |
| `ack` | `op` 的发送方 | `cid`、`board`、`seq`、`op`（被接受时）、`rejected`（被拒绝时），见[回执](#回执) |
| `live` | 显示同一块白板的其他连接 | 原 `live` 消息的字段，加上 `src` |
| `pong` | `ping` 的发送方 | `ts` |
| `caps` | 权限发生变化的连接 | `caps`：`{write, clear, meta, unlock}` |
| `locked` | 显示这块白板的连接 | `board`、`locked`：白板进入或解除只读 |
| `deleted` | 显示这块白板的连接 | `board`：白板被删除 |
| `error` | 握手或 `open` 的发送方 | `reason`，可选 `detail`、`board`、`supported` |

```jsonc
{"t":"init","v":2,"server":{"name":"inksync","version":"2.0.0","protocol":2,"build":"2.0.0"},
 "client":"k3m9x0a1b2c4","info":{...},"board":{...},"strokes":[...],"seq":42,"epoch":"a1b2c3d4e5f6",
 "locked":null,"caps":{"write":true,"clear":true,"meta":false,"unlock":false},"readonly":false}
{"t":"sync","v":2,"server":{...},"client":"k3m9x0a1b2c4","info":{...},"board":{...},"ops":[...],
 "seq":45,"epoch":"a1b2c3d4e5f6","locked":null,"caps":{...},"readonly":false}
{"t":"op","op":{"op":"remove","ids":["c3f1-17"],"seq":43},"src":"k3m9x0a1b2c4","board":"49b773c7c7c2"}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-7","board":"49b773c7c7c2","seq":43,"op":{"op":"remove","ids":["c3f1-17"],"seq":43}}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-8","board":"49b773c7c7c2","seq":43,"rejected":"denied"}
{"t":"live","id":"c3f1-17","phase":"m","p":[...],"src":"k3m9x0a1b2c4"}
{"t":"caps","caps":{"write":true,"clear":true,"meta":true,"unlock":true}}
{"t":"locked","board":"49b773c7c7c2","locked":null}
{"t":"error","reason":"denied"}
```

### 快照字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `v` | 整数 | 协议版本（只在 `hello` 的应答中）。 |
| `server` | 对象 | `{name, version, protocol, build}`（只在 `hello` 的应答中）。前端的构建号与 `build` 不同时，页面是旧的（`outdated` 事件）。 |
| `client` | 字符串 | 服务端为该连接记录的客户端 id（只在 `hello` 的应答中）。 |
| `info` | 对象 | 使用者的 `info(request)` 钩子提供的内容（只在 `hello` 的应答中）。白板应用见下表。 |
| `board` | 对象 | 白板的元数据，见 [format.zh-CN.md](format.zh-CN.md#meta-字段)。 |
| `strokes` | 数组 | 全部笔画，按 `n` 升序，点列为扁平数组。 |
| `ops` | 数组 | `seq` 大于 `since` 的操作，按顺序排列。 |
| `seq` | 整数 | 白板当前的 `seq`。 |
| `epoch` | 字符串 | 白板的纪元。 |
| `locked` | 对象或 null | 正常白板为 `null`；否则为只读原因，见[只读白板](#只读白板)。 |
| `caps` | 对象 | 这条连接在这块白板上能做什么：`write`（书写）、`clear`（清空）、`meta`（改元数据）、`unlock`（解除只读）。 |
| `readonly` | 布尔值 | 连接是否只读。 |

白板应用的 `info`：

| 字段 | 说明 |
| --- | --- |
| `version` | 程序版本号 |
| `hostname` | `<名称>.local` 形式的主机名 |
| `port` | 监听端口 |
| `urls` | 候选地址：`http://<主机名>.local:<端口>/`，以及已知时的 `http://<局域网 IP>:<端口>/` |
| `data_dir` | 存储目录 |

## 白板应用的扩展

白板应用在用户空间（空间名为空串）上加了下列功能（`whiteboard/hub.py`）。

### 跟随当前白板

白板应用的页面以 `follow: true` 连接，服务端打开「当前白板」。Mac 上选择、新建或删除白板之后，服务端向所有跟随的连接发送 `switch`，它们一起切换。当前白板不变时，重连的客户端按续传规则得到 `sync`。

跟随的连接收到的 `init`、`sync`、`switch` 另外带有 `boards`（白板列表，按显示顺序）和 `folders`（文件夹名单，包括空文件夹）。

### 扩展消息

客户端发往服务端（都需要 `manage` 权限；没有权限或没有产生改动的消息没有应答）：

| `t` | 字段 | 行为 |
| --- | --- | --- |
| `sel` | `board` | 将 `board` 设为当前白板，所有跟随的连接收到 `switch`。白板不存在或已是当前白板时忽略。只读白板会从磁盘重新读取。 |
| `newboard` | `kind`、`folder`（可选） | 新建白板（`kind` 为 `note` 时是 column 画布，其他值为无限画布）并设为当前白板，发送 `switch`。`folder` 为已有文件夹时，新白板放入该文件夹。文档板通过 `POST /api/doc` 新建。 |
| `delboard` | `board` | 删除白板文件、缩略图和文档原件，发送 `switch`。没有剩余白板时新建一块空白板。 |
| `rename` | `board`、`name` | 给任意一块白板改名，发送 `boards`。不改变 `updated`。 |
| `folder` | `board`、`folder` | 将白板放入文件夹（写 `data.folder`）；空串表示移出。新的名字会登记到文件夹名单中。发送 `boards`，不改变 `updated`。 |
| `order` | `ids` | `ids` 是发送方在当前层级（最外层或某个文件夹内）看到的顺序。所列白板只在它们原本占据的位置之间调换，其他白板位置不变。至少需要 2 个已知 id。发送 `boards`。 |
| `newfolder` | `name` | 新建空文件夹。名字为空或已存在时忽略。发送 `boards`。 |
| `delfolder` | `name` | 删除文件夹，其中的白板移出文件夹，不删除任何白板。发送 `boards`。 |
| `renamefolder` | `name`、`to` | 给文件夹改名，并更新其中各白板的 `data.folder`。`to` 为空、与 `name` 相同或已存在，或 `name` 不存在时忽略。发送 `boards`。 |

服务端发往跟随的连接：

| `t` | 字段 | 说明 |
| --- | --- | --- |
| `boards` | `boards`、`folders`、`board` | 只是白板列表变了（例如改名之后），客户端无需重新载入笔画。 |
| `perms` | `perms` | 权限变化后的权限名单，见[权限](#权限)。 |

没有 `manage` 权限的连接在 `boards` 中只看得到自己所在的那一块，`folders` 为空。

文件夹以名字区分，见 [format.zh-CN.md](format.zh-CN.md#文件夹)。

### 已安装应用的空间

`apps/` 下每个已安装的应用是一个空间，白板保存在 `spaces/<应用名>/`。只有已安装的应用名能打开空间；运行中安装的应用立即可用。规则：能写字的设备可以打开和新建白板（每分钟最多 60 块），修改元数据和解除只读需要 `manage`。应用的白板不进入用户的白板列表，Mac 在白板选择界面中按应用分页查看（只读）。

## 操作

操作是 `op` 消息中的 `op` 对象；每个被接受的操作都会改变它所属的白板（`op` 消息的 `board`，省略时为连接当前的白板），并获得那块白板的下一个 `seq`。

操作所属的白板不是连接当前的白板时，还要经规则 `can_open` 许可。只读连接的操作都被拒绝。

| `op` | 字段 | 规则 | 服务端行为 | 广播形式 |
| --- | --- | --- | --- | --- |
| `add` | `strokes` | `can_write` | 逐条校验笔画，忽略已存在的 id，为每条笔画分配新的 `n`。每个操作最多 2000 条笔画。 | `add` |
| `restore` | `strokes` | `can_write` | 与 `add` 相同，但保留每条笔画自带的 `n`。 | `add` |
| `remove` | `ids` | `can_write` | 删除所列笔画，忽略未知的 id。最多 5000 个 id。 | `remove` |
| `mask` | `masks`：`[{id, m}]` | `can_write` | 用 `m` 替换各笔画的遮罩；`m` 为空时删除遮罩。忽略未知的 id 和未变化的遮罩。最多 2000 项。 | `mask`，只含发生变化的项 |
| `clear` | — | `can_write` 与 `can_clear` | 删除全部笔画。白板为空时忽略。 | `clear` |
| `meta` | `meta` | 规则 `can_edit_meta` | 可修改 `name`、`background`、`layers`、`data`；`data` 按键合并（值为 null 的键删除），其余整体替换。`canvas` 不可修改。`data` 中出现 `protected_data_keys` 的键时整个操作被拒绝。 | `meta`，附带修改后的完整 `meta` |

不合法、被规则拒绝或没有产生改动的操作不会被记录，其回执不含 `op`。

```jsonc
{"op":"add","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                        "p":[120.5,40.25,0.03, 121.0,41.0,0.04],"dev":"ipad"}]}
{"op":"restore","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                            "p":[...],"n":42,"dev":"ipad"}]}
{"op":"remove","ids":["c3f1-17"]}
{"op":"mask","masks":[{"id":"c3f1-17","m":[[5,120,40,180,40]]}]}
{"op":"clear"}
{"op":"meta","meta":{"background":{"pattern":"grid"},"name":"线性代数","data":{"done":true}}}
```

笔画字段与限制见 [format.zh-CN.md](format.zh-CN.md#笔画字段)，遮罩格式见 [format.zh-CN.md](format.zh-CN.md#遮罩m)。

### 操作规则

- **层叠顺序。** 服务端按递增顺序分配 `n`，客户端按 `n` 升序绘制。`restore` 保留原来的 `n`，因此撤销擦除后层叠关系与擦除前相同。
- **撤销。** 撤销和重做在客户端完成。客户端立即在本地应用改动，再发送等价的 `remove`、`restore` 或 `mask`，因此断网时也可以撤销。撤销 `clear` 时发送 `restore`。
- **重复。** `add` 和 `restore` 忽略已存在的笔画 id，但仍然发送 `ack`，因此重连后重发是安全的。
- **不合法数据。** 不合法的值被丢弃或限制到合法范围内（见 [format.zh-CN.md](format.zh-CN.md#笔画字段)）。一条不合法的消息不会导致连接断开。
- **超长笔画。** 超过 20000 个点的笔画整条丢弃，不做截断。客户端在提交时切分更长的笔画（`stroke.js` 的 `splitLongStroke`，`MAX_STROKE_POINTS` = 20000）：相邻两段共用一个采样点，各端均为圆头，不设置 `cut`，各段在同一个 `add` 中发送，并作为一步撤销。
- **完整遮罩。** `mask` 发送每条笔画当前的完整遮罩，而不是增量。完整遮罩在断线重连后以及任何到达顺序下都是正确的。
- **擦除合并发送。** 擦除拖动过程中，客户端收集操作，每个动画帧发送一次，顺序为 `remove`、`restore`、`mask`。

## 回执

服务端对每条 `op` 消息向发送方发送且只发送一条 `ack`，`board` 是操作所属的白板。

| 情况 | `ack` |
| --- | --- |
| 已接受 | `{"t":"ack","cid":…,"board":…,"seq":<新的 seq>,"op":<规整后并带 seq 的操作>}` |
| 重复或没有改动 | `{"t":"ack","cid":…,"board":…,"seq":<当前 seq>}` |
| 不合法 | 同上，另加 `"rejected":"invalid"` |
| 规则不允许、连接只读 | 同上，另加 `"rejected":"denied"` |
| 白板只读 | 同上，另加 `"rejected":"locked"` |
| 白板已删除 | 同上，另加 `"rejected":"deleted"`；已删除的白板不会因为这个操作被重新载入或写回 |
| 处理时服务端出错 | 同上，另加 `"rejected":"error"` |

收到 `ack` 后，客户端将 `cid` 从待发队列中移除，并应用返回的 `op`（`net.js` 的 `_ack`），使发送方的白板与服务端规整后的结果一致。带 `rejected` 的回执：客户端触发 `rejected` 事件并交出这个操作，然后重新取当前白板的完整内容，使本地显示与服务端一致。重复的操作不算被拒绝：重连之后重发是正常的。

> **警告**
> 客户端自己发出的 `mask` 操作随 `ack` 返回时，客户端不应用它（`pad.js` 的 `applyOp`，`context.mine`）。发送之后本地遮罩可能已经增加；应用较早的完整遮罩会撤销较新的擦除。因此服务端上限 `MAX_MASK_SEGMENTS`（1024）必须能容纳客户端可能产生的任何遮罩（`MASK_LIMIT` = 400）；`tests/test_models.py` 检查这一条件。

## 序号、纪元与重连

每个被接受的操作获得所属白板的下一个 `seq`，服务端保留每块白板最近 4000 条操作（`inksync.hub.OPS_HISTORY`）用于补发。

客户端记录收到的最后一个 `seq` 和 `epoch`，并在 `hello` 或 `open` 中以 `since` 和 `epoch` 发送。

| 条件 | 应答 |
| --- | --- |
| 打开的白板与请求中的 `board` 相同，`epoch` 一致，`since` 为不小于 0 的整数，并且历史记录包含 `since` 之后的全部操作 | `sync`，包含缺失的操作（没有缺失时为空） |
| 其他任何情况：白板不同、`epoch` 不同、缺少 `since`，或历史记录已不包含 `since` 之后的全部操作 | `init`，包含整块白板 |

服务进程每次把白板载入内存时生成新的 `epoch`。服务端重启后 `seq` 从 0 开始；如果没有 `epoch`，持有较大旧 `seq` 的客户端会误认为白板没有变化。`epoch` 改变后，客户端会收到完整快照。

长时间没有连接、也没有未保存改动的白板会从内存中释放（默认 120 秒），只保留 `(epoch, seq)`。再次载入时沿用这对值，因此停在最新 `seq` 的客户端得到空的 `sync`；落后的客户端因操作历史已经释放而得到 `init`。

客户端从 `init`、`sync`、`switch` 的 `seq`，`op` 消息的 `op.seq`，以及 `ack` 的 `seq` 更新最后一个 `seq`。

## 只读白板

文件未能完整读出的白板在发送时带有非 null 的 `locked`，服务端拒绝对它的所有操作。

```jsonc
"locked": {"reason": "partial", "dropped": 3}
```

| `reason` | 附加字段 |
| --- | --- |
| `unreadable` | `detail` |
| `corrupt` | `detail` |
| `partial` | `dropped` |
| `newer` | `version` |

各原因的含义见 [format.zh-CN.md](format.zh-CN.md#版本与读不全的文件)。

- 针对该白板的每个 `op`（包括 `meta`）都会收到带 `"rejected":"locked"` 的 `ack`，白板内容不变。
- 客户端停止接受输入，并对每块白板显示一次提示。
- `rename` 和 `folder` 仍然有效，它们只改写文件中的 `meta`。
- `unlock` 作用于连接当前的白板，需经规则 `can_unlock` 许可（白板应用：`manage` 权限）。服务端先将文件复制到 `backups/locked/`；复制失败时白板保持只读，不发送任何消息。复制成功后，服务端向显示这块白板的所有连接发送 `{"t":"locked","board":…,"locked":null}`。白板 id 和 `epoch` 不变，客户端保留当前视图和撤销记录。

## 错误处理

处理消息时出现意外错误，服务端只记录日志，不断开连接。

如果该消息是 `op`，服务端仍然发送 `ack`。客户端每次重连后都会重发未收到回执的操作；如果服务端因为某个操作出错而断开连接，会导致客户端不断重连和重发。

## 权限与设备角色

权限决定一条连接或一个请求可以执行哪些动作；角色只决定界面布局。

### 身份与规则（inksync）

inksync 的每条连接有一个身份（`Principal`：`id`、`local`、`address`、`attrs`），由使用者的 `authenticate(request)` 在连接建立时确定。每次动作时，服务端调用规则（`Policy`）的方法判断是否允许：

| 方法 | 用于 |
| --- | --- |
| `can_open`、`can_create`、`create_limit` | 打开、新建白板（`hello`、`open`，以及不是当前白板的 `op`） |
| `can_write` | `add`、`restore`、`remove`、`mask`、`live` |
| `can_clear` | `clear` |
| `can_edit_meta`、`protected_data_keys` | `meta` |
| `can_unlock` | `unlock` |

判断结果随快照发给客户端（`caps`），规则的依据变化后服务端发送 `caps` 消息。接口见 [packages/inksync/README.zh-CN.md](../packages/inksync/README.zh-CN.md)。

### 白板应用的权限

白板应用把四项权限映射到上面的规则上（用户空间）：

| 权限 | 界面名称 | 允许的动作 |
| --- | --- | --- |
| `manage` | 「管理白板」 | 打开当前白板以外的白板；扩展消息 `sel`、`newboard`、`delboard`、`rename`、`folder`、`order`、`newfolder`、`delfolder`、`renamefolder`；`unlock`；`GET /api/info`、`GET /api/boards`、`GET /api/spaces/{应用}/boards`、`GET`/`POST /api/thumb/{board}`、`POST /api/doc` |
| `settings` | 「设置白板」 | `meta` 操作 |
| `clear` | 「清空白板」 | `clear` 操作 |
| `export` | 「导出白板」 | `GET /api/export/{board}` |

在当前白板上书写（`add`、`restore`、`remove`、`mask`、`live`）不需要权限。文件夹、文档原件信息只能通过扩展消息修改（`protected_data_keys` 包含 `folder`、`doc`、`app`）。

没有 `manage` 时，连接只看得到自己所在的白板：快照和 `boards` 消息中的 `boards` 只包含这一块，`folders` 为空。读取非当前白板的 `GET /api/doc/{board}/{页码}` 同样需要 `manage`。

### 权限的授予

权限根据 TCP 连接确定（`netinfo.is_local_request`），不依据 `role` 或 `?role=`，因为这两项由客户端自行填写。

| 对端 | 权限 |
| --- | --- |
| 本机：回环地址、Mac 的局域网地址，或对端地址等于服务端接收该连接的地址（任意网卡，IPv4 或 IPv6） | 全部四项 |
| 带有 `X-Forwarded-For` 或 `Forwarded` 的请求 | 即使来自回环地址也按其他设备处理：请求经过了代理 |
| 其他设备 | 仅限在 Mac 的「白板设置」中开启的项目；默认没有任何权限 |
| 无法获取地址 | 无 |

- 该设置以 `remote_permissions` 保存在 `config.json` 中。
- 修改设置立即生效：服务端按保存的请求重新计算所有已建立连接的身份，向权限有变化的连接发送 `caps`、`{"t":"perms","perms":[…]}` 和更新后的 `boards` 消息，页面随之显示或隐藏入口。
- 页面载入时也会通过 `<html>` 上的 `data-perms` 获得权限清单。客户端只据此隐藏入口，权限由服务端执行检查。
- 检查更新和选择存储目录使用本机的 pywebview 接口，其他设备无法使用。

### 设备角色

角色（`mac` 或 `ipad`）决定界面布局。

| 步骤 | 规则 |
| --- | --- |
| 1. URL 参数 | `?role=mac` 或 `?role=ipad` 优先级最高。Mac 窗口（pywebview）使用 `?role=mac`。 |
| 2. 触摸检测 | `navigator.maxTouchPoints > 1` 时判定为 `ipad`（iPadOS 的 Safari 默认报告 Mac 的 User-Agent）。 |
| 3. 服务端提示 | 服务端在页面中写入 `data-role`：User-Agent 含有 `iPad`、`iPhone` 或 `iPod` 时为 `ipad`，否则为 `mac`。 |

## HTTP 接口

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| GET | `/` | — | 应用页面，带 `data-role`、`data-perms` 和 `data-build` |
| GET | `/ws` | — | WebSocket |
| GET | `/static/...` | — | 白板应用的前端文件，`Cache-Control: no-cache` |
| GET | `/inksync/...` | — | 手写板的前端文件（inksync 的 `serve_sdk`），入口 `/inksync/inkpad.js`；`/inksync/version.js` 由服务端生成，写入构建号。`Cache-Control: no-cache` |
| GET | `/profile.mobileconfig?host=<主机名>` | — | iPad 配置描述文件（Web Clip） |
| GET | `/icon.png` | — | 180 px 的应用图标 |
| GET | `/ipad?host=<主机名>` | — | iPad 外壳安装页，见 [ipad-shell.md](ipad-shell.md) |
| GET | `/ipad/version` | — | 本机附带的 iPad 外壳版本 |
| GET | `/ipad/Whiteboard.ipa` | — | iPad 外壳安装包；未附带时返回 404 |
| GET | `/api/info` | `manage` | `build`、`hostname`、`port`、`urls`、`data_dir`、`current`、`clients`（`id`、`role`（设备类型）、`since`） |
| GET | `/api/boards` | `manage` | `boards`、`folders`、`current` |
| GET | `/api/thumb/{board}` | `manage` | 缩略图 PNG。未上传缩略图时：文档板返回原件首页，其他白板返回空白 PNG。 |
| POST | `/api/thumb/{board}` | `manage` | 上传缩略图。请求体为 PNG，最大 512 KB。 |
| POST | `/api/debug` | — | 客户端诊断信息，写入服务端日志。请求体为 JSON 对象；记录前 20 个键，最多 1000 个字符。 |
| POST | `/api/recording` | — | 将输入录制保存到 `recordings/`。请求体为带 `events` 数组的 JSON 对象，最大 32 MB。保留最新的 50 个文件，总共不超过 256 MB。见 [recording.md](recording.md)。 |
| POST | `/api/doc?name=<文件名>&folder=<文件夹>` | `manage` | 由 PDF 或图片新建文档板。请求体为文件本身，最大 256 MB。 |
| GET | `/api/doc/{board}/{页码}?w=<宽度>` | 当前白板不需要，其他白板需要 `manage` | 渲染后的页面图像 |
| GET | `/api/apps` | — | `apps`（已安装的应用名称）、`ipad_home` |
| GET | `/api/spaces/{应用}/boards?offset=&limit=` | `manage` | 某个应用空间里的白板：`boards`（元数据，按 `updated` 从新到旧）、`total`。`limit` 为 1～500，默认 100。应用不存在时返回 `404`。 |
| GET | `/apps/{应用}/{路径}` | — | 已安装应用的静态文件；目录返回其中的 `index.html`。见 [embed.zh-CN.md](embed.zh-CN.md)。 |
| GET | `/sdk/inkpad.js` | — | 1.0.1 的地址，跳转到 `/inksync/inkpad.js`。 |
| GET | `/api/export/{board}` | `export` | 笔迹合并到原件之后的文档板 |

缺少权限时返回 `403`。

### 接口细节

| 接口 | 细节 |
| --- | --- |
| `POST /api/thumb/{board}` | 白板不存在时返回 `404`；请求体不是 PNG 时返回 `400`。 |
| `POST /api/doc` | 文件名也可以通过 `X-Filename` 请求头发送。`folder` 可省略，仅在该文件夹存在时生效。请求体按 64 KB 分块读取直到结束（`StreamReader.read(n)` 最多返回 `n` 字节）。超过上限返回 `413`，文件类型不支持或无法读取返回 `400`。响应为 `{"board": <meta>}`。随后服务端向所有跟随的连接发送 `switch`。 |
| `GET /api/doc/{board}/{页码}` | `w` 默认为 1200，限制在 160～2600 之间。PDF 页面和不透明图片返回 JPEG（质量 82），带透明度的图片返回 PNG。`ETag` 为 `"<board>-<页码>-<宽度>"`，`If-None-Match` 匹配时返回 `304`。`Cache-Control: private, max-age=31536000, immutable`，因为原件不会改变。白板不是文档板、原件丢失或页码超出范围时返回 `404`。 |
| `GET /api/export/{board}` | 仅限文档板（否则返回 `404`）。`Content-Type: application/octet-stream`，`Content-Disposition: attachment; filename*=UTF-8''<文件名>`，`Cache-Control: no-store`。文件名为 `<原文件名>-批注<扩展名>`。 |
| `POST /api/recording` | 保存为 `recordings/<YYYYmmdd-HHMMSS>[-<名称>].json`；`name` 只保留字母、数字、`-` 和 `_`，最长 40 个字符。响应为 `{"ok": true, "path": ..., "events": <条数>}`。 |

- 页面图片层的 `src` 带 `{w}`，前端只按 640、1024、1600、2400 这几个宽度请求（见 [format.zh-CN.md](format.zh-CN.md#meta-字段)），浏览器缓存因此可以长期有效。
- 同一时间最多渲染 2 页（`server.RENDER_LIMIT`）。所有 pdfium 调用由同一把锁串行执行（`docs._PDFIUM_LOCK`），因为 pdfium 不是线程安全的，并发调用会导致进程崩溃。
- 页面使用带 `download` 属性的 `<a>` 链接触发下载，不给 `location.href` 赋值。在 iPad 外壳（WKWebView）中，给 `location.href` 赋值会离开当前页面并关闭 WebSocket，见 [ipad-shell.md](ipad-shell.md)。

## mDNS 与 Bonjour

iPad 通过 `<主机名>.local` 访问 Mac；程序还可以注册两个 DNS-SD 服务。

| 服务 | 用途 | 默认 | 实现 | 命令行参数 |
| --- | --- | --- | --- | --- |
| `.local` 主机名 | Mac 的地址 | 始终存在 | 由 macOS 的 mDNSResponder 发布，程序不参与 | — |
| `_http._tcp`（名称为「Whiteboard」，TXT 为 `path=/`） | 通用服务发现 | macOS 上关闭，其他平台开启 | zeroconf 异步 API（`netinfo.MDNSAdvertiser`） | `--mdns` 强制开启，`--no-mdns` 强制关闭 |
| `_whiteboard._tcp` | iPad 外壳查找 Mac | macOS 上开启，其他平台关闭 | macOS：通过系统 mDNSResponder 调用 `DNSServiceRegister`；其他平台：zeroconf（`netinfo.BonjourService`） | `--no-bonjour` 关闭 |

`_whiteboard._tcp` 的 TXT 记录：

| 键 | 值 |
| --- | --- |
| `host` | `<主机名>.local` |
| `port` | 实际监听的端口 |
| `version` | 程序版本号 |
| `name` | 电脑名称 |
| `source` | 服务名，白板应用为 `whiteboard`。iPad 外壳「来源」为 `@<名字>` 时只连接同名的服务。 |
| `path` | 外壳连上之后打开的页面路径 |

其他项目用 `inksync.netinfo.advertise(app, port, source, path)` 注册同一种服务。

注册规则：

- 注册在 HTTP 服务就绪之后以后台任务执行，超时为 5 s（`netinfo.REGISTER_TIMEOUT`）。任何失败只记录日志，不影响服务端。
- 必须使用 zeroconf 的异步 API。同步 API 会阻塞 asyncio 事件循环，抛出 `EventLoopBlocked`，并使服务端启动一直等待到超时。
- macOS 上不使用 zeroconf，因为它会在系统的 mDNS 响应程序之外再启动一个响应程序。

## 1.x 页面

升级时仍打开着的 1.x 页面（`hello` 中没有 `v`）在白板应用 2.0 的用户空间中照常工作，直到页面重新载入。

| 1.x 页面发送 | 2.0 的处理 |
| --- | --- |
| 没有 `v` 的 `hello` | 按 `follow: true` 处理，打开当前白板。 |
| 带 `pin` 的 `hello`（1.0.1 的嵌入手写板） | `{"t":"error","reason":"pin"}`：应用白板已改为独立空间，1.0.1 的嵌入接口没有使用者。 |
| 没有 `board` 的 `op` | 按连接当前的白板处理。 |

发给 1.x 页面的消息换回 1.x 的形式：快照带 `role`；元数据（包括 `op` 和 `ack` 中的）换回 `kind`、`folder`、`doc`、`underlay` 和字符串形式的 `background`；`ack` 不带 `board` 和 `rejected`；`locked` 换成整块 `switch`；`caps` 和 `deleted` 不发。

2.0 的页面在服务端升级之后收到 `outdated` 事件（前端的构建号与快照中的 `server.build` 不同），白板应用据此在没有进行中的书写时重新载入页面，同一构建号只重新载入一次。这一兼容在 2.1 移除。

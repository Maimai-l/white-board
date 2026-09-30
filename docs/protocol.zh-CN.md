[English](protocol.md) | 简体中文

# 同步协议

客户端与服务端通过 `/ws` 上的一条 WebSocket 同步白板，消息为 JSON 文本；另有若干 HTTP 接口提供页面、缩略图和文档板。

客户端先在本地绘制，再发送操作。服务端负责编号、转发和保存操作，不阻塞本地绘制。

| 项目 | 值 |
| --- | --- |
| WebSocket 路径 | `/ws` |
| 消息格式 | JSON 文本帧；每条消息是一个带类型字段 `t` 的对象。二进制帧和非对象的 JSON 被忽略。 |
| 单条消息上限 | 8 MB（`server.MAX_WS_MESSAGE`） |
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
| `client` | 字符串 | 客户端 id，须符合 `^[A-Za-z0-9_-]{4,64}$`，否则服务端分配新的 id。网页客户端将 id 保存在 `localStorage` 的 `whiteboard.client` 键中。 |
| `role` | 字符串 | `mac` 或 `ipad`，其他值按 `mac` 处理。仅决定界面布局。 |
| `board` | 字符串或 null | 客户端上次显示的白板 id。 |
| `since` | 整数 | 客户端收到的最后一个 `seq`。 |
| `epoch` | 字符串或 null | 客户端收到的最后一个 `epoch`。 |
| `pin` | 对象 | 可选。把连接固定在一块白板上，见[固定白板的连接](#固定白板的连接)。 |

服务端以 `init` 或 `sync` 应答，见[序号、纪元与重连](#序号纪元与重连)。同一连接上的第二条 `hello` 被忽略。

### 固定白板的连接

连接分为跟随当前白板和固定在一块白板上两种。`hello` 中没有 `pin` 时，连接跟随当前白板：任一设备切换白板，它也随之切换。带有 `pin` 时，连接始终停留在指定的白板上。其他应用嵌入手写板时使用固定连接，例如每道题一块白板。

| `pin` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `board` | 字符串 | 白板 id，须符合 `^[A-Za-z0-9_-]{1,64}$`。 |
| `app` | 字符串 | 应用名称，须符合 `^[a-z0-9-]{1,32}$`。新建白板时记入 `meta.app`。 |
| `name` | 字符串 | 可选。新建白板的名称。 |
| `kind` | 字符串 | 可选。新建白板的类型，`board` 或 `note`，默认为 `board`。 |
| `folder` | 字符串 | 可选。新建白板所在的文件夹；文件夹不存在时自动建立。 |

```jsonc
{"t":"hello","role":"ipad","client":"k3m9x0a1b2c4","board":null,"since":0,"epoch":null,
 "pin":{"board":"qb-9709-s23-12-q3","app":"qb","name":"9709 s23 P12 Q3","folder":"刷题"}}
```

| 规则 | 行为 |
| --- | --- |
| 白板不存在 | 服务端按给定的 `app`、`name`、`kind` 和 `folder` 新建白板，当前白板不变。 |
| 白板存在且有 `meta.app` | 任何设备都可以固定到这块白板。 |
| 白板存在但没有 `meta.app`（用户自己的白板） | 需要「管理白板」权限，与切换白板相同。 |
| `pin` 无效或权限不足 | 服务端发送 `{"t":"error","reason":"pin"}` 并关闭连接。 |
| 操作与 `live` | 作用于固定的白板，并发给所有正在显示这块白板的连接：固定在它上面的其他连接，以及它是当前白板时跟随的连接。 |
| `switch` 与 `boards` | 不发给固定连接。 |
| 固定的白板被删除 | 服务端发送 `{"t":"deleted","board":"<id>"}`。此后的操作只回执，不含 `op`，也不应用。 |
| `unlock` | 作用于固定的白板。 |

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

- `cid` 的格式为 `<客户端 id>-<时间的 36 进制>-<计数器的 36 进制>`。
- 待发队列保存在浏览器缓存（IndexedDB）中，页面重新载入后恢复。
- 每次收到 `init`、`sync` 或 `switch` 后，客户端按顺序重发待发队列中的全部操作。
- 收到快照后，客户端将待发操作重新应用到本地白板上。

## 客户端发往服务端的消息

| `t` | 字段 | 权限 | 服务端的应答 |
| --- | --- | --- | --- |
| `hello` | `client`、`role`、`board`、`since`、`epoch` | — | 向发送方发送 `init` 或 `sync` |
| `op` | `cid`、`op` | 取决于操作，见[操作](#操作) | 向发送方发送 `ack`；向其他所有客户端发送 `op` |
| `live` | `id`、`phase` 及各阶段的字段 | — | 加上 `src` 后转发给其他所有客户端 |
| `ping` | `ts` | — | 返回带相同 `ts` 的 `pong` |
| `sel` | `board` | `manage` | 向所有客户端发送 `switch` |
| `newboard` | `kind`、`folder`（可选） | `manage` | 向所有客户端发送 `switch` |
| `delboard` | `board` | `manage` | 向所有客户端发送 `switch` |
| `rename` | `board`、`name` | `manage` | 向所有客户端发送 `boards` |
| `folder` | `board`、`folder` | `manage` | 向所有客户端发送 `boards` |
| `order` | `ids` | `manage` | 向所有客户端发送 `boards` |
| `newfolder` | `name` | `manage` | 向所有客户端发送 `boards` |
| `delfolder` | `name` | `manage` | 向所有客户端发送 `boards` |
| `renamefolder` | `name`、`to` | `manage` | 向所有客户端发送 `boards` |
| `unlock` | — | `manage` | 向所有客户端发送 `switch` |

缺少所需权限或没有产生任何改动的消息不会得到应答。`op` 例外，服务端总是发送回执。

```jsonc
{"t":"hello","client":"k3m9x0a1b2c4","role":"ipad","board":"49b773c7c7c2","since":42,"epoch":"a1b2c3d4e5f6"}
{"t":"op","cid":"k3m9x0a1b2c4-m1x2y3-7","op":{"op":"clear"}}
{"t":"live","id":"c3f1-17","phase":"b","tool":"pen","color":"#1b1b1f","w":3}
{"t":"live","id":"c3f1-17","phase":"m","p":[120.5,40.25,0.03, 121.0,41.0,0.04]}
{"t":"live","id":"c3f1-17","phase":"e"}
{"t":"ping","ts":1730000000000}
{"t":"sel","board":"49b773c7c7c2"}
{"t":"newboard","kind":"note","folder":"数学"}
{"t":"delboard","board":"49b773c7c7c2"}
{"t":"rename","board":"49b773c7c7c2","name":"线性代数"}
{"t":"folder","board":"49b773c7c7c2","folder":"数学"}
{"t":"order","ids":["49b773c7c7c2","0d4be1a2f9c3"]}
{"t":"newfolder","name":"数学"}
{"t":"delfolder","name":"数学"}
{"t":"renamefolder","name":"数学","to":"线性代数"}
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

### 白板管理

白板管理消息改变白板列表或当前白板。

| `t` | 行为 |
| --- | --- |
| `sel` | 保存所有白板，然后将 `board` 设为当前白板。白板不存在或已是当前白板时忽略。只读白板会从磁盘重新读取。 |
| `newboard` | 新建 `kind` 为 `board` 或 `note` 的白板并设为当前白板（其他值，包括 `doc`，均新建 `board`）。`folder` 为已有文件夹时，新白板放入该文件夹。文档板通过 `POST /api/doc` 新建。 |
| `delboard` | 删除白板文件、缩略图和文档原件。没有剩余白板时新建一块空白板。 |
| `rename` | 给任意一块白板改名，不限于当前白板。不改变 `updated`。 |
| `folder` | 将白板放入文件夹；空串表示移出文件夹。新的名字会登记到文件夹名单中。不改变 `updated`。 |
| `order` | `ids` 是发送方在当前层级（最外层或某个文件夹内）看到的顺序。所列白板只在它们原本占据的位置之间调换，其他白板位置不变。至少需要 2 个已知 id。 |
| `newfolder` | 新建空文件夹。名字为空或已存在时忽略。 |
| `delfolder` | 删除文件夹，其中的白板移出文件夹，不删除任何白板。 |
| `renamefolder` | 给文件夹改名，并更新其中各白板的 `meta.folder`。`to` 为空、与 `name` 相同或已存在，或 `name` 不存在时忽略。 |

文件夹以名字区分，见 [format.zh-CN.md](format.zh-CN.md#文件夹)。

## 服务端发往客户端的消息

| `t` | 接收方 | 字段 |
| --- | --- | --- |
| `init` | `hello` 的发送方 | `role`、`client`、`info`、`board`、`strokes`、`seq`、`epoch`、`locked`、`boards`、`folders` |
| `sync` | `hello` 的发送方 | `role`、`client`、`info`、`board`、`ops`、`seq`、`epoch`、`locked`、`boards`、`folders` |
| `switch` | 所有跟随当前白板的客户端 | `board`、`strokes`、`seq`、`epoch`、`locked`、`boards`、`folders` |
| `boards` | 所有跟随当前白板的客户端 | `boards`、`folders`、`board` |
| `op` | 显示同一块白板的客户端，发送方除外 | `op`（含 `seq`）、`src`、`board` |
| `ack` | `op` 的发送方 | `cid`、`seq`、`op`（仅在操作被接受时） |
| `live` | 显示同一块白板的客户端，发送方除外 | 原 `live` 消息的字段，加上 `src` |
| `pong` | `ping` 的发送方 | `ts` |
| `error` | 带有无效 `pin` 的 `hello` 的发送方 | `reason` 为 `pin` |
| `deleted` | 固定在被删除白板上的客户端 | `board` |

```jsonc
{"t":"init","role":"ipad","client":"k3m9x0a1b2c4","info":{...},"board":{...},"strokes":[...],
 "seq":42,"epoch":"a1b2c3d4e5f6","locked":null,"boards":[...],"folders":["数学"]}
{"t":"sync","role":"ipad","client":"k3m9x0a1b2c4","info":{...},"board":{...},"ops":[...],
 "seq":45,"epoch":"a1b2c3d4e5f6","locked":null,"boards":[...],"folders":["数学"]}
{"t":"switch","board":{...},"strokes":[...],"seq":0,"epoch":"f6e5d4c3b2a1","locked":null,
 "boards":[...],"folders":["数学"]}
{"t":"boards","boards":[...],"folders":["数学"],"board":{...}}
{"t":"op","op":{"op":"remove","ids":["c3f1-17"],"seq":43},"src":"k3m9x0a1b2c4","board":"49b773c7c7c2"}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-7","seq":43,"op":{"op":"remove","ids":["c3f1-17"],"seq":43}}
{"t":"ack","cid":"k3m9x0a1b2c4-m1x2y3-8","seq":43}
{"t":"live","id":"c3f1-17","phase":"m","p":[...],"src":"k3m9x0a1b2c4"}
{"t":"pong","ts":1730000000000}
```

### 快照字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `board` | 对象 | 当前白板的 `meta`，见 [format.zh-CN.md](format.zh-CN.md#meta-字段)。 |
| `strokes` | 数组 | 当前白板的全部笔画，按 `n` 升序，点列为扁平数组。 |
| `ops` | 数组 | `seq` 大于 `since` 的操作，按顺序排列。 |
| `seq` | 整数 | 白板当前的 `seq`。 |
| `epoch` | 字符串 | 白板在内存中的纪元。 |
| `locked` | 对象或 null | 正常白板为 `null`；否则为只读原因，见[只读白板](#只读白板)。 |
| `boards` | 数组 | 全部白板的元数据，按显示顺序排列。 |
| `folders` | 数组 | 文件夹名单，包括空文件夹。 |
| `role` | 字符串 | 服务端为该连接记录的角色。 |
| `client` | 字符串 | 服务端为该连接记录的客户端 id。 |
| `info` | 对象 | 服务端信息，见下表。 |

只有白板列表发生变化时（例如改名之后），服务端发送 `boards`，客户端无需重新载入笔画。

| `info` 字段 | 说明 |
| --- | --- |
| `version` | 程序版本号 |
| `hostname` | `<名称>.local` 形式的主机名 |
| `port` | 监听端口 |
| `urls` | 候选地址：`http://<主机名>.local:<端口>/`，以及已知时的 `http://<局域网 IP>:<端口>/` |
| `data_dir` | 存储目录 |

## 操作

操作是 `op` 消息中的 `op` 对象；每个被接受的操作都会改变当前白板，并获得一个 `seq`。

| `op` | 字段 | 权限 | 服务端行为 | 广播形式 |
| --- | --- | --- | --- | --- |
| `add` | `strokes` | — | 逐条校验笔画，忽略已存在的 id，为每条笔画分配新的 `n`。每个操作最多 2000 条笔画。 | `add` |
| `restore` | `strokes` | — | 与 `add` 相同，但保留每条笔画自带的 `n`。 | `add` |
| `remove` | `ids` | — | 删除所列笔画，忽略未知的 id。最多 5000 个 id。 | `remove` |
| `mask` | `masks`：`[{id, m}]` | — | 用 `m` 替换各笔画的遮罩；`m` 为空时删除遮罩。忽略未知的 id 和未变化的遮罩。最多 2000 项。 | `mask`，只含发生变化的项 |
| `clear` | — | `clear` | 删除全部笔画。白板为空时忽略。 | `clear` |
| `meta` | `meta` | `settings` | 只接受 `background` 和 `name`。 | `meta`，附带修改后的完整 `meta` |

不合法、缺少权限或没有产生改动的操作不会被记录，其回执不含 `op`。

```jsonc
{"op":"add","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                        "p":[120.5,40.25,0.03, 121.0,41.0,0.04],"dev":"ipad"}]}
{"op":"restore","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                            "p":[...],"n":42,"dev":"ipad"}]}
{"op":"remove","ids":["c3f1-17"]}
{"op":"mask","masks":[{"id":"c3f1-17","m":[[5,120,40,180,40]]}]}
{"op":"clear"}
{"op":"meta","meta":{"background":"grid","name":"线性代数"}}
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

服务端对每条 `op` 消息向发送方发送且只发送一条 `ack`。

| 情况 | `ack` |
| --- | --- |
| 已接受 | `{"t":"ack","cid":...,"seq":<新的 seq>,"op":<规整后并带 seq 的操作>}` |
| 不合法、重复、无改动、无权限、白板只读 | `{"t":"ack","cid":...,"seq":<当前 seq>}` |
| 处理时服务端出错 | `{"t":"ack","cid":...,"seq":<当前 seq 或 0>}` |

收到 `ack` 后，客户端将 `cid` 从待发队列中移除，并应用返回的 `op`（`net.js` 的 `_ack`），使发送方的白板与服务端规整后的结果一致。

> **警告**
> 客户端自己发出的 `mask` 操作随 `ack` 返回时，客户端不应用它（`app.js` 的 `applyOp`，`context.mine`）。发送之后本地遮罩可能已经增加；应用较早的完整遮罩会撤销较新的擦除。因此服务端上限 `MAX_MASK_SEGMENTS`（1024）必须能容纳客户端可能产生的任何遮罩（`MASK_LIMIT` = 400）；`tests/test_models.py` 检查这一条件。

## 序号、纪元与重连

每个被接受的操作获得所属白板的下一个 `seq`，服务端保留最近 4000 条操作（`hub.OPS_HISTORY`）用于补发。

客户端记录收到的最后一个 `seq` 和 `epoch`，并在 `hello` 中以 `since` 和 `epoch` 发送。

| 条件 | 应答 |
| --- | --- |
| `board` 等于当前白板，`epoch` 一致，`since` 为不小于 0 的整数，并且历史记录包含 `since` 之后的全部操作 | `sync`，包含缺失的操作（没有缺失时为空） |
| 其他任何情况：白板不同、`epoch` 不同、缺少 `since`，或历史记录已不包含 `since` 之后的全部操作 | `init`，包含整块白板 |

每次白板被载入服务端内存时生成新的 `epoch`。服务端重启后 `seq` 从 0 开始；如果没有 `epoch`，持有较大旧 `seq` 的客户端会误认为白板没有变化。`epoch` 改变后，客户端会收到完整快照。

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

- 针对该白板的每个 `op`（包括 `meta`）都会收到不含 `op` 的 `ack`，白板内容不变。
- 客户端停止接受输入，并对每块白板显示一次提示。
- `rename` 和 `folder` 仍然有效，它们只改写文件中的 `meta`。
- `unlock` 作用于当前白板，需要 `manage` 权限。服务端先将文件复制到 `backups/locked/`；复制失败时白板保持只读，不发送任何消息。复制成功后，服务端向所有客户端发送 `locked` 为 `null` 的 `switch`。白板 id 和 `epoch` 不变，因此客户端保留当前视图和撤销记录。

## 错误处理

处理消息时出现意外错误，服务端只记录日志，不断开连接。

如果该消息是 `op`，服务端仍然发送 `ack`。客户端每次重连后都会重发未收到回执的操作；如果服务端因为某个操作出错而断开连接，会导致客户端不断重连和重发。

## 权限与设备角色

权限决定一条连接或一个请求可以执行哪些动作；角色只决定界面布局。

### 权限

| 权限 | 界面名称 | 允许的动作 |
| --- | --- | --- |
| `manage` | 「管理白板」 | `sel`、`newboard`、`delboard`、`rename`、`folder`、`order`、`newfolder`、`delfolder`、`renamefolder`、`unlock`；`GET /api/info`、`GET /api/boards`、`GET`/`POST /api/thumb/{board}`、`POST /api/doc` |
| `settings` | 「设置白板」 | `meta` 操作 |
| `clear` | 「清空白板」 | `clear` 操作 |
| `export` | 「导出白板」 | `GET /api/export/{board}` |

在当前白板上书写（`add`、`restore`、`remove`、`mask`、`live`）不需要权限。

### 权限的授予

权限根据 TCP 对端地址确定，不依据 `role` 或 `?role=`，因为这两项由客户端自行填写。

| 对端 | 权限 |
| --- | --- |
| 回环地址，或 Mac 自身的局域网地址 | 全部四项 |
| 其他设备 | 仅限在 Mac 的「白板设置」中开启的项目；默认没有任何权限 |
| 无法获取地址 | 无 |

- 该设置以 `remote_permissions` 保存在 `config.json` 中。
- WebSocket 连接的权限在连接建立时确定。修改设置后，对新的连接和新载入的页面生效。
- 页面通过 `<html>` 上的 `data-perms` 获得权限清单。客户端只据此隐藏入口，权限由服务端执行检查。
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
| GET | `/static/...` | — | 前端文件，`Cache-Control: no-cache` |
| GET | `/profile.mobileconfig?host=<主机名>` | — | iPad 配置描述文件（Web Clip） |
| GET | `/icon.png` | — | 180 px 的应用图标 |
| GET | `/ipad?host=<主机名>` | — | iPad 外壳安装页，见 [ipad-shell.md](ipad-shell.md) |
| GET | `/ipad/version` | — | 本机附带的 iPad 外壳版本 |
| GET | `/ipad/Whiteboard.ipa` | — | iPad 外壳安装包；未附带时返回 404 |
| GET | `/api/info` | `manage` | `build`、`hostname`、`port`、`urls`、`data_dir`、`current`、`clients`（`id`、`role`、`since`） |
| GET | `/api/boards` | `manage` | `boards`、`folders`、`current` |
| GET | `/api/thumb/{board}` | `manage` | 缩略图 PNG。未上传缩略图时：文档板返回原件首页，其他白板返回空白 PNG。 |
| POST | `/api/thumb/{board}` | `manage` | 上传缩略图。请求体为 PNG，最大 512 KB。 |
| POST | `/api/debug` | — | 客户端诊断信息，写入服务端日志。请求体为 JSON 对象；记录前 20 个键，最多 1000 个字符。 |
| POST | `/api/recording` | — | 将输入录制保存到 `recordings/`。请求体为带 `events` 数组的 JSON 对象，最大 32 MB。见 [recording.md](recording.md)。 |
| POST | `/api/doc?name=<文件名>&folder=<文件夹>` | `manage` | 由 PDF 或图片新建文档板。请求体为文件本身，最大 256 MB。 |
| GET | `/api/doc/{board}/{页码}?w=<宽度>` | — | 渲染后的页面图像 |
| GET | `/api/export/{board}` | `export` | 笔迹合并到原件之后的文档板 |

缺少权限时返回 `403`。

### 接口细节

| 接口 | 细节 |
| --- | --- |
| `POST /api/thumb/{board}` | 白板不存在时返回 `404`；请求体不是 PNG 时返回 `400`。 |
| `POST /api/doc` | 文件名也可以通过 `X-Filename` 请求头发送。`folder` 可省略，仅在该文件夹存在时生效。请求体按 64 KB 分块读取直到结束（`StreamReader.read(n)` 最多返回 `n` 字节）。超过上限返回 `413`，文件类型不支持或无法读取返回 `400`。响应为 `{"board": <meta>}`。随后服务端向所有客户端发送 `switch`。 |
| `GET /api/doc/{board}/{页码}` | `w` 默认为 1200，限制在 160～2600 之间。PDF 页面和不透明图片返回 JPEG（质量 82），带透明度的图片返回 PNG。`ETag` 为 `"<board>-<页码>-<宽度>"`，`If-None-Match` 匹配时返回 `304`。`Cache-Control: private, max-age=31536000, immutable`，因为原件不会改变。白板不是文档板、原件丢失或页码超出范围时返回 `404`。 |
| `GET /api/export/{board}` | 仅限文档板（否则返回 `404`）。`Content-Type: application/octet-stream`，`Content-Disposition: attachment; filename*=UTF-8''<文件名>`，`Cache-Control: no-store`。文件名为 `<原文件名>-批注<扩展名>`。 |
| `POST /api/recording` | 保存为 `recordings/<YYYYmmdd-HHMMSS>[-<名称>].json`；`name` 只保留字母、数字、`-` 和 `_`，最长 40 个字符。响应为 `{"ok": true, "path": ..., "events": <条数>}`。 |

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

注册规则：

- 注册在 HTTP 服务就绪之后以后台任务执行，超时为 5 s（`netinfo.REGISTER_TIMEOUT`）。任何失败只记录日志，不影响服务端。
- 必须使用 zeroconf 的异步 API。同步 API 会阻塞 asyncio 事件循环，抛出 `EventLoopBlocked`，并使服务端启动一直等待到超时。
- macOS 上不使用 zeroconf，因为它会在系统的 mDNS 响应程序之外再启动一个响应程序。

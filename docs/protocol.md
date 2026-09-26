# 同步协议

一条 WebSocket（`/ws`），JSON 文本消息。设计目标是**本地零延迟**：客户端先画，
再把操作发出去；服务端只负责编号、转发和落盘，任何一步都不阻塞本地绘制。

## 两条通道

| 通道 | 消息 | 是否持久化 | 作用 |
| --- | --- | --- | --- |
| 实时笔迹 | `live` | 否 | 书写过程中的采样点，让对端几乎同步看到笔在走 |
| 正式操作 | `op` | 是 | 笔画写完、擦除、清屏、撤销、改白板设置 |

实时笔迹丢了没关系，笔画结束时的 `op` 会把完整点列补上。

## 客户端 → 服务端

```jsonc
{"t":"hello","client":"<客户端 id>","role":"mac|ipad","board":"<白板 id>","since":42,"epoch":"<纪元>"}
{"t":"op","cid":"<本地操作 id>","op":{...}}   // 见下方「操作」
{"t":"live","id":"<笔画 id>","phase":"b","tool":"pen","color":"#1b1b1f","w":3}
{"t":"live","id":"<笔画 id>","phase":"m","p":[x,y,压感, ...]}
{"t":"live","id":"<笔画 id>","phase":"e"}      // 结束；"x" 表示这一笔作废
{"t":"sel","board":"<白板 id>"}                // 仅 Mac
{"t":"newboard","kind":"board|note"} / {"t":"delboard","board":"<白板 id>"}   // 仅 Mac
                                             // 文档板不走这里，走 POST /api/doc
{"t":"ping","ts":1730000000000}
```

## 服务端 → 客户端

```jsonc
{"t":"init","board":{...},"strokes":[...],"seq":42,"epoch":"...","boards":[...],"info":{...}}
{"t":"sync","ops":[...],"board":{...},"seq":42,"epoch":"...","boards":[...]}  // 只补差量
{"t":"switch", ...}          // 与 init 同结构，Mac 切换白板（含拖入文档新建）时广播给所有人
{"t":"op","op":{...,"seq":43},"src":"<来源客户端 id>"}
{"t":"ack","cid":"<本地操作 id>","seq":43,"op":{...}}     // 回执，带上服务端分配的层叠序号
{"t":"live", ..., "src":"<来源客户端 id>"}
{"t":"pong","ts":...}
```

## 操作

```jsonc
{"op":"add","strokes":[{"id":"c3f1-17","tool":"pen","color":"#1b1b1f","w":3,
                        "p":[x,y,压感, ...],"n":42,"dev":"ipad"}]}
{"op":"remove","ids":["c3f1-17"]}       // 擦除，或撤销一笔
{"op":"restore","strokes":[...]}        // 撤销擦除 / 撤销清屏，按原 n 复位层叠关系
{"op":"mask","masks":[{"id":"c3f1-17","m":[[5,120,40,180,40]]}]}   // 橡皮啃掉的缺口
{"op":"clear"}
{"op":"meta","meta":{"background":"grid","name":"随便起的名字"}}   // 仅 Mac
```

- `n` 由服务端分配并单调递增，客户端按 `n` 升序绘制。撤销擦除时带上原来的 `n`，
  层叠关系不会乱。
- 撤销在**客户端**完成：本地有一个操作栈，撤销时立刻在本地生效，再把等价的
  `remove` / `restore` 发出去，所以断网时撤销照样能用。
- 重复 `id` 的 `add` 会被服务端忽略，但仍然回 `ack`，这样重连重发是安全的。
- 非法数据（坏颜色、NaN 坐标）在服务端被丢弃或收敛到合法范围，不会因为一条坏
  消息断开连接。**超长点列是整条丢掉**，所以画得特别久的一笔由客户端在提交时
  切成几段（`stroke.js` 的 `splitLongStroke`），接缝共用同一个采样点、两端都是
  默认圆头，看不出接缝；切出来的几段一起发、算一步撤销。
- `mask` 发的是整条笔画当前的**全量**遮罩而不是增量：遮罩本来就小，全量在断线
  重连、乱序到达的情况下都不会错，不用管顺序。
- 服务端的校验结果会顺着 `ack` 回到发送端自己，发送端照常走一遍 `applyOp`
  （`net.js` 的 `_ack`）。**`mask` 例外：自己发出去的遮罩回来时直接跳过。**
  遮罩发的是全量，而回执回到手里时本地往往已经又擦了几下，照盖就是拿旧快照
  覆盖新状态——擦掉的点白丢，下一段胶囊接不回去，擦痕一节一节像一串香肠。
  本地是自己这些改动的权威：发之前就已经原样应用过了。所以服务端的上限必须装得下客户端合法产出的东西，否则
  这边刚擦掉的墨等回执一到自己又回来一部分，而且回执什么时候到取决于网络，
  看上去就是随机的。遮罩的上限因此和前端同一个口径（总胶囊段数，
  `models.MAX_MASK_SEGMENTS` 对 `stroke.js` 的 `MASK_LIMIT`），
  `tests/test_models.py` 里有用例把两边钉在一起。

## 序号、纪元与重连

服务端给每个被接受的操作分配递增的 `seq`，并保留最近 4000 条操作。客户端记住
最后收到的 `seq`，重连时在 `hello` 里带上 `since` 和 `epoch`：

| 条件 | 服务端回应 |
| --- | --- |
| 白板相同 + `epoch` 相同 + 历史覆盖得到 `since` | `sync`，只发缺失的操作 |
| 白板不同 / `epoch` 不同 / 断线太久历史已滑出窗口 | `init`，发整块白板 |

`epoch` 在白板被载入内存时生成。服务端重启后 `seq` 从 0 重新开始，光比序号会误判成
「没有新内容」，`epoch` 就是用来识别这种情况的——它一变，客户端就会拿到完整快照，
而不是守着过期内容。

白板元数据只有 `id / name / kind / background / created / updated`，文档板多一个 `doc`
（原件类型与每页尺寸，见 [format.md](format.md)）。`kind` 是延伸方式（`board` 四向无限 /
`note` 宽度固定只向下延伸 / `doc` 文档板），建板时定下，之后不能改——`meta` 操作只接受
`background` 和 `name`。老文件里残留的 `cols / rows / unit` 读取时直接丢掉。

## HTTP 接口

WebSocket 之外还有几条普通的 HTTP 路由，文档板（beta）用的是后三条：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/info` | 主机名、端口、候选地址、当前在线客户端 |
| GET | `/api/boards` | 白板列表 |
| GET / POST | `/api/thumb/{board}` | 缩略图；文档板没有上传过时直接返回原件首页 |
| POST | `/api/debug` | 客户端上报的卡顿 / 报错，打到服务端日志 |
| POST | `/api/doc?name=<文件名>` | 上传 PDF / 图片新建文档板，请求体就是文件本身 |
| GET | `/api/doc/{board}/{页码}?w=<像素宽>` | 渲染好的页面位图（JPEG / PNG） |
| GET | `/api/export/{board}` | 笔迹合进原件后的 PDF / 图片 |

- 上传按块读完请求体（`StreamReader.read(n)` 只保证「至多 n 字节」），上限 256 MB。
- 页面位图带 ETag，`Cache-Control: immutable`：原件不会变，客户端可以一直缓存。
- 渲染串在一把锁后面：pdfium 不是线程安全的，两个线程同时渲染会直接把进程带走。
- 新建文档板之后服务端主动广播一条 `switch`，iPad 不用自己轮询。

## mDNS

`.local` 主机名由 macOS 自带的 mDNSResponder 发布，程序本身不参与。`--mdns`
额外注册的 `_http._tcp` 服务只是方便服务发现，因此：

- 必须用 zeroconf 的**异步** API。同步 API 会阻塞调用方所在的事件循环，在 asyncio
  里调用会抛 `EventLoopBlocked`，并把服务端启动拖到超时。
- 注册放在 HTTP 服务就绪**之后**的后台任务里，并且带 5 秒超时，任何异常只记日志。

## 设备识别

- 服务端按 User-Agent 在首页里写入 `data-role`，iPad / iPhone 判为 `ipad`。
- 客户端再用 `navigator.maxTouchPoints` 校正一次（iPadOS 的 Safari 默认报 Mac 的 UA）。
- URL 上的 `?role=mac` / `?role=ipad` 优先级最高，pywebview 窗口用的就是它。
- 白板列表、背景、存储目录这些只在 `mac` 角色下出现；服务端对
  `sel` / `newboard` / `delboard` / `meta` 也只接受来自 `mac` 的请求。

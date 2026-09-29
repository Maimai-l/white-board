[English](format.md) | 简体中文

# 白板文件格式（.wbz）

`.wbz` 文件保存一块白板，内容是经 zlib 压缩的 UTF-8 JSON，每条笔画的点列另外编码为紧凑的二进制格式。

白板内容始终是矢量数据。缩略图和导出的图片是单独的文件，不会作为内容读回。

## 存储目录

存储目录保存全部白板文件、白板索引、文档板原件和备份。

```
<存储目录>/
├── index.json                 白板列表、文件夹名单、当前白板（未压缩）
├── boards/<白板 id>.wbz        每块白板一个文件
├── thumbs/<白板 id>.png        白板选择界面的缩略图（不属于内容）
├── docs/<白板 id>.<扩展名>     文档板的原件（PDF 或图片），程序从不修改
├── backups/upgrade/<...>/     版本变更后复制的 boards/ 与 index.json
├── backups/locked/<id>-<时间>.wbz   解除只读之前复制的只读白板文件
└── recordings/<时间>[-<名称>].json  输入录制文件，见 recording.md
```

| 平台 | 默认存储目录 |
| --- | --- |
| macOS | `~/Library/Application Support/Whiteboard/boards-data` |
| Windows | `%APPDATA%\Whiteboard\boards-data` |
| Linux | `$XDG_DATA_HOME/whiteboard/boards-data`（默认 `~/.local/share/whiteboard/boards-data`） |

存储目录可以在 Mac 的「白板设置」中修改，也可以用 `run.py --data-dir` 仅对本次运行指定。

`index.json` 和 `.wbz` 文件的写入均为原子操作：数据先写入同一目录下的临时文件，经 `fsync` 落盘后再替换目标文件。

## 白板索引（index.json）

`index.json` 保存白板元数据列表，白板选择界面据此显示，无需解压每个 `.wbz` 文件。

```json
{
  "boards": [
    {"id": "49b773c7c7c2", "name": "", "kind": "board", "background": "grid",
     "folder": "数学", "created": 1758000000.0, "updated": 1758000123.4}
  ],
  "folders": ["数学"],
  "current": "49b773c7c7c2"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `boards` | `meta` 数组 | 白板元数据，结构与 [`meta`](#meta-字段) 相同。数组顺序即显示顺序。 |
| `folders` | 字符串数组 | 文件夹名单，包括空文件夹。 |
| `current` | 字符串 | 当前打开的白板 id。 |

索引缺失、无法解析，或索引中的白板 id 集合与 `boards/` 中的文件不一致时，程序根据 `.wbz` 文件重建索引。

| 数据 | 重建后的结果 |
| --- | --- |
| 白板元数据 | 从各 `.wbz` 文件读取，文件名即白板 id。 |
| 无法读取的白板文件 | 保留在列表中，使用默认元数据，`updated` 为 0。 |
| 白板顺序 | 按 `updated` 从新到旧排列，手动排列的顺序丢失。 |
| 含有白板的文件夹 | 从各白板的 `meta.folder` 恢复。 |
| 空文件夹 | 丢失。 |

给白板改名或归入文件夹时，程序同时写入索引和 `.wbz` 文件中的 `meta`，因为重建索引以文件为依据。

### 白板顺序

`boards` 数组的顺序就是白板选择界面上的顺序。拖动卡片会改写这个数组；顺序只保存在索引中。

### 文件夹

文件夹只有一层，不能嵌套。文件夹仅以名字区分，没有单独的文件夹 id。

- 白板在 `meta.folder` 中记录所属文件夹的名字。未归入文件夹的白板没有 `folder` 字段。
- `index.json` 在 `folders` 中另存文件夹名单，因为空文件夹没有白板可以记录它。
- 某个名字出现在白板的 `meta.folder` 中但不在 `folders` 中时，载入索引时会补入 `folders`（`BoardStore._sync_folders`）。
- 文件夹改名时，其中每块白板的 `meta.folder` 都会随之改写。
- 删除文件夹只从 `folders` 中移除该名字，其中的白板移出文件夹，不删除任何白板。
- 文件夹名会去除首尾空白，最长 64 个字符。

## 文件结构

`.wbz` 文件的内容是对下列对象执行 `zlib.compress(json_utf8, 6)` 的结果。

```jsonc
{
  "v": 1,
  "meta": {
    "id": "49b773c7c7c2",
    "name": "",
    "kind": "board",
    "background": "grid",
    "folder": "数学",
    "created": 1758000000.0,
    "updated": 1758000123.4
  },
  "strokes": [
    {
      "id": "c3f1a2-7k-1f",
      "tool": "pen",
      "color": "#1b1b1f",
      "w": 3.0,
      "n": 42,
      "dev": "ipad",
      "cut": 2,
      "m": [[5, 120, 40, 180, 40]],
      "p": "A6ABwAKABAiZ1QG2ev8="
    }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `v` | 整数 | 文件格式版本号，见[版本与读不全的文件](#版本与读不全的文件)。 |
| `meta` | 对象 | 白板元数据，见 [meta 字段](#meta-字段)。 |
| `strokes` | 数组 | 笔画，见[笔画字段](#笔画字段)。 |

### meta 字段

`meta` 描述白板本身；服务端每次读写时都用 `models.sanitize_meta` 将其规整到合法范围。

| 字段 | 类型 | 取值与限制 |
| --- | --- | --- |
| `id` | 字符串 | `^[A-Za-z0-9_.:-]{1,64}$`。新建的白板使用 12 位十六进制字符。文件名为 `<id>.wbz`。 |
| `name` | 字符串 | 去除首尾空白，最长 64 个字符。空串表示未命名，界面按 `kind` 显示默认名称。 |
| `kind` | 字符串 | `board`（大白板，向四个方向延伸）、`note`（笔记，宽度固定，向下延伸）、`doc`（文档板）。创建时确定，之后不能修改。非法值按 `board` 处理。 |
| `background` | 字符串 | `blank`、`grid`、`lines`、`dots`。非法值按 `grid` 处理。 |
| `folder` | 字符串 | 可选。所属文件夹的名字，最长 64 个字符。未归入文件夹时省略。 |
| `created` | 数字 | 创建时间，Unix 秒。 |
| `updated` | 数字 | 最近一次被接受的操作的时间，Unix 秒。改名和归入文件夹不改变它。 |
| `doc` | 对象 | 仅当 `kind` 为 `doc` 时存在，见[文档板](#文档板)。 |

旧文件中已废弃的 `cols`、`rows`、`unit` 字段在读取时丢弃。`kind` 为 `doc` 但没有合法 `doc` 对象的白板按 `kind` 为 `board` 读取。

### 笔画字段

笔画是钢笔、马克笔或荧光笔的一条连续线条；内存、WebSocket 和磁盘使用相同的字段名。

| 字段 | 类型 | 取值与限制 |
| --- | --- | --- |
| `id` | 字符串 | 由客户端生成，全局唯一。`^[A-Za-z0-9_.:-]{1,64}$`。 |
| `tool` | 字符串 | `pen`、`marker`、`highlighter`。非法值按 `pen` 处理。 |
| `color` | 字符串 | `#rrggbb`。非法值按 `#1b1b1f` 处理。 |
| `w` | 数字 | 线宽（世界单位），限制在 0.5～96 之间，默认 3。 |
| `n` | 整数 | 层叠序号，0 ≤ `n` < 2^40。按 `n` 升序绘制。由服务端分配。文件中缺少此字段时，使用该笔画在数组中的下标。 |
| `dev` | 字符串 | 书写该笔画的设备，最长 16 个字符。 |
| `cut` | 整数 | 可选。切口端，取值 1～3，为 0 时省略。见[切口端（cut）](#切口端cut)。 |
| `m` | 数组 | 可选。橡皮擦遮罩，为空时省略。见[遮罩（m）](#遮罩m)。 |
| `p` | 字符串 | 点列。文件中为[点列编码](#点列编码p)的 base64；内存和 WebSocket 中为扁平数组 `[x, y, 压感, ...]`。 |

| `p` 的限制 | 值 |
| --- | --- |
| 每个点的数值个数 | 3（`x`、`y`、压感） |
| 最少点数 | 1 |
| 最多点数 | 20000（`models.MAX_POINTS_PER_STROKE`） |
| 允许的数值 | 仅限有限数（不允许 NaN、无穷大和布尔值） |

违反 `p` 限制的笔画整条丢弃，不做截断。

## 笔画轮廓

每条笔画渲染为一个填充的闭合轮廓，轮廓由 perfect-freehand 根据笔画的采样点、线宽和压感生成。

| 用途 | 实现 |
| --- | --- |
| 屏幕 | `whiteboard/web/static/js/vendor/perfect-freehand.js`（perfect-freehand 1.2.3，未经修改的 ESM 构建），由 `stroke.js` 调用 |
| 导出 | `whiteboard/freehand.py`，Python 移植版本，供服务端的 `/api/export/{board_id}` 使用 |

两份实现必须生成完全相同的轮廓点。`tests/test_browser.py::test_python_outline_matches_perfect_freehand` 使用真机录制的笔画逐点比对，容差为 1e-9。

> **警告**
> 修改仓库中的 perfect-freehand 文件或 `freehand.py` 之后，须运行 `test_python_outline_matches_perfect_freehand`。

### 工具属性

| 工具 | 不透明度 | 线宽倍数 | 线宽随压感变化 |
| --- | --- | --- | --- |
| `pen` | 1 | 1 | 是 |
| `marker` | 1 | 2.6 | 否 |
| `highlighter` | 0.3 | 6 | 否 |

线宽倍数在落笔时计入，保存的 `w` 已包含该倍数。

### 压感与粗细

每个采样点的半径由本项目自己的压感曲线计算，不使用 perfect-freehand 的 `thinning`。

```text
half   = max(0.3, w / 2)
force  = p · (1 + KNEE) / (p + KNEE)                        p 限制在 0～1
radius = half · (FLOOR + (1 − FLOOR) · force^GAMMA)          钢笔
radius = half                                                马克笔、荧光笔
```

| 常数 | 值 | 定义位置 |
| --- | --- | --- |
| `PEN_KNEE` | 0.05 | `stroke.js`、`inkpdf.py` |
| `PEN_FLOOR` | 0.2 | `stroke.js`、`inkpdf.py` |
| `PEN_GAMMA` | 1.2 | `stroke.js`、`inkpdf.py` |

该曲线按 iPad Safari 的实际读数设计：iPad Safari 报告的压感大致在 0.003～0.13 之间，而不是完整的 0～1。鼠标和手指没有压感读数，输入层将移动速度换算为压感值（`input.js` 的 `pressureFor`，`stroke.js` 的 `pressureForFactor`）。

### 轮廓参数

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `size` | `max(w, 0.6)` | 线宽 |
| 传入的压感 | `radius / w` | `thinning` 为 1 时，perfect-freehand 的半径公式化简为 `size × 压感`，结果等于 `radius`。 |
| `thinning` | 1 | 粗细完全由上文的压感曲线决定。 |
| `smoothing` | 0.5 | 轮廓点之间的最小间距为 `(size × smoothing)²`。 |
| `streamline` | 0.5 | 对输入位置的平滑。 |
| `simulatePressure` | false | 速度已换算进压感值。 |
| `start.cap` / `end.cap` | 由 `cut` 决定 | 切口端不画圆头，见[切口端（cut）](#切口端cut)。 |
| 起笔阈值（`START_NOISE`） | 1 个世界单位 | 起笔处丢弃的长度。 |
| 坐标放大倍数（`INK_SCALE`） | 10 | 调用前坐标与长度乘以 10，调用后除以 10。 |

程序分别调用 `getStrokePoints` 和 `getStrokeOutlinePoints`，不使用 `getStroke`，使起笔阈值（1 个世界单位）与线宽无关。

`INK_SCALE` 将 perfect-freehand 中固定的 `END_NOISE_THRESHOLD = 3` 的作用范围缩小到 0.3 个世界单位，笔画末端因此保留轮廓点，不会在末端圆头之前变细。

> **注意**
> `START_NOISE`、`INK_SCALE`、`OUTLINE_SMOOTHING`、`OUTLINE_STREAMLINE` 在 `stroke.js` 和 `inkpdf.py` 中必须取相同的值。

### 输入过滤

采样点进入轮廓计算之前，输入层会去掉可能使笔画末端变形的采样点。

| 规则 | 值 | 代码 |
| --- | --- | --- |
| 采样点之间的最小间距 | 1.2 个屏幕像素，**并且**不小于笔半宽的 12 % | `input.js` 的 `MIN_STEP_PX`、`MIN_STEP_RATIO` |
| 抬笔时去掉的末尾采样点 | 与最后一个点的距离小于笔半宽 30 % 的采样点 | `input.js` 的 `TAIL_SETTLE`、`trimSettledTail` |

### 路径构造

轮廓点连成一条闭合路径：每段是二次贝塞尔曲线，经过相邻两点的中点，以轮廓点为控制点。

该路径即 perfect-freehand README 中的 `Q` + `T` 路径；第 *i* 段的控制点是第 *i* 个轮廓点，因此 `stroke.js` 直接调用 `quadraticCurveTo`（`quadPath`）。

PDF 不支持二次曲线。`inkpdf.py` 将每段精确转换为三次曲线：

```text
C1 = P0 + 2/3 · (Q − P0)
C2 = P2 + 2/3 · (Q − P2)
```

每条笔画按 nonzero 规则一次填充。一次填充使半透明荧光笔在自身重叠处保持均匀的不透明度。

## 切口端（cut）

`cut` 标出笔画的哪一端是切出来的，这一端画成平口，不画圆头。

| `cut` | 起点 | 终点 |
| --- | --- | --- |
| 缺省 / 0 | 圆头 | 圆头 |
| 1 | 平口 | 圆头 |
| 2 | 圆头 | 平口 |
| 3 | 平口 | 平口 |

在切口处画圆头会重新覆盖一部分已擦除的区域。

| 渲染方 | 是否处理 `cut` |
| --- | --- |
| 屏幕（`stroke.js` 的 `strokeOutline`） | 是 |
| PDF 导出（`inkpdf.py` 的 `outline_path`） | 是 |
| 图片导出（`docs.py` 的 `export_image`） | 否，始终为圆头 |

由 `splitLongStroke` 切分的笔画不设置 `cut`（见 [protocol.zh-CN.md](protocol.zh-CN.md#操作规则)）。

## 遮罩（m）

遮罩记录像素橡皮擦在笔画上扫过的胶囊形区域；渲染时从轮廓中裁去这些区域，笔画的采样点保持不变。

### 遮罩格式

```jsonc
"m": [
  [r, x0, y0, x1, y1, ...],   // 一条链：半径，随后是两个或更多点
  ...
]
```

| 元素 | 说明 |
| --- | --- |
| 链 | 一次擦除拖动：由半径为 `r` 的胶囊连成的折线。 |
| `r` | 胶囊半径（世界单位），大于 0。 |
| `x0, y0, x1, y1, ...` | 折线上的点，至少 2 个。 |
| 一条链的段数 | `max(1, (len − 3) / 2)` |

校验规则（`models.sanitize_mask`）：

- 链必须是长度为奇数且不小于 5 的数组，并且 `r` 大于 0。
- 含有非数值元素的链整条丢弃。
- 超过段数上限的链被丢弃，跨越上限的那条链被截断。

### 记录擦除

像素橡皮擦只向遮罩中添加内容，不直接切分笔画（`stroke.js` 的 `eraseKind` 只返回 `"bite"` 或 `null`）。

- 同一次拖动中，新一段的半径与链的半径相差不超过 1 %，并且起点等于链的最后一个点时，新一段接入该链（`addMask`）。
- 完全落在新胶囊内的旧链被删除。
- 遮罩覆盖笔画的全部宽度时，笔画显示为断开，但数据中仍然是一条笔画，只有一个 id。

### 遮罩渲染

渲染时不从墨迹中减去胶囊，因为对互相重叠的胶囊做 even-odd 相减时，重叠部分会互相抵消。

| 用途 | 方法 |
| --- | --- |
| 屏幕 | 在所有胶囊的并集内重新绘制背景。 |
| PDF | 将胶囊分成若干组，组内互不重叠，每组依次执行一次 `W* n` 裁剪。擦除后的缺口仍为矢量。 |

详见 [eraser.md](eraser.md)。

### 遮罩段数上限

每条笔画的胶囊段数有上限，因为裁剪的开销随段数增长。

| 上限 | 值 | 位置 | 超出时的处理 |
| --- | --- | --- | --- |
| `MASK_LIMIT` | 400 段 | `stroke.js` | 先抽稀遮罩（RDP，容差 `r / 6`）；仍然超出时将遮罩转换为切分（`app-eraser.js` 的 `bakeMask`）。 |
| `MAX_MASK_SEGMENTS` | 1024 段 | `models.py` | 服务端丢弃或截断超出上限的链。 |

实测一条笔画的整屏重绘时间：400 段为 2.9 ms，1000 段为 15 ms，2000 段为 57 ms。

`bakeMask` 沿每个胶囊切分笔画，用切分后剩余的各段替换原笔画，清空遮罩，并发送一条 `remove` 和一条 `restore`。各段沿用原笔画的 `n`。切分无法表示只擦去一部分边缘的情形，因此这一步会同时清除贴近边缘的细条。

> **警告**
> `MAX_MASK_SEGMENTS` 必须不小于 `MASK_LIMIT`，两者都按每条笔画的总段数计算。服务端截断了客户端认为合法的遮罩时，已擦除的墨迹会在其他设备上重新出现。`tests/test_models.py` 检查这一条件。

### 遮罩同步

`mask` 操作发送每条笔画当前的完整遮罩，而不是增量。完整遮罩在断线重连和消息乱序到达时仍然正确。见 [protocol.zh-CN.md](protocol.zh-CN.md#操作)。

## 点列编码（p）

磁盘上的扁平点列 `[x, y, 压感, ...]` 编码为字节串，以 base64 字符串保存。

| 步骤 | 规则 |
| --- | --- |
| 1. 坐标量化 | `round(值 × 8)`（1/8 个世界单位，`QUANT = 8`） |
| 2. 增量 | 每个点保存 `x − 前一个 x` 和 `y − 前一个 y`。第一个点的前一个值为 (0, 0)。 |
| 3. zigzag varint | 每个增量先做 zigzag 映射 `(v << 1) ^ (v >> 63)`，再写成无符号 LEB128 varint（每字节 7 位，最高位为续位标志）。 |
| 4. 压感 | 限制在 0～1 之间，存为 1 字节 `round(p × 255)`。 |
| 5. base64 | 字节串经 base64 编码后写入 JSON 的 `p` 字段。 |
| 6. zlib | 完整的 JSON 文档经 zlib 压缩（级别 6）。 |

字节布局：

```text
uvarint  count
重复 count 次：
  svarint  dx      （量化后）
  svarint  dy      （量化后）
  uint8    pressure
```

解码按相反顺序进行：`x = Σdx / 8`，`y = Σdy / 8`，`压感 = 字节 / 255`。varint 超过 63 位、读取越界或缺少压感字节时报错。

一条 500 个点的笔画通常小于 2 KB。代码位于 `whiteboard/codec.py`；`encode_points` / `decode_points`（以及对应的 `_b64` 函数）互为逆运算，有往返测试覆盖。

## 版本与读不全的文件

`v` 是文件格式版本号，当前为 1（`store.FILE_VERSION`）。只有文件格式变化时才修改它，程序版本变化不修改它。没有 `v` 的文件按版本 1 读取。

`store.open_board` 在以下情况下报告白板未能完整读出：

| `reason` | 条件 | 附加字段 |
| --- | --- | --- |
| `unreadable` | 文件无法打开（权限不足、外置磁盘未挂载等），可能是暂时的。 | `detail`：错误信息 |
| `corrupt` | 文件可以打开，但解压或 JSON 解析失败，或顶层不是对象。一条笔画也读不出。 | `detail`：错误信息 |
| `partial` | 文件大部分可以读出，但有笔画无法解码或不合法。 | `dropped`：笔画条数 |
| `newer` | `v` 大于 `FILE_VERSION` 或不是整数，文件由更新的版本写入。 | `version`：`v` 的值 |

属于 `newer` 时不再报告 `partial`。`.wbz` 文件不存在不算错误，白板以空内容打开。

### 只读白板

未能完整读出的白板以只读白板的方式打开。

- 可以读出的内容照常显示。
- 服务端拒绝对它的所有操作，包括 `meta`（见 [protocol.zh-CN.md](protocol.zh-CN.md#只读白板)）。
- 自动保存不写入该文件，因此读出的部分内容不会覆盖原文件。
- 改名和归入文件夹只改写文件中的 `meta`，文件的其余内容（包括本版本不认识的字段）原样保留。文件无法读取时，修改失败。
- 再次选择这块白板时，程序重新读取文件。
- 索引中存在、但文件无法读取的白板仍然显示在列表中，打开时为只读。

### 解除只读

拥有「管理白板」权限的设备可以选择继续编辑只读白板。

1. 服务端将文件复制到 `backups/locked/<id>-<YYYYmmdd-HHMMSS>.wbz`（同名文件已存在时依次追加 `-1`、`-2`……）。
2. 复制失败时，白板保持只读。
3. 复制成功后，白板变为可编辑，并照常保存。下一次保存时，未能读出的笔画和本版本不认识的字段将丢失。

## 备份

程序在存储目录中写入两类备份。

| 目录 | 时机 | 内容 | 保留数量 |
| --- | --- | --- | --- |
| `backups/upgrade/<时间>.<纳秒>_<旧版本>_to_<新版本>/` | 版本变更（升级或降级）后第一次启动，在打开任何白板之前 | `boards/` 和 `index.json` | 最近 5 份 |
| `backups/locked/` | 解除只读白板之前 | 原 `.wbz` 文件 | 不清理 |

- `docs/` 不备份：程序从不修改原件。
- `thumbs/` 不备份：缩略图会重新生成。
- `boards/` 为空时不做版本变更备份。
- 版本变更备份先写入 `.partial-<名称>`，完成后再改为正式名称。
- 版本变更备份失败时，程序照常启动，下次启动时重新尝试备份。

## 读取文件

使用 Python 标准库和 `whiteboard.codec` 即可读取 `.wbz` 文件。

```python
import json, zlib
from whiteboard.codec import decode_points_b64

payload = json.loads(zlib.decompress(open("boards/xxx.wbz", "rb").read()))
for stroke in payload["strokes"]:
    points = decode_points_b64(stroke["p"])   # [x, y, 压感, ...]
    print(stroke["tool"], stroke["color"], len(points) // 3, "个点")
```

普通白板的 PNG 导出在浏览器中完成（界面上的导出按钮）。`.wbz` 文件只包含矢量数据。

## 文档板

文档板（`kind` 为 `doc`，beta）由 PDF 或图片生成；笔迹保存在 `.wbz` 文件中，原件原样保存在 `docs/` 中。

### meta.doc 字段

```jsonc
"doc": {
  "type": "pdf",
  "name": "讲义.pdf",
  "ext": ".pdf",
  "pages": [[595.28, 841.89], [595.28, 841.89]]
}
```

| 字段 | 类型 | 取值与限制 |
| --- | --- | --- |
| `type` | 字符串 | `pdf` 或 `image` |
| `name` | 字符串 | 原始文件名，最长 128 个字符，仅用于生成导出文件名。 |
| `ext` | 字符串 | 原始扩展名，小写，`^\.[a-z0-9]{1,7}$`。 |
| `pages` | 数组 | 每页的 `[宽, 高]`，保留两位小数，每个值满足 0 < v < 10^6，最多 400 页。PDF 以 pt 为单位，图片以像素为单位。尺寸已计入 `/Rotate`。 |

| 支持的原件 | 扩展名 |
| --- | --- |
| PDF | `.pdf` |
| 图片 | `.png`、`.jpg`、`.jpeg`、`.gif`、`.bmp`、`.webp`、`.tif`、`.tiff` |

新建的文档板以去掉扩展名的文件名作为 `name`，`background` 为 `blank`，原件保存为 `docs/<白板 id><扩展名>`。

### 页面排列

页面在世界坐标中自上而下排列，每页相对最宽的一页水平居中，页间距为 24 个世界单位。

| 常数 | 值 | 代码 |
| --- | --- | --- |
| `PAGE_GAP` | 24 | `whiteboard/docs.py`、`whiteboard/web/static/js/boardstate.js` |

> **警告**
> 两处的 `PAGE_GAP` 必须相同，否则导出时笔迹会落到错误的页面上。

### PDF 导出

PDF 导出将笔迹作为新的内容流追加到每一页，原有的内容流对象保持不变。

| 步骤 | 规则 |
| --- | --- |
| 分页 | 按笔画包围盒的中心归页。落在页间空隙中的笔画归入最近的一页。 |
| 抽稀 | Ramer–Douglas–Peucker，容差为 `w × 0.05`，限制在 0.35～1.5 pt 之间。 |
| 轮廓 | 与屏幕相同的轮廓，处理 `cut` 和遮罩，每条笔画一次填充。 |
| 坐标 | 取整到 1/4 pt 的整数，再用 `cm` 缩放回原尺寸。 |
| 不透明度 | `/ExtGState` 中的 `/WBa<百分比>` 条目，设置 `ca` 和 `CA`。 |
| 页面内容 | `/Contents` 改为数组：`q`、原有内容流、`Q`、笔迹内容流。 |
| 压缩 | 笔迹内容流使用 Flate 压缩（zlib 级别 9）。 |
| 重写 | 每次导出都用 `PdfWriter(clone_from=...)` 从原件生成新文件，只保留仍被引用的对象。 |

实测每条笔画 300～450 字节。同一块白板多次导出的文件大小相同，因为原件从不修改。代码位于 `whiteboard/inkpdf.py`、`whiteboard/docs.py`。

`inkpdf.page_matrix` 针对每个 `/Rotate` 取值（0、90、180、270）给出从显示坐标（左上角为原点、y 向下）到 PDF 用户坐标的 `cm` 矩阵，并计入 CropBox 的偏移；`page_geometry` 读取继承的 `/Rotate` 和页面框。

### 图片导出

图片导出将笔迹按原分辨率绘制到原图上。

| 规则 | 值 |
| --- | --- |
| 抽稀 | 与 PDF 导出相同。 |
| 栅格化 | 每条笔画只在自身包围盒内以 3 倍超采样（`SUPERSAMPLE`）绘制，再缩小到原尺寸。超采样后的区域超过 64,000,000 像素时，该笔画不做超采样。 |
| JPEG 输出 | 沿用原图的量化表和色度采样方式；原图没有量化表时，质量为 90。 |
| 其他格式 | `.png` 仍输出 PNG；`.gif`、`.bmp`、`.webp`、`.tif`、`.tiff` 输出为 PNG。 |
| `cut` 和遮罩 | 与 PDF 导出一致：`cut` 标记的端点为平口；每条笔画只减去自身遮罩中的胶囊。 |

导出文件名为 `<去掉扩展名的原文件名>-批注<扩展名>`。

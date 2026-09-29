[English](recording.md) | 简体中文

# 输入录制

输入录制在 iPad 上记录 Apple Pencil 的原始输入和白板状态，并在开发机上回放。它用于只有真实的笔才会触发的问题：压感沿笔画变化、倾角持续变化、同一位置被投递两次，以及抬笔时刻的时序。

## 录制输入

诊断面板打开时即可录制。

1. 连续点击左上角的连接状态圆点三次，每两次之间不超过 1.2 s。诊断面板随即打开，左下角出现「录制输入」一格。
2. 点击「录制输入」。该格显示已录制的事件数（「N 条」）。
3. 执行出现问题的操作。
4. 点击「停止录制」。该格显示「已存到 <路径>」或「已下载」。

在桌面浏览器中，也可以在地址后加 `?debug=1` 打开诊断面板。

> **注意**
> 录制过程中关闭诊断面板，会先停止录制并保存录像。

> **注意**
> 诊断面板第一行显示当前运行的版本：打包后的应用显示版本号，源码运行时显示 `分支@短 commit`。截图和问题报告中应包含这一行。

入口设在屏幕上，是因为 iPad 通过配置描述文件安装的 Web Clip 打开白板，没有地址栏，无法修改查询参数。

## 保存位置

录像发送到 Mac，保存在存储目录中。上传失败时（例如页面从其他来源打开），改由浏览器下载文件。

| 目标 | 路径 |
| --- | --- |
| 服务端 | `<存储目录>/recordings/<YYYYMMDD-HHMMSS>[-<名称>].json` |
| 浏览器下载（备用） | `recording-<自纪元起的毫秒数>.json` |

时间戳为 Mac 的本地时间。`<名称>` 取自录像的 `name` 字段，只保留字母、数字、`-` 和 `_`，最多 40 个字符；从面板开始的录制没有名称。

### `POST /api/recording`

该接口把一份录像保存为文件。

| 项目 | 数值 |
| --- | --- |
| 请求体 | 包含 `events` 数组的 JSON 对象 |
| 大小上限 | 32 MiB（`MAX_RECORDING_BYTES`） |
| 权限 | 不需要 |
| 响应 | `{"ok": true, "path": "<文件路径>", "events": <事件数>}` |
| 错误 | 请求体不是包含 `events` 数组的 JSON 对象时返回 `400`；超过大小上限时返回 `413` |
| 服务端日志 | `[录制] <路径>，<N> 条事件` |

请求体原样写入磁盘。

## 回放录像

`whiteboard.recorder.replay(data, options)` 把白板恢复到录制开始时的状态，再把录下的事件依次交给输入控制器。

```js
await whiteboard.recorder.replay(data)                   // original timing
await whiteboard.recorder.replay(data, { wait: false })  // no delays
await whiteboard.recorder.replay(data, { speed: 4 })     // four times faster
```

| 选项 | 默认值 | 作用 |
| --- | --- | --- |
| `wait` | `true` | 按各事件的 `t` 值在事件之间等待。 |
| `speed` | `1` | 等待时间除以该值。`wait` 为 `false` 时不起作用。 |

回放依次执行以下步骤：

1. 把白板重置为 `meta` 和 `before`，并把视口设为 `viewport`。
2. 按 `fingerDraw` 设置手指书写。
3. `source` 为 `shell` 时把输入来源切换到 iPad 外壳，否则切换到浏览器。
4. 把新笔画 id 的前缀和计数器设为 `ids`，使新笔画获得与录制时相同的 id。
5. 逐条输入事件：若带有 `tool` 和 `viewport` 则先应用，重新读取画布位置，然后调用输入控制器（`onDown`、`onMove`、`onUp`，外壳批次调用 `receiveShell`）。
6. 恢复原来的输入来源和 id 前缀。id 计数器不回退。

返回值是白板上的全部笔画。将其与 `after` 比较，即可确认回放是否复现了录制结果。

事件以普通对象而不是合成的 `PointerEvent` 传入，因为 `PointerEvent` 构造函数不接受 `altitudeAngle` 和 `azimuthAngle`，也无法提供 `getCoalescedEvents()`。

### 在开发机上回放

以下 Playwright 代码在已打开白板的页面中回放一份已保存的录像。

```python
import json

data = json.load(open("recordings/20260924-203011.json"))
result = page.evaluate(
    "async ([d]) => whiteboard.recorder.replay(d, { wait: false })", [data]
)
assert result == data["after"]
```

使用回放的测试：

| 测试 | 检查内容 |
| --- | --- |
| `tests/test_browser.py::test_recorder_replays_an_erase_exactly` | 录下的一次像素擦除回放后与 `after` 完全一致。 |
| `tests/test_browser.py::test_recorder_panel_rides_the_diagnostics_toggle` | 录制一格随诊断面板一起出现。 |
| `tests/test_shell_input.py::test_shell_recording_replays_exactly` | 在 iPad 外壳中录制的录像回放后与 `after` 完全一致。 |

### 回放的局限

回放复现从输入到笔画和遮罩的过程，不复现网络。

> **警告**
> 回放把 `before` 直接写入本地状态，服务端并不知道这些笔画。服务端会丢弃回放期间发出的 `mask` 操作，回执也不会返回。由回执引起的问题，例如回传的遮罩覆盖本地较新的状态，在回放中不会出现。

回放结果与 `after` 一致时，问题位于输入到遮罩之间，可以用回放排查。二者不一致时，差异来自回放未模拟的部分，例如网络路径。例如，一份录像在设备上擦出 32 条胶囊链、237 个点，回放得到 7 条链、298 个点；缺少的 61 个点被回执覆盖。见 [eraser.zh-CN.md](eraser.zh-CN.md#同步)。

同一份录像多次回放，除新笔画的随机 id 外，结果逐字节相同。

## 文件格式

录像是一个 JSON 对象。指针事件的字段保存原始值，不做四舍五入，以便回放出的笔画与 `after` 完全一致。

### 顶层字段

| 字段 | 类型 | 内容 |
| --- | --- | --- |
| `schema` | number | 格式版本，当前为 `1` |
| `name` | string | 录像名称；从面板开始时为空 |
| `at` | string | 开始时间，ISO 8601 |
| `agent` | string | `navigator.userAgent` |
| `build` | string | 运行的版本，与诊断面板第一行相同 |
| `role` | string | 客户端角色 |
| `dpr` | number | `devicePixelRatio` |
| `stage` | array | 画布的 `[left, top, width, height]`，单位 CSS px，保留两位小数 |
| `viewport` | object | 开始时的 `{scale, x, y}` |
| `fingerDraw` | boolean | 是否开启手指书写 |
| `source` | string | `browser` 或 `shell` |
| `shell` | object 或 null | `source` 为 `shell` 时为 `{version, bridge}` |
| `ids` | object | 新笔画 id 的 `{prefix, counter}` |
| `before` | array | 开始时白板上的全部笔画 |
| `meta` | object | 开始时的白板元数据 |
| `events` | array | 录下的事件，见下文 |
| `after` | array | 结束时白板上的全部笔画 |
| `viewportAtEnd` | object | 结束时的 `{scale, x, y}` |

### 指针事件

指针事件在白板舞台元素（`#stage`）的捕获阶段记录，早于输入控制器收到事件。记录的类型为 `pointerdown`、`pointermove`、`pointerup` 和 `pointercancel`。

| 字段 | 来源 | 出现条件 |
| --- | --- | --- |
| `type` | 事件类型 | 始终 |
| `t` | 距开始的毫秒数，保留两位小数 | 始终 |
| `id` | `pointerId` | 始终 |
| `pt` | `pointerType` | 始终 |
| `btn` | `button` | 始终 |
| `btns` | `buttons` | 始终 |
| `x`、`y` | `clientX`、`clientY` | 始终 |
| `p` | `pressure` | 始终 |
| `tx`、`ty` | `tiltX`、`tiltY` | 非零时 |
| `alt` | `altitudeAngle`（弧度） | 提供时 |
| `az` | `azimuthAngle`（弧度） | 提供时 |
| `tw` | `twist` | 非零时 |
| `primary` | 非主指针时为 `false` | 非主指针时 |
| `tool` | 当前工具设置 | `pointerdown` 时 |
| `viewport` | `{scale, x, y}` | 视口自上一条事件以来发生变化时 |
| `c` | `getCoalescedEvents()` 返回的采样点，每个含 `x`、`y`、`p`、`tx`、`ty`、`alt`、`az`、`tw` | `pointermove` 且采样点多于一个时 |

视口在每次变化时记录，而不只在落笔时记录，因此平移或缩放之后的笔画回放时落在正确的世界坐标上。

### 外壳批次

在 iPad 外壳中，Apple Pencil 的输入来自外壳而不是指针事件。每一批采样原样记录。

```json
{"type": "shell", "t": 1234.56, "batch": { ... }}
```

| 字段 | 内容 | 出现条件 |
| --- | --- | --- |
| `type` | `"shell"` | 始终 |
| `t` | 距开始的毫秒数，保留两位小数 | 始终 |
| `batch` | [ipad-shell.zh-CN.md](ipad-shell.zh-CN.md) 5.1 节定义的对象，全部字段和数值不变 | 始终 |
| `tool` | 当前工具设置 | 批次中有 `ph` 为 `down` 的采样点时 |
| `viewport` | `{scale, x, y}` | 视口发生变化时 |

此期间 Safari 自身的 pen 事件同样被记录。回放外壳录像时输入来源切换到外壳，这些事件与在设备上一样不参与书写。

## 笔的采样率

诊断面板显示笔的采样率；调整输入处理之前应先查看。

```
帧 60fps  最长 18ms
笔事件 122/s  新位置 64/s  合并 1
```

| 数值 | 含义 |
| --- | --- |
| `帧` | 帧率和最长帧间隔 |
| `笔事件` | 每秒的笔指针事件数 |
| `新位置` | 每秒位置与上一条事件不同的笔事件数 |
| `合并` | 最近一次绘制移动事件中 `getCoalescedEvents()` 返回的采样点数 |

有效采样率是新位置数。其余事件的位置与上一条事件相同，其中大部分的压感和倾角也相同，因此不带有新的信息。

iPad 上的实测结果（八份录像和面板读数）：

| 测量项 | 结果 |
| --- | --- |
| 书写时的笔事件 | 每秒约 120–125 条 |
| 新位置 | 每秒约 64 个，约每帧一个 |
| 各字段均相同的重复事件 | 没有新位置的事件中，80%–100% 的压感和倾角也与上一条相同（最新一份录像为 100%） |
| 白板内容的影响 | 无：1 条笔画与 692 条笔画的采样率相同 |
| 坐标分辨率 | 100% 的 `clientX` / `clientY` 为整数（1 CSS px） |
| `getCoalescedEvents()` | 从未返回多于一个采样点；录像中没有任何事件带有 `c` 字段 |
| `pointerrawupdate` | Safari 不支持 |

- MDN 将 `getCoalescedEvents()` 标为 limited availability；Apple 开发者论坛上的讨论（最后一帖为 2023 年 11 月）指出 Mobile Safari 未实现该接口。
- 书写速度为 1.6 px/ms 时，相邻两个新位置相距 26 个屏幕像素，而笔迹宽度为 13 px。
- 因此平滑参数按 60 Hz 输入调整。`perfect-freehand` 的 `streamline` 选项在生成轮廓时平滑笔的位置，输入层保存笔的位置时不再额外平滑。

以下设置不改变新位置数（约每秒 64 个）：

| 设置 | 结果 |
| --- | --- |
| 实时层画布使用 `{ desynchronized: true }` | 采样率不变；书写时出现闪烁，因为 `drawLive` 每帧清除并重绘实时笔画。未采用。 |
| 设置 → Apple Pencil → 关闭「随手写」 | 不变 |
| 设置 → 应用 → Safari → 高级 → 功能开关 → 关闭「Prefer Page Rendering Updates near 60fps」，强制退出后重新打开 | 不变。白板以 Web Clip 运行，没有资料说明该 Safari 开关对其是否生效。 |
| 关闭低电量模式 | 不变 |

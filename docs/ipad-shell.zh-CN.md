[English](ipad-shell.md) | 简体中文

# iPad 外壳

iPad 外壳是一个可选的 iPad 原生应用。它用 WKWebView 加载 Mac 上的白板页面，并把 UIKit 的 Apple Pencil 采样点转发给该页面。

本文档规定外壳第一阶段的功能、外壳与网页之间的接口、构建与分发流程以及验收标准。第二阶段只列出启动条件和基本要求。

## 1. 目标

在 iPad 上，网页收到的 Apple Pencil 输入与原生应用的输入质量相同。其他设备继续通过浏览器访问，功能不变。

### 1.1 Safari 与 UIKit 的输入质量

在同一台 iPad 上，Safari 提供的 Pencil 采样点比 UIKit 少，精度也更低。

| 项目 | Safari（网页） | 原生（UIKit） |
|---|---|---|
| 每秒采样点数 | 约 120 | 240 至 244 |
| 每秒不重复的新位置数 | 约 60 | 147 至 240 |
| 坐标精度 | 整数 CSS 像素 | 带小数，取自 `preciseLocation(in:)` |
| 时间戳精度 | 整数毫秒 | `UITouch.timestamp`，单位为秒，带小数 |
| 预测采样点 | 无 | `predictedTouches(for:)` |

数据来源：Safari 的数据取自白板录像 `20260927-181756.json`，原生的数据取自 InkProbe 的四份会话（仓库根目录下的 `20260924-*.zip`）。两者取自同一台 iPad。

在同一份 Safari 输入上，Google Ink、ink-stroke-modeler、Xournal++、Krita 与当前实现的平滑算法画出的笔迹，在静态图和回放中都没有可辨别的差别。因此，书写质量受限于输入数据，而不是平滑算法。

### 1.2 原则

外壳只提供输入和平台服务，不包含白板逻辑。笔迹几何、同步、橡皮擦和界面全部保留在网页中。

| 结果 | 说明 |
|---|---|
| Safari 访问保持完整可用 | 通过 Safari 访问时功能完整，只是输入质量较低。 |
| 多数改动不需要重新安装外壳 | 除外壳本身的原生代码外，其余改动都随 Mac 端更新生效。外壳下次启动时加载新的网页代码。 |

## 2. 前提条件

外壳通过 TrollStore 安装，只能在特定范围的 iPadOS 版本上运行。

| 项目 | 要求 |
|---|---|
| 安装方式 | 通过 TrollStore 安装未签名的 IPA，不需要 Apple 开发者账号。 |
| 支持的 iPadOS（TrollStore） | 14.0 beta 2 至 16.6.1、16.7 RC（20H18）和 17.0。16.7.x 的其他版本以及 17.0.1 及以后的版本不受支持。 |
| 目标 iPad | Safari 版本为 15.6，在支持范围之内。 |
| 部署目标 | iPadOS 15.0，与 InkProbe 相同（`ipad/project.yml`）。 |
| 设备类型 | 只支持 iPad（`TARGETED_DEVICE_FAMILY = 2`）。 |

> **警告**
> 目标 iPad 不得升级系统。升级到支持范围之外的版本后，无法再安装新版外壳。

## 3. 总体结构

外壳、网页、Mac 端服务和 CI 各自承担一项职责。

| 部分 | 职责 |
|---|---|
| 外壳（Swift，`ipad/`） | 用 WKWebView 加载 Mac 上的白板页面；采集 Pencil 触摸并转发给网页；关闭干扰书写的系统手势；检查并安装外壳自身的更新。 |
| 网页 | 把外壳转发的采样点作为 Pencil 输入来源；其余行为不变。 |
| Mac 端服务 | 注册 Bonjour 服务；提供安装页、外壳版本信息和 IPA 下载。 |
| GitHub Actions | 构建 IPA，并把它打包进 Mac 应用。 |

手指输入不经过外壳，由网页的 pointer 事件处理。平移、缩放、手掌屏蔽和手指书写开关的现有逻辑不变。

外壳工程用 XcodeGen 生成，做法与 InkProbe 相同：

```sh
python3 ipad/make_icon.py        # 生成图标（仓库中不存放二进制文件）
cd ipad && xcodegen generate
```

## 4. 外壳的功能

### 4.1 连接 Mac 与加载页面

外壳查找 Mac 时不需要手动输入主机名和端口。外壳按以下顺序尝试。

| 步骤 | 方式 | 行为 |
|---|---|---|
| 1 | 已保存的地址 | 本机保存过地址时，外壳直接加载。加载失败时（例如 Mac 的端口因被占用而顺延），转到第 2 步。 |
| 2 | Bonjour 自动发现 | 外壳用 `NWBrowser` 查找 `_whiteboard._tcp` 服务（注册方式见 8.4 节）。找到一台时，等待 1 秒确认没有其他 Mac 后连接；找到多台时，列出各台 Mac 的名称，由用户选择；5 秒内一台都没有找到时，显示第 3 步的操作说明。 |
| 3 | 一键链接 | 外壳注册 URL scheme `whiteboard-shell`。在 Safari 中打开安装页（8.2 节）并点「打开外壳」，即打开 `whiteboard-shell://connect?host=<主机名>.local&port=<端口>`，外壳保存该地址并连接。此步骤用于路由器屏蔽 Bonjour 的网络。 |

页面加载完成后，外壳保存该地址，下次启动时使用。主机名只接受字母、数字、`.` 和 `-`；端口必须在 1 至 65535 之间。

切换到另一台 Mac 有两个入口：

| 入口 | 行为 |
|---|---|
| iOS「设置」App 中外壳页面的「重新查找 Mac」开关 | 外壳下次启动或回到前台时，清除已保存的地址，复位该开关，并开始 Bonjour 查找。 |
| 白板页面「白板设置」中「Mac」一组的「换一台 Mac」按钮 | 网页向外壳发送 `rediscover`（5.2 节）。 |

用户主动要求切换时，即使只找到一台 Mac，外壳也会列出来由用户确认，以免自动连回原来的 Mac。白板页面仍在加载状态时，列表中另有「取消」按钮。

「设置」App 中外壳的页面还显示「版本」和「当前 Mac」（未保存地址时为「未连接」）。

页面的加载方式：

- 页面地址为 `http://<主机名>.local:<端口>/?role=ipad`。`role=ipad` 使服务端返回 iPad 界面（`whiteboard/server.py` 的 `detect_role`）。主机名和端口取自 Bonjour 服务的 TXT 记录（8.4 节）。
- 每次加载都忽略本地缓存，超时时间为 8 秒。
- 加载失败时，外壳显示错误原因以及「重试」「重新查找 Mac」两个按钮。
- 开始新的导航时，外壳停止发送采样点，直到页面重新完成握手（5.2 节）。
- 网页内容进程终止时，外壳重新加载页面。
- scheme 不是 `http`、`https`、`about`、`blob`、`data` 的链接交给系统打开；`window.open` 的地址交给 Safari 打开。
- iPadOS 16.4 及以上版本中，WKWebView 允许调试（`isInspectable = true`）。

`Info.plist` 包含以下内容：

| 键 | 值 | 用途 |
|---|---|---|
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `true` | 允许加载局域网内的 http 地址。 |
| `NSLocalNetworkUsageDescription` | 说明文字 | iPadOS 14 起访问局域网必须提供。 |
| `NSBonjourServices` | `["_whiteboard._tcp"]` | iPadOS 14 起查找 Bonjour 服务必须声明。 |
| `CFBundleURLTypes` | scheme `whiteboard-shell` | 一键链接（第 3 步）。 |
| `LSApplicationQueriesSchemes` | `["apple-magnifier"]` | TrollStore 安装链接（8.3 节）。 |
| `UIRequiresFullScreen`、`UIStatusBarHidden` | `true` | 全屏运行，不显示状态栏。 |

WKWebView 铺满屏幕，页面不滚动、不缩放：

```swift
scrollView.isScrollEnabled = false
scrollView.bounces = false
scrollView.minimumZoomScale = 1
scrollView.maximumZoomScale = 1
scrollView.contentInsetAdjustmentBehavior = .never
```

### 4.1.1 导出文件

外壳把导出请求作为下载处理，并把文件交给系统的分享面板。

WKWebView 默认把带 `download` 属性的链接，以及赋给 `location.href` 的 `data:` 地址或导出接口地址，当作一次导航处理。导航会替换白板页面、断开 WebSocket，文件也不会保存。因此外壳采用以下规则：

| 条件 | 决定 |
|---|---|
| `navigationAction.shouldPerformDownload` 为真 | `.download` |
| 响应头 `Content-Disposition` 包含 `attachment`（文档板导出） | `.download` |
| `navigationResponse.canShowMIMEType` 为假 | `.download` |

`WKDownloadDelegate` 把每个文件保存到单独的临时目录 `export-<UUID>` 中，同名文件不会互相覆盖。建议文件名为空时使用 `whiteboard`。下载完成后，外壳弹出 `UIActivityViewController`，由用户存入「文件」或分享。下载失败时显示「导出失败」提示。

对网页的要求：所有下载都通过带 `download` 属性的链接触发（`exporter.js` 的 `downloadURL`）。导出 PNG 时先把 `data:` 地址转换为 `blob:` 地址再下载。

### 4.2 采集 Pencil 输入

外壳在窗口的 `sendEvent(_:)` 中读取 Pencil 触摸，读取时间早于所有手势识别器。

> **注意**
> 挂在 WKWebView 上的手势识别器收不到完整的笔画：网页调用 `preventDefault` 后，WebKit 内部的识别器会使其失败。见 12 节 Q2。

组成部分（`ipad/Whiteboard/PencilCapture.swift`）：

| 组成部分 | 作用 |
|---|---|
| `ShellWindow`（`UIWindow` 子类） | 重写 `sendEvent(_:)`，在调用 `super` 之前把每个事件交给 `PencilTracker`。 |
| `PencilTracker` | 读取 Pencil 触摸，按 5.1 节的格式编码采样点，每个事件发出一批。 |
| `EstimateRecognizer` | 只接收 `touchesEstimatedPropertiesUpdated`。挂在 WKWebView 的父视图上，不挂在 WKWebView 上。 |

采样点的采集规则：

- 只读取 `type == .pencil` 的触摸。
- 只跟踪起点位于 WKWebView 或其子视图上的触摸，落在状态页或提示框上的触摸不处理。
- 触摸编号由外壳分配，从 1 开始，每次触摸加 1。
- 真实采样点取自 `event.coalescedTouches(for:)`：

  | 触摸阶段 | 生成的 `ph` |
  |---|---|
  | `began` | 第一个采样点为 `down`，其余为 `move` |
  | `moved` | 全部为 `move` |
  | `ended` / `cancelled` | 最后一个采样点为 `up` / `cancel`，其余为 `move` |
  | `stationary` | 不生成采样点 |

- 预测采样点取自 `event.predictedTouches(for:)`。每一批都附带最近一次的预测，只含估计属性更新的批次也不例外；没有正在书写的 Pencil 触摸时，预测为 `null`。
- 坐标一律用 `preciseLocation(in: webView)`，方位角用 `azimuthAngle(in: webView)`。非有限数值按 `0` 发送。
- 每个采样点的字段见 5.1 节。
- 每个触摸事件和每次估计属性更新回调各发送一批，不按固定时间间隔发送。空批次不发送。
- 只在握手成功（5.2 节）后发送。

`EstimateRecognizer` 的设置：

| 设置 | 值 |
|---|---|
| `allowedTouchTypes` | `[.pencil]` |
| `cancelsTouchesInView`、`delaysTouchesBegan`、`delaysTouchesEnded` | `false` |
| `shouldRecognizeSimultaneouslyWith` | 对所有识别器返回 `true` |
| `shouldRequireFailureOf`、`canPrevent`、`canBePrevented` | `false` |
| 状态 | 从不识别；其全部触摸结束或取消时进入失败状态。 |

`EstimateRecognizer` 提前失败时，只影响力度修正，不影响书写。InkProbe 的 `TouchLogger.swift` 实现了相同的读取逻辑。

### 4.3 关闭系统手势对书写的干扰

外壳在网页已有措施的基础上，关闭干扰书写的系统行为。

网页中已有的措施（`touch-action: none`、阻止 `touchstart` 的默认行为等）保留。外壳另外采取以下措施：

| 系统行为 | 措施 |
|---|---|
| Scribble（随手写，iPadOS 14 起在可编辑区域把手写识别为文字） | 在 WKWebView 上添加 `UIScribbleInteraction`，`scribbleInteraction(_:shouldBeginAt:)` 返回 `false`。 |
| 长按菜单与链接预览 | `allowsLinkPreview = false` |
| 数据检测 | `dataDetectorTypes = []` |
| 前进后退滑动手势 | `allowsBackForwardNavigationGestures = false` |
| 屏幕边缘的系统手势 | `preferredScreenEdgesDeferringSystemGestures = .all`：在屏幕边缘需要再滑动一次，系统才会响应。 |
| 状态栏与主屏幕指示条 | 隐藏。 |

这些措施是否足够，按 9.2 节的检查项在真机上确认（12 节 Q5）。

### 4.4 检查和安装外壳更新

见第 8 节。

## 5. 外壳与网页之间的接口

外壳与网页通过 `evaluateJavaScript` 和 WebKit 消息处理器交换采样批次和握手消息。

### 5.1 外壳发给网页的采样批次

外壳用以下调用发送一批采样点：

```js
window.whiteboardShell && window.whiteboardShell.receive(<JSON>)
```

批次结构：

```json
{
  "bridge": 1,
  "samples": [
    {"id": 3, "ph": "move", "k": "real", "t": 5321.0412, "x": 412.53, "y": 688.07,
     "f": 0.3333, "fmax": 4.1667, "alt": 0.9531, "az": 2.2104, "est": ["force"], "ui": 15173}
  ],
  "pred": {"id": 3, "samples": [{"t": 5321.0495, "x": 414.10, "y": 689.52, "f": 0.34, "fmax": 4.1667, "alt": 0.95, "az": 2.21}]},
  "updates": [{"ui": 15170, "f": 0.3712, "alt": 0.9520, "az": 2.2110}]
}
```

| 字段 | 含义 |
|---|---|
| `bridge` | 接口版本号（第 6 节）。 |
| `id` | 触摸编号。同一次落笔到抬笔之间不变，由外壳分配，从 1 开始。 |
| `ph` | `down`、`move`、`up` 或 `cancel`。 |
| `k` | 固定为 `real`，表示真实采样点。预测采样点放在 `pred` 中，不放在 `samples` 中。 |
| `t` | `UITouch.timestamp`，单位为秒。 |
| `x`、`y` | `preciseLocation(in: webView)`，单位为 point。页面缩放为 1 且不滚动时，数值等于网页的 `clientX`、`clientY`。 |
| `f`、`fmax` | `force`、`maximumPossibleForce`。 |
| `alt` | `altitudeAngle`，单位为弧度。0 表示笔贴在屏幕上，π/2 表示笔竖直。 |
| `az` | `azimuthAngle(in: webView)`，单位为弧度。 |
| `est` | `estimatedPropertiesExpectingUpdates` 中的属性名，取值为 `force`、`azimuth`、`altitude`、`location`，可以为空数组。 |
| `ui` | `estimationUpdateIndex`；`est` 为空时为 `null`。 |
| `pred` | 这一批对应的预测采样点，格式为 `{id, samples}`，每个采样点包含 `t`、`x`、`y`、`f`、`fmax`、`alt`、`az`。每一批都整体替换上一批的预测；没有预测时为 `null`。 |
| `updates` | 这一批期间收到的估计属性更新，每项为 `{ui, f, alt, az}`，按 `ui` 对应到之前发送过的采样点。 |

> **注意**
> 0.9.43 版外壳停止发送采样点时，网页自身会按同样的格式生成批次（`shell-fallback.js`，12 节 Q2）。这类批次带有 `fallback: true` 或 `orphan: true`，其中的采样点为 `k: "safari"`。外壳不会发送这些字段，它们会出现在录像中。

### 5.2 网页发给外壳的消息

网页通过 `window.webkit.messageHandlers.whiteboard.postMessage(<对象>)` 发送消息。

| 消息 | 发送时机 | 外壳的处理 |
|---|---|---|
| `{"type": "hello", "bridge": [最低版本, 最高版本]}` | 页面启动时（`shell.js` 的 `connectShell`） | 回复握手结果。 |
| `{"type": "rediscover"}` | 用户在「白板设置」中点「换一台 Mac」 | 重新查找并列出局域网中的 Mac（4.1 节），不回复。 |

外壳对 `hello` 的回复：

```js
window.whiteboardShell && window.whiteboardShell.hello({"shellVersion": "1.3.0", "bridge": 1, "active": true})
```

| `active` | 行为 |
|---|---|
| `true` | 外壳开始发送采样点。 |
| `false` | 外壳不发送采样点，网页继续使用 Safari 的 pointer 事件。 |

只有当 `active` 为 `true` 且回复中的 `bridge` 在网页自身支持的范围内时，网页才切换到外壳输入来源，并把 `document.documentElement.dataset.shell` 设为 `active`（否则设为 `inactive`）。不在外壳中运行时，`window.webkit.messageHandlers.whiteboard` 不存在，网页不做任何处理。

### 5.3 时间

网页只使用同一笔画内采样点之间的时间差，不把 `t` 与 `performance.now()` 直接比较。

依赖当前时间的逻辑（例如抬笔后 500 ms 内屏蔽手掌的 `PALM_GRACE`）使用收到该批采样点时的 `performance.now()`。

## 6. 接口版本协商

外壳与网页在握手时就整数接口版本 `bridge` 达成一致。

| 规则 | 说明 |
|---|---|
| 版本号 | `bridge` 是整数，当前为 `1`（`PencilTracker.bridge`），只在接口格式有不兼容的改动时加 1。 |
| 外壳版本 | 与 Mac 端相同，由同一个 git tag 决定。 |
| 支持范围 | 网页声明 `[最低, 最高]`，即 `input.js` 中的 `SHELL_BRIDGE = [1, 1]`。`whiteboard/ipadshell.py` 中的 `BRIDGE` 必须与之相同（`tests/test_ipadshell.py::test_bridge_range_matches_the_web_page`），`/ipad/version` 返回该范围。 |
| 在范围内 | 外壳回复 `active: true`。 |
| 不在范围内 | 外壳回复 `active: false`，每次运行显示一次提示「外壳版本与白板版本不兼容，请更新外壳」，并执行更新检查（8.3 节）。 |
| 兼容期 | 网页提高最高版本时，保留对上一个版本的支持，直到下一个版本发布。因此 Mac 端已更新而外壳尚未更新时，外壳仍可使用。 |

## 7. 网页端的改动

### 7.1 输入来源

`input.js` 把外壳的采样点转换为与 pointer 事件相同形式的对象，之后与 Safari 的 pointer 事件走同一条处理路径（`onDown`、`onMove`、`onUp`、`addSample`、`startErase` 等）。

转换规则（`input.js` 的 `shellEvent`）：

| 事件属性 | 取值 |
|---|---|
| `clientX`、`clientY` | `x`、`y` |
| `pressure` | `f / fmax`，限制在 0 至 1 之间；`fmax` 不为正数时为 0 |
| `tiltX`、`tiltY` | 0（`penAltitude` 因此读取 `altitudeAngle`） |
| `altitudeAngle` | `alt`；缺失时为 π/2 |
| `azimuthAngle` | `az` |
| `timeStamp` | `t × 1000`（毫秒，保留小数） |
| `pointerId` | `1000000 + id`（与浏览器的 pointerId 区分） |
| `pointerType` | `pen` |
| `fromShell` | `true` |

规则：

- 外壳处于 `active` 状态时，`#stage` 上 Safari 的 `pointerType === "pen"` 事件不参与书写。这些事件仍用于更新手掌屏蔽的计时、作为坐标偏差的对比基准（7.4 节），以及驱动 0.9.43 版外壳的补救逻辑（12 节 Q2）。手指和鼠标的 pointer 事件照常处理。
- `down` 时，网页用 `document.elementFromPoint` 判断落点。落点元素不在 `#stage` 内时，整次落笔都不处理，由界面控件自己的点击事件处理。因此 Pencil 仍可以点击工具栏和笔具盘上的按钮。
- 预测采样点只绘制在实时层上，不写入笔画，也不发送给其他设备。
- 在外壳中，页面按 iPad 处理：显示笔具盘；即使没有「设置白板」权限，也始终显示「白板设置」按钮，因为「换一台 Mac」位于其中。

### 7.2 压感

外壳来源的压力取 `f / fmax`，不做换算。

InkProbe 四份会话中，`force / maximumPossibleForce` 的中位数为 0.017 至 0.084，95 分位数为 0.129 至 0.398。Safari 录像中的压力在 0.007 至 0.125 之间。两者处于同一量级，因此假定 Safari 报告的压力即为 `force / maximumPossibleForce`（12 节 Q1）。

这四份会话都是橡皮擦测试，不是正常书写，因此该假定需要验证：在同一台 iPad 上，用 InkProbe 和白板（Safari）各写同样的内容，比较两边压力的分布。

| 结果 | 处理 |
|---|---|
| 假定成立 | 不做换算。笔画格式和压感曲线（`stroke.js` 的 `PEN_KNEE`、`PEN_FLOOR`、`PEN_GAMMA`）保持不变。 |
| 假定不成立 | 在输入层把 `f / fmax` 换算到 Safari 的量程后再存入笔画。笔画格式保持不变，换算关系由对比结果确定。 |

估计属性更新（第一阶段）：

- 只有对应采样点仍属于正在书写的那一笔时，才应用更新。网页用新的 `f` 和 `alt` 重新计算该点的压感，倾角修正和平滑方法与 `addSample` 相同。
- 笔画提交之后才到达的更新被丢弃，计入诊断面板的「抬笔后丢弃」。
- 待更新表超过 4096 项时清空。

InkProbe 四份会话的统计（只计 Pencil 的 80 笔，共 19139 个等待更新的采样点，全部收到了更新）：

| 会话 | 更新数 | 抬笔之后到达 | 比例 | 更新延迟中位数 / 95 分位数 | 抬笔后更新的力度变化（`Δf / fmax` 中位数） |
|---|---|---|---|---|---|
| test1-calibr | 3862 | 128 | 3.3% | 25.4 / 39.7 ms | −0.029 |
| test2-calibr | 3388 | 203 | 6.0% | 26.0 / 39.6 ms | −0.020 |
| test3-drag | 7191 | 101 | 1.4% | 25.3 / 39.0 ms | −0.090 |
| test4-contrast | 4698 | 43 | 0.9% | 26.1 / 41.3 ms | −0.016 |

合计 475 个更新（2.5%）在抬笔之后到达。每一笔中，这些更新都对应最后约 6 个采样点（约 25 ms），并且都使力度减小。丢弃它们后，笔画末端几个点的压力偏大 0.02 至 0.09（以 `f / fmax` 计）。输入层的 `trimSettledTail` 通常会去掉笔停住之后的末尾几个点，剩余的影响仅为笔画末端略粗。

### 7.3 录像

录像原样保存外壳发送的批次，使回放结果与原笔画完全一致。

| 项目 | 格式 |
|---|---|
| 顶层 `source` | `browser` 或 `shell` |
| 顶层 `shell` | `source` 为 `shell` 时为 `{version, bridge}`，否则为 `null` |
| 外壳批次条目 | `{"type": "shell", "t": ..., "batch": {...}}`；`batch` 即 5.1 节的对象，保留全部字段和原始数值，不做四舍五入 |

同时录下的 Safari pen 事件在回放时不参与书写。回放外壳来源的录像时，每一批都交给输入来源层（`receiveShell`），结果必须与录像中的 `after` 一致。测试：`tests/test_shell_input.py::test_shell_recording_replays_exactly`，做法与 `tests/test_browser.py::test_recorder_replays_an_erase_exactly` 相同。录像格式见 [recording.zh-CN.md](recording.zh-CN.md)。

### 7.4 诊断面板

诊断面板（`perf.js`）显示输入来源；在外壳中另外显示外壳的采样率和各项偏差计数。

| 行 | 内容 | 显示条件 |
|---|---|---|
| `输入来源 <source>  外壳 <版本>  bridge <n>` | 输入来源（`browser` 或 `shell`）、外壳版本和 `bridge` | 始终显示；外壳版本已知时才显示后半部分 |
| `外壳 <n>/s  新位置 <n>/s` | 外壳来源的每秒采样点数和每秒新位置数，计算方法与 `penHz`、`penMoveHz` 相同 | 外壳版本已知或来源为 `shell` |
| `坐标偏差 <d>px（对上 <n> 个）  力度更新 <n>  抬笔后丢弃 <n>` | 坐标偏差及参与比对的采样点数；已应用的估计属性更新数；抬笔后丢弃的更新数 | 同上 |
| `外壳断笔 <n>  Safari 补点 <n>` | 外壳未发送 `up`、由网页结束的笔画数；用 Safari 事件补充的采样点数。正常情况下两者均为 0。 | 同上 |

坐标偏差比较同一笔画中外壳采样点与 Safari pen 事件的坐标；外壳处于 `active` 状态时，Safari 的 pen 事件仍会到达：

- 两边的时间轴在落笔时刻对齐，因为两边的第一个采样点来自同一个 `UITouch`。
- 对每个 Safari 事件，在前后 25 ms（`SHELL_MATCH_MS`）内查找距离最近的外壳采样点。偏差为整笔中 `|Δx|` 或 `|Δy|` 的最大值。
- 每笔最多保留 512 个采样点用于比对（`SHELL_TRACE`）。
- 取整本身最多产生 0.5 px 的偏差；超过此值说明两边的坐标系没有对齐。

用 Safari 事件补充的采样点不计入外壳的采样率。

## 8. 构建、发布与更新

### 8.1 在 CI 中构建 IPA

`.github/workflows/build-macos.yml` 构建 IPA，将其打包进 Mac 应用，并一同发布。

| 任务 | Runner | 步骤 |
|---|---|---|
| `ipad` | `macos-26` | 确定版本号；`brew install xcodegen ldid`；`python3 ipad/make_icon.py`；`xcodegen generate`；`xcodebuild -sdk iphoneos -configuration Release` 不签名编译；`ldid -S` 伪签名；把 `Payload/` 打包为 `Whiteboard-<版本>-ipad.ipa`；上传构建产物 `Whiteboard-ipad`。 |
| `build` | `macos-14`（arm64），依赖 `ipad` | 把 IPA 下载到 `packaging/ipad/Whiteboard.ipa`；PyInstaller（`packaging/whiteboard.spec`）将其放入应用资源目录的 `whiteboard/ipad/Whiteboard.ipa`；用 `packaging/smoke_ipad.py` 做冒烟测试。 |
| `release` | `ubuntu-latest`，依赖 `ipad` 和 `build`，只在 tag 时运行 | 在 GitHub Release 中发布 Mac 的 zip 和 IPA。IPA 同时单独发布，用于首次安装。带 `-` 的 tag 发布为预发布版。 |

版本号：

| 值 | 来源 |
|---|---|
| 版本号 | tag `v1.2.3` → `1.2.3`；手动运行时取输入参数 `version`（默认 `0.0.0-dev`）；其他情况为 `0.0.0-dev`。 |
| `CFBundleShortVersionString` | 版本号的数字部分：`1.0.0-rc.1` → `1.0.0`，`0.0.0-dev` → `0.0.0`。通过 `MARKETING_VERSION` 传入。 |
| `CFBundleVersion` | `github.run_number` |

`build-macos.yml` 的触发条件：推送 `v*` tag（构建并发布）；修改 `whiteboard/**`、`packaging/**`、`ipad/**`、`run.py`、`requirements.txt` 或该工作流的 pull request（只构建）；`workflow_dispatch`（只构建，不发布）。

`.github/workflows/ipad-check.yml` 只编译外壳，不生成 IPA。它在任意分支推送了 `ipad/**` 或该工作流的改动时运行，也在修改 `ipad/**` 的 pull request 中运行。

本地用 PyInstaller 打包时，如果没有 `packaging/ipad/Whiteboard.ipa`，打包仍然成功；此时应用不带外壳，安装页给出 Release 的链接。

### 8.2 首次安装

安装页 `GET /ipad` 供 iPad 通过 TrollStore 安装外壳，并把 Mac 的地址交给外壳。访问该页面不需要任何权限。

| 元素 | 内容 |
|---|---|
| 页头 | 版本号和 `<主机名>:<端口>` |
| 「安装白板外壳」 | 链接到 `apple-magnifier://install?url=http://<主机名>.local:<端口>/ipad/Whiteboard.ipa`，由 TrollStore 安装。下方说明：点击无反应时，先在 TrollStore 的设置中打开 URL Scheme。 |
| 没有附带 IPA（从源码运行） | 不显示上述按钮，改为显示「这个版本没有附带外壳，请从 Release 下载」以及 `https://github.com/<仓库>/releases` 的链接。 |
| 「打开外壳」 | 链接到 `whiteboard-shell://connect?host=<主机名>.local&port=<端口>`，把这台 Mac 的地址交给外壳（4.1 节第 3 步）。 |

主机名取查询参数 `host`；没有该参数时取 `netinfo.local_hostname()`，与 `/profile.mobileconfig` 使用的值相同。端口为服务实际监听的端口。

iPad 打开安装页的方式与获取描述文件的方式相同：Mac 窗口的「连接 iPad」卡片在描述文件下载按钮旁显示 `外壳安装页 <地址>ipad`。描述文件继续保留，供没有 TrollStore 的 iPad 使用。

### 8.3 更新

外壳比较自身版本与 Mac 应用附带的 IPA 版本，并在后者较新时提示安装。

服务端接口（不需要任何权限，`Cache-Control: no-store`）：

| 接口 | 响应 |
|---|---|
| `GET /ipad/version` | `{"version": "1.3.0", "ipa": true, "bridge": [1, 1]}`。从源码运行时 `ipa` 为 `false`。 |
| `GET /ipad/Whiteboard.ipa` | Mac 应用附带的 IPA（`application/octet-stream`，`Content-Disposition: attachment`）。没有附带 IPA 时返回 404「这个版本没有附带外壳」。 |

更新检查：

- 外壳在每次页面加载完成后、页面已加载时每次回到前台时，以及握手版本不兼容后，请求 `/ipad/version`。请求忽略缓存，超时时间为 5 秒。
- `ipa` 为 `true` 且 `version` 比外壳自身版本新时，外壳显示「有新版本 X，是否更新？」，按钮为「更新」和「以后再说」。
- 点「更新」后，外壳打开 `apple-magnifier://install?url=http://<主机名>.local:<端口>/ipad/Whiteboard.ipa`，由 TrollStore 下载并安装。这是 TrollStore 1.3 起提供的 URL 安装接口，需要在 TrollStore 的设置中开启 URL Scheme。
- 点「以后再说」后，本次运行期间不再提示。

版本比较规则（`MacAddress.swift` 的 `ShellVersion`）：

| 规则 | 示例 |
|---|---|
| 去掉开头的 `v` 或 `V`。 | `v1.2.0` 等于 `1.2.0` |
| 忽略第一个 `-` 及其后的全部内容（预发布版后缀）。 | `1.0.0-rc.1` 等于 `1.0.0`，不提示更新 |
| 按点分隔，逐段按数字比较；每段数字之后的非数字字符忽略；缺少的段按 0 计。 | `1.10.0` 比 `1.9.3` 新；`1.2` 等于 `1.2.0` |

> **注意**
> 外壳自身的版本号（`CFBundleShortVersionString`）只含数字。忽略预发布版后缀，可以避免预发布版 Mac 应用在每次启动时把同一版本的外壳作为更新提示。

多数改动不需要更新外壳：外壳加载的是 Mac 上的页面，Mac 端更新后，iPad 下次打开外壳时即使用新的网页代码。只有外壳的原生代码或 `bridge` 版本改变时，才需要更新外壳。

### 8.4 Mac 端注册 Bonjour 服务

服务端注册 `_whiteboard._tcp` 服务，使外壳无需手动输入即可找到 Mac。

| 项目 | 行为 |
|---|---|
| 默认 | 只在 macOS 上注册（`netinfo.bonjour_default()`）。`--no-bonjour` 可关闭注册。 |
| 时机 | 端口确定后在后台任务中注册；服务端停止时注销。 |
| macOS | 通过 `ctypes` 调用 libSystem 中的 `DNSServiceRegister`，由系统的 mDNSResponder 处理。回调传 NULL，不需要 run loop，因此在 `--headless` 模式下同样有效。注册超时时间为 5 秒。 |
| 其他平台 | zeroconf（`MDNSAdvertiser`）。 |
| 服务名 | 下表中的 `name` 字段。 |
| 失败 | 只写日志，服务端照常启动。外壳可以改用安装页连接。 |

TXT 记录的字段：

| 字段 | 值 |
|---|---|
| `host` | 本机的 `.local` 主机名，与 `netinfo.local_hostname()` 相同 |
| `port` | 服务实际监听的端口（端口被占用而顺延时，为顺延后的值） |
| `version` | Mac 端版本号 |
| `name` | 显示给用户的电脑名称（`scutil --get ComputerName`；取不到时为去掉 `.local` 的主机名） |

> **警告**
> 在 macOS 上必须通过系统的 mDNSResponder 注册，不得启动第二个 mDNS 响应程序。基于 zeroconf 的 `MDNSAdvertiser` 会自行监听 mDNS 端口，因此在 macOS 上默认关闭（`netinfo.mdns_default()`）。

现有的 `_http._tcp` 注册（`--mdns`、`--no-mdns`）保持不变。

在 Mac 上验证，窗口模式和 `--headless` 模式都要满足：

```sh
dns-sd -B _whiteboard._tcp                    # 能看到这台 Mac
dns-sd -L "<服务名>" _whiteboard._tcp          # TXT 记录包含 host、port、version、name
```

外壳一端，`MacDiscovery` 用 `NWBrowser`（`.bonjourWithTXTRecord`，关闭点对点）查找，直接从 TXT 记录中读取主机名和端口。`name` 缺失或为空时使用服务名。结果按名称排序。

## 9. 第一阶段的验收标准

### 9.1 输入数据

| 编号 | 标准 | 检查方法 |
|---|---|---|
| A1 | 外壳来源的每秒新位置数不低于同一台 iPad 上 InkProbe 记录值的 95% | 诊断面板读数；同样的内容分别在外壳和 InkProbe 中写一遍 |
| A2 | 外壳采样点的坐标带小数 | 外壳来源的录像 |
| A3 | 外壳采样点与 Safari pen 事件的坐标偏差不超过 1 CSS 像素 | 诊断面板的「坐标偏差」一行 |
| A4 | 外壳来源的录像回放后与 `after` 完全一致 | `tests/test_shell_input.py::test_shell_recording_replays_exactly` |

### 9.2 功能不退化

在外壳中逐项检查，每项都必须与 Safari 中的行为相同：

- 钢笔、马克笔、荧光笔书写。
- 对象橡皮擦和像素橡皮擦，包括像素橡皮擦直径随倾角变化。
- 手指平移和缩放、手掌屏蔽、手指书写开关。
- 用 Pencil 点击工具栏和笔具盘上的按钮。
- 快速书写时不出现长按菜单、文字选择或 Scribble 识别。
- 断线重连，以及离线书写后重新同步。

### 9.3 浏览器版本不受影响

- 不经过外壳、直接用 Safari 访问时，行为与改动前相同。
- `tests/` 中现有的全部测试通过。

### 9.4 连接

- 同一局域网内只有一台 Mac 运行白板时，首次打开外壳后无需任何操作即可进入白板。
- 有两台 Mac 时，外壳列出两台，选择后进入对应的白板。
- Mac 的端口改变后（例如 8848 被占用，顺延到 8849），外壳下次启动时自动连接到新端口。
- 在屏蔽 Bonjour 的网络中（可在 Mac 端用 `--no-bonjour` 模拟），通过安装页的「打开外壳」按钮进入白板，全程不需要输入文字。

### 9.5 构建、安装与更新

- 推送 tag 后，Release 中同时出现 Mac 的 zip 和 iPad 的 IPA，Mac 应用中包含同版本的 IPA。
- 在 iPad 的 Safari 中打开安装页，点「安装白板外壳」，能通过 TrollStore 完成安装。
- Mac 端版本比外壳新时，外壳在启动时提示更新；确认后通过 TrollStore 完成安装，重新打开后外壳显示新版本号。

## 10. 第二阶段：原生绘制正在书写的笔画

### 10.1 启动条件

第一阶段完成后，只有在外壳中书写时墨迹仍明显落后于笔尖，才启动第二阶段。

判断方法：用 240 fps 慢动作视频分别拍摄外壳和 InkProbe 中的书写过程，比较笔尖位置与墨迹末端之间相差的帧数。

### 10.2 基本要求

- 在 WKWebView 上方添加一个透明的原生图层，只绘制正在书写的那一笔（包括预测部分）。抬笔后，这一笔交给网页正式绘制，原生图层清空。
- 原生图层绘制的轮廓必须与网页 `stroke.js` 绘制的轮廓一致，否则抬笔时墨迹会发生位移。为此需要把 perfect-freehand 和 `stroke.js` 的轮廓计算移植到 Swift，并逐点比对。`whiteboard/freehand.py` 和 `tests/test_docs.py` 已用同样的方法保证 Python 移植与原版逐点一致。
- 第二阶段的详细需求在启动时另行编写。

## 11. 不在本次范围内

- Pencil 双击和捏压切换工具（`UIPencilInteraction`）。
- Pencil 悬停（需要 iPadOS 16.1 及以上，以及支持悬停的 iPad 和 Pencil）。
- App Store 或 TestFlight 分发。
- 使用 PencilKit 绘制笔迹。笔迹必须在所有设备上一致，因此只能由网页绘制。

## 12. 需要实测确认的问题

| 编号 | 问题 | 确认方法 | 影响与状态 |
|---|---|---|---|
| Q1 | Safari 报告的压力是否等于 `force / maximumPossibleForce` | 见 7.2 节 | 决定外壳来源的压力是否需要换算。待确认。 |
| Q2 | WKWebView 上的手势识别器能否收到全部 Pencil 触摸，且不影响网页收到的 pointer 事件 | 在外壳原型中打印两边的采样点数 | **已确认不能。** 见下文。 |
| Q3 | 每次调用 `evaluateJavaScript` 的耗时，以及每帧调用一次是否造成掉帧 | 诊断面板的帧间隔；外壳记录调用耗时 | 耗时过长时，减少发送次数或改用其他传递方式。待确认。外壳每 5 秒在日志中写入调用次数、平均耗时和最长耗时（`EvalStats`），可在 Mac 的「控制台」App 中查看。 |
| Q4 | TrollStore 能否从局域网的 http 地址下载 IPA | 用 8.2 节的安装页实际安装一次 | 如果不能：首次安装改为在 Safari 中下载 IPA 后用 TrollStore 打开；更新改为由外壳下载 IPA，再通过系统分享面板交给 TrollStore 打开。待确认。 |
| Q5 | 4.3 节的措施能否完全阻止 Scribble 和长按菜单 | 按 9.2 节的检查项快速书写 | 如果不能，需要寻找其他关闭方法。待确认。 |
| Q6 | 升级系统后，已安装的外壳能否继续运行 | 本文档不要求验证；决定是否升级 iPad 系统之前，查阅 TrollStore 的说明 | 决定该 iPad 能否升级系统。 |
| Q7 | 无窗口模式下，`NSNetService` 注册是否需要额外运行 run loop 才能生效 | 按 8.4 节的验证方法，在 `--headless` 下检查 | 实现中已解决：注册采用 `DNSServiceRegister`，回调传 NULL，不依赖 run loop。仍需按 8.4 节在 Mac 上验证一次。 |

Q2 的结论与措施：

- 0.9.43 版的录像 `20260927-211825` 中，三笔都只收到落笔后约 30 ms 的采样点，此后没有 `move`，也没有 `up`。网页调用 `preventDefault` 后，WebKit 内部用于推迟其他手势的识别器会使 WKWebView 内部挂接的识别器失败。
- 外壳改为在窗口的 `sendEvent` 中读取采样点（4.2 节）。只有估计属性更新仍使用手势识别器，挂在 WKWebView 的父视图上。
- 0.9.43 版外壳需要重新安装才能修正；在此之前，网页用 Safari 的 pen 事件补充缺失部分（`whiteboard/web/static/js/shell-fallback.js`）。补充后的笔画精度与 Safari 相同，但笔画完整：

  | 情况 | 网页的处理 |
  |---|---|
  | Safari 落笔 50 ms（`SHELL_STALL_MS`）后，外壳仍未发送这一笔的任何采样点 | 由网页用 Safari 的 pen 事件起笔。 |
  | 外壳的一笔超过 50 ms 没有新的真实采样点，而 Safari 的 pen 事件仍在到达 | 由网页用 Safari 的 pen 事件完成这一笔。 |
  | Safari 报告抬笔后 150 ms（`SHELL_ORPHAN_MS`）内外壳没有发送 `up` | 由网页结束这一笔。 |
  | 点按期间外壳没有发送任何采样点 | 150 ms 后，由网页用 Safari 的落笔和抬笔生成这一点。 |

  这些批次同样经过 `receiveShell`，因此会写入录像，回放结果一致。新版外壳不会触发这段逻辑；所有 iPad 都更新到新版外壳后，可以将其连同 `input.js` 中的调用点一起删除。

## 13. 第一阶段的实现位置

| 部分 | 位置 |
|---|---|
| 外壳（第 4 节） | `ipad/Whiteboard/`：`AppDelegate.swift`（窗口、URL scheme、回到前台），`PencilCapture.swift`（4.2、5.1），`ShellViewController.swift`（4.1、4.1.1、4.3、5.2、8.3），`StatusView.swift`（查找、列表、错误页面），`MacDiscovery.swift`（Bonjour 查找、更新检查），`MacAddress.swift`（地址、本机保存、版本比较），`Settings.bundle`（「重新查找 Mac」、版本号、当前 Mac），`Info.plist`；`ipad/project.yml`、`ipad/make_icon.py` |
| 网页（第 7 节） | `input.js` 的外壳输入部分（输入来源、坐标偏差、估计属性更新、预测），`shell-fallback.js`（0.9.43 版外壳的补救逻辑），`shell.js`（握手），`recorder.js`（7.3），`perf.js`（7.4），`ui.js`（外壳中的笔具盘和「白板设置」按钮），`ui-settings.js`（「Mac」一组、「连接 iPad」卡片上的安装页地址），`exporter.js`（4.1.1） |
| Mac 端服务（第 8 节） | `whiteboard/ipadshell.py`（`/ipad`、`/ipad/version`、`/ipad/Whiteboard.ipa`），`whiteboard/server.py` 中的路由，`netinfo.BonjourService`（8.4），`runner.py`，`run.py`（`--no-bonjour`） |
| 构建（8.1 节） | `.github/workflows/build-macos.yml` 的 `ipad` 任务；`.github/workflows/ipad-check.yml`；`packaging/whiteboard.spec`；`packaging/smoke_ipad.py`（检查打包后的应用能提供 IPA 和安装页） |
| 测试 | `tests/test_shell_input.py`（网页端，含 A2、A4），`tests/test_ipadshell.py`（安装页、版本接口、Bonjour 注册、接口版本范围、下载处理） |

需要在真机上完成的检查：9.1 节的 A1、A3，9.2 节全部，9.4 节，9.5 节的安装与更新，以及 12 节的 Q1 至 Q5、Q7。

# iPad 外壳需求

本文档规定 iPad 外壳（以下简称"外壳"）第一阶段的功能、外壳与网页之间的接口、构建和分发流程，以及验收标准。第二阶段只列出启动条件和基本要求。

## 1. 目标

在 iPad 上，用 Apple Pencil 书写时，网页得到的输入数据与原生应用相同。其他设备继续通过浏览器访问，功能不变。

### 1.1 需要解决的问题

以下数据来自同一台 iPad：Safari 的数据取自白板的录像 `20260927-181756.json`，原生的数据取自 InkProbe 的四份会话（仓库根目录下的 `20260924-*.zip`）。

| 项目 | Safari（网页） | 原生（UIKit） |
|---|---|---|
| 每秒采样数 | 约 120 | 240 至 244 |
| 每秒不重复的新位置 | 约 60 | 147 至 240 |
| 坐标精度 | 整数 CSS 像素 | 带小数，取自 `preciseLocation(in:)` |
| 时间戳精度 | 整数毫秒 | `UITouch.timestamp`，秒，带小数 |
| 预测采样 | 无 | `predictedTouches(for:)` |

输入平滑算法的对比（Google Ink、ink-stroke-modeler、Xournal++、Krita 与当前实现）表明，在同一份 Safari 输入上，各算法画出的笔迹在静态图和回放中都没有可辨别的差别。因此，要改善书写手感，需要改善的是输入数据本身，而不是平滑算法。

### 1.2 原则

外壳只提供输入和平台服务，不包含白板逻辑。笔迹几何、同步、橡皮、界面全部保留在网页中。这一原则带来两个结果：

- 用 Safari 访问的版本始终完整可用，只是输入数据的质量较低。
- 除了外壳本身的原生代码，其余改动都随 Mac 端更新自动生效，不需要重新安装外壳。

## 2. 前提条件

- **安装方式**：通过 TrollStore 安装未签名的 IPA，不需要 Apple 开发者账号。
- **系统版本**：TrollStore 支持 iPadOS 14.0 beta 2 至 16.6.1、16.7 RC（20H18）和 17.0，16.7.x 的其他版本以及 17.0.1 及以后的版本不受支持。录像中这台 iPad 的 Safari 版本为 15.6，符合条件。这台 iPad 以后不能升级系统，否则无法安装新版外壳。
- **最低系统版本**：外壳的部署目标定为 iPadOS 15.0，与 InkProbe 相同。
- **设备**：只支持 iPad。

## 3. 总体结构

| 部分 | 负责的事 |
|---|---|
| 外壳（Swift，新增） | 用 WKWebView 加载 Mac 上的白板页面；采集 Pencil 触摸并转发给网页；关闭系统手势对书写的干扰；检查并安装外壳自身的更新 |
| 网页（现有，需改动） | 把外壳转来的采样作为 Pencil 输入来源；其余行为不变 |
| Mac 端服务（现有，需改动） | 注册 Bonjour 服务；提供安装页、外壳的版本信息和 IPA 下载 |
| GitHub Actions（现有，需改动） | 构建 IPA，并把它打包进 Mac 应用 |

手指输入不经过外壳，仍由网页的 pointer 事件处理。平移、缩放、手掌屏蔽、手指书写开关的现有逻辑都不变。

外壳源码放在仓库的 `ipad/` 目录下，用 XcodeGen 生成工程，做法与 InkProbe 相同。

## 4. 外壳的功能

### 4.1 连接 Mac 与加载页面

用户在任何情况下都不需要手动输入主机名和端口。外壳按以下顺序取得 Mac 的地址：

1. **已保存的地址。** 外壳启动时，如果本机保存过地址，直接加载。加载失败时（例如 Mac 的端口因为被占用而顺延），转到第 2 步。
2. **Bonjour 自动发现。** 外壳用 `NWBrowser` 查找 `_whiteboard._tcp` 服务（Mac 端的注册方式见 8.4 节）：
   - 找到一台 Mac 时，直接连接；
   - 找到多台时，列出各台 Mac 的名称，由用户点选；
   - 5 秒内一台都没有找到时，显示第 3 步的操作说明。
3. **一键链接。** 外壳注册 URL scheme `whiteboard-shell`。在 iPad 的 Safari 中打开 Mac 提供的安装页（8.2 节），点"打开外壳"，即打开 `whiteboard-shell://connect?host=<主机名>.local&port=<端口>`，外壳收到后保存地址并连接。这一步用于路由器屏蔽 Bonjour 的网络。

连接成功的地址保存在本机，下次启动直接使用。外壳的设置页提供"重新查找 Mac"，用于换到另一台 Mac。

页面的加载方式：

- 页面地址为 `http://<主机名>.local:<端口>/?role=ipad`。`role=ipad` 让服务端返回 iPad 界面，服务端的 `detect_role` 已经支持这个参数。Bonjour 解析得到的主机名和端口来自服务的 TXT 记录（8.4 节）。
- `Info.plist` 需要包含：
  - `NSAppTransportSecurity` → `NSAllowsLocalNetworking = true`，允许加载局域网内的 http 地址；
  - `NSLocalNetworkUsageDescription`，iPadOS 14 起访问局域网必须提供；
  - `NSBonjourServices = ["_whiteboard._tcp"]`，iPadOS 14 起查找 Bonjour 服务必须声明服务类型；
  - `CFBundleURLTypes` 中注册 `whiteboard-shell`。
- 页面加载失败时，外壳显示错误原因，以及"重试""重新查找 Mac"两个按钮。
- WKWebView 铺满屏幕，禁止页面滚动和缩放（`scrollView.isScrollEnabled = false`，`bounces = false`，最小和最大缩放都为 1）。

### 4.2 采集 Pencil 输入

> 实测修订（见 12 节 Q2）：下面挂在 WKWebView 上的手势识别器收不到完整的笔画。实现改为子类化 `UIWindow`，在 `sendEvent(_:)` 中读取每个触摸事件里的 Pencil 触摸，读取的字段不变；`touchesEstimatedPropertiesUpdated` 只会送给手势识别器，所以另挂一个只接收更新的识别器，挂在 WKWebView 的父视图上。

- 在 WKWebView 上添加一个自定义的 `UIGestureRecognizer` 子类，设置如下：
  - `allowedTouchTypes = [UITouch.TouchType.pencil]`，只接收 Pencil；
  - `cancelsTouchesInView = false`、`delaysTouchesBegan = false`、`delaysTouchesEnded = false`，不影响 WKWebView 自身收到的触摸；
  - 对所有其他手势识别器都返回可以同时识别（`shouldRecognizeSimultaneouslyWith` 返回 `true`）。
- 在 `touchesBegan`、`touchesMoved`、`touchesEnded`、`touchesCancelled` 中，对每个 Pencil 触摸：
  - 用 `event.coalescedTouches(for:)` 取出这一次回调中的全部真实采样；
  - 用 `event.predictedTouches(for:)` 取出预测采样；
  - 每个采样读取的字段见 5.1 节。坐标一律用 `preciseLocation(in: webView)`，方位角用 `azimuthAngle(in: webView)`。
- 在 `touchesEstimatedPropertiesUpdated` 中接收估计属性的更新（通常是力度）。InkProbe 的 `TouchLogger.swift` 已经实现了相同的读取逻辑，可以直接参照。
- 每次触摸回调结束时，把这次回调产生的全部采样打成一批，发送给网页（格式见第 5 节）。不按固定时间间隔发送。

### 4.3 关闭系统手势对书写的干扰

- 网页中已有的拦截（`touch-action: none`、阻止 `touchstart` 默认行为等）保留。
- 外壳额外关闭以下系统行为：
  - Scribble（随手写）：iPadOS 14 起，Pencil 在可编辑区域书写会被识别为文字输入。外壳在 WKWebView 上添加 `UIScribbleInteraction`，其代理方法 `scribbleInteraction(_:shouldBeginAt:)` 返回 `false`。
  - 长按菜单与文字选择：WKWebView 的配置中关闭链接预览（`allowsLinkPreview = false`）。
- 这些措施是否足够，需要按 9.2 节的检查项实测。

### 4.4 检查和安装外壳更新

见第 8 节。

## 5. 外壳与网页之间的接口

### 5.1 外壳发给网页的采样

外壳调用 `webView.evaluateJavaScript("window.whiteboardShell && window.whiteboardShell.receive(<JSON>)")` 发送一批采样。JSON 的结构如下：

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
| `bridge` | 接口版本号，见第 6 节 |
| `id` | 触摸编号。同一次落笔到抬笔之间不变，由外壳分配，从 1 开始递增 |
| `ph` | `down`、`move`、`up`、`cancel` |
| `k` | 固定为 `real`，表示真实采样。预测采样放在 `pred` 中，不放在 `samples` 中 |
| `t` | `UITouch.timestamp`，单位为秒 |
| `x`、`y` | `preciseLocation(in: webView)`，单位为 point。页面缩放为 1 且不滚动时，数值等于网页的 `clientX`、`clientY` |
| `f`、`fmax` | `force`、`maximumPossibleForce` |
| `alt` | `altitudeAngle`，弧度，0 表示笔贴在屏幕上，π/2 表示笔竖直 |
| `az` | `azimuthAngle(in: webView)`，弧度 |
| `est` | `estimatedPropertiesExpectingUpdates` 中包含的属性名，可以为空数组 |
| `ui` | `estimationUpdateIndex`；`est` 为空时写 `null` |
| `pred` | 这一批对应的预测采样。每一批都整体替换上一批的预测；没有预测时为 `null` |
| `updates` | 这一批期间收到的估计属性更新，按 `ui` 对应到之前发送过的采样 |

### 5.2 网页发给外壳的消息

网页通过 `window.webkit.messageHandlers.whiteboard.postMessage(<对象>)` 发送消息。第一阶段只有一种：

- 页面加载完成后，网页发送 `{"type": "hello", "bridge": [最低版本, 最高版本]}`，表示网页支持的接口版本范围。

外壳收到后回复（通过 `evaluateJavaScript` 调用 `window.whiteboardShell.hello(<JSON>)`）：

```json
{"shellVersion": "1.3.0", "bridge": 1, "active": true}
```

`active` 为 `true` 时，外壳开始发送采样；为 `false` 时，外壳不发送采样，网页继续使用 Safari 的 pointer 事件。

### 5.3 时间

网页只使用同一次落笔内采样之间的时间差，不把 `t` 与 `performance.now()` 直接比较。网页中依赖当前时间的逻辑（例如抬笔后 500 ms 内屏蔽手掌的 `PALM_GRACE`）使用收到这一批采样时的 `performance.now()`。

## 6. 版本协商

- `bridge` 是整数，只在接口格式有不兼容的改动时加 1。外壳的版本号与 Mac 端相同，由同一个 git tag 决定。
- 网页声明自己支持的范围 `[最低, 最高]`。外壳的 `bridge` 落在这个范围内时，回复 `active: true`；否则回复 `active: false`，并在外壳界面上提示"外壳版本与白板版本不兼容，请更新外壳"。
- 网页每次提高最高版本时，保留对上一个版本的支持，直到下一个版本发布。这样 Mac 端更新而外壳尚未更新时，外壳仍然可以使用。

## 7. 网页端的改动

### 7.1 输入来源

- `input.js` 增加一个输入来源层。Safari 的 pointer 事件和外壳的采样都转换成同一种内部采样，之后的处理（`addSample`、`startErase` 等）不区分来源。
- 内部采样包含：时间（毫秒，允许小数）、`clientX`、`clientY`、压力、`tiltX`、`tiltY` 或 `altitudeAngle`、指针类型、是否来自外壳。
- 外壳处于 `active` 状态时，`#stage` 上 `pointerType === "pen"` 的 pointer 事件一律忽略，改用外壳的采样。手指和鼠标的 pointer 事件照常处理。
- 外壳的采样落在 `#ui` 元素上时（在 `down` 时用 `document.elementFromPoint` 判断），整次落笔都忽略外壳的采样，由界面控件自己的点击事件处理。这样 Pencil 仍然可以点工具栏按钮。
- 预测采样只用于实时层显示，不写入笔画。每一批的预测替换上一批的预测。

### 7.2 压感

- 外壳来源的压力取 `f / fmax`。
- InkProbe 四份会话中，`force / maximumPossibleForce` 的中位数为 0.017 至 0.084，95 分位为 0.129 至 0.398。Safari 录像中的压力在 0.007 至 0.125 之间。两者在同一量级，推测 Safari 报的压力就是 `force / maximumPossibleForce`。
- 这四份会话都是橡皮测试，不是正常书写，所以上面的推测需要验证：在同一台 iPad 上，用 InkProbe 和白板（Safari）各写同样的内容，比较两边压力的分布。
  - 推测成立时，外壳来源的压力不做换算，笔迹格式和现有压感曲线（`stroke.js` 的 `PEN_KNEE`、`PEN_FLOOR`、`PEN_GAMMA`）都不变。
  - 推测不成立时，在输入层把 `f / fmax` 换算到 Safari 的量程再存入笔画，笔迹格式仍然不变。换算关系由这次对比确定。
- 估计属性更新：第一阶段只用于尚未提交的笔画。收到更新时，如果对应的采样仍属于正在书写的那一笔，就修正它的压力；笔画提交之后到达的更新丢弃。丢弃的更新占多少比例、对笔宽有多大影响，用 InkProbe 数据统计后写入本文档。
- InkProbe 四份会话的统计（只算 Pencil 的 80 笔，共 19139 个等待更新的采样，全部收到了更新）：

  | 会话 | 更新数 | 抬笔之后才到 | 比例 | 更新延迟中位数 / 95 分位 | 抬笔后那些更新的力度变化（`Δf / fmax` 中位数） |
  |---|---|---|---|---|---|
  | test1-calibr | 3862 | 128 | 3.3% | 25.4 / 39.7 ms | −0.029 |
  | test2-calibr | 3388 | 203 | 6.0% | 26.0 / 39.6 ms | −0.020 |
  | test3-drag | 7191 | 101 | 1.4% | 25.3 / 39.0 ms | −0.090 |
  | test4-contrast | 4698 | 43 | 0.9% | 26.1 / 41.3 ms | −0.016 |

  合计 475 个，占 2.5%。每一笔都是最后约 6 个采样（约 25 ms）的更新落在抬笔之后，而且这些更新都是把力度改小：丢掉它们，笔画末端那几个点的压力偏大约 0.02 至 0.09（以 `f / fmax` 计）。输入层的 `trimSettledTail` 通常会砍掉笔停住之后的末尾几个点，剩下的影响限于末端收笔略粗。诊断面板的"抬笔后丢弃"一行显示实际丢弃的个数。

### 7.3 录像

- 录像增加外壳来源的采样，保留 5.1 节中的全部字段和原始数值，与现有录像一样不做四舍五入。
- 每条录像的顶层增加 `source` 字段，取值为 `browser` 或 `shell`，同时记录外壳版本和 `bridge`。
- 回放外壳来源的录像时，把采样直接交给输入来源层，结果必须与录像中的 `after` 一致。`tests/test_browser.py::test_recorder_replays_an_erase_exactly` 的做法同样适用。

### 7.4 诊断面板

诊断面板（`perf.js`）增加以下几行：

- 当前输入来源：`browser` 或 `shell`，以及外壳版本和 `bridge`；
- 外壳来源的每秒采样数和每秒新位置数，计算方法与现有的 `penHz`、`penMoveHz` 相同；
- 坐标偏差：同一次落笔时，外壳采样与 Safari pen 事件的 `clientX`、`clientY` 之差（外壳处于 `active` 状态时，Safari 的 pen 事件仍然会到达，只是不参与书写，可以用来对比）。

## 8. 构建、发布与更新

### 8.1 构建

- 在 `.github/workflows/build-macos.yml` 中增加一个 `ipad` 任务，步骤照搬 InkProbe 的 `.github/workflows/build.yml`：macOS 26 runner、`xcodegen generate`、`xcodebuild -sdk iphoneos` 不签名编译、`ldid -S` 伪签名、打包为 `Whiteboard-<版本>-ipad.ipa`。
- 外壳的版本号与 Mac 端相同，由 tag 决定，写入 `CFBundleShortVersionString`。
- 现有的 macOS 构建任务改为依赖 `ipad` 任务，并把 IPA 复制进 `Whiteboard.app` 的资源目录。这样每个版本的 Mac 应用都自带同版本的外壳。
- Release 中同时发布 Mac 的 zip 和 iPad 的 IPA。IPA 也单独发布，用于首次安装。

### 8.2 首次安装

- Mac 端服务新增安装页 `GET /ipad`，不需要任何权限。页面上有两个按钮：
  - "安装白板外壳"：链接到 `apple-magnifier://install?url=http://<主机名>.local:<端口>/ipad/Whiteboard.ipa`，由 TrollStore 安装；
  - "打开外壳"：链接到 `whiteboard-shell://connect?host=<主机名>.local&port=<端口>`，把这台 Mac 的地址交给外壳（4.1 节第 3 步）。
- 页面中的主机名和端口由服务端填入，取值与 `/profile.mobileconfig` 使用的相同。
- 从源码运行、Mac 应用中没有 IPA 时，安装页说明"这个版本没有附带外壳，请从 Release 下载"，并给出 Release 页面的链接。
- iPad 到达安装页的方式与现在到达描述文件的方式相同：在 Mac 窗口的"连接 iPad"卡片中，现有的描述文件下载按钮旁边增加安装页的地址。描述文件保留，供没有 TrollStore 的 iPad 使用。

### 8.3 更新

- Mac 端服务新增两个接口，和 `/profile.mobileconfig` 一样不需要任何权限：
  - `GET /ipad/version`，返回 `{"version": "1.3.0", "ipa": true, "bridge": [1, 1]}`。`ipa` 表示 Mac 应用是否自带 IPA，从源码运行时为 `false`。
  - `GET /ipad/Whiteboard.ipa`，返回 Mac 应用自带的 IPA。
- 外壳每次启动和每次从后台回到前台时请求 `/ipad/version`。
  - 如果 `ipa` 为 `true` 且 `version` 比外壳自身的版本新，外壳显示对话框："有新版本 X，是否更新？"
  - 用户确认后，外壳打开 `apple-magnifier://install?url=http://<主机名>.local:<端口>/ipad/Whiteboard.ipa`，由 TrollStore 下载并安装。这是 TrollStore 1.3 起提供的 URL 安装接口，需要在 TrollStore 的设置中开启 URL Scheme。
  - 用户选择"以后再说"时，本次运行期间不再提示。
- 大多数改动不需要这个流程：外壳加载的是 Mac 上的页面，Mac 端更新后，iPad 下次打开外壳时自动使用新的网页代码。只有外壳的原生代码或 `bridge` 版本改变时，外壳才需要更新。

### 8.4 Mac 端注册 Bonjour 服务

- Mac 端服务启动后，注册 `_whiteboard._tcp` 服务，服务端停止时注销。TXT 记录包含：
  - `host`：本机的 `.local` 主机名，与 `netinfo.local_hostname()` 相同；
  - `port`：服务实际监听的端口（端口被占用而顺延时，写顺延后的值）；
  - `version`：Mac 端版本号；
  - `name`：显示给用户的 Mac 名称，取系统的电脑名称。
- 在 macOS 上必须通过系统的 mDNSResponder 注册，不能再启动第二个 mDNS 响应程序。现有的 `MDNSAdvertiser` 使用 zeroconf 库，它会自己监听 mDNS 端口，因此在 macOS 上默认关闭（`netinfo.mdns_default()`）。新的注册改用系统接口，两种做法可选：
  - pyobjc 提供的 `NSNetService`（`pyobjc-framework-Cocoa` 已经是依赖）；
  - 通过 `ctypes` 调用 libSystem 中的 `DNSServiceRegister`。
- 与现有的 `MDNSAdvertiser` 一样，注册失败只写日志，不影响服务启动。
- 验证方法：在 Mac 的终端运行 `dns-sd -B _whiteboard._tcp`，能看到这台 Mac；再运行 `dns-sd -L <服务名> _whiteboard._tcp`，TXT 记录中的四个字段与实际一致。无窗口模式（`--headless`）下也要满足。
- 现有的 `_http._tcp` 注册（`--mdns` 参数）保持不变。

## 9. 第一阶段的验收标准

### 9.1 输入数据

| 编号 | 标准 | 检查方法 |
|---|---|---|
| A1 | 外壳来源每秒不重复的新位置数不低于同一台 iPad 上 InkProbe 记录值的 95% | 诊断面板读数；同样的内容分别在外壳和 InkProbe 中写一遍 |
| A2 | 外壳采样的坐标带小数 | 外壳来源的录像 |
| A3 | 外壳采样与 Safari pen 事件的坐标偏差不超过 1 CSS 像素 | 诊断面板的坐标偏差一行 |
| A4 | 外壳来源的录像回放后与 `after` 完全一致 | 新增一条与 `test_recorder_replays_an_erase_exactly` 相同做法的测试 |

### 9.2 功能不退化

在外壳中逐项检查，每项都必须与 Safari 中的行为相同：

- 钢笔、马克笔、荧光笔书写；
- 对象橡皮和像素橡皮，包括像素橡皮直径随倾角变化；
- 手指平移和缩放，手掌屏蔽，手指书写开关；
- 用 Pencil 点击工具栏按钮和笔具盘；
- 快速书写时不出现长按菜单、文字选择或 Scribble 识别；
- 断线重连、离线书写后重新同步。

### 9.3 浏览器版本不受影响

- 不经过外壳、直接用 Safari 访问时，行为与改动前相同。
- `tests/` 中现有的全部测试通过。

### 9.4 连接

- 同一局域网内只有一台 Mac 运行白板时，首次打开外壳后不做任何操作即可进入白板。
- 有两台 Mac 时，外壳列出两台，点选后进入对应的白板。
- Mac 的端口改变后（例如 8848 被占用，顺延到 8849），外壳下次启动时自动连接到新端口。
- 在屏蔽 Bonjour 的网络中（可以用关闭 Mac 端注册的方式模拟），通过安装页的"打开外壳"按钮进入白板，全程不需要输入文字。

### 9.5 构建、安装与更新

- 打 tag 后，Release 中同时出现 Mac 的 zip 和 iPad 的 IPA，Mac 应用中包含同版本的 IPA。
- 在 iPad 的 Safari 中打开安装页，点"安装白板外壳"，能通过 TrollStore 完成安装。
- Mac 端版本比外壳新时，外壳在启动时提示更新；确认后通过 TrollStore 完成安装，重新打开后外壳显示新版本号。

## 10. 第二阶段：原生绘制正在书写的笔画

### 10.1 启动条件

第一阶段完成后，如果在外壳中书写时仍然感觉墨迹跟不上笔，再启动第二阶段。判断方法：用 240 fps 慢动作视频分别拍摄外壳和 InkProbe 中的书写过程，比较笔尖位置与墨迹末端之间相差的帧数。

### 10.2 基本要求

- 在 WKWebView 上方加一个透明的原生图层，只绘制正在书写的那一笔（包括预测部分）。抬笔后，这一笔交给网页正式绘制，原生图层清空。
- 原生图层画出的形状必须与网页 `stroke.js` 画出的形状一致，否则抬笔时墨迹会发生位移。为此需要把 perfect-freehand 和 `stroke.js` 的轮廓计算移植到 Swift，并逐点比对。仓库中的 `whiteboard/freehand.py` 和 `tests/test_docs.py` 已经用同样的方法保证了 Python 移植与原版逐点一致，可以照此执行。
- 第二阶段的详细需求在启动时另写。

## 11. 不在本次范围内

- Pencil 双击和捏压切换工具（`UIPencilInteraction`）。
- Pencil 悬停。它需要 iPadOS 16.1 以上和支持悬停的 iPad 与 Pencil。
- App Store 或 TestFlight 分发。
- 使用 PencilKit 绘制笔迹。笔迹必须在所有设备上一致，所以只能由网页绘制。

## 12. 需要实测确认的问题

| 编号 | 问题 | 确认方法 | 影响 |
|---|---|---|---|
| Q1 | Safari 报的压力是否等于 `force / maximumPossibleForce` | 见 7.2 节 | 决定外壳来源的压力是否需要换算 |
| Q2 | WKWebView 上的手势识别器能否收到全部 Pencil 触摸，且不影响网页收到的 pointer 事件 | 外壳原型中打印两边的采样数 | 决定 4.2 节的做法是否可行。**已确认不可行**：0.9.43 的录像 20260927-211825 中，三笔都只收到落笔后约 30 ms 的采样，此后没有 move，也没有 up。采样改为在窗口的 `sendEvent` 中读取，只有估计属性更新仍用手势识别器，挂在 WKWebView 的父视图上。网页端另加两道保护：外壳断流超过 50 ms 时用 Safari 的 pen 事件补完这一笔；Safari 报抬笔后 150 ms 内外壳没有 up 时，由网页替它收尾 |
| Q3 | `evaluateJavaScript` 每次调用的耗时，以及每帧调用一次是否会造成掉帧 | 诊断面板的帧间隔；外壳中记录调用前后的时间 | 如果耗时过长，改为减少发送次数或改用其他传递方式 |
| Q4 | TrollStore 能否从局域网的 http 地址下载 IPA | 用 8.2 节的安装页实际安装一次 | 如果不能：首次安装改为在 Safari 中下载 IPA 后用 TrollStore 打开；更新改为外壳自己下载 IPA，再通过系统分享菜单交给 TrollStore 打开 |
| Q5 | 4.3 节的措施能否完全阻止 Scribble 和长按菜单 | 按 9.2 节的检查项快速书写 | 如果不能，需要找其他关闭方法 |
| Q6 | 升级系统后，已安装的外壳是否还能继续运行 | 本文档不要求验证；在决定是否升级 iPad 系统之前查阅 TrollStore 的说明 | 决定这台 iPad 能否升级系统 |
| Q7 | 在无窗口模式下，`NSNetService` 注册是否需要额外运行 run loop 才能生效 | 按 8.4 节的验证方法，在 `--headless` 下检查 | 如果需要且不便处理，改用 `DNSServiceRegister`。实现已直接采用 `DNSServiceRegister`（回调传 NULL，不依赖 run loop），仍需按 8.4 节在 Mac 上验证一次 |

## 13. 第一阶段的实现位置

| 部分 | 位置 |
|---|---|
| 外壳（第 4 节） | `ipad/`：`PencilCapture.swift`（4.2、5.1），`ShellViewController.swift`（4.1、4.3、5.2、8.3），`MacDiscovery.swift`（Bonjour 查找与更新检查），`MacAddress.swift`（地址、本机保存、版本比较），`Settings.bundle`（"重新查找 Mac"与版本号） |
| 网页（第 7 节） | `input.js` 的"外壳输入"一节（输入来源、坐标偏差、估计属性更新、预测），`shell.js`（握手），`recorder.js`（7.3），`perf.js`（7.4） |
| Mac 端服务（第 8 节） | `ipadshell.py`（`/ipad`、`/ipad/version`、`/ipad/Whiteboard.ipa`），`netinfo.BonjourService`（8.4），连接 iPad 卡片上的安装页地址 |
| 构建（8.1 节） | `.github/workflows/build-macos.yml` 的 `ipad` 任务；`ipad-check.yml` 在外壳源码变动时编译一遍；`packaging/smoke_ipad.py` 检查打包后的应用带着 IPA |
| 测试 | `tests/test_shell_input.py`（网页端，含 A2、A4），`tests/test_ipadshell.py`（安装页、版本接口、Bonjour 注册） |

需要在真机上完成的检查：9.1 节的 A1、A3，9.2 节全部，9.4 节，9.5 节的安装与更新，以及第 12 节的 Q1 至 Q5、Q7。

# 合作记忆（给 agent 看）

CLAUDE.md 写的是长期规则；这里记的是和用户合作中学到的东西、踩过的坑、手上没做完的事。
上下文压缩或新开会话之后，靠这份文件接上。**学到一条、踩到一个坑、留下一件没做完的事，当场写进来，
和那次改动一起提交。** 过时的条目直接删，不留历史（历史在 git 里）。

## 接力顺序

1. CLAUDE.md
2. 本文件
3. `docs/backlog/README.md`（每条需求的状态）和[白板需求池](https://claude.ai/artifact/SDGbJjVwBENjJ2mmUJVRuK)的「新需求」
4. 开着的 PR：`gh api "repos/Maimai-l/white-board/pulls?state=open" --jq '.[] | "\(.number) \(.title)"'`
5. dev 最近一次运行和 appcast 最上面的版本：
   `gh api "repos/Maimai-l/white-board/actions/runs?branch=dev&per_page=1" --jq '.workflow_runs[0] | "\(.display_title) \(.conclusion)"'`，
   `curl -s https://maimai-l.github.io/white-board/appcast.xml | grep -o "shortVersionString>[^<]*" | head -1`

## 用户的偏好（从反馈里学到的）

- **界面照苹果的惯例。** 用户对不按惯例的界面反应很强烈：按钮背后的圆底、设计稿里没有的彩色圆点、
  同一个界面两个地方设置同一样东西、两个弹窗叠在一起互相遮住，都被骂过。拿不准时按 macOS / iPadOS
  系统设置和自带应用的做法。
- **改界面先画设计稿**，用户确认再写代码，做完拿截图和设计稿对照。设计稿用 Artifact 的 Design 类型，
  照应用自己的样子画；账号里的 ENDFIELD 设计系统是别的项目的，不用。
- **能连白板的不只是 iPad**：局域网里任何设备的浏览器都能打开。管所有设备的地方写「其他设备」，不写 iPad。
- **总设置和白板设置并存**，像 iPhone 的控制中心和系统设置：白板库左下角有完整的设置，白板设置面板里
  常改的几项照样留着。
- **每台设备自己的偏好开关放在「更多」菜单**（Mac 工具栏的更多、iPad 笔具盘的「…」）：用手指绘图、形状识别、
  页码滑条，各自记在这台设备上，只在用得上的白板上出现。
- **iPad 上 Pencil 只用来写字**：界面控件（页码滑条）不响应 Pencil，只认手指和鼠标。
- **Mac 窗口照 Mac 表单的惯例**：对话框后面变暗的地方能拖窗口，点暗处不关对话框。
- **汇报用中文，先说结论**；发了 alpha 一定给版本号，并说明用户要在真机上试什么。用户在真机上验收，
  发现问题会直接说「紧急」，紧急的先做。
- 需求的方式有好几种选择时先问用户选哪种（例如快速翻页选了右侧页码滑条），选定后自己定细节，在汇报里说明。

## 踩过的坑

- **dev 上的运行没有通知。** 合并之后直接盯那次运行的结论
  （`actions/runs?head_sha=<合并提交>&branch=dev`），不要只等 appcast 出新版本：失败了只会表现为「一直没出」。
- **GitHub Pages 部署**：刚上传的包要过一会儿才出现在列表里；按名字过滤的查询和不过滤的列表更新快慢不同。
  现在 ci.yml 等不过滤的列表、部署失败再重试（docs/design/ci-and-updates.zh-CN.md 3.3 节第 6 步）。
- **macOS 构建机偶尔排不上**（「not acquired by Runner … even after multiple attempts」）：不是代码的问题，重跑一次。
- **测命中、按住时长，要用真输入。** `support.FIRE` / `draw` 直接往 `#stage` 发事件，测不出盖在上面的界面
  截走输入；Playwright 的 `tap()` 没有按住的时间。用 `finger_tap`、`finger_hold`、`finger_drag`、
  `touch_frames`（CDP 真触摸）和 `pencil_stroke`（CDP 真笔）。
- **iPad 会丢手指的 pointerup**（捕获被系统拿走时），手势里留下「幽灵手指」，缩放方向就乱了：
  `input.js` 的 `dropStaleTouches` 管这个，别删。
- **真手指的触摸落在 `#stage` 里面的画布上**，不是 `#stage` 本身：判断用 `stage.contains(target)`。
- **只量一次的性能测试会误报**：CI 共享机器偶尔一抖，量几次取中位数。
- **提交说明只能写英文**（tests/test_release_notes.py 会拦），推之前跑 `make checks`；不能 force push。
- **iPad 外壳的更新**：alpha 的版本号都一样，外壳靠源码指纹（`WBShellRevision`）判断要不要提示，只有
  `ipad/` 的代码变了才提示。用户需要手动重装过一次带这个逻辑的外壳（2026-11-22 告诉过用户，没确认装没装）。

## 手上没做完的事

- iPad 尺寸的导出对话框：需要先画设计稿给用户确认，还没开始。
- 等用户在真机上确认的（发版检查表里有对应条目）：
  - 白板库里点一下打开白板、长按文档页加空白页、页码滑条（含 Pencil 不拖滑条）；
  - 双指缩放方向（幽灵手指的修复）；
  - Mac 上导入对话框后面拖动窗口；
  - 外壳在 alpha 之间提示更新（要先手动重装一次外壳）。

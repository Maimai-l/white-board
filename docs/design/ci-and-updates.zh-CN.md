# 模块化 CI 与 Sparkle 增量更新

这份设计回答两件事：一次改动多快能得到可信的结果，以及合进 dev 的改动多快能装到 Mac 上。
目标是 agent 一天可以迭代很多轮，每一轮都有完整的门禁，并在一小时内自动装到 Mac 上。

## 1. 现状和问题

| 问题 | 证据 |
| --- | --- |
| 同一个提交跑三遍 | `2c62f30` 同时推到 `claude/*`、`dev`、`stable`，测试跑了三次（#181–#183） |
| 每次都等最慢的那组 | `pytest` 一个 job 装依赖 40 s、跑 2 分半，其他 job 早就结束 |
| 不能只跑一部分 | 只有「全跑」；改一行文档也装浏览器 |
| 失败看不到现场 | 没有运行摘要，失败没有截图 |
| 打包和测试脱节 | `build-macos.yml` 不等测试，打 tag 才发布 |
| 每次更新下整包 | 旧更新器从 GitHub Release 下 zip，约 90 MB；没有 tag 就没有更新 |

## 2. CI：一个 workflow，按需挑 job

`.github/workflows/ci.yml` 取代 `tests.yml`、`ipad-check.yml`、`build-macos.yml`。

```
            ┌─ checks ──┐
            ├─ unit ────┤
            ├─ e2e ─────┤
plan ───────┼─ webkit ──┼──► publish（dev：alpha；tag：stable；其他：演练）
            ├─ js ──────┤        │
            ├─ inksync ─┤        └─► release（只有 tag）
            └─ ipad ─► package ─┘
               extension ───────┘
```

### 2.1 plan：决定跑哪些 job

`tools/ci_plan.py` 是唯一的决策点，输入事件、改动的文件、提交说明和手动输入，输出每个 job 跑不跑。
它是纯函数，规则有白盒测试（`tests/test_ci_plan.py`）。

| 改动 | 跑的 job |
| --- | --- |
| 只改文档（`*.md`、`docs/**`、`*.txt`） | checks |
| `whiteboard/**/*.py`、`run.py` | checks、unit、e2e、webkit、package |
| `whiteboard/web/**`、`packages/inksync/inksync/web/**` | checks、unit、e2e、webkit、js、package |
| `packages/inksync/**` | checks、unit、inksync、e2e、webkit、package |
| `tests/js/**` | checks、js |
| `tests/**`（其余） | checks、unit、e2e、webkit |
| `ipad/**` | checks、ipad |
| `packaging/**`、`requirements*.txt` | checks、unit、package |
| `extensions/**` | checks、extension、e2e |
| `.github/**`、`tools/ci_plan.py`、`Makefile` | 全部 |
| 认不出的路径 | 全部（宁可多跑） |

覆盖规则，优先级从高到低：

1. 推到 `dev` 或推 tag：全部。alpha 和正式版都必须过完整门禁。预发布 tag 只发到 alpha 通道，和以前「预发布版不推给用户」一致。
2. 手动运行时填的 `jobs`（如 `e2e,webkit` 或 `all`）。
3. 提交说明里的 `[ci: e2e,webkit]` 或 `[ci: all]`。
4. 按改动的文件。新分支第一次推送拿不到前一个提交，跑全部。

checks 总是跑。package 需要 iPad 外壳，所以 package 跑时 ipad 也跑。

### 2.2 job 的分工

| job | 内容 | 机器 |
| --- | --- | --- |
| checks | ruff（只开语法错误、未定义名字这类真错误）+ CHANGELOG 和提交说明的检查 | ubuntu |
| unit | `pytest tests --ignore=tests/e2e` | ubuntu |
| e2e | `pytest tests/e2e`（Chromium） | ubuntu |
| webkit | 主要流程用 WebKit 再跑一遍 | ubuntu |
| js | `node --test tests/js` | ubuntu |
| inksync | 单独安装 inksync 再测，确认它不依赖白板应用 | ubuntu |
| ipad | 编译 iPad 外壳，打成 IPA | macos-26 |
| package | PyInstaller 打包、放入 Sparkle、签名、冒烟测试、压缩 | macos-15 |
| extension | 打包浏览器插件 | ubuntu |
| publish | 生成 appcast 和增量包，发布到 GitHub Pages | macos-15 |
| release | 发 GitHub Release（只有 tag） | ubuntu |

### 2.3 触发、并发和缓存

- 触发：推送到 `dev` 和 `claude/**`、推 tag `v*`、PR、手动。`stable` 只接收已经在 dev 通过的提交，不再单独跑。
- 并发：同一个分支上新的推送会取消正在跑的那次（`cancel-in-progress`），tag 除外。发布到 Pages 的步骤用一个独立的并发组排队，不取消。
- 缓存：Python 依赖用 `setup-python` 的 pip 缓存。测试依赖写在 `requirements-test.txt`，本地和 CI 用同一份。
- 浏览器测试（e2e、webkit）在 `mcr.microsoft.com/playwright/python:v<版本>-noble` 容器里跑（`.github/actions/playwright`）：浏览器和系统依赖都在镜像里。GitHub 机器上 apt 只有 60–220 kB/s，WebKit 要装 126 MB 系统包，第一次运行时装不完。make 仍在机器上跑，`PY` 换成容器里的 python。镜像版本取自 `requirements-test.txt` 里固定的 `playwright==` 版本，升级只改那一行。
- 共用的安装步骤放在 `.github/actions/setup-python-deps`，每个 job 只写自己跑什么。
- 每个 job 都有 `timeout-minutes`：卡住的 job 最多占几十分钟，而不是默认的 6 小时。

### 2.4 结果要能直接看

- 每个 pytest job 输出 JUnit XML，`tools/ci_summary.py` 把通过数和失败的用例名写进运行摘要。
- e2e 和 webkit 失败时，把当时打开的每个页面截图，作为产物上传。文件名只留字母、数字、`-` 和 `_`。
- plan 把它的决定（每个 job 跑不跑、为什么）写进摘要，看一眼就知道这次为什么没跑某个 job。

### 2.5 本地一条命令

`Makefile` 的目标和 CI 的 job 一一对应：`make checks`、`make unit`、`make e2e`、`make webkit`、`make js`、`make inksync`，
`make test` 等于前面几个全部，`make fast` 是 checks + unit + js（不开浏览器，一分钟以内）。CI 的每个 job 调用同名的 make 目标，
本地和 CI 跑的是同一套命令。

## 3. 更新：Sparkle

### 3.1 通道

| 通道 | 何时发布 | 谁会收到 |
| --- | --- | --- |
| alpha | `dev` 每次 CI 全部通过；预发布 tag `vX.Y.Z-rc.N` | 装着 alpha 构建的 Mac（开发者自己） |
| stable | 推 tag `vX.Y.Z`（不带 `-`） | 所有用户；alpha 用户也会收到更新的正式版 |

两个通道共用一个 `appcast.xml`。alpha 的条目带 `sparkle:channel=alpha`，正式版的不带。
应用通过 `allowedChannelsForUpdater:` 决定看不看 alpha：

- 配置项 `update_channel` 没写时，alpha 构建第一次启动把它写成 `alpha`，正式版构建写成 `stable`。
- 所以 alpha 用户装上一个正式版之后仍然留在 alpha 通道，不会掉回 stable。
- 切回正式版：把配置里的 `update_channel` 改成 `stable`。

### 3.2 版本号

| 字段 | alpha | 正式版 |
| --- | --- | --- |
| `CFBundleVersion`（Sparkle 比较用） | 构建时间 `YYYYMMDD.HHMMSS`（UTC） | 同左 |
| `CFBundleShortVersionString`（显示用） | `1.3.0-alpha.20261119.153012` | `1.3.0` |
| `whiteboard.__version__` | 同显示版本 | 同显示版本 |

- 用构建时间而不是 workflow 的运行编号：workflow 改名或重建时编号会从 1 开始，而时间只会增长。同一个提交先出 alpha 再打 tag，正式版的时间更晚，会被当作更新。
- 显示版本里带着构建时间，Sparkle 的对话框和「关于」面板里一眼能看出是哪一次构建，不需要另写版本显示器。
- `whiteboard/__init__.py` 里的版本号是下一个要发布的版本。发版后在 dev 上改成下一个版本号，并在 CHANGELOG 里加上 `(unreleased)` 那一节。

### 3.3 发布流程（publish job）

1. 下载 package 产出的 `Whiteboard-<版本>.zip`。
2. 从 Pages 取当前的 `appcast.xml`（URL 带时间戳，绕过 CDN 缓存），下载它列出的完整包。
3. 写更新说明：dev 上的 alpha 用这次推送包含的提交标题，tag（正式版和预发布）用 CHANGELOG 里那个版本的一节（`packaging/release_notes.py`）。
4. `generate_appcast --channel alpha（正式版不加）--maximum-versions 3 --maximum-deltas 3 --embed-release-notes --download-url-prefix <站点>/`，
   私钥从 Secret 经标准输入传入（`--ed-key-file -`），不落盘，也不进日志。
5. 删掉 appcast 不再引用的文件，用 `upload-pages-artifact` 和 `deploy-pages` 发布。

增量包：每个新版本对前 3 个版本各生成一个，Python 运行时和依赖基本不变，所以增量包通常只有几 MB 甚至更小。
没有增量包可用时（跳了很多版本）Sparkle 下载完整包。

### 3.4 演练（其他分支）

在 `claude/**` 等分支上，publish 以演练模式运行，不发布任何东西：

1. 生成一对临时密钥。
2. 复制一份应用，改掉版本号和一个文件后重新签名，作为「下一个版本」。
3. 用两个版本跑 `generate_appcast`，确认生成了增量包和带签名的 appcast。
4. 用 `BinaryDelta apply` 把增量包应用到旧版本上，确认结果和新版本逐字节相同。

这样 Sparkle 的整条发布链路在每个分支上都有验证，而不是等到 dev 上才第一次出错。

### 3.5 应用里

- `whiteboard/sparkle.py` 用 PyObjC 加载 `Contents/Frameworks/Sparkle.framework`，在主线程创建 `SPUStandardUpdaterController`。
- 「检查更新」按钮在 Sparkle 可用时交给 Sparkle 的对话框；不可用时（从源码运行、旧构建、没配置公钥）走原来的流程。
- Info.plist：`SUFeedURL`、`SUPublicEDKey`、`SUEnableAutomaticChecks`；alpha 构建 `SUScheduledCheckInterval` = 3600 s、`SUAutomaticallyUpdate` = YES（自动下载，退出时安装），正式版每天查一次。
- 应用不是沙盒应用，Sparkle 的 XPC 服务用不上，打包时删掉。

### 3.6 签名

- 默认 ad hoc 签名。`packaging/sign_app.sh` 由内向外签：`Autoupdate`、`Updater.app`、`Sparkle.framework`，最后是应用本身，保留各自的 identifier、entitlements 和 flags，并在日志里打印 designated requirement。
- 仓库 Secret 里有 `MAC_CERT_P12`（base64）和 `MAC_CERT_PASSWORD` 时，改用这张自签名证书：建临时钥匙串、设好 partition list、签完删掉。证书保持不变的话，每次更新后系统权限不用重新允许。
- 更新的真实性由 EdDSA 签名保证：从 ad hoc 换成证书时，Sparkle 照样接受更新。

### 3.7 从旧更新器过渡

现有 1.2.0 用户的应用里只有旧更新器，它只认 GitHub Release。第一个带 Sparkle 的正式版照常发 GitHub Release，
旧更新器把用户带到这个版本，之后由 Sparkle 接手。旧更新器的代码保留到下一个正式版之后再删。

## 4. 需要仓库主人做的事

| 事项 | 在哪里 | 不做的后果 |
| --- | --- | --- |
| 生成 EdDSA 密钥：Sparkle 包里的 `bin/generate_keys`，再用 `generate_keys -x 文件` 导出私钥 | 自己的 Mac | 不能发布更新 |
| 私钥存成 Secret `SPARKLE_ED_PRIVATE_KEY` | 仓库 Settings → Secrets | publish 跳过，摘要里写明原因 |
| 公钥存成 Variable `SPARKLE_PUBLIC_ED_KEY` | 仓库 Settings → Variables | 打出的应用不带 Sparkle 配置，继续用旧更新器 |
| 开启 Pages，来源选 GitHub Actions；`github-pages` 环境允许 `dev` 分支和 `v*` tag | 仓库 Settings → Pages / Environments | 发布这一步失败 |
| （可选）自签名证书 `MAC_CERT_P12`、`MAC_CERT_PASSWORD` | 仓库 Settings → Secrets | 继续用 ad hoc 签名 |

私钥只在你自己的 Mac 和仓库 Secret 里。丢了私钥，已安装的应用就收不到之后的更新，只能手动装一次新版本。

## 5. 验证

| 项目 | 怎么验证 |
| --- | --- |
| plan 的规则 | `tests/test_ci_plan.py` 白盒测试 |
| 各 job 能跑通 | 推到工作分支，看每个 job 的结果 |
| 打出来的应用带着能加载的 Sparkle | package 里的冒烟测试：`--check-updater` 输出加载结果和配置 |
| 增量包能用 | publish 演练：BinaryDelta 应用后逐字节相同 |
| 真机上自动更新 | 只能在 Mac 上：装一个 alpha，dev 再合一个改动，一小时内应收到更新；写进发版检查表 |

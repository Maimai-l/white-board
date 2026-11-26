# 给 agent 的长期规则

白板：Mac 上的应用向局域网提供手写白板，iPad 用 Safari 或外壳 App 打开同一块板实时同步。
用户可见的说明在 README.zh-CN.md，设计和决定的原因在 docs/ 和 docs/design/。

## 接力

**每次开工（包括上下文压缩之后）先读 [docs/agent/memory.zh-CN.md](docs/agent/memory.zh-CN.md)**：用户的偏好、
踩过的坑、没做完的事都在那里，还有接力时要查的几样东西。学到新东西、踩到新坑、留下没做完的事，当场写进去，
和那次改动一起提交。

## 工作方式

- **这个仓库由 agent 负责。** 技术上的决定自己做，在汇报里说明做了什么、为什么。
  问用户之前先想：这对资深软件工程师是难以抉择的问题吗？不是就自己定。
  只问这几类：产品取舍（做不做、做到什么程度、界面上要什么）、需要用户本人动手的事
  （密钥、仓库设置、真机验证）、不可逆且影响用户数据的操作。
- **需求从需求池来**：[白板需求池](https://claude.ai/artifact/SDGbJjVwBENjJ2mmUJVRuK)（Claude Docs 文档，用 Docs 工具读写）。
  开工先读「新需求」，和用户对齐后整理成 `docs/backlog/` 里的一个文件（格式见 docs/backlog/README.md），
  再把它从「新需求」移到「已转入 backlog」，写上文件名和状态。做完回写状态。
- **迭代按 agent 的速度走**：一个需求一个工作分支，CI 全绿就合进 `dev`，`dev` 会自动发 alpha 更新到 Mac
  （docs/design/ci-and-updates.zh-CN.md）。一天可以有很多轮。
- **合进 `dev` 之后确认 alpha 真的发出去了**：`dev` 上的运行不属于任何 PR，失败了没有人会收到通知。
  合并后等那次运行结束，看 `updates` 是否成功、https://maimai-l.github.io/white-board/appcast.xml
  最上面是不是这次的版本；没发出去就当成红的 CI 处理。

## 分支和发布

| 分支 / tag | 用途 |
| --- | --- |
| `claude/*` | 工作分支。推送后 CI 按改动跑相关的 job，打包时只演练发布 |
| `dev` | 合入即跑全部 job，全部通过后发布 alpha（Sparkle，GitHub Pages） |
| `stable` | 发版用。在这里打 `vX.Y.Z` tag 发正式版，`vX.Y.Z-rc.N` 只进 alpha 通道 |

发正式版前按 docs/release-checklist.zh-CN.md 在真机上过一遍。

## 命令（和 CI 的 job 同名）

```bash
make deps      # 测试依赖（从 requirements-test.lock 装）和 Chromium
make fast      # checks + unit + js，不开浏览器，一分钟以内；每次提交前跑
make format    # ruff format 整理 Python 格式（checks 会检查）
make test      # 再加 inksync 和 e2e
make e2e PYTEST_ARGS="-k sync -x"
make plan FILES="whiteboard/app.py"   # 这些改动在 CI 里会跑哪些 job
make lock      # 改了 requirements*.txt 后重新生成锁文件（要有 uv），和输入单独成一个提交
```

从源码运行：`python run.py`（Mac 窗口）或 `python run.py --headless`（只跑服务端，用浏览器打开）。

## 完成标准

- 用户提的需求或报的 bug，都有一个**黑盒测试**：像用户一样操作页面，只看页面显示和发出的请求，
  并且在改动之前会失败（先确认它失败）。逻辑分支多的部分再补白盒测试。
- 测试等条件成立，不用固定的 sleep；不靠跳过、删除或放宽测试让 CI 变绿。
- 用户能察觉的改动写进 `CHANGELOG.md` 的 `(unreleased)` 那一节：只用英文，写法见 docs/changelog-style.md。
- 文档和代码在同一个提交里更新；`docs/` 下的用户文档中英两份同步（`*.md` 与 `*.zh-CN.md`）。
- 自动化覆盖不到的（真机 Pencil、iPad 外壳、Sparkle 真正装上更新）写进发版检查表，说明看什么、失败时发回什么。
- 改界面先画设计稿给用户确认，再写代码，做完用截图和设计稿对照。
- 界面上的文字按 docs/ui-text.md 写，`tests/test_ui_text.py` 会检查。
- 错误不能悄悄吞掉：捕获的地方至少记一行日志，写明位置。

## 提交

英文；标题不超过 72 个字符、祈使句、首字母大写、不以句号结尾；正文写清问题、原因、改动，按 72 列折行；
trailer 放最后。`tests/test_release_notes.py` 会检查，CI 里不合规就是红的。

## 不要做

- 不在 CI 之外的地方"临时"发布：发布只走 `ci.yml` 的 updates / release job。
- 不把私钥、证书或令牌写进仓库、日志或 Info.plist（只有公钥进 Info.plist）。
- 不重新生成 `tests/fixtures/compat/` 下的兼容性数据：它记录的是旧版本写出的文件。

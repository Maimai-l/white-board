# Backlog

需求的来源是[白板需求池](https://claude.ai/artifact/SDGbJjVwBENjJ2mmUJVRuK)；和用户对齐之后整理成这里的一个文件，
做的时候以这里为准。规则见仓库根目录的 CLAUDE.md。

## 格式

文件名 `NNN-短名.md`，编号只增不减。每个文件：

```markdown
# 标题

状态：待办 | 进行中 | 完成 | 暂缓　　来源：需求池 / 流程改进

## 为什么
## 要做什么（已和用户对齐的决定写在这里）
## 验收：黑盒测试
## 不做
## 进展（日期、提交、遗留问题）
```

完成后状态改成「完成」，进展里写上合并的提交；同时回写需求池里的状态。

## 顺序

越靠上越先做。

| # | 事项 | 状态 |
| --- | --- | --- |
| [001](001-export-redesign.md) | 导出重做：清晰、自动分页、选页、打印式对话框 | 完成 |
| [010](010-export-v2.md) | 导出第二版：整张导出、从零画页框、从上往下分页、导出白板文件 | 进行中 |
| [002](002-shape-recognition-lines.md) | 形状识别（第一版：直线） | 完成 |
| [003](003-ruler.md) | 尺子（iPad） | 完成 |
| [004](004-macos-runner-queue.md) | CI：macOS 机器排队 5–10 分钟 | 待办 |
| [005](005-lint-and-format.md) | 统一格式化（ruff format） | 待办 |
| [006](006-dependency-lock.md) | 依赖锁定 | 待办 |
| [007](007-condition-waits.md) | 测试里的固定等待改成等条件 | 待办 |
| [008](008-swallowed-errors.md) | 被吞掉的错误补日志 | 待办 |
| [009](009-aoci-index-trial.md) | AOCI-CODE 索引试验 | 暂缓 |

## 已完成

| 事项 | 合并 |
| --- | --- |
| 拆分 `tests/test_browser.py` 为 `tests/e2e/` | `66e0d7c` |
| 模块化 CI（`ci.yml`、plan、make 目标、失败截图） | 本分支，见 docs/design/ci-and-updates.zh-CN.md |
| Sparkle 增量更新（alpha / stable 通道、发布演练） | 本分支 |
| CLAUDE.md 与 backlog | 本分支 |

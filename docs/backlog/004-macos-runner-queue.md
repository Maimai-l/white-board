# CI：macOS 机器排队 5–10 分钟

状态：完成　　来源：流程改进

## 为什么

2026-11-19 的几次运行里，`package` 和 `updates-dry-run`（macos-15）每次都要排队 5–10 分钟才分到机器，
打包本身只要 1 分多。一个改动从推送到 alpha 发布，大半时间花在排队上。

## 要做什么

- 量一周：每次运行里 macOS job 的排队时间（`created_at` 到 `started_at`）。
- 把 `updates-dry-run` 合进 `package` 同一个 job，省掉一次排队。
- 比较 macos-15 和 macos-26 的排队时间；`ipad` job 用 macos-26 时没有排队。

## 验收

- 推送到工作分支到发布演练完成的时间，中位数降到 10 分钟以内。

## 进展

- 2026-11-20 量了 2026-11-19 的 30 次运行（257 个 job）：ubuntu 的 job 都在几秒内分到机器；
  macOS 的 job 要么几秒，要么正好 5 分 05 秒或 10 分 05 秒，等满 15 分钟就被取消（7 个 macOS job
  没分到机器就取消了，其中 2 个是排满 15 分钟，那两次运行因此是红的）。macos-15 有 39% 的 job 等了
  一分钟以上，macos-26 是 4/24。推送到发布演练完成：中位数 7 分 54 秒，最长 34 分 42 秒；
  没排队时 3–4.5 分钟，每排一次队多 5 或 10 分钟。另外，PR 分支的每次推送都跑两遍（push 和
  pull_request 两个事件，并发组不同、互不取消），macOS 机器要排两份队。
- 2026-11-20 `claude/ci-macos-queue`：不再监听 pull_request；iPad 外壳的编译步骤抽成
  `.github/actions/ipad-shell`，package 自己编译外壳、最后接着演练发布，单独的 ipad job 只在不打包
  时跑；package 和 updates 换到 macos-26。工作分支上串起来的 macOS job 从三个（ipad → package →
  updates-dry-run）减到一个，dev 上从三个减到两个。合入后再量一周，看最长时间是否落到 15 分钟以内。
- 2026-11-20 合入 `dev`（`f3f3bf0`）。改完之后工作分支上从推送到发布演练完成约 3.5 分钟（3 次）。
- 2026-11-20 合并后发现：从 11-19 13:37 起 `dev` 再没发出 alpha。导出那次是 ipad job 排满 15 分钟被取消，
  这次是 updates 的签名核对报缺文件——`appcast.py fetch` 只取回完整包，没取旧条目里的增量包
  （`claude/appcast-fetch-deltas` 修复）。`dev` 的运行不属于 PR，失败没人收到通知：CLAUDE.md 加了
  「合进 dev 之后确认 alpha 发出去了」。
- 2026-11-20 验收：改完之后工作分支上推送到发布演练完成 3.0–3.5 分钟（4 次），没有一个 macOS job 排队；
  `dev` 上合并到 alpha 发布约 10 分钟。修好发布之后连发两次 alpha（`104440`、`105516`），增量包 57–270 KB。

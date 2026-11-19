# CI：macOS 机器排队 5–10 分钟

状态：待办　　来源：流程改进

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

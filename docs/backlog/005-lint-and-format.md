# 统一格式化（ruff format）

状态：完成　　来源：流程改进

## 为什么

现在 CI 只开了 ruff 的错误类规则（`E9,F63,F7,F82`，008 之后加了吞掉异常的 `S110,BLE001`）。完整规则下有 967 条问题，其中 926 条是行太长；
没有统一格式，评审时会在格式上来回。

## 要做什么

- 一个单独的提交做 `ruff format`（只改格式），再在 checks 里加 `ruff format --check`。
- 逐步打开其他规则，每开一条先把现有代码改干净。

## 验收

- `make checks` 包含格式检查并通过。

## 进展

- 2026-11-20 `ruff.toml`：行宽 120（现有代码就是按这个宽度写的，格式化只动 74 个文件；100 要动 96 个），
  兼容性夹具 `tests/fixtures/` 不动；规则从 Makefile 挪进 ruff.toml，内容不变。只改格式的提交 `1e8653b`
  记在 `.git-blame-ignore-revs`。`make checks` 加了 `ruff format --check`（在原来的 dev 上 76 个文件不合格式，
  现在全部通过），`make format` 一键整理。其余规则（完整规则下主要是行太长）以后要开时一条一条开。

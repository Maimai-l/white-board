# 被吞掉的错误补日志

状态：完成　　来源：流程改进

## 为什么

`whiteboard/` 里有 16 处 `except …: pass`（例如 `whiteboard/server.py:235`、`whiteboard/app.py:550`），
出错时日志里什么都没有，问题无从查起。

## 要做什么

- 每处至少记一行日志，写明位置和异常；确实无所谓的情况写明为什么。

## 验收

- `grep` 不到不带日志也不带说明的 `except …: pass`。

## 进展

- 2026-11-20 `claude/swallowed-errors`：
  - Python（`whiteboard/`、`packages/inksync/inksync/`、`run.py`、`packaging/`）：不记日志也不交出异常的
    `except` 共 31 处（`whiteboard/` 10、inksync 21）。13 处补了日志（写明函数名和异常；预料之中、
    无害的用 `log.debug`，其余 `log.warning`），18 处确实可以不管，就地写了 `# 忽略：…` 说明为什么：
    输入校验里的 `TypeError` / `ValueError` 换默认值或返回 None 由调用方拒绝、刚自己取消的任务等它停下时的
    `CancelledError`、等保存完成时的失败（`Hub._save_done` 已经记过日志）。`server.running_build` 的
    `except Exception` 收窄成 `OSError` / `SubprocessError`；`server.list_apps` 拆成「没有 apps 文件夹」
    （常态，忽略）和其他 `OSError`（记警告）。`run.py`、`packaging/` 里的都已经打印了原因，没有要改的。
  - 页面脚本（`whiteboard/web/static/js`、inksync 的 `web/`，不含 `vendor/`）：没有上报的 `catch` /
    `.catch(…)` 共 41 处（白板 16、inksync 25）。17 处补了上报：白板用 `reportError`（送到 Mac 的日志，
    7 处，例如缩略图上传失败、笔具盘载入失败、检查更新失败），inksync 用 `console.warn`（10 处，
    例如 IndexedDB 读写失败、收到的消息不是 JSON）；24 处写了 `// 忽略：…`（localStorage 用不了、
    关掉本来就要丢的连接、上报本身失败等）。`uploadThumb` 以前不看响应状态，现在 500 也算失败。
  - 防止回退：`make checks` 的 ruff 加了 `S110`、`BLE001`（origin/dev 上 9 条，现在 0 条）；
    `tests/test_no_silent_catch.py` 扫 Python 的每个 `except` 和页面脚本的每个 `catch`，既不记日志
    也没有「忽略：」说明就失败（origin/dev 上 31 + 41 处，现在 0 处）。
  - 测试：白盒 `tests/test_logged_failures.py`（apps 读不了、配置里的端口不是数字、读不到 git 时日志里有
    这一行）；黑盒 `tests/e2e/test_diagnostics.py::test_a_failed_thumbnail_upload_is_reported_to_the_server`
    （缩略图上传返回 500 时页面向 `/api/debug` 上报）。三条白盒和这条黑盒在改动前都是红的。

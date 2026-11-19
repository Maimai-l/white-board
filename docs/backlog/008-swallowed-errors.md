# 被吞掉的错误补日志

状态：待办　　来源：流程改进

## 为什么

`whiteboard/` 里有 16 处 `except …: pass`（例如 `whiteboard/server.py:235`、`whiteboard/app.py:550`），
出错时日志里什么都没有，问题无从查起。

## 要做什么

- 每处至少记一行日志，写明位置和异常；确实无所谓的情况写明为什么。

## 验收

- `grep` 不到不带日志也不带说明的 `except …: pass`。

## 进展

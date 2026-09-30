[English](README.md) | 简体中文

# inksync

手写板的同步服务端：白板存储（`.wbz`）、带广播与自动保存的操作日志，以及 WebSocket 协议。它可以挂到任何 aiohttp 应用上，因此其他项目的服务端可以自行保存和同步白板，不需要运行白板应用。

```python
from aiohttp import web
from inksync import BoardStore, Hub, mount

app = web.Application()
mount(app, Hub(BoardStore("data")), path="/ws")
web.run_app(app, port=8848)
```

页面中用 `createInkPad(element, {app, board, transport: {url: "ws://…/ws"}})` 连接它；页面由同一服务提供时可以省略 `transport`。

## 安装

```bash
pip install "git+https://github.com/Maimai-l/white-board.git#subdirectory=packages/inksync"
```

需要 Python 3.10 或更高版本、aiohttp 3.9 或更高版本。

## `mount(app, hub, path="/ws", permissions=None, info=None)`

| 参数 | 说明 |
| --- | --- |
| `app` | aiohttp 应用。 |
| `hub` | 建立在 `BoardStore` 之上的 `Hub`。每个存储目录一个 Hub。 |
| `path` | WebSocket 接口的路径。 |
| `permissions` | `request -> frozenset`，取值为 `manage`、`settings`、`clear`、`export`。默认为 `local_only`：本机四项全部给予，其他设备没有权限。书写不需要任何权限。 |
| `pin_policy` | `(allowed, app, board_id, existing) -> bool`：固定连接能否打开或新建某块白板。默认：应用的白板和新白板任何设备都可以，用户自己的白板需要 `manage`。 |
| `info` | `request -> dict`，在 `init` / `sync` 中作为 `info` 发给客户端。默认为 `{"version": …}`。 |

`mount` 同时启动自动保存（每 3 秒），并在应用关闭时保存白板、断开连接。

修改权限规则后，调用 `await hub.refresh_permissions(lambda client: permissions(client.request))`：权限有变化的已建立连接立即收到 `perms` 和更新后的白板列表。带有 `X-Forwarded-For` 或 `Forwarded` 的请求一律不算本机，因此本机上的反向代理不会使请求获得本机权限。

## 让 iPad 外壳找到服务

```python
from inksync.netinfo import advertise
advertise(app, port=8900, source="qb", path="/")   # 在 web.run_app(app, port=8900) 之前调用
```

服务以带有 `source` 和 `path` 的 TXT 记录注册 `_whiteboard._tcp`。在「设置」App 中把外壳的「来源」设为 `@qb`，外壳即连接这个服务并打开 `path`，Apple Pencil 输入与白板相同。在 macOS 以外的系统上需要 zeroconf：`pip install "inksync[discovery] @ git+…"`。

## 模块

| 模块 | 内容 |
| --- | --- |
| `inksync.ws` | 协议处理（`Session`）、`websocket_handler`、`mount`、`detect_role` |
| `inksync.hub` | `Hub`（内存中的白板、固定连接、文件夹、广播、自动保存）、`BoardRuntime`、`Client` |
| `inksync.store` | `BoardStore`：`index.json`、`.wbz` 读写、只读判断、解锁前备份 |
| `inksync.models` | 元数据、笔画、遮罩和底图的校验 |
| `inksync.codec` | 点数据编码 |
| `inksync.netinfo` | `.local` 主机名、局域网地址、Bonjour 注册（`BonjourService`、`advertise`） |

`BoardStore` 为应用放在白板旁边的文件提供两个钩子：`_delete_extras(board_id)` 和 `_import_extras(path, old_id, board_id, meta)`。白板应用用它们处理缩略图和文档板原件（`whiteboard/store.py`）。

## 参考

- 协议：[docs/protocol.zh-CN.md](../../docs/protocol.zh-CN.md)
- 文件格式：[docs/format.zh-CN.md](../../docs/format.zh-CN.md)
- 前端手写板与传输：[docs/embed.zh-CN.md](../../docs/embed.zh-CN.md)

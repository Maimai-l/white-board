"""aiohttp 服务端：静态页面、描述文件下载与 WebSocket 同步通道。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import WSMsgType, web

from . import models, netinfo, profile, resources
from .config import Config
from .hub import Hub
from .store import BoardStore

log = logging.getLogger(__name__)

WEB_DIR = resources.web_dir()
MAX_WS_MESSAGE = 8 * 1024 * 1024
MAX_THUMB_BYTES = 512 * 1024
MAX_DOC_BYTES = 256 * 1024 * 1024
# 同时最多渲染两页：渲染走线程池，再多也只是互相抢 CPU。
RENDER_LIMIT = 2
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# aiohttp 的类型安全键，避免字符串键的命名冲突。
CONFIG_KEY: "web.AppKey[Config]" = web.AppKey("config")
STORE_KEY: "web.AppKey[BoardStore]" = web.AppKey("store")
HUB_KEY: "web.AppKey[Hub]" = web.AppKey("hub")
RENDER_KEY: "web.AppKey[asyncio.Semaphore]" = web.AppKey("render_lock")


def detect_role(user_agent: str, override: Optional[str] = None) -> str:
    """区分访问设备：Mac 端拿到完整 GUI，iPad 端只有书写界面。"""
    if override in ("mac", "ipad"):
        return override
    ua = (user_agent or "").lower()
    if "ipad" in ua or "iphone" in ua or "ipod" in ua:
        return "ipad"
    return "mac"


def may_control(request: web.Request) -> bool:
    """这个请求能不能动白板管理、设置、导出这些。

    默认只有本机可以：局域网上的别的设备拿到的是书写界面，换白板、建板删板、
    改名、导入导出、检查更新一律走不通。判断看的是 TCP 对端地址，不是 ``?role=``
    或者握手里那个 role——那两样客户端想填什么填什么。想让别的设备也能管，
    在 Mac 的白板设置里打开「允许其他设备控制」。
    """
    config: Config = request.app[CONFIG_KEY]
    return config.allow_remote_control or netinfo.is_own_address(request.remote)


def require_control(request: web.Request) -> None:
    if not may_control(request):
        raise web.HTTPForbidden(text="只有本机可以做这个操作")


# --------------------------------------------------------------------- 页面

async def handle_index(request: web.Request) -> web.Response:
    # 外部设备一律给书写界面，`?role=mac` 也没用：完整 GUI 里全是管理操作。
    role = (
        detect_role(request.headers.get("User-Agent", ""), request.query.get("role"))
        if may_control(request)
        else "ipad"
    )
    html = (WEB_DIR / "index.html").read_text("utf-8")
    html = html.replace("{{ROLE}}", role)
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def handle_profile(request: web.Request) -> web.Response:
    config: Config = request.app[CONFIG_KEY]
    host = request.query.get("host") or netinfo.local_hostname()
    url = f"http://{host}:{config.port}/"
    return web.Response(
        body=profile.build_profile(url),
        content_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": 'attachment; filename="whiteboard.mobileconfig"',
            "Cache-Control": "no-store",
        },
    )


async def handle_icon(request: web.Request) -> web.Response:
    return web.Response(body=profile.icon_png(180), content_type="image/png")


async def handle_info(request: web.Request) -> web.Response:
    require_control(request)
    config: Config = request.app[CONFIG_KEY]
    hub: Hub = request.app[HUB_KEY]
    return web.json_response(
        {
            "hostname": netinfo.local_hostname(),
            "port": config.port,
            "urls": netinfo.candidate_urls(config.port),
            "data_dir": str(config.data_dir),
            "current": hub.current_id,
            "clients": [
                {"id": c.id, "role": c.role, "since": c.connected_at}
                for c in hub.clients.values()
            ],
        }
    )


async def _read_body(request: web.Request, limit: int) -> bytes:
    """按块读完请求体。``StreamReader.read(n)`` 只保证「至多 n 字节」，
    大文件必须自己循环，否则会读成半截。"""
    chunks = []
    total = 0
    while True:
        chunk = await request.content.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=total)
        chunks.append(chunk)
    return b"".join(chunks)


async def handle_debug(request: web.Request) -> web.Response:
    """诊断模式下客户端上报的卡顿数据，直接打到服务端日志里。

    iPad 上看不到开发者工具，这样用户把 Mac 终端里的输出贴出来就够定位了。
    """
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        raise web.HTTPBadRequest()
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest()
    fields = {k: payload[k] for k in list(payload)[:20] if isinstance(k, str)}
    log.warning("[诊断] %s", json.dumps(fields, ensure_ascii=False)[:1000])
    return web.json_response({"ok": True})


async def handle_boards(request: web.Request) -> web.Response:
    require_control(request)
    hub: Hub = request.app[HUB_KEY]
    return web.json_response({"boards": hub.store.list_metas(), "current": hub.current_id})


_BLANK_THUMB = profile._png(3, 2, [bytearray(b"\xff" * 12) for _ in range(2)])


async def handle_thumb_get(request: web.Request) -> web.StreamResponse:
    require_control(request)  # 缩略图只有白板选择界面在用
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    path = hub.store.thumb_path(board_id)
    if not path.exists():
        thumb = await _doc_thumb(request.app, board_id)
        if thumb is not None:
            body, content_type = thumb
            return web.Response(
                body=body, content_type=content_type, headers={"Cache-Control": "no-cache"}
            )
        # 还没生成缩略图的白板返回一张空白图，界面上就是一块空白板。
        return web.Response(
            body=_BLANK_THUMB, content_type="image/png", headers={"Cache-Control": "no-store"}
        )
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def _doc_thumb(app: web.Application, board_id: str):
    """文档板没有上传过缩略图时，直接拿原件首页当封面。"""
    from . import docs

    hub: Hub = app[HUB_KEY]
    meta = hub.store.get_meta(board_id)
    if not meta or meta.get("kind") != "doc":
        return None
    path = hub.store.doc_path(board_id)
    if path is None:
        return None
    try:
        async with app[RENDER_KEY]:
            return await asyncio.to_thread(docs.render_page, path, 0, 420)
    except Exception as exc:  # noqa: BLE001 - 封面画不出来不影响选板
        log.warning("文档板封面渲染失败 %s：%s", board_id, exc)
        return None


async def handle_thumb_post(request: web.Request) -> web.Response:
    """Mac 端在切换 / 关闭白板时上传缩略图，用于无文字的白板选择界面。"""
    require_control(request)
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    if not hub.store.get_meta(board_id):
        raise web.HTTPNotFound()
    body = await _read_body(request, MAX_THUMB_BYTES)
    try:
        hub.store.save_thumb(board_id, body)
    except (ValueError, OSError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"ok": True})


# ------------------------------------------------------------- 文档板（beta）

async def handle_doc_upload(request: web.Request) -> web.Response:
    """上传一份 PDF / 图片，新建文档板并切过去。"""
    from . import docs

    require_control(request)  # 建板算管理操作
    hub: Hub = request.app[HUB_KEY]
    filename = request.query.get("name") or request.headers.get("X-Filename") or ""
    body = await _read_body(request, MAX_DOC_BYTES)
    try:
        meta = await asyncio.to_thread(hub.import_doc, body, filename)
    except docs.DocError as exc:
        log.warning("导入文档失败：%s", exc)
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 坏文件不该把服务端带崩
        log.exception("导入文档出错")
        raise web.HTTPBadRequest(text="文件读不出来") from exc
    await hub.broadcast({"t": "switch", **hub.snapshot()})
    return web.json_response({"board": meta})


async def handle_doc_page(request: web.Request) -> web.StreamResponse:
    """渲染文档板的某一页。原件不会变，所以可以长期缓存。"""
    from . import docs

    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    meta = hub.store.get_meta(board_id)
    if not meta or meta.get("kind") != "doc":
        raise web.HTTPNotFound()
    path = hub.store.doc_path(board_id)
    if path is None:
        raise web.HTTPNotFound(text="原件已丢失")
    try:
        index = int(request.match_info["index"])
        width = int(request.query.get("w", "1200"))
    except ValueError:
        raise web.HTTPBadRequest()

    width = max(docs.MIN_RENDER_WIDTH, min(docs.MAX_RENDER_WIDTH, width))
    etag = f'"{board_id}-{index}-{width}"'
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers={"ETag": etag})

    try:
        async with request.app[RENDER_KEY]:
            body, content_type = await asyncio.to_thread(docs.render_page, path, index, width)
    except docs.DocError as exc:
        raise web.HTTPNotFound(text=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("渲染文档页失败")
        raise web.HTTPInternalServerError(text="渲染失败") from exc
    return web.Response(
        body=body,
        content_type=content_type,
        headers={"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable"},
    )


async def handle_doc_export(request: web.Request) -> web.StreamResponse:
    """把笔迹合进原件导出；PDF 走矢量叠加，图片按原分辨率栅格化。"""
    from . import docs

    require_control(request)  # 导出能把任意一块白板整份取走
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    meta = hub.store.get_meta(board_id)
    if not meta or meta.get("kind") != "doc":
        raise web.HTTPNotFound()
    path = hub.store.doc_path(board_id)
    if path is None:
        raise web.HTTPNotFound(text="原件已丢失")

    strokes = hub.strokes_of(board_id)
    name = docs.default_export_name(meta)
    out_dir = Path(tempfile.mkdtemp(prefix="whiteboard-export-"))
    out = out_dir / name
    try:
        await asyncio.to_thread(docs.export, path, strokes, out)
        body = out.read_bytes()
    except docs.DocError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("导出文档失败")
        raise web.HTTPInternalServerError(text="导出失败") from exc
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    quoted = urllib.parse.quote(name)
    log.info("导出文档板 %s：%d 笔，%.1f KB", board_id, len(strokes), len(body) / 1024)
    return web.Response(
        body=body,
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------- WebSocket

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=MAX_WS_MESSAGE)
    await ws.prepare(request)

    hub: Hub = request.app[HUB_KEY]
    # 权限在握手之前就按对端地址定下来，之后客户端说什么都改不了它。
    session = _Session(request.app, ws, control=may_control(request))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue
            if isinstance(payload, dict):
                await session.dispatch(payload)
    except asyncio.CancelledError:
        raise
    finally:
        await session.close()
        log.info("连接断开：%s（在线 %d）", session.client_id, len(hub.clients))
    return ws


class _Session:
    """一条 WebSocket 连接的协议处理。"""

    def __init__(self, app: web.Application, ws: web.WebSocketResponse, control: bool = True):
        self.app = app
        self.hub: Hub = app[HUB_KEY]
        self.ws = ws
        self.control = control
        self.client = None
        self.client_id = "-"

    def _may_manage(self) -> bool:
        """能不能动白板管理。既要是 Mac 那套界面，也要来自本机。"""
        return self.client.role == "mac" and self.client.control

    async def dispatch(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == "hello":
            await self._hello(msg)
            return
        if self.client is None:
            return  # 未握手前只接受 hello
        handler = {
            "op": self._op,
            "live": self._live,
            "sel": self._select,
            "newboard": self._new_board,
            "delboard": self._del_board,
            "rename": self._rename,
            "ping": self._ping,
        }.get(kind)
        if handler is not None:
            await handler(msg)

    # ------------------------------------------------------------- 握手

    async def _hello(self, msg: Dict[str, Any]) -> None:
        if self.client is not None:
            return
        from .hub import Client  # 局部导入避免循环

        client_id = msg.get("client")
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
            client_id = models.new_id()
        role = detect_role("", msg.get("role"))
        self.client = Client(client_id, self.ws, role, control=self.control)
        self.client_id = client_id
        self.hub.clients[client_id] = self.client
        log.info(
            "连接建立：%s（%s%s，在线 %d）",
            client_id,
            role,
            "" if self.client.control else "，只读管理",
            len(self.hub.clients),
        )

        board_id = msg.get("board")
        since = msg.get("since")
        runtime = self.hub.board()
        config: Config = self.app[CONFIG_KEY]
        common = {
            "role": role,
            "client": client_id,
            "info": {
                "hostname": netinfo.local_hostname(),
                "port": config.port,
                "urls": netinfo.candidate_urls(config.port),
                "data_dir": str(config.data_dir),
            },
            "boards": self.hub.store.list_metas(),
            "board": dict(runtime.meta),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
        }

        # 断线重连：白板没换、epoch 一致且历史够长时只补差量，避免整块白板重传。
        same_epoch = msg.get("epoch") == runtime.epoch
        if board_id == self.hub.current_id and same_epoch and isinstance(since, int) and since >= 0:
            ops = runtime.ops_since(since)
            if ops is not None:
                await self.client.send({"t": "sync", "ops": ops, **common})
                return

        await self.client.send({"t": "init", "strokes": runtime.stroke_list(), **common})

    # ------------------------------------------------------------- 操作

    async def _op(self, msg: Dict[str, Any]) -> None:
        raw = msg.get("op")
        runtime = self.hub.board()
        if isinstance(raw, dict) and raw.get("op") == "meta" and not self._may_manage():
            raw = None  # 背景 / 名字这些只在本机的 Mac 界面里调整
        op = runtime.apply(raw) if raw is not None else None
        cid = msg.get("cid")
        if op is None:
            # 重复或无效操作：仍然回执，客户端好把它从待发队列里移除。
            await self.client.send({"t": "ack", "cid": cid, "seq": runtime.seq})
            return
        await self.client.send({"t": "ack", "cid": cid, "seq": op["seq"], "op": op})
        await self.hub.broadcast(
            {"t": "op", "op": op, "src": self.client.id, "board": self.hub.current_id},
            exclude=self.client.id,
        )

    async def _live(self, msg: Dict[str, Any]) -> None:
        """书写过程中的实时点，不落盘，只转发给对端。"""
        msg = dict(msg)
        msg["src"] = self.client.id
        await self.hub.broadcast(msg, exclude=self.client.id)

    # --------------------------------------------------------- 白板管理

    async def _broadcast_switch(self) -> None:
        snapshot = self.hub.snapshot()
        await self.hub.broadcast({"t": "switch", **snapshot})

    async def _select(self, msg: Dict[str, Any]) -> None:
        if not self._may_manage():
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.select_board(board_id):
            await self._broadcast_switch()

    async def _new_board(self, msg: Dict[str, Any]) -> None:
        if not self._may_manage():
            return
        kind = msg.get("kind")
        self.hub.create_board(kind if kind in models.KINDS else "board")
        await self._broadcast_switch()

    async def _rename(self, msg: Dict[str, Any]) -> None:
        """给白板改名。改的可能不是当前这块，所以只广播列表，不走整块 switch。"""
        if not self._may_manage():
            return
        board_id = msg.get("board")
        name = msg.get("name")
        if not isinstance(board_id, str) or not isinstance(name, str):
            return
        if not self.hub.rename_board(board_id, name):
            return
        await self.hub.broadcast(
            {
                "t": "boards",
                "boards": self.hub.store.list_metas(),
                "board": dict(self.hub.board().meta),
            }
        )

    async def _del_board(self, msg: Dict[str, Any]) -> None:
        if not self._may_manage():
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.delete_board(board_id):
            await self._broadcast_switch()

    async def _ping(self, msg: Dict[str, Any]) -> None:
        await self.client.send({"t": "pong", "ts": msg.get("ts")})

    async def close(self) -> None:
        if self.client is not None:
            # 同一个客户端 id 可能已经用新连接重新登记（iOS 唤醒后旧 socket 才断开），
            # 只有登记的还是自己时才注销，否则会把新连接从广播名单里摘掉。
            if self.hub.clients.get(self.client.id) is self.client:
                self.hub.clients.pop(self.client.id, None)
            self.client = None


# --------------------------------------------------------------------- 应用

@web.middleware
async def revalidate_static(request: web.Request, handler):
    """前端文件必须每次回源确认。

    否则 Safari 会按启发式规则把 js / css 缓存住，Mac 上更新了代码，
    iPad 的主屏图标点开还是旧的。局域网里多一次 304 的开销可以忽略。
    """
    response = await handler(request)
    if request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def create_app(config: Config, store: Optional[BoardStore] = None) -> web.Application:
    store = store or BoardStore(config.data_dir)
    app = web.Application(
        client_max_size=MAX_THUMB_BYTES + 4096, middlewares=[revalidate_static]
    )
    app[CONFIG_KEY] = config
    app[STORE_KEY] = store
    app[HUB_KEY] = Hub(store)
    app[RENDER_KEY] = asyncio.Semaphore(RENDER_LIMIT)

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/profile.mobileconfig", handle_profile)
    app.router.add_get("/icon.png", handle_icon)
    app.router.add_get("/api/info", handle_info)
    app.router.add_get("/api/boards", handle_boards)
    app.router.add_post("/api/debug", handle_debug)
    app.router.add_get("/api/thumb/{board_id}", handle_thumb_get)
    app.router.add_post("/api/thumb/{board_id}", handle_thumb_post)
    app.router.add_post("/api/doc", handle_doc_upload)
    app.router.add_get("/api/doc/{board_id}/{index}", handle_doc_page)
    app.router.add_get("/api/export/{board_id}", handle_doc_export)
    app.router.add_static("/static/", WEB_DIR / "static", name="static")

    async def _on_startup(_app: web.Application) -> None:
        _app[HUB_KEY].start_autosave()

    async def _on_shutdown(_app: web.Application) -> None:
        # 必须在等待处理协程之前断开长连接，否则关服务要干等到超时
        await _app[HUB_KEY].close_clients()

    async def _on_cleanup(_app: web.Application) -> None:
        await _app[HUB_KEY].stop()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
    return app

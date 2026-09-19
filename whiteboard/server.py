"""aiohttp 服务端：静态页面、描述文件下载与 WebSocket 同步通道。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import WSMsgType, web

from . import models, netinfo, profile
from .config import Config
from .hub import Hub
from .store import BoardStore

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
MAX_WS_MESSAGE = 8 * 1024 * 1024
MAX_THUMB_BYTES = 512 * 1024
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# aiohttp 的类型安全键，避免字符串键的命名冲突。
CONFIG_KEY: "web.AppKey[Config]" = web.AppKey("config")
STORE_KEY: "web.AppKey[BoardStore]" = web.AppKey("store")
HUB_KEY: "web.AppKey[Hub]" = web.AppKey("hub")


def detect_role(user_agent: str, override: Optional[str] = None) -> str:
    """区分访问设备：Mac 端拿到完整 GUI，iPad 端只有书写界面。"""
    if override in ("mac", "ipad"):
        return override
    ua = (user_agent or "").lower()
    if "ipad" in ua or "iphone" in ua or "ipod" in ua:
        return "ipad"
    return "mac"


# --------------------------------------------------------------------- 页面

async def handle_index(request: web.Request) -> web.Response:
    role = detect_role(request.headers.get("User-Agent", ""), request.query.get("role"))
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
    hub: Hub = request.app[HUB_KEY]
    return web.json_response({"boards": hub.store.list_metas(), "current": hub.current_id})


_BLANK_THUMB = profile._png(3, 2, [bytearray(b"\xff" * 12) for _ in range(2)])


async def handle_thumb_get(request: web.Request) -> web.StreamResponse:
    hub: Hub = request.app[HUB_KEY]
    path = hub.store.thumb_path(request.match_info["board_id"])
    if not path.exists():
        # 还没生成缩略图的白板返回一张空白图，界面上就是一块空白板。
        return web.Response(
            body=_BLANK_THUMB, content_type="image/png", headers={"Cache-Control": "no-store"}
        )
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def handle_thumb_post(request: web.Request) -> web.Response:
    """Mac 端在切换 / 关闭白板时上传缩略图，用于无文字的白板选择界面。"""
    hub: Hub = request.app[HUB_KEY]
    board_id = request.match_info["board_id"]
    if not hub.store.get_meta(board_id):
        raise web.HTTPNotFound()
    body = await request.content.read(MAX_THUMB_BYTES + 1)
    if len(body) > MAX_THUMB_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=MAX_THUMB_BYTES, actual_size=len(body)
        )
    try:
        hub.store.save_thumb(board_id, body)
    except (ValueError, OSError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- WebSocket

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=MAX_WS_MESSAGE)
    await ws.prepare(request)

    hub: Hub = request.app[HUB_KEY]
    session = _Session(request.app, ws)
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

    def __init__(self, app: web.Application, ws: web.WebSocketResponse):
        self.app = app
        self.hub: Hub = app[HUB_KEY]
        self.ws = ws
        self.client = None
        self.client_id = "-"

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
        self.client = Client(client_id, self.ws, role)
        self.client_id = client_id
        self.hub.clients[client_id] = self.client
        log.info("连接建立：%s（%s，在线 %d）", client_id, role, len(self.hub.clients))

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
        if isinstance(raw, dict) and raw.get("op") == "meta" and self.client.role != "mac":
            raw = None  # 白板尺寸 / 背景只在 Mac 端调整
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
        if self.client.role != "mac":
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.select_board(board_id):
            await self._broadcast_switch()

    async def _new_board(self, msg: Dict[str, Any]) -> None:
        if self.client.role != "mac":
            return
        self.hub.create_board()
        await self._broadcast_switch()

    async def _del_board(self, msg: Dict[str, Any]) -> None:
        if self.client.role != "mac":
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

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/profile.mobileconfig", handle_profile)
    app.router.add_get("/icon.png", handle_icon)
    app.router.add_get("/api/info", handle_info)
    app.router.add_get("/api/boards", handle_boards)
    app.router.add_post("/api/debug", handle_debug)
    app.router.add_get("/api/thumb/{board_id}", handle_thumb_get)
    app.router.add_post("/api/thumb/{board_id}", handle_thumb_post)
    app.router.add_static("/static/", WEB_DIR / "static", name="static")

    async def _on_startup(_app: web.Application) -> None:
        _app[HUB_KEY].start_autosave()

    async def _on_cleanup(_app: web.Application) -> None:
        await _app[HUB_KEY].stop()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app

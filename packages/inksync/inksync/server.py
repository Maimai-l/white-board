"""WebSocket 同步协议（v2）的服务端、多个空间的管理，以及前端文件的路由。

    from aiohttp import web
    from inksync import FileStorage, Hub, mount, serve_sdk

    app = web.Application()
    mount(app, Hub(FileStorage("data")), path="/ws")
    serve_sdk(app, prefix="/inksync/")
    web.run_app(app, port=8900)

协议见 docs/design/inksync-2-interface.zh-CN.md 与 docs/protocol.md。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from aiohttp import WSMsgType, web

from . import __version__, models
from .hub import Conn, Hub
from .policy import Principal, local_principal

log = logging.getLogger(__name__)

PROTOCOL = 2
# 单条消息上限：一次粘贴或恢复大量笔画时也够用
MAX_WS_MESSAGE = 8 * 1024 * 1024
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
CORE_MESSAGES = frozenset({"hello", "open", "op", "live", "ping", "unlock"})
WEB_DIR = Path(__file__).resolve().parent / "web"
SPACE_IDLE_CLOSE = 600.0

Authenticate = Callable[[web.Request], Principal]
Info = Callable[[web.Request], Dict[str, Any]]


def build_id() -> str:
    """前端与服务端核对版本用的构建号。使用者可以通过 ``inksync.server.BUILD`` 覆盖。"""
    return BUILD or __version__


BUILD: Optional[str] = None


def server_field() -> Dict[str, Any]:
    return {"name": "inksync", "version": __version__, "protocol": PROTOCOL, "build": build_id()}


# ---------------------------------------------------------------------- 空间


class Spaces:
    """多个空间。``factory(name)`` 在第一次请求某个空间时调用，返回 Hub 或 None（不存在）。"""

    def __init__(self, factory: Callable[[str], Optional[Hub]], idle_close: float = SPACE_IDLE_CLOSE):
        self.factory = factory
        self.idle_close = idle_close
        self._hubs: Dict[str, Hub] = {}
        self._idle_since: Dict[str, float] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def get(self, name: str) -> Optional[Hub]:
        if not isinstance(name, str) or not models.SPACE_RE.match(name):
            return None
        hub = self._hubs.get(name)
        if hub is None:
            hub = self.factory(name)
            if hub is None:
                return None
            hub.name = name
            self._hubs[name] = hub
            if self._running:
                hub.start()
        return hub

    def peek(self, name: str) -> Optional[Hub]:
        """已经建立的空间；不存在时不调用 factory。"""
        return self._hubs.get(name)

    def hubs(self) -> Dict[str, Hub]:
        return dict(self._hubs)

    async def close(self, name: str) -> None:
        hub = self._hubs.pop(name, None)
        self._idle_since.pop(name, None)
        if hub is None:
            return
        await hub.close_connections()
        await hub.stop()

    def start(self) -> None:
        self._running = True
        for hub in self._hubs.values():
            hub.start()
        if self._task is None and self.idle_close > 0:
            self._task = asyncio.get_running_loop().create_task(self._reap())

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(min(60.0, self.idle_close))
            stamp = time.monotonic()
            for name, hub in list(self._hubs.items()):
                if not hub.idle():
                    self._idle_since.pop(name, None)
                    continue
                since = self._idle_since.setdefault(name, stamp)
                if stamp - since >= self.idle_close and getattr(hub, "closable", True):
                    log.info("关闭闲置的空间 %r", name)
                    try:
                        await self.close(name)
                    except Exception:  # noqa: BLE001
                        log.exception("关闭空间 %r 出错", name)

    async def close_connections(self) -> None:
        for hub in self._hubs.values():
            await hub.close_connections()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for hub in list(self._hubs.values()):
            await hub.stop()
        self._running = False


class _Single(Spaces):
    """只有一个空间（名称为空串）。"""

    def __init__(self, hub: Hub):
        super().__init__(lambda name: hub if name == "" else None, idle_close=0)
        hub.closable = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- 协议


class Session:
    """一条 WebSocket 连接的协议处理。"""

    def __init__(self, spaces: Spaces, ws: web.WebSocketResponse, request: Any,
                 authenticate: Authenticate, info: Optional[Info]):
        self.spaces = spaces
        self.ws = ws
        self.request = request
        self.authenticate = authenticate
        self.info = info
        self.hub: Optional[Hub] = None
        self.conn: Optional[Conn] = None
        # 身份在连接建立时按请求确定，握手里客户端说什么都改不了它
        try:
            self.principal = authenticate(request)
        except Exception:  # noqa: BLE001 - 身份算不出来按匿名的其他设备处理
            log.exception("authenticate 出错")
            self.principal = Principal(id=None, local=False, address=getattr(request, "remote", "") or "")

    async def _refuse(self, reason: str, **extra: Any) -> None:
        try:
            await self.ws.send_json({"t": "error", "reason": reason, **extra})
            await self.ws.close()
        except (ConnectionResetError, RuntimeError):
            pass

    async def safe_dispatch(self, msg: Dict[str, Any]) -> None:
        """处理一条消息；出了任何没想到的错只记日志，连接不断。

        op 出错时照样回执：客户端的待发队列存在 IndexedDB 里，收不到回执就会在
        重连后原样重发。要是这条消息每次都让连接断开，客户端就会一直断、一直重发，
        刷新页面也出不来。
        """
        try:
            await self.dispatch(msg)
        except Exception:  # noqa: BLE001 - 一条坏消息不能断开连接
            log.exception("处理消息出错（%s）：%.200s", self.conn.id if self.conn else "-",
                          json.dumps(msg, default=str))
            if msg.get("t") == "op" and self.conn is not None:
                board = msg.get("board") if isinstance(msg.get("board"), str) else self.conn.board
                await self.conn.send({"t": "ack", "cid": msg.get("cid"), "board": board, "seq": 0,
                                      "rejected": "error"})

    async def dispatch(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == "hello":
            await self._hello(msg)
            return
        conn, hub = self.conn, self.hub
        if conn is None or hub is None:
            return  # 未握手前只接受 hello
        if kind == "op":
            await hub.handle_op(conn, msg.get("board"), msg.get("op"), msg.get("cid"))
        elif kind == "live":
            if conn.readonly or conn.board is None or hub.is_deleted(conn.board):
                return
            out = dict(msg)
            out["src"] = conn.id
            out.pop("board", None)
            await hub.broadcast_board(conn.board, out, exclude=conn.id)
        elif kind == "ping":
            await conn.send({"t": "pong", "ts": msg.get("ts")})
        elif kind == "open":
            await self._open(msg)
        elif kind == "unlock":
            if conn.board is None:
                return
            meta = hub.board_meta(conn.board)
            if meta is not None and hub.policy.can_unlock(conn.principal, meta):
                await hub.unlock(conn.board)
        else:
            handler = hub.handler(kind) if isinstance(kind, str) else None
            if handler is not None:
                await handler(conn, msg)

    async def _hello(self, msg: Dict[str, Any]) -> None:
        if self.conn is not None:
            return
        version = msg.get("v")
        space = msg.get("space", "")
        hub = self.spaces.get(space if isinstance(space, str) else "")
        if hub is None:
            await self._refuse("space")
            return
        legacy = version is None
        if legacy and not hub.accept_v1:
            await self._refuse("version", supported=[PROTOCOL])
            return
        if not legacy and version != PROTOCOL:
            await self._refuse("version", supported=[PROTOCOL])
            return
        if legacy and msg.get("pin") is not None:
            await self._refuse("pin")
            return

        client_id = msg.get("client")
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
            client_id = models.new_id()
        principal = self.principal
        device = msg.get("device") if isinstance(msg.get("device"), str) else ""
        if legacy:
            device = msg.get("role") if msg.get("role") in ("mac", "ipad") else "mac"
        conn = Conn(hub, client_id, self.ws, principal, request=self.request, device=device[:32],
                    readonly=msg.get("readonly") is True, legacy=legacy)

        follow = legacy or msg.get("follow") is True or msg.get("board") is None
        if follow:
            board_id = await hub.hello_handler(conn, msg) if hub.hello_handler else None
            if board_id is None:
                await self._refuse("board")
                return
        else:
            board_id = msg.get("board")
            reason = await hub.admit(conn, board_id, msg.get("create"))
            if reason is not None:
                await self._refuse(reason)
                return

        self.hub = hub
        self.conn = conn
        hub.attach(conn)
        log.info("连接建立：%s（空间 %r，%s%s，在线 %d）", client_id, hub.name, device or "-",
                 "，只读" if conn.readonly else "", len(hub.conns))
        extra = {
            "v": PROTOCOL,
            "server": server_field(),
            "client": client_id,
            "info": dict(self.info(self.request)) if self.info else {},
        }
        await hub.open(conn, board_id, "init", msg.get("since"), msg.get("epoch"), msg.get("board"), extra)

    async def _open(self, msg: Dict[str, Any]) -> None:
        conn, hub = self.conn, self.hub
        board_id = msg.get("board")
        readonly = msg.get("readonly") is True
        previous = conn.readonly
        conn.readonly = readonly
        reason = await hub.admit(conn, board_id, msg.get("create"))
        if reason is not None:
            conn.readonly = previous
            await conn.send({"t": "error", "reason": reason, "board": board_id})
            return
        await hub.open(conn, board_id, "init", msg.get("since"), msg.get("epoch"), msg.get("board"))

    async def close(self) -> None:
        if self.conn is not None and self.hub is not None:
            self.hub.detach(self.conn)
            log.info("连接断开：%s（在线 %d）", self.conn.id, len(self.hub.conns))
        self.conn = None


async def serve_ws(request: web.Request, spaces: Spaces, authenticate: Authenticate,
                   info: Optional[Info]) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=MAX_WS_MESSAGE)
    await ws.prepare(request)
    session = Session(spaces, ws, request, authenticate, info)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue
            if isinstance(payload, dict):
                await session.safe_dispatch(payload)
    except asyncio.CancelledError:
        raise
    finally:
        await session.close()
    return ws


def mount(
    app: web.Application,
    target: Union[Hub, Spaces],
    path: str = "/ws",
    authenticate: Optional[Authenticate] = None,
    info: Optional[Info] = None,
) -> Spaces:
    """把同步协议挂到 ``app`` 的 ``path`` 上，并接管自动保存与关闭。返回空间集合。

    ``target`` 为一个 Hub（单个空间，名称为空串）或 :class:`Spaces`。
    ``authenticate`` 默认只判断请求是否来自本机。
    """
    spaces = target if isinstance(target, Spaces) else _Single(target)
    auth = authenticate or local_principal

    async def handle(request: web.Request) -> web.WebSocketResponse:
        return await serve_ws(request, spaces, auth, info)

    app.router.add_get(path, handle)

    async def _on_startup(_app: web.Application) -> None:
        spaces.start()
        if isinstance(target, Hub):
            spaces.get("")

    async def _on_shutdown(_app: web.Application) -> None:
        # 必须在等待处理协程之前断开长连接，否则关服务要干等到超时
        await spaces.close_connections()

    async def _on_cleanup(_app: web.Application) -> None:
        await spaces.stop()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)
    return spaces


# ---------------------------------------------------------------------- 前端文件


def serve_sdk(app: web.Application, prefix: str = "/inksync/") -> None:
    """在 ``prefix`` 下提供前端文件，入口为 ``<prefix>inkpad.js``。

    ``version.js`` 由服务端生成，写入构建号，前端据此判断页面是否过期。"""
    if not prefix.startswith("/") or not prefix.endswith("/"):
        raise ValueError("prefix 必须以 / 开头和结尾")
    root = WEB_DIR.resolve()

    async def version_js(_request: web.Request) -> web.Response:
        body = (
            f"export const VERSION = {json.dumps(__version__)};\n"
            f"export const BUILD = {json.dumps(build_id())};\n"
            f"export const PROTOCOL = {PROTOCOL};\n"
        )
        return web.Response(text=body, content_type="text/javascript", headers={"Cache-Control": "no-cache"})

    async def handle(request: web.Request) -> web.StreamResponse:
        tail = request.match_info.get("tail", "")
        target = (root / tail).resolve()
        if root not in target.parents or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target, headers={"Cache-Control": "no-cache"})

    app.router.add_get(f"{prefix}version.js", version_js)
    app.router.add_get(prefix + "{tail:.+}", handle)

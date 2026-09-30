"""WebSocket 同步协议的服务端（docs/protocol.md），可以挂到任何 aiohttp 应用上。

    from aiohttp import web
    from inksync import BoardStore, Hub, mount

    app = web.Application()
    mount(app, Hub(BoardStore("data")))      # /ws
    web.run_app(app, port=8848)

权限默认只给本机（manage、settings、clear、export 全部），局域网上的其他设备只能写字；
传入 ``permissions`` 可以按请求放开。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, FrozenSet, Optional

from aiohttp import WSMsgType, web

from . import __version__, models, netinfo
from .hub import Client, Hub

log = logging.getLogger(__name__)

# 单条消息上限：一次粘贴或恢复大量笔画时也够用
MAX_WS_MESSAGE = 8 * 1024 * 1024
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
# 连接可以拥有的权限，见 docs/protocol.md「权限」
PERMISSIONS = ("manage", "settings", "clear", "export")


def detect_role(user_agent: str, override: Optional[str] = None) -> str:
    """区分访问设备：Mac 端拿到完整 GUI，iPad 端只有书写界面。"""
    if override in ("mac", "ipad"):
        return override
    ua = (user_agent or "").lower()
    if "ipad" in ua or "iphone" in ua or "ipod" in ua:
        return "ipad"
    return "mac"



def local_only(request: web.Request) -> FrozenSet[str]:
    """默认的权限：本机全部给，其他设备只能写字（见 netinfo.is_local_request）。"""
    return frozenset(PERMISSIONS) if netinfo.is_local_request(request) else frozenset()


# 固定连接能不能打开或新建某块白板：(权限, 应用名, 白板 id, 已有的 meta 或 None) -> bool
PinPolicy = Callable[[FrozenSet[str], str, str, Optional[Dict[str, Any]]], bool]


def default_pin_policy(allowed: FrozenSet[str], app: str, board_id: str, existing: Optional[Dict[str, Any]]) -> bool:
    """应用自己的白板（或新建）只要能写字就行；用户自己的白板要「管理白板」权限。"""
    return existing is None or bool(existing.get("app")) or "manage" in allowed


def websocket_handler(
    get_hub: Callable[[web.Request], Hub],
    permissions: Optional[Callable[[web.Request], FrozenSet[str]]] = None,
    info: Optional[Callable[[web.Request], Dict[str, Any]]] = None,
    pin_policy: Optional[PinPolicy] = None,
):
    """返回处理 ``/ws`` 的 aiohttp 处理函数。

    ``get_hub`` 取这条请求对应的 Hub；``permissions`` 按请求决定这条连接的权限
    （默认 :func:`local_only`）；``info`` 返回握手时发给客户端的 ``info`` 字段（默认只有
    版本号）；``pin_policy`` 决定固定连接能打开哪些白板（默认 :func:`default_pin_policy`）。
    """
    decide = permissions or local_only

    async def handle_ws(request: web.Request) -> web.WebSocketResponse:
        details = info(request) if info else {"version": __version__}
        return await serve_ws(request, get_hub(request), frozenset(decide(request)), details, pin_policy)

    return handle_ws


async def serve_ws(
    request: web.Request,
    hub: Hub,
    allowed: FrozenSet[str],
    info: Dict[str, Any],
    pin_policy: Optional[PinPolicy] = None,
) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=MAX_WS_MESSAGE)
    await ws.prepare(request)

    # 权限在握手之前就按对端地址定下来，客户端说什么都改不了它；Mac 上改了权限设置
    # 时由 Hub.refresh_permissions 按同一个请求重新计算。
    session = Session(hub, ws, allowed=allowed, info=info, request=request, pin_policy=pin_policy)
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
        log.info("连接断开：%s（在线 %d）", session.client_id, len(hub.clients))
    return ws


class Session:
    """一条 WebSocket 连接的协议处理，见 docs/protocol.md。"""

    def __init__(
        self,
        hub: Hub,
        ws: web.WebSocketResponse,
        allowed: FrozenSet[str] = frozenset(PERMISSIONS),
        info: Optional[Dict[str, Any]] = None,
        request: Any = None,
        pin_policy: Optional[PinPolicy] = None,
    ):
        self.hub = hub
        self.ws = ws
        self.allowed = allowed
        self.info = info if info is not None else {"version": __version__}
        self.request = request
        self.pin_policy = pin_policy or default_pin_policy
        self.client = None
        self.client_id = "-"

    def _may(self, permission: str) -> bool:
        """能不能做这件事，只看这条连接拿到的权限。

        权限在连接建立时按 TCP 对端地址定死（见 ``permissions``），握手里报的
        role 不参与判断——那个客户端想填什么填什么。以前这里对「管理白板」和
        「设置白板」多一道 ``role == "mac"``，因为那两项当时只有 Mac 那套界面里
        有入口；现在触摸设备的右上角也有这一组，再拦着就是 Mac 放开了权限、
        iPad 上点了却没反应。
        """
        return permission in self.client.allowed

    async def safe_dispatch(self, msg: Dict[str, Any]) -> None:
        """处理一条消息；出了任何没想到的错只记日志，连接不断。

        op 出错时照样回执：客户端的待发队列存在 IndexedDB 里，收不到回执就会在
        重连后原样重发。要是这条消息每次都让连接断开，客户端就会一直断、一直重发，
        刷新页面也出不来。
        """
        try:
            await self.dispatch(msg)
        except Exception:  # noqa: BLE001 - 一条坏消息不能断开连接
            log.exception("处理消息出错（%s）：%.200s", self.client_id, json.dumps(msg, default=str))
            if msg.get("t") == "op" and self.client is not None:
                try:
                    seq = self.hub.board(self.hub.board_of(self.client)).seq
                except Exception:  # noqa: BLE001 - 出错的可能正是读白板这一步
                    seq = 0
                await self.client.send({"t": "ack", "cid": msg.get("cid"), "seq": seq})

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
            "folder": self._folder,
            "order": self._order,
            "newfolder": self._new_folder,
            "delfolder": self._del_folder,
            "renamefolder": self._rename_folder,
            "unlock": self._unlock,
            "ping": self._ping,
        }.get(kind)
        if handler is not None:
            await handler(msg)

    # ------------------------------------------------------------- 握手

    async def _hello(self, msg: Dict[str, Any]) -> None:
        if self.client is not None:
            return

        client_id = msg.get("client")
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
            client_id = models.new_id()
        role = detect_role("", msg.get("role"))
        pinned = None
        created = False
        if msg.get("pin") is not None:
            pinned, created = self._pin(msg.get("pin"))
            if pinned is None:
                await self.ws.send_json({"t": "error", "reason": "pin"})
                await self.ws.close()
                return
        self.client = Client(client_id, self.ws, role, allowed=self.allowed, pinned=pinned, request=self.request)
        self.client_id = client_id
        self.hub.clients[client_id] = self.client
        log.info(
            "连接建立：%s（%s%s%s，在线 %d）",
            client_id,
            role,
            "" if self.client.allowed else "，只能写字",
            f"，固定在 {pinned}" if pinned else "",
            len(self.hub.clients),
        )

        board_id = msg.get("board")
        since = msg.get("since")
        current = self.hub.board_of(self.client)
        runtime = self.hub.board(current)
        common = {
            "role": role,
            "client": client_id,
            # 服务端信息（版本、地址等）由挂载方提供，见 websocket_handler
            "info": dict(self.info),
            "boards": self.hub.store.list_metas(),
            "folders": self.hub.store.folders(),
            "board": dict(runtime.meta),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
            # 读不全的白板以只读方式打开，客户端据此提示并停止书写
            "locked": runtime.problem,
        }

        # 断线重连：白板没换、epoch 一致且历史够长时只补差量，避免整块白板重传。
        same_epoch = msg.get("epoch") == runtime.epoch
        if board_id == current and same_epoch and isinstance(since, int) and since >= 0:
            ops = runtime.ops_since(since)
            if ops is not None:
                await self.client.send(self.hub.visible(self.client, {"t": "sync", "ops": ops, **common}))
                return

        await self.client.send(self.hub.visible(self.client, {"t": "init", "strokes": runtime.stroke_list(), **common}))
        if created:
            # 新建的白板要出现在其他设备的白板列表里
            await self._broadcast_boards()

    # ------------------------------------------------------------- 操作

    def _pin(self, raw: Any) -> "tuple[Optional[str], bool]":
        """握手里的 ``pin``：找到或新建指定的白板，返回 ``(id, 是否新建)``；不允许时 id 为 None。

        应用自己建的白板（meta 里有 ``app``）只要能写字就能打开；用户自己的白板
        要有「管理白板」权限，和在界面上切换白板相同。
        """
        if not isinstance(raw, dict):
            return None, False
        board_id, app = raw.get("board"), raw.get("app")
        if not isinstance(board_id, str) or not models.PINNED_ID_RE.match(board_id):
            return None, False
        if not isinstance(app, str) or not models.APP_RE.match(app):
            return None, False
        existing = self.hub.store.get_meta(board_id)
        if not self.pin_policy(self.allowed, app, board_id, existing):
            log.warning("拒绝固定到白板 %s（应用 %s）", board_id, app)
            return None, False
        name = raw.get("name") if isinstance(raw.get("name"), str) else ""
        folder = raw.get("folder") if isinstance(raw.get("folder"), str) else ""
        kind = raw.get("kind") if isinstance(raw.get("kind"), str) else "board"
        try:
            self.hub.pin_board(board_id, app, name=name, kind=kind, folder=folder, underlay=raw.get("underlay"))
        except ValueError as exc:  # 应用的白板数量到了上限
            log.warning("拒绝新建白板 %s：%s", board_id, exc)
            return None, False
        return board_id, existing is None

    def _board_id(self) -> Optional[str]:
        """这条连接所在的白板；固定的白板已被删除时返回 None。"""
        board_id = self.hub.board_of(self.client)
        if self.client.pinned and self.hub.store.get_meta(board_id) is None:
            return None
        return board_id

    async def _op(self, msg: Dict[str, Any]) -> None:
        raw = msg.get("op")
        board_id = self._board_id()
        if board_id is None:
            # 固定的白板已被删除：回执但不应用，否则会把一块已删除的白板重新写回磁盘
            await self.client.send({"t": "ack", "cid": msg.get("cid"), "seq": 0})
            return
        runtime = self.hub.board(board_id)
        if isinstance(raw, dict):
            kind = raw.get("op")
            if kind == "meta" and not self._may("settings"):
                raw = None  # 背景这些属于白板设置
            elif kind == "clear" and not self._may("clear") and not runtime.meta.get("app"):
                raw = None  # 一下把整块白板抹掉，单独一项权限；应用自己的白板（例如一道题）不受限
        op = runtime.apply(raw) if raw is not None else None
        cid = msg.get("cid")
        if op is None:
            # 重复或无效操作：仍然回执，客户端好把它从待发队列里移除。
            await self.client.send({"t": "ack", "cid": cid, "seq": runtime.seq})
            return
        await self.client.send({"t": "ack", "cid": cid, "seq": op["seq"], "op": op})
        await self.hub.broadcast_board(
            board_id,
            {"t": "op", "op": op, "src": self.client.id, "board": board_id},
            exclude=self.client.id,
        )

    async def _live(self, msg: Dict[str, Any]) -> None:
        """书写过程中的实时点，不落盘，只转发给看着同一块白板的连接。"""
        board_id = self._board_id()
        if board_id is None:
            return
        msg = dict(msg)
        msg["src"] = self.client.id
        await self.hub.broadcast_board(board_id, msg, exclude=self.client.id)

    # --------------------------------------------------------- 白板管理

    async def _broadcast_switch(self) -> None:
        snapshot = self.hub.snapshot()
        await self.hub.broadcast({"t": "switch", **snapshot})

    async def _select(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.select_board(board_id):
            await self._broadcast_switch()

    async def _new_board(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        kind = msg.get("kind")
        # 在某个文件夹里按的「新建」，新白板就落在那个文件夹里
        folder = models.sanitize_folder(msg.get("folder"))
        meta = self.hub.create_board(kind if kind in models.KINDS else "board")
        if folder and folder in self.hub.store.folders():
            self.hub.move_board(meta["id"], folder)
        await self._broadcast_switch()

    async def _rename(self, msg: Dict[str, Any]) -> None:
        """给白板改名。改的可能不是当前这块，所以只广播列表，不走整块 switch。"""
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        name = msg.get("name")
        if not isinstance(board_id, str) or not isinstance(name, str):
            return
        if not self.hub.rename_board(board_id, name):
            return
        await self._broadcast_boards()

    async def _folder(self, msg: Dict[str, Any]) -> None:
        """把白板放进文件夹，或者移出来（``folder`` 是空串）。同样只广播列表。"""
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        folder = msg.get("folder")
        if not isinstance(board_id, str) or not isinstance(folder, str):
            return
        if not self.hub.move_board(board_id, folder):
            return
        await self._broadcast_boards()

    async def _order(self, msg: Dict[str, Any]) -> None:
        """拖动排序：界面送来的是当前这一层看到的顺序。"""
        if not self._may("manage"):
            return
        if not self.hub.reorder_boards(msg.get("ids")):
            return
        await self._broadcast_boards()

    async def _new_folder(self, msg: Dict[str, Any]) -> None:
        """新建一个空文件夹。空文件夹只有索引里有记录，所以必须单独有这一条。"""
        if not self._may("manage"):
            return
        if not self.hub.create_folder(msg.get("name")):
            return
        await self._broadcast_boards()

    async def _del_folder(self, msg: Dict[str, Any]) -> None:
        """删文件夹不删白板：里面的白板移到没归类那一段。"""
        if not self._may("manage"):
            return
        if not self.hub.delete_folder(msg.get("name")):
            return
        await self._broadcast_boards()

    async def _rename_folder(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        if not self.hub.rename_folder(msg.get("name"), msg.get("to")):
            return
        await self._broadcast_boards()

    async def _broadcast_boards(self) -> None:
        """只刷白板列表：改的可能不是当前这块，没必要让所有人重载笔画。"""
        await self.hub.broadcast(
            {
                "t": "boards",
                "boards": self.hub.store.list_metas(),
                "folders": self.hub.store.folders(),
                "board": dict(self.hub.board().meta),
            }
        )

    async def _del_board(self, msg: Dict[str, Any]) -> None:
        if not self._may("manage"):
            return
        board_id = msg.get("board")
        if isinstance(board_id, str) and self.hub.delete_board(board_id):
            await self._broadcast_switch()
            # 固定在这块白板上的连接不跟随切换，单独告诉它们白板已被删除
            pinned = [c for c in list(self.hub.clients.values()) if c.pinned == board_id]
            for client in pinned:
                await client.send({"t": "deleted", "board": board_id})

    async def _unlock(self, msg: Dict[str, Any]) -> None:
        """用户看过提示、确认要编辑一块读不全的白板。只对当前这块，要管理权限。"""
        if not self._may("manage"):
            return
        board_id = self._board_id()
        if board_id is None or not self.hub.unlock(board_id):
            return
        if board_id == self.hub.current_id:
            # 所有设备一起解除只读，发整块 switch：白板没换，客户端不会丢视角和撤销栈
            await self._broadcast_switch()
        pinned = [c for c in list(self.hub.clients.values()) if c.pinned == board_id]
        snapshot = self.hub.snapshot(board_id)
        for client in pinned:
            await client.send({"t": "switch", **snapshot})

    async def _ping(self, msg: Dict[str, Any]) -> None:
        await self.client.send({"t": "pong", "ts": msg.get("ts")})

    async def close(self) -> None:
        if self.client is not None:
            # 同一个客户端 id 可能已经用新连接重新登记（iOS 唤醒后旧 socket 才断开），
            # 只有登记的还是自己时才注销，否则会把新连接从广播名单里摘掉。
            if self.hub.clients.get(self.client.id) is self.client:
                self.hub.clients.pop(self.client.id, None)
            self.client = None


def mount(
    app: web.Application,
    hub: Hub,
    path: str = "/ws",
    permissions: Optional[Callable[[web.Request], FrozenSet[str]]] = None,
    info: Optional[Callable[[web.Request], Dict[str, Any]]] = None,
    pin_policy: Optional[PinPolicy] = None,
) -> None:
    """把同步协议挂到 ``app`` 的 ``path`` 上，并接管 Hub 的自动保存与关闭。

    权限默认只给本机（:func:`local_only`）。之后改了权限规则，调用
    ``hub.refresh_permissions(lambda client: permissions(client.request))`` 让已连接的设备生效。
    """
    app.router.add_get(path, websocket_handler(lambda _request: hub, permissions, info, pin_policy))

    async def _on_startup(_app: web.Application) -> None:
        hub.start_autosave()

    async def _on_shutdown(_app: web.Application) -> None:
        # 必须在等待处理协程之前断开长连接，否则关服务要干等到超时
        await hub.close_clients()

    async def _on_cleanup(_app: web.Application) -> None:
        await hub.stop()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    app.on_cleanup.append(_on_cleanup)

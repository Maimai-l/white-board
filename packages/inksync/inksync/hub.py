"""实时同步的核心：操作日志、广播与自动保存。

设计要点：

* **本地优先**。客户端先在本地画，再把操作发给服务端；服务端只负责编号、
  转发与持久化，不参与绘制判断，因此本地书写不会被网络拖慢。
* **单调序号**。每个被接受的操作拿到一个递增 ``seq``；客户端记住自己收到
  的最后一个 ``seq``，断线重连时带上它，服务端补发缺失的操作；缺口太大
  （超出历史窗口）则直接补发整块白板。
* **两种连接**。默认的连接「跟随」当前白板：Mac 端选择白板，iPad 跟着切换，
  符合点对点的使用方式。握手时指定了白板的连接是「固定」的：只收发那一块白板，
  不随切换改变。其他应用（例如刷题页面）嵌入手写板时用这种连接，每道题一块白板。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, FrozenSet, List, Optional

from . import models
from .store import BoardStore

log = logging.getLogger(__name__)

# 保留的操作历史条数，决定断线多久之后需要全量补齐。
OPS_HISTORY = 4000
# 每个应用最多能通过固定连接建多少块白板，防止被不停地新建
MAX_APP_BOARDS = 5000
AUTOSAVE_INTERVAL = 3.0


def _list(value: Any) -> List[Any]:
    """线上数据里本该是列表的字段：不是列表就当空的，不能让切片或遍历抛错。"""
    return value if isinstance(value, list) else []


class BoardRuntime:
    """一块白板在内存中的状态。

    ``problem`` 不为空时这块白板是**锁住的**：磁盘上的文件没能完整读出来
    （损坏、个别笔画解不开、或者是更新的版本写的）。锁住的白板照常显示读得出来
    的内容，但不接受笔画上的任何修改，自动保存也不写它——写回就会用残缺的内容
    盖掉原件。改名、归类只改文件里的 meta，其余内容原样搬过去（store.edit_meta）。
    用户确认之后可以解锁（见 :meth:`Hub.unlock`），解锁前先备份原文件。
    """

    def __init__(
        self,
        meta: Dict[str, Any],
        strokes: List[Dict[str, Any]],
        problem: Optional[Dict[str, Any]] = None,
    ):
        self.meta = meta
        self.problem = problem
        # 每次把白板载入内存都换一个 epoch：服务端重启后序号从头开始，
        # 旧客户端拿着重启前的序号来续传会被识别出来，改发整块白板。
        self.epoch = models.new_id()
        self.strokes: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
            (s["id"], s) for s in strokes
        )
        self.seq = 0
        self.ops: Deque[Dict[str, Any]] = deque(maxlen=OPS_HISTORY)
        self.next_n = max((s.get("n", 0) for s in strokes), default=-1) + 1
        self.dirty = False

    @property
    def locked(self) -> bool:
        return self.problem is not None

    # ------------------------------------------------------------- 操作应用

    def _record(self, op: Dict[str, Any]) -> Dict[str, Any]:
        self.seq += 1
        op["seq"] = self.seq
        self.ops.append(op)
        self.dirty = True
        self.meta["updated"] = models.now()
        return op

    def _insert(self, stroke: Dict[str, Any], keep_order: bool) -> Dict[str, Any]:
        if keep_order and "n" in stroke:
            self.next_n = max(self.next_n, stroke["n"] + 1)
        else:
            stroke["n"] = self.next_n
            self.next_n += 1
        self.strokes[stroke["id"]] = stroke
        return stroke

    def apply(self, raw: Any) -> Optional[Dict[str, Any]]:
        """校验并应用一个操作，返回带 ``seq`` 的规范化操作；非法或空操作返回 None。"""
        if not isinstance(raw, dict) or self.locked:
            return None
        kind = raw.get("op")

        if kind in ("add", "restore"):
            keep_order = kind == "restore"
            accepted: List[Dict[str, Any]] = []
            for item in _list(raw.get("strokes"))[:2000]:
                stroke = models.sanitize_stroke(item)
                if stroke is None or stroke["id"] in self.strokes:
                    continue  # 重复 id 直接忽略，重连重发时不会画两遍
                accepted.append(self._insert(stroke, keep_order))
            if not accepted:
                return None
            return self._record({"op": "add", "strokes": accepted})

        if kind == "remove":
            ids = [i for i in models.sanitize_ids(raw.get("ids")) if i in self.strokes]
            if not ids:
                return None
            for stroke_id in ids:
                self.strokes.pop(stroke_id, None)
            return self._record({"op": "remove", "ids": ids})

        if kind == "mask":
            # 遮罩发的是全量而不是增量：遮罩本来就小，全量在断线重连、乱序到达
            # 的情况下都不会错，不用管顺序。
            changed: List[Dict[str, Any]] = []
            for item in _list(raw.get("masks"))[:2000]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                stroke = self.strokes.get(item["id"])
                if stroke is None:
                    continue
                mask = models.sanitize_mask(item.get("m"))
                if mask == stroke.get("m", []):
                    continue
                if mask:
                    stroke["m"] = mask
                else:
                    stroke.pop("m", None)
                changed.append({"id": stroke["id"], "m": mask})
            if not changed:
                return None
            return self._record({"op": "mask", "masks": changed})

        if kind == "clear":
            if not self.strokes:
                return None
            self.strokes.clear()
            return self._record({"op": "clear"})

        if kind == "meta":
            incoming = raw.get("meta")
            if not isinstance(incoming, dict):
                return None
            merged = dict(self.meta)
            for key in ("background", "name"):
                if key in incoming:
                    merged[key] = incoming[key]
            merged = models.sanitize_meta(merged)
            if merged == self.meta:
                return None
            self.meta = merged
            return self._record({"op": "meta", "meta": dict(merged)})

        return None

    # --------------------------------------------------------------- 同步

    def ops_since(self, since: int) -> Optional[List[Dict[str, Any]]]:
        """返回 ``seq > since`` 的操作；历史不足以补齐时返回 None。"""
        if since >= self.seq:
            return []
        if not self.ops or self.ops[0]["seq"] > since + 1:
            return None
        return [op for op in self.ops if op["seq"] > since]

    def stroke_list(self) -> List[Dict[str, Any]]:
        return sorted(self.strokes.values(), key=lambda s: s.get("n", 0))


class Client:
    """一条 WebSocket 连接。

    ``allowed`` 是连接建立时按 TCP 对端地址定下来的权限集合：本机全给，别的设备
    只有 Mac 上一项项放开的那些。``role`` 是客户端自己报的，只用来决定界面长相，
    不能拿来当权限判断。

    ``pinned`` 是固定连接所在白板的 id；为 None 时这条连接跟随当前白板。
    """

    __slots__ = ("id", "ws", "role", "allowed", "connected_at", "pinned", "request")

    def __init__(
        self,
        client_id: str,
        ws: Any,
        role: str,
        allowed: "FrozenSet[str]" = frozenset(),
        pinned: Optional[str] = None,
        request: Any = None,
    ):
        self.id = client_id
        self.ws = ws
        self.role = role
        self.allowed = allowed
        self.connected_at = time.time()
        self.pinned = pinned
        # 建立连接的那个请求：权限改动之后按它重新计算（Hub.refresh_permissions）
        self.request = request

    async def send(self, message: Dict[str, Any]) -> None:
        try:
            await self.ws.send_json(message)
        except (ConnectionResetError, RuntimeError, ValueError) as exc:
            log.debug("发送失败 %s：%s", self.id, exc)


class Hub:
    """管理所有连接、当前白板与自动保存。"""

    def __init__(self, store: BoardStore):
        self.store = store
        self.clients: Dict[str, Client] = {}
        self._boards: Dict[str, BoardRuntime] = {}
        self.current_id: str = store.current_id or store.list_metas()[0]["id"]
        self._autosave_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ 白板管理

    def board(self, board_id: Optional[str] = None) -> BoardRuntime:
        board_id = board_id or self.current_id
        runtime = self._boards.get(board_id)
        if runtime is None:
            meta, strokes, problem = self.store.open_board(board_id)
            runtime = BoardRuntime(meta, strokes, problem)
            self._boards[board_id] = runtime
        return runtime

    def snapshot(self, board_id: Optional[str] = None) -> Dict[str, Any]:
        runtime = self.board(board_id)
        return {
            "board": dict(runtime.meta),
            "strokes": runtime.stroke_list(),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
            "locked": runtime.problem,
            "boards": self.store.list_metas(),
            "folders": self.store.folders(),
        }

    def board_of(self, client: Client) -> str:
        """这条连接所在的白板：固定连接是它指定的那块，其余是当前白板。"""
        return client.pinned or self.current_id

    def pin_board(
        self,
        board_id: str,
        app: str,
        name: str = "",
        kind: str = "board",
        folder: str = "",
        underlay: Any = None,
    ) -> Dict[str, Any]:
        """固定连接指定的白板：已有就原样返回，没有就新建。新建不改变当前白板。

        新建的白板在 meta 里记下建立它的应用（``app``），权限判断据此区分
        「应用自己的白板」和用户的白板（见 server._Session._hello）。
        """
        meta = self.store.get_meta(board_id)
        if meta is not None:
            return meta
        if sum(1 for m in self.store.list_metas() if m.get("app") == app) >= MAX_APP_BOARDS:
            raise ValueError(f"应用 {app} 的白板已达上限 {MAX_APP_BOARDS}")
        meta = self.store.create_board(
            name,
            make_current=False,
            id=board_id,
            kind=kind if kind in ("board", "note") else "board",
            app=app,
            underlay=underlay,
        )
        clean = models.sanitize_folder(folder)
        if clean:
            self.move_board(board_id, clean)
            meta = self.store.get_meta(board_id) or meta
        log.info("应用 %s 新建白板 %s", app, board_id)
        return meta

    def select_board(self, board_id: str) -> bool:
        if not self.store.get_meta(board_id) or board_id == self.current_id:
            return False
        self.save_all()
        # 上次没读全的那块再打开时重新读一遍：读不出来可能只是暂时的（外置盘
        # 没挂上），锁住的白板内存里没有任何改动，丢掉重读不会丢东西
        cached = self._boards.get(board_id)
        if cached is not None and cached.locked:
            del self._boards[board_id]
        self.current_id = board_id
        self.store.set_current(board_id)
        return True

    def unlock(self, board_id: Optional[str] = None) -> bool:
        """用户确认之后解锁一块读不全的白板：先把原文件备份，之后照常编辑、存盘。

        备份失败就不解锁——解锁之后的第一次存盘就会覆盖原件。
        """
        board_id = board_id or self.current_id
        runtime = self.board(board_id)
        if not runtime.locked:
            return False
        try:
            backup = self.store.backup_board_file(board_id)
        except OSError as exc:
            log.error("白板 %s 解锁前备份失败，保持只读：%s", board_id, exc)
            return False
        log.warning("白板 %s 已解锁（%s），原文件备份在 %s", board_id, runtime.problem.get("reason"), backup)
        runtime.problem = None
        return True

    def create_board(self, kind: str = "board") -> Dict[str, Any]:
        self.save_all()
        meta = self.store.create_board(kind=kind)
        self._boards[meta["id"]] = BoardRuntime(meta, [])
        self.current_id = meta["id"]
        return meta

    def import_board_file(self, path) -> Dict[str, Any]:
        """把存储目录之外的 ``.wbz`` 复制成新白板并切过去（见 ``store.import_board_file``）。"""
        self.save_all()
        meta = self.store.import_board_file(path)
        self.current_id = meta["id"]
        return meta

    def rename_board(self, board_id: str, name: str) -> bool:
        """给任意一块白板改名，不必是当前这块。改名不算「编辑」，不动 updated。"""
        return self._edit_meta(board_id, name=name if isinstance(name, str) else "")

    def move_board(self, board_id: str, folder: str) -> bool:
        """把白板放进某个文件夹，空串是移出来。和改名一样不动 updated。"""
        clean = models.sanitize_folder(folder)
        if not self._edit_meta(board_id, folder=clean):
            return False
        # 在「归入文件夹」里直接输一个新名字就等于新建，名字要登记进名单，
        # 否则这个文件夹只写在白板上，界面上的文件夹列表里没有它。
        if clean:
            self.store.create_folder(clean)
        return True

    def _edit_meta(self, board_id: str, **changes: Any) -> bool:
        runtime = self._boards.get(board_id)
        if runtime is None:
            return self.store.edit_meta(board_id, **changes)
        if runtime.locked:
            # 锁住的白板自动保存不会写它，改名只能直接改文件里的 meta，
            # 其余内容原样保留；文件读不出来就改不成
            if not self.store.edit_meta(board_id, **changes):
                return False
            runtime.meta = self.store.get_meta(board_id) or runtime.meta
            return True
        # 已经在内存里的那块不能直接写文件：自动保存会拿内存里的 meta 覆盖回去。
        merged = models.sanitize_meta(dict(runtime.meta, **changes))
        if merged == runtime.meta:
            return False
        runtime.meta = merged
        runtime.dirty = True  # 索引先更新，文件交给自动保存
        self.store.update_meta(merged)
        return True

    def reorder_boards(self, ids: Any) -> bool:
        """拖动排序。顺序只存在索引里，白板文件本身不动，所以不必管内存里那几块。"""
        return self.store.set_order(ids)

    # ------------------------------------------------------------- 文件夹

    def create_folder(self, name: str) -> str:
        return self.store.create_folder(name)

    def delete_folder(self, name: str) -> bool:
        """删文件夹不删白板：里面的白板先移出来，再把名字从名单里去掉。"""
        clean = models.sanitize_folder(name)
        if clean not in self.store.folders():
            return False
        # 已经载入内存的那几块要走 Hub 自己这条路，否则自动保存会把改动覆盖回去
        for board_id, runtime in self._boards.items():
            if runtime.meta.get("folder") == clean:
                self._edit_meta(board_id, folder="")
        return self.store.delete_folder(clean)

    def rename_folder(self, name: str, to: str) -> bool:
        """给文件夹改名。名字就是身份，里面每块白板上记的名字都要跟着改。"""
        clean = models.sanitize_folder(name)
        target = models.sanitize_folder(to)
        folders = self.store.folders()
        # 先把不成立的情况挡掉：改到一半才发现改不了的话，内存里那几块已经动过了
        if not target or target == clean or clean not in folders or target in folders:
            return False
        # 这里不能走 move_board：它会把新名字当成一个新文件夹记进名单，
        # 名单里多出一个同名的，下面的改名就成了「改到一个已经存在的名字」。
        for board_id, runtime in self._boards.items():
            if runtime.meta.get("folder") == clean:
                self._edit_meta(board_id, folder=target)
        return self.store.rename_folder(clean, target)

    def strokes_of(self, board_id: str) -> List[Dict[str, Any]]:
        """导出用：已经载入内存的用内存里的，其余的从磁盘读。"""
        runtime = self._boards.get(board_id)
        if runtime is not None:
            return runtime.stroke_list()
        _, strokes = self.store.load_board(board_id)
        return strokes

    def delete_board(self, board_id: str) -> bool:
        if not self.store.delete_board(board_id):
            return False
        self._boards.pop(board_id, None)
        self.current_id = self.store.current_id or self.store.list_metas()[0]["id"]
        return True

    # -------------------------------------------------------------- 广播

    async def broadcast(
        self,
        message: Dict[str, Any],
        exclude: Optional[str] = None,
    ) -> None:
        """发给跟随当前白板的连接。切换白板、白板列表这类消息只和它们有关。"""
        await self._send_all(
            [c for c in list(self.clients.values()) if c.id != exclude and c.pinned is None], message
        )

    async def broadcast_board(
        self,
        board_id: str,
        message: Dict[str, Any],
        exclude: Optional[str] = None,
    ) -> None:
        """发给所有正在看 ``board_id`` 的连接：跟随当前白板的，以及固定在它上面的。"""
        await self._send_all(
            [
                c
                for c in list(self.clients.values())
                if c.id != exclude and self.board_of(c) == board_id
            ],
            message,
        )

    async def _send_all(self, targets: List[Client], message: Dict[str, Any]) -> None:
        if targets:
            await asyncio.gather(*(client.send(self.visible(client, message)) for client in targets))

    def visible(self, client: Client, message: Dict[str, Any]) -> Dict[str, Any]:
        """没有「管理白板」权限的连接看不到完整的白板列表，只看得到自己所在的那块。

        列表里有每块白板的 id 和名字；拿到 id 就能访问那块白板的文档页等内容。
        """
        if "boards" not in message or "manage" in client.allowed:
            return message
        own = self.board_of(client)
        return dict(
            message,
            boards=[meta for meta in message.get("boards") or [] if meta.get("id") == own],
            folders=[],
        )

    async def refresh_permissions(self, compute) -> None:
        """权限设置改了：按 ``compute(client)`` 重新计算每条连接的权限，有变化就通知它。"""
        for client in list(self.clients.values()):
            try:
                allowed = frozenset(compute(client))
            except Exception:  # noqa: BLE001 - 算不出来就收回全部权限
                log.exception("重新计算 %s 的权限出错", client.id)
                allowed = frozenset()
            if allowed == client.allowed:
                continue
            client.allowed = allowed
            log.info("连接 %s 的权限改为：%s", client.id, ", ".join(sorted(allowed)) or "只能写字")
            await client.send({"t": "perms", "perms": sorted(allowed)})
            # 拿到或失去「管理白板」时，白板列表也跟着变
            await client.send(
                self.visible(client, {
                    "t": "boards",
                    "boards": self.store.list_metas(),
                    "folders": self.store.folders(),
                    "board": dict(self.board(self.board_of(client)).meta),
                })
            )

    # ------------------------------------------------------------ 自动保存

    def save_all(self) -> None:
        """把有改动的白板写盘。一块失败不影响别的，它留着脏标记，下一轮再试。"""
        for board_id, runtime in list(self._boards.items()):
            if not runtime.dirty or runtime.locked:
                continue
            try:
                self.store.save_board(runtime.meta, runtime.stroke_list())
                runtime.dirty = False
            except Exception:  # noqa: BLE001 - 任何一块出错都不能挡住其余几块
                log.exception("白板 %s 保存失败", board_id)

    async def _autosave_loop(self) -> None:
        while True:
            await asyncio.sleep(AUTOSAVE_INTERVAL)
            try:
                self.save_all()
            except Exception:  # noqa: BLE001 - 自动保存不能把后台任务打死
                log.exception("自动保存出错")

    def start_autosave(self) -> None:
        if self._autosave_task is None:
            self._autosave_task = asyncio.create_task(self._autosave_loop())

    async def close_clients(self) -> None:
        """关服务前先把连接断干净。

        不主动关的话，aiohttp 会一直等这些长连接的处理协程结束，
        关窗口时就要干等十几秒。
        """
        clients = list(self.clients.values())
        self.clients.clear()
        for client in clients:
            try:
                await client.ws.close(code=1001, message=b"shutdown")
            except (ConnectionResetError, RuntimeError, OSError) as exc:
                log.debug("关闭连接 %s 失败：%s", client.id, exc)

    async def stop(self) -> None:
        if self._autosave_task is not None:
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass
            self._autosave_task = None
        self.save_all()

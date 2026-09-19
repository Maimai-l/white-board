"""实时同步的核心：操作日志、广播与自动保存。

设计要点：

* **本地优先**。客户端先在本地画，再把操作发给服务端；服务端只负责编号、
  转发与持久化，不参与绘制判断，因此本地书写不会被网络拖慢。
* **单调序号**。每个被接受的操作拿到一个递增 ``seq``；客户端记住自己收到
  的最后一个 ``seq``，断线重连时带上它，服务端补发缺失的操作；缺口太大
  （超出历史窗口）则直接补发整块白板。
* **同一块白板**。Mac 端选择白板，iPad 跟随，符合点对点的使用方式。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional

from . import models
from .store import BoardStore

log = logging.getLogger(__name__)

# 保留的操作历史条数，决定断线多久之后需要全量补齐。
OPS_HISTORY = 4000
AUTOSAVE_INTERVAL = 3.0


class BoardRuntime:
    """一块白板在内存中的状态。"""

    def __init__(self, meta: Dict[str, Any], strokes: List[Dict[str, Any]]):
        self.meta = meta
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
        if not isinstance(raw, dict):
            return None
        kind = raw.get("op")

        if kind in ("add", "restore"):
            keep_order = kind == "restore"
            accepted: List[Dict[str, Any]] = []
            for item in raw.get("strokes", [])[:2000]:
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
    """一条 WebSocket 连接。"""

    __slots__ = ("id", "ws", "role", "connected_at")

    def __init__(self, client_id: str, ws: Any, role: str):
        self.id = client_id
        self.ws = ws
        self.role = role
        self.connected_at = time.time()

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
            meta, strokes = self.store.load_board(board_id)
            runtime = BoardRuntime(meta, strokes)
            self._boards[board_id] = runtime
        return runtime

    def snapshot(self, board_id: Optional[str] = None) -> Dict[str, Any]:
        runtime = self.board(board_id)
        return {
            "board": dict(runtime.meta),
            "strokes": runtime.stroke_list(),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
            "boards": self.store.list_metas(),
        }

    def select_board(self, board_id: str) -> bool:
        if not self.store.get_meta(board_id) or board_id == self.current_id:
            return False
        self.save_all()
        self.current_id = board_id
        self.store.set_current(board_id)
        return True

    def create_board(self) -> Dict[str, Any]:
        self.save_all()
        meta = self.store.create_board()
        self._boards[meta["id"]] = BoardRuntime(meta, [])
        self.current_id = meta["id"]
        return meta

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
        only: Optional[Iterable[str]] = None,
    ) -> None:
        targets = [
            client
            for client in list(self.clients.values())
            if client.id != exclude and (only is None or client.role in only)
        ]
        if targets:
            await asyncio.gather(*(client.send(message) for client in targets))

    # ------------------------------------------------------------ 自动保存

    def save_all(self) -> None:
        for board_id, runtime in self._boards.items():
            if runtime.dirty:
                try:
                    self.store.save_board(runtime.meta, runtime.stroke_list())
                    runtime.dirty = False
                except OSError as exc:
                    log.error("白板 %s 保存失败：%s", board_id, exc)

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

    async def stop(self) -> None:
        if self._autosave_task is not None:
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass
            self._autosave_task = None
        self.save_all()

"""白板应用的用户空间：在 inksync 的 Hub 上加白板应用自己的功能。

* **跟随当前白板**：白板应用的页面以 ``follow`` 连接（1.x 页面的连接也按跟随处理），
  打开「当前白板」；Mac 上选择、新建、删除白板之后，所有跟随的连接一起切换。
* **白板列表、文件夹、排序**：``boards`` 消息，以及 ``sel``、``newboard``、``delboard``、
  ``rename``、``folder``、``order``、``newfolder``、``delfolder``、``renamefolder``
  这些扩展消息（docs/protocol.md「白板管理」）。
* **权限**：四项权限（manage、settings、clear、export）映射到 inksync 的规则；
  ``perms`` 消息告诉页面它现在有哪些权限。
* **1.x 页面**：升级时仍开着的旧页面照常工作，发给它们的消息换回 1.x 的形式
  （docs/design/inksync-2.zh-CN.md 7.3 节）。

除 ``board``、``save_all`` 这类同步的读写外，改动都是协程，在事件循环线程上调用。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

from inksync.hub import AUTOSAVE_INTERVAL, OPS_HISTORY, BoardRuntime, Conn  # noqa: F401
from inksync.hub import Hub as CoreHub
from inksync.policy import Policy, Principal
from inksync.storage import BoardFileError

from . import models
from .store import BoardStore

log = logging.getLogger(__name__)


def perms_of(who: Principal) -> FrozenSet[str]:
    return frozenset(who.attrs.get("perms", ())) if who is not None else frozenset()


class WhiteboardPolicy(Policy):
    """用户空间的规则：四项权限的含义见 docs/protocol.md「权限」。"""

    # 文件夹、文档板原件、1.0.1 的应用名只能由白板应用自己改
    protected_data_keys = frozenset({"folder", "doc", "app"})

    def __init__(self, hub: "Hub"):
        self.hub = hub

    def can_open(self, who, board_id, meta):
        return "manage" in perms_of(who) or board_id == self.hub.current_id

    def can_create(self, who, board_id, spec):
        return "manage" in perms_of(who)

    def can_write(self, who, meta):
        return True

    def can_clear(self, who, meta):
        return "clear" in perms_of(who)

    def can_edit_meta(self, who, meta, patch):
        return "settings" in perms_of(who)

    def can_unlock(self, who, meta):
        return "manage" in perms_of(who)

    def create_limit(self, who):
        return None


class Hub:
    """白板应用的用户空间，见模块说明。``core`` 是底下的 inksync Hub。"""

    def __init__(self, store: BoardStore, autosave: float = AUTOSAVE_INTERVAL):
        self.store = store
        self.core = CoreHub(store.core, policy=WhiteboardPolicy(self), autosave=autosave, accept_v1=True)
        self.core.name = ""
        self.core.closable = False
        self.core.set_hello(self._hello)
        self.core.set_encoder(self._encode)
        for name, handler in (
            ("sel", self._on_select),
            ("newboard", self._on_new_board),
            ("delboard", self._on_delete_board),
            ("rename", self._on_rename),
            ("folder", self._on_folder),
            ("order", self._on_order),
            ("newfolder", self._on_new_folder),
            ("delfolder", self._on_delete_folder),
            ("renamefolder", self._on_rename_folder),
        ):
            self.core.register(name, handler)

    # ------------------------------------------------------------ 查询

    @property
    def current_id(self) -> str:
        return self.store.current_id

    @property
    def clients(self) -> Dict[str, Conn]:
        return self.core.conns

    def board(self, board_id: Optional[str] = None) -> BoardRuntime:
        return self.core.board(board_id or self.current_id)

    def followers(self) -> List[Conn]:
        return [c for c in self.core.connections() if c.ext.get("follow")]

    def save_all(self) -> None:
        self.core.save_all()

    async def strokes_of(self, board_id: str) -> List[Dict[str, Any]]:
        return await self.core.strokes(board_id) or []

    def boards_payload(self, conn: Optional[Conn] = None) -> Dict[str, Any]:
        """白板列表。没有「管理白板」权限的连接只看得到自己所在的那块，文件夹为空。"""
        metas = self.store.list_metas()
        for meta in metas:
            runtime = self.core.runtime(meta["id"])
            if runtime is not None:
                meta.update(name=runtime.meta["name"], updated=runtime.meta["updated"])
        folders = self.store.folders()
        if conn is not None and "manage" not in perms_of(conn.principal):
            metas = [m for m in metas if m["id"] == conn.board]
            folders = []
        return {"boards": metas, "folders": folders}

    # ------------------------------------------------------------ 连接

    async def _hello(self, conn: Conn, msg: Dict[str, Any]) -> Optional[str]:
        """白板应用的页面总是跟随当前白板。"""
        conn.ext["follow"] = True
        return self.current_id

    def _encode(self, conn: Conn, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = msg.get("t")
        if conn.ext.get("follow") and kind in ("init", "sync", "switch"):
            msg = {**msg, **self.boards_payload(conn)}
        if not conn.legacy:
            return msg
        # 1.x 页面：元数据换回 1.x 的字段，1.x 不认识的消息不发
        if kind in ("caps", "deleted"):
            return None
        if kind == "locked":
            runtime = self.core.runtime(msg.get("board") or "")
            if runtime is None:
                return None
            snapshot = self.core.snapshot(conn, runtime, "switch")
            return self._encode(conn, snapshot)
        msg = dict(msg)
        if kind in ("init", "sync", "switch"):
            msg["role"] = conn.device or "mac"
        if isinstance(msg.get("board"), dict):
            msg["board"] = models.to_v1(msg["board"])
        if isinstance(msg.get("boards"), list):
            msg["boards"] = [models.to_v1(m) for m in msg["boards"]]
        op = msg.get("op")
        if isinstance(op, dict) and op.get("op") == "meta":
            msg["op"] = dict(op, meta=models.to_v1(op.get("meta")))
        if kind == "ack":
            msg.pop("board", None)
            msg.pop("rejected", None)
        return msg

    def _may(self, conn: Conn, permission: str) -> bool:
        return permission in perms_of(conn.principal)

    # ------------------------------------------------------------ 广播

    async def broadcast_switch(self) -> None:
        """所有跟随的连接切到当前白板（整块快照）。"""
        current = self.current_id
        for conn in self.followers():
            await self.core.open(conn, current, reply="switch")

    async def broadcast_boards(self) -> None:
        """只刷白板列表：改的可能不是当前这块，没必要让所有人重载笔画。"""
        for conn in self.followers():
            board = self.core.board_meta(conn.board) if conn.board else None
            await conn.send({"t": "boards", **self.boards_payload(conn), "board": board})

    async def send_perms(self, conns: List[Conn]) -> None:
        """权限变了：告诉这些连接新的权限，并按新权限重发白板列表。"""
        for conn in conns:
            await conn.send({"t": "perms", "perms": sorted(perms_of(conn.principal))})
            if conn.ext.get("follow"):
                board = self.core.board_meta(conn.board) if conn.board else None
                await conn.send({"t": "boards", **self.boards_payload(conn), "board": board})

    # ------------------------------------------------------------ 白板管理

    async def _edit(self, board_id: str, patch: Dict[str, Any]) -> bool:
        """整理动作（改名、归类）：不改 ``updated``。已载入的白板经过内存，其余直接改文件。"""
        if self.core.runtime(board_id) is not None:
            return await self.core.edit_meta(board_id, patch, touch=False, quiet=True) is not None
        return self.store.edit_meta(board_id, **patch)

    async def select_board(self, board_id: str) -> bool:
        if not self.core.exists(board_id) or board_id == self.current_id:
            return False
        # 上次没读全的那块再打开时重新读一遍：读不出来可能只是暂时的（外置盘
        # 没挂上），锁住的白板内存里没有任何改动，丢掉重读不会丢东西
        runtime = self.core.runtime(board_id)
        if runtime is not None and runtime.locked:
            self.core.forget(board_id)
        self.store.set_current(board_id)
        await self.broadcast_switch()
        return True

    async def create_board(self, kind: str = "board", folder: str = "", name: str = "") -> Dict[str, Any]:
        """新建白板并设为当前白板（不广播，调用方决定）。"""
        spec = dict(models.spec_for_kind(kind if kind in ("board", "note") else "board"), name=name)
        clean = models.sanitize_folder(folder)
        if clean and clean in self.store.folders():
            spec["data"] = {"folder": clean}
        meta = await self.core.create_board(None, spec)
        self.store.put_front(meta["id"])
        self.store.set_current(meta["id"])
        return meta

    async def rename_board(self, board_id: str, name: str) -> bool:
        return await self._edit(board_id, {"name": name if isinstance(name, str) else ""})

    async def move_board(self, board_id: str, folder: str) -> bool:
        """把白板放进某个文件夹，空串是移出来。名字还不在名单里就登记进去。"""
        clean = models.sanitize_folder(folder)
        if not await self._edit(board_id, {"data": {"folder": clean or None}}):
            return False
        if clean:
            self.store.create_folder(clean)
        return True

    async def reorder_boards(self, ids: Any) -> bool:
        return self.store.set_order(ids)

    def create_folder(self, name: str) -> str:
        return self.store.create_folder(name)

    async def delete_folder(self, name: str) -> bool:
        """删文件夹不删白板：里面的白板先移出来，再把名字从名单里去掉。"""
        clean = models.sanitize_folder(name)
        if clean not in self.store.folders():
            return False
        for meta in self.store.list_metas():
            if models.folder_of(self.core.board_meta(meta["id"]) or meta) == clean:
                await self._edit(meta["id"], {"data": {"folder": None}})
        return self.store.forget_folder(clean)

    async def rename_folder(self, name: str, to: str) -> bool:
        """给文件夹改名。名字就是身份，里面每块白板上记的名字都要跟着改。"""
        clean = models.sanitize_folder(name)
        target = models.sanitize_folder(to)
        folders = self.store.folders()
        if not target or target == clean or clean not in folders or target in folders:
            return False
        for meta in self.store.list_metas():
            if models.folder_of(self.core.board_meta(meta["id"]) or meta) == clean:
                await self._edit(meta["id"], {"data": {"folder": target}})
        self.store.rename_folder_entry(clean, target)
        return True

    async def delete_board(self, board_id: str) -> bool:
        if not await self.core.delete_board(board_id):
            return False
        self.store.delete_files(board_id)
        self.store.forget_board(board_id)
        return True

    async def unlock(self, board_id: Optional[str] = None) -> bool:
        return await self.core.unlock(board_id or self.current_id)

    # ------------------------------------------------------------ 文档板与本地文件

    async def add_doc(self, prepared: Dict[str, Any], folder: str = "") -> Dict[str, Any]:
        """为 ``store.prepare_doc`` 准备好的原件建板，设为当前白板（不广播）。"""
        meta = self.store.doc_meta(prepared)
        clean = models.sanitize_folder(folder)
        if clean and clean in self.store.folders():
            meta["data"]["folder"] = clean
        meta = await self.core.create_board(None, meta=meta)
        self.store.put_front(meta["id"])
        self.store.set_current(meta["id"])
        return meta

    async def import_doc(self, data: bytes, filename: str) -> Dict[str, Any]:
        return await self.add_doc(self.store.prepare_doc(data, filename))

    async def import_board_file(self, path: Path) -> Dict[str, Any]:
        """把存储目录之外的 ``.wbz``（例如备份）复制成新白板并设为当前白板（不广播）。

        文档板连同原件一起导入；找不到原件时不导入，抛 :class:`BoardFileError`。"""
        meta, _raw, old_id = await self.core.import_file(path)
        board_id = meta["id"]
        try:
            self.store.copy_doc_original(path, old_id, board_id, meta)
        except BoardFileError:
            await self.core.delete_board(board_id)
            raise
        patch: Dict[str, Any] = {}
        doc = models.doc_of(meta)
        if doc is not None:
            patch["layers"] = models.doc_layers(board_id, doc)
        if models.folder_of(meta):
            patch["data"] = {"folder": None}
        if patch:
            await self.core.edit_meta(board_id, patch, touch=False)
        self.store.put_front(board_id)
        self.store.set_current(board_id)
        return self.core.board_meta(board_id) or meta

    # ------------------------------------------------------------ 扩展消息

    async def _on_select(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id = msg.get("board")
        if self._may(conn, "manage") and isinstance(board_id, str):
            await self.select_board(board_id)

    async def _on_new_board(self, conn: Conn, msg: Dict[str, Any]) -> None:
        if not self._may(conn, "manage"):
            return
        await self.create_board(msg.get("kind") or "board", msg.get("folder") or "")
        await self.broadcast_switch()

    async def _on_delete_board(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id = msg.get("board")
        if self._may(conn, "manage") and isinstance(board_id, str) and await self.delete_board(board_id):
            await self.broadcast_switch()

    async def _on_rename(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id, name = msg.get("board"), msg.get("name")
        if not self._may(conn, "manage") or not isinstance(board_id, str) or not isinstance(name, str):
            return
        if await self.rename_board(board_id, name):
            await self.broadcast_boards()

    async def _on_folder(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id, folder = msg.get("board"), msg.get("folder")
        if not self._may(conn, "manage") or not isinstance(board_id, str) or not isinstance(folder, str):
            return
        if await self.move_board(board_id, folder):
            await self.broadcast_boards()

    async def _on_order(self, conn: Conn, msg: Dict[str, Any]) -> None:
        if self._may(conn, "manage") and await self.reorder_boards(msg.get("ids")):
            await self.broadcast_boards()

    async def _on_new_folder(self, conn: Conn, msg: Dict[str, Any]) -> None:
        if self._may(conn, "manage") and self.create_folder(msg.get("name")):
            await self.broadcast_boards()

    async def _on_delete_folder(self, conn: Conn, msg: Dict[str, Any]) -> None:
        if self._may(conn, "manage") and await self.delete_folder(msg.get("name")):
            await self.broadcast_boards()

    async def _on_rename_folder(self, conn: Conn, msg: Dict[str, Any]) -> None:
        if self._may(conn, "manage") and await self.rename_folder(msg.get("name"), msg.get("to")):
            await self.broadcast_boards()

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self.core.start()

    async def stop(self) -> None:
        await self.core.stop()

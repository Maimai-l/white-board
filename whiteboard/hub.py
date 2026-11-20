"""白板应用的用户空间：在 inksync 的 Hub 上加白板应用自己的功能。

* **跟随当前白板**：白板应用的页面以 ``follow`` 连接（1.0.x 页面的连接也按跟随处理），
  打开「当前白板」；Mac 上选择、新建、删除白板之后，所有跟随的连接一起切换。
* **白板列表、文件夹、排序**：``boards`` 消息，以及 ``sel``、``newboard``、``delboard``、
  ``rename``、``folder``、``order``、``newfolder``、``delfolder``、``renamefolder``
  这些扩展消息（docs/protocol.md「白板管理」）。
* **复制**：``duplicate`` 复制一块白板（笔画、背景、画布、文件夹，文档板连同原件），
  排在原来那块后面，不换当前白板。
* **回收站**：``delboard`` 把白板移进回收站（``meta.data.trashed`` 记下时间），
  ``restore``、``purge``、``emptytrash`` 恢复或彻底删除；在回收站里超过
  ``TRASH_TTL`` 的白板由后台任务彻底删除（``start_maintenance``）。回收站里的白板
  不会是当前白板，1.0.x 页面看不到它们。
* **权限**：四项权限（manage、settings、clear、export）映射到 inksync 的规则；
  ``perms`` 消息告诉页面它现在有哪些权限。
* **速写**：``/sketch`` 页面（以及 Edge 插件的弹出页）开始一次速写时，用一块
  临时白板 ``sketch``（只在内存中，不存盘），跟随的连接一起切过去；速写页面关闭后
  白板丢弃，跟随的连接回到原来的当前白板。当前白板的记录始终不变。
* **1.0.x 页面**：升级时仍开着的旧页面照常工作，发给它们的消息换回 1.0.x 的形式
  （docs/design/inksync-redesign.zh-CN.md 7.3 节）。

除 ``board``、``save_all`` 这类同步的读写外，改动都是协程，在事件循环线程上调用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

from inksync.hub import AUTOSAVE_INTERVAL, OPS_HISTORY, BoardRuntime, Conn  # noqa: F401
from inksync.hub import Hub as CoreHub
from inksync.policy import Policy, Principal
from inksync.storage import BoardFileError

from . import models
from .store import BoardStore

log = logging.getLogger(__name__)

SKETCH_ID = "sketch"
SKETCH_SPEC = {"name": "速写", "canvas": {"mode": "infinite"}, "background": {"pattern": "blank"}}
# 速写页面断开之后等多久再结束：刷新页面、网络抖一下都不应该把速写丢掉
SKETCH_GRACE = 3.0
# 回收站里的白板放多久之后彻底删除，以及多久检查一次
TRASH_TTL = 30 * 86400.0
PURGE_INTERVAL = 3600.0


def perms_of(who: Principal) -> FrozenSet[str]:
    return frozenset(who.attrs.get("perms", ())) if who is not None else frozenset()


class WhiteboardPolicy(Policy):
    """用户空间的规则：四项权限的含义见 docs/protocol.md「权限」。"""

    # 文件夹、文档板原件、1.0.1 的应用名、回收站标记只能由白板应用自己改
    protected_data_keys = frozenset({"folder", "doc", "app", "trashed"})

    def __init__(self, hub: "Hub"):
        self.hub = hub

    def can_open(self, who, board_id, meta):
        return "manage" in perms_of(who) or board_id == self.hub.follow_target()

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

    def __init__(
        self,
        store: BoardStore,
        autosave: float = AUTOSAVE_INTERVAL,
        sketch_grace: float = SKETCH_GRACE,
        trash_ttl: float = TRASH_TTL,
        purge_interval: float = PURGE_INTERVAL,
    ):
        self.store = store
        self.trash_ttl = trash_ttl
        self.purge_interval = purge_interval
        self._maintenance: Optional[asyncio.Task] = None
        # 跟随的连接现在是否应该在速写板上（Mac 上选了别的白板就不再跟着速写）
        self.sketch_follow = False
        self.sketch_grace = sketch_grace
        self._sketch_check: Optional[asyncio.Task] = None
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
            ("duplicate", self._on_duplicate),
            ("restore", self._on_restore),
            ("purge", self._on_purge),
            ("emptytrash", self._on_empty_trash),
        ):
            self.core.register(name, handler)
        self.core.on("detached", self._on_detached)

    # ------------------------------------------------------------ 查询

    @property
    def current_id(self) -> str:
        return self.store.current_id

    @property
    def clients(self) -> Dict[str, Conn]:
        return self.core.conns

    def board(self, board_id: Optional[str] = None) -> BoardRuntime:
        return self.core.board(board_id or self.current_id)

    @property
    def sketch_active(self) -> bool:
        return self.core.is_transient(SKETCH_ID)

    def follow_target(self) -> str:
        """跟随的连接应该显示哪块白板：速写进行中且没有被打断时是速写板，否则是当前白板。"""
        return SKETCH_ID if self.sketch_follow and self.sketch_active else self.current_id

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
        return self.follow_target()

    def _encode(self, conn: Conn, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = msg.get("t")
        if conn.ext.get("follow") and kind in ("init", "sync", "switch"):
            msg = {**msg, **self.boards_payload(conn)}
        if not conn.legacy:
            return msg
        # 1.0.x 页面：元数据换回 1.0.x 的字段，1.0.x 不认识的消息不发
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
            # 1.0.x 没有回收站：回收站里的白板对它来说就是删掉了
            msg["boards"] = [models.to_v1(m) for m in msg["boards"] if models.trashed_at(m) is None]
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
        """所有跟随的连接切到当前白板（整块快照）。速写进行中时，这表示用户在 Mac 上选了
        别的白板：跟随的连接离开速写板，速写页面本身不受影响。"""
        self.sketch_follow = False
        current = self.current_id
        for conn in self.followers():
            await self.core.open(conn, current, reply="switch")

    async def announce(self, previous: Optional[str]) -> None:
        """白板列表变了之后通知跟随的连接：当前白板换了（``previous`` 是之前那块）就整块
        切换，否则只刷列表。"""
        if self.current_id != previous:
            await self.broadcast_switch()
        else:
            await self.broadcast_boards()

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

    # ------------------------------------------------------------ 速写

    async def start_sketch(self) -> Dict[str, Any]:
        """开始（或继续）一次速写：没有速写板就建一块空白的，跟随的连接都切过去。"""
        if self._sketch_check is not None:
            self._sketch_check.cancel()
            self._sketch_check = None
        if not self.sketch_active:
            self.core.create_transient(SKETCH_ID, SKETCH_SPEC)
        self.sketch_follow = True
        for conn in self.followers():
            if conn.board != SKETCH_ID:
                await self.core.open(conn, SKETCH_ID, reply="switch")
        return self.core.board_meta(SKETCH_ID) or {}

    async def end_sketch(self) -> bool:
        """结束速写：丢弃速写板，还在上面的跟随连接回到当前白板，其他连接收到 ``deleted``。"""
        if self._sketch_check is not None and self._sketch_check is not asyncio.current_task():
            self._sketch_check.cancel()
        self._sketch_check = None
        if not self.sketch_active:
            return False
        viewers = [c for c in self.core.connections() if c.board == SKETCH_ID]
        self.core.drop_transient(SKETCH_ID)
        self.sketch_follow = False
        current = self.current_id
        for conn in viewers:
            if conn.ext.get("follow"):
                await self.core.open(conn, current, reply="switch")
            else:
                await conn.send({"t": "deleted", "board": SKETCH_ID})
        return True

    def sketch_owners(self) -> List[Conn]:
        """正在显示速写板、但不是跟随连接的连接：速写页面自己。"""
        return [c for c in self.core.connections() if c.board == SKETCH_ID and not c.ext.get("follow")]

    def _on_detached(self, conn: Conn) -> None:
        if conn.board != SKETCH_ID or conn.ext.get("follow") or not self.sketch_active:
            return
        if self._sketch_check is None or self._sketch_check.done():
            self._sketch_check = asyncio.get_running_loop().create_task(self._maybe_end_sketch())

    async def _maybe_end_sketch(self) -> None:
        await asyncio.sleep(self.sketch_grace)
        if not self.sketch_owners():
            await self.end_sketch()
        self._sketch_check = None

    # ------------------------------------------------------------ 白板管理

    async def _edit(self, board_id: str, patch: Dict[str, Any]) -> bool:
        """整理动作（改名、归类）：不改 ``updated``。已载入的白板经过内存，其余直接改文件。"""
        if self.core.runtime(board_id) is not None:
            return await self.core.edit_meta(board_id, patch, touch=False, quiet=True) is not None
        return self.store.edit_meta(board_id, **patch)

    def is_trashed(self, board_id: str) -> bool:
        return models.trashed_at(self.core.board_meta(board_id)) is not None

    async def ensure_current(self) -> bool:
        """当前白板不存在或者在回收站里时，换成第一块还在用的白板；一块都没有就新建一块
        空白板。换了返回 True（不广播，调用方决定）。"""
        current = self.current_id
        if current and self.core.exists(current) and not self.is_trashed(current):
            return False
        live = self.store.first_live_id()
        if live is None:
            await self.create_board()
        else:
            self.store.set_current(live)
        return True

    async def select_board(self, board_id: str) -> bool:
        if not self.core.exists(board_id) or board_id == self.current_id or self.is_trashed(board_id):
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
        """删文件夹不删白板：里面的白板先移出来，再把名字从名单里去掉。回收站里的白板
        留着原来的文件夹名，恢复时文件夹重新建出来。"""
        clean = models.sanitize_folder(name)
        if clean not in self.store.folders():
            return False
        for meta in self.store.list_metas():
            meta = self.core.board_meta(meta["id"]) or meta
            if models.folder_of(meta) == clean and models.trashed_at(meta) is None:
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
        """彻底删除：白板文件、缩略图和文档原件都删掉。当前白板被删时换成第一块还在用的。"""
        if not await self.core.delete_board(board_id):
            return False
        self.store.delete_files(board_id)
        self.store.forget_board(board_id)
        return True

    async def duplicate_board(self, board_id: str) -> Optional[Dict[str, Any]]:
        """复制一块白板（不广播，不换当前白板），返回新白板的元数据。

        新白板有新的 id，笔画、背景、画布、文件夹都一样，名字是原来显示的名字加
        「 副本」；文档板连同原件和缩略图一起复制。新白板直接写成文件，不载入内存，
        排在原来那块后面。速写板、回收站里的、读不全（只读）的白板不复制，返回 None。"""
        if self.core.is_transient(board_id) or not self.core.exists(board_id) or self.is_trashed(board_id):
            return None
        runtime = await self.core.load(board_id)
        if runtime is None:
            return None
        if runtime.locked:
            log.warning("hub.duplicate_board：白板 %s 没有读全，不复制", board_id)
            return None
        source = self.core.board_meta(board_id) or runtime.meta
        strokes = [dict(s) for s in runtime.stroke_list()]
        new_id = models.new_id()
        stamp = models.now()
        data = {k: v for k, v in (source.get("data") or {}).items() if k != "trashed"}
        meta = dict(source, id=new_id, name=models.copy_name(source), created=stamp, updated=stamp, data=data)
        doc = models.doc_of(source)
        if doc is not None:
            meta["layers"] = models.doc_layers(new_id, doc)
        meta = models.sanitize_meta(meta, self.core.policy.allow_src)
        storage = self.store.core
        mtime = await asyncio.to_thread(storage.write_file, meta, strokes)
        storage.put_meta(meta, mtime)
        try:
            self.store.copy_files(board_id, new_id)
        except OSError as exc:
            log.error("hub.duplicate_board：复制白板 %s 的缩略图或原件失败：%s", board_id, exc)
        self.store.put_after(new_id, board_id)
        log.info("复制白板 %s 为 %s（%d 笔）", board_id, new_id, len(strokes))
        return self.core.board_meta(new_id) or meta

    # ------------------------------------------------------------ 回收站

    async def trash_board(self, board_id: str) -> bool:
        """把白板移进回收站（不广播）。它是当前白板时换成第一块还在用的，
        一块都没有了就新建一块。速写板、不存在或已经在回收站里的返回 False。"""
        if self.core.is_transient(board_id) or not self.core.exists(board_id) or self.is_trashed(board_id):
            return False
        if not await self._edit(board_id, {"data": {"trashed": time.time()}}):
            return False
        log.info("白板 %s 移进回收站", board_id)
        await self.ensure_current()
        return True

    async def restore_board(self, board_id: str) -> bool:
        """从回收站恢复（不广播，不换当前白板）。原来的文件夹已经删了就重新建一个。"""
        meta = self.core.board_meta(board_id)
        if self.core.is_transient(board_id) or models.trashed_at(meta) is None:
            return False
        if not await self._edit(board_id, {"data": {"trashed": None}}):
            return False
        folder = models.folder_of(meta)
        if folder and folder not in self.store.folders():
            self.store.create_folder(folder)
        log.info("白板 %s 从回收站恢复", board_id)
        return True

    async def purge_board(self, board_id: str) -> bool:
        """彻底删除回收站里的一块白板（不广播）；不在回收站里的不删。"""
        if not self.is_trashed(board_id):
            return False
        return await self.delete_board(board_id)

    def trashed_ids(self) -> List[str]:
        return [m["id"] for m in self.store.list_metas() if self.is_trashed(m["id"])]

    async def empty_trash(self) -> int:
        """彻底删除回收站里的全部白板（不广播），返回删了几块。"""
        purged = 0
        for board_id in self.trashed_ids():
            if await self.purge_board(board_id):
                purged += 1
        if purged:
            log.info("清空回收站：彻底删除 %d 块白板", purged)
        return purged

    async def purge_expired(self, now: Optional[float] = None) -> List[str]:
        """彻底删除在回收站里放了 ``trash_ttl`` 秒以上的白板（不广播），返回它们的 id。"""
        now = time.time() if now is None else now
        purged = []
        for board_id in self.trashed_ids():
            stamp = models.trashed_at(self.core.board_meta(board_id))
            if stamp is not None and now - stamp >= self.trash_ttl and await self.purge_board(board_id):
                log.info("白板 %s 在回收站里超过 %.0f 天，已彻底删除", board_id, self.trash_ttl / 86400)
                purged.append(board_id)
        return purged

    def start_maintenance(self) -> None:
        """后台任务：马上清理一次过期的回收站，之后每 ``purge_interval`` 秒一次。"""
        if self._maintenance is None or self._maintenance.done():
            self._maintenance = asyncio.get_running_loop().create_task(self._maintain())

    async def stop_maintenance(self) -> None:
        task, self._maintenance = self._maintenance, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # 忽略：刚刚自己取消的，等它真正停下
            pass

    async def _maintain(self) -> None:
        while True:
            previous = self.current_id
            try:
                if await self.purge_expired():
                    await self.announce(previous)
            except Exception:  # noqa: BLE001 - 一次清理失败不能让后台任务停掉，下次再试
                log.exception("hub._maintain：清理回收站出错")
            await asyncio.sleep(self.purge_interval)

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
        # 导入的白板不进文件夹，也不在回收站里（备份里可能是一块删掉的白板）
        if models.folder_of(meta) or models.trashed_at(meta) is not None:
            patch["data"] = {"folder": None, "trashed": None}
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
        """删除就是移进回收站；彻底删除走 ``purge``。"""
        board_id = msg.get("board")
        previous = self.current_id
        if self._may(conn, "manage") and isinstance(board_id, str) and await self.trash_board(board_id):
            await self.announce(previous)

    async def _on_duplicate(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id = msg.get("board")
        if self._may(conn, "manage") and isinstance(board_id, str) and await self.duplicate_board(board_id):
            await self.broadcast_boards()

    async def _on_restore(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id = msg.get("board")
        if self._may(conn, "manage") and isinstance(board_id, str) and await self.restore_board(board_id):
            await self.broadcast_boards()

    async def _on_purge(self, conn: Conn, msg: Dict[str, Any]) -> None:
        board_id = msg.get("board")
        previous = self.current_id
        if self._may(conn, "manage") and isinstance(board_id, str) and await self.purge_board(board_id):
            await self.announce(previous)

    async def _on_empty_trash(self, conn: Conn, msg: Dict[str, Any]) -> None:
        previous = self.current_id
        if self._may(conn, "manage") and await self.empty_trash():
            await self.announce(previous)

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
        self.start_maintenance()

    async def stop(self) -> None:
        await self.stop_maintenance()
        await self.core.stop()

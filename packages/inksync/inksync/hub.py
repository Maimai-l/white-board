"""一个空间的运行时：连接、内存中的白板、操作、广播、保存、释放和事件。

设计要点：

* **本地优先**。客户端先在本地画，再把操作发给服务端；服务端只负责编号、
  转发与持久化，不参与绘制判断，因此本地书写不会被网络拖慢。
* **单调序号**。每个被接受的操作拿到一个递增 ``seq``；客户端记住自己收到
  的最后一个 ``seq``，断线重连时带上它，服务端补发缺失的操作；缺口太大
  （超出历史窗口）则直接补发整块白板。
* **保存不阻塞同步**。事件循环线程中只复制内容，编码和写盘在工作线程中进行；
  用改动计数判断保存期间是否又有新改动（docs/design/inksync-redesign.zh-CN.md 4.3 节）。
* **每条连接同一时间显示一块白板**，可以切换；操作带着自己所属的白板，
  切换之前没送达的操作照样落到原来那块。

所有方法都在事件循环线程中调用。
"""

from __future__ import annotations

import asyncio
import logging
import time
import zlib
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from . import models
from .policy import DefaultPolicy, Policy, Principal, RateLimiter
from .storage import BoardFileError, FileStorage

log = logging.getLogger(__name__)

# 保留的操作历史条数，决定断线多久之后需要全量补齐。
OPS_HISTORY = 4000
AUTOSAVE_INTERVAL = 3.0
IDLE_UNLOAD = 120.0
MAX_OP_STROKES = 2000


def _list(value: Any) -> List[Any]:
    """线上数据里本该是列表的字段：不是列表就当空的，不能让切片或遍历抛错。"""
    return value if isinstance(value, list) else []


def _copy_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(meta)
    for key in ("canvas", "background", "data"):
        if isinstance(out.get(key), dict):
            out[key] = dict(out[key])
    if isinstance(out.get("layers"), list):
        out["layers"] = [dict(layer) for layer in out["layers"]]
    return out


class BoardRuntime:
    """一块白板在内存中的状态。

    ``problem`` 不为空时这块白板是**锁住的**：磁盘上的文件没能完整读出来
    （损坏、个别笔画解不开、或者是更新的版本写的）。锁住的白板照常显示读得出来
    的内容，但不接受笔画上的任何修改，自动保存也不写它——写回就会用残缺的内容
    盖掉原件。用户确认之后可以解锁（见 :meth:`Hub.unlock`），解锁前先备份原文件。
    """

    def __init__(
        self,
        meta: Dict[str, Any],
        strokes: List[Dict[str, Any]],
        problem: Optional[Dict[str, Any]] = None,
        epoch: Optional[str] = None,
        seq: int = 0,
    ):
        self.meta = meta
        self.problem = problem
        # 每次服务进程把白板载入内存都换一个 epoch：重启后序号从头开始，旧客户端拿着
        # 重启前的序号来续传会被识别出来，改发整块白板。进程内释放后再载入沿用原来的。
        self.epoch = epoch or models.new_id()
        self.strokes: "OrderedDict[str, Dict[str, Any]]" = OrderedDict((s["id"], s) for s in strokes)
        self.seq = seq
        self.ops: Deque[Dict[str, Any]] = deque(maxlen=OPS_HISTORY)
        self.next_n = max((s.get("n", 0) for s in strokes), default=-1) + 1
        self.version = 0          # 每次改动加一
        self.saved_version = 0    # 已写盘的版本
        self.saving: Optional[Future] = None
        self.last_used = time.monotonic()

    @property
    def locked(self) -> bool:
        return self.problem is not None

    @property
    def dirty(self) -> bool:
        return self.version > self.saved_version

    def touch(self) -> None:
        self.last_used = time.monotonic()

    # ------------------------------------------------------------- 操作应用

    def _record(self, op: Dict[str, Any], touch: bool = True) -> Dict[str, Any]:
        self.seq += 1
        op["seq"] = self.seq
        self.ops.append(op)
        self.version += 1
        if touch:
            self.meta["updated"] = models.now()
        self.touch()
        return op

    def _insert(self, stroke: Dict[str, Any], keep_order: bool) -> Dict[str, Any]:
        if keep_order and "n" in stroke:
            self.next_n = max(self.next_n, stroke["n"] + 1)
        else:
            stroke["n"] = self.next_n
            self.next_n += 1
        self.strokes[stroke["id"]] = stroke
        return stroke

    def apply(
        self,
        raw: Any,
        allow_src: Callable[[str], bool] = models.default_allow_src,
        validate_data: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
        touch: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """校验并应用一个操作。返回 ``(规范化并带 seq 的操作, None)``；
        没有产生改动时返回 ``(None, None)``；不合法时返回 ``(None, "invalid")``。"""
        if not isinstance(raw, dict):
            return None, "invalid"
        if self.locked:
            return None, "locked"
        kind = raw.get("op")

        if kind in ("add", "restore"):
            if not isinstance(raw.get("strokes"), list):
                return None, "invalid"
            keep_order = kind == "restore"
            accepted: List[Dict[str, Any]] = []
            valid = 0
            for item in raw["strokes"][:MAX_OP_STROKES]:
                stroke = models.sanitize_stroke(item)
                if stroke is None:
                    continue
                valid += 1
                if stroke["id"] in self.strokes:
                    continue  # 重复 id 直接忽略，重连重发时不会画两遍
                accepted.append(self._insert(stroke, keep_order))
            if not accepted:
                return None, (None if valid else "invalid")
            return self._record({"op": "add", "strokes": accepted}), None

        if kind == "remove":
            ids = [i for i in models.sanitize_ids(raw.get("ids")) if i in self.strokes]
            if not ids:
                return None, None
            for stroke_id in ids:
                self.strokes.pop(stroke_id, None)
            return self._record({"op": "remove", "ids": ids}), None

        if kind == "mask":
            # 遮罩发的是全量而不是增量：遮罩本来就小，全量在断线重连、乱序到达
            # 的情况下都不会错，不用管顺序。遮罩整体赋值或删除，不原地修改列表，
            # 工作线程里正在编码的副本不受影响。
            changed: List[Dict[str, Any]] = []
            for item in _list(raw.get("masks"))[:MAX_OP_STROKES]:
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
                return None, None
            return self._record({"op": "mask", "masks": changed}), None

        if kind == "clear":
            if not self.strokes:
                return None, None
            self.strokes.clear()
            return self._record({"op": "clear"}), None

        if kind == "meta":
            merged = models.apply_meta_patch(self.meta, raw.get("meta"), allow_src)
            if merged is None:
                return None, "invalid"
            if validate_data is not None and merged.get("data") != self.meta.get("data"):
                data = validate_data(merged["data"])
                if data is None:
                    return None, "invalid"
                merged["data"] = models.sanitize_data(data) or {}
            if merged == self.meta:
                return None, None
            self.meta = merged
            return self._record({"op": "meta", "meta": _copy_meta(merged)}, touch), None

        return None, "invalid"

    # --------------------------------------------------------------- 同步

    def ops_since(self, since: int) -> Optional[List[Dict[str, Any]]]:
        """返回 ``seq > since`` 的操作；历史不足以补齐时返回 None。"""
        if since == self.seq:
            return []
        if since > self.seq:
            return None
        if not self.ops or self.ops[0]["seq"] > since + 1:
            return None
        return [op for op in self.ops if op["seq"] > since]

    def stroke_list(self) -> List[Dict[str, Any]]:
        return sorted(self.strokes.values(), key=lambda s: s.get("n", 0))


class Conn:
    """一条连接。``principal`` 在连接建立时确定；``board`` 是它当前显示的白板。

    ``ext`` 留给扩展保存自己的状态（例如白板应用记下「跟随当前白板」）。"""

    __slots__ = ("id", "ws", "principal", "request", "device", "board", "readonly", "caps",
                 "legacy", "ext", "connected_at", "hub")

    def __init__(self, hub: "Hub", client_id: str, ws: Any, principal: Principal, request: Any = None,
                 device: str = "", readonly: bool = False, legacy: bool = False):
        self.hub = hub
        self.id = client_id
        self.ws = ws
        self.principal = principal
        self.request = request
        self.device = device
        self.board: Optional[str] = None
        self.readonly = readonly
        self.caps: Dict[str, bool] = {}
        self.legacy = legacy
        self.ext: Dict[str, Any] = {}
        self.connected_at = time.time()

    async def send(self, message: Dict[str, Any]) -> None:
        await self.hub.send(self, message)


Handler = Callable[[Conn, Dict[str, Any]], Awaitable[None]]


class Hub:
    """一个空间：见模块说明。"""

    def __init__(
        self,
        storage: FileStorage,
        policy: Optional[Policy] = None,
        autosave: float = AUTOSAVE_INTERVAL,
        idle_unload: float = IDLE_UNLOAD,
        accept_v1: bool = False,
    ):
        self.storage = storage
        self.policy = policy or DefaultPolicy()
        self.autosave = autosave
        self.idle_unload = idle_unload
        # 1.0.x 页面（hello 里没有 v）是否接受，见 docs/design/inksync-redesign.zh-CN.md 7.3 节
        self.accept_v1 = accept_v1
        self.name = ""
        self.conns: Dict[str, Conn] = {}
        self._runtimes: Dict[str, BoardRuntime] = {}
        self._retained: Dict[str, Tuple[str, int]] = {}
        self._loading: Dict[str, "asyncio.Future[Optional[BoardRuntime]]"] = {}
        self._deleted: set = set()
        self._io = ThreadPoolExecutor(max_workers=2, thread_name_prefix="inksync-io")
        self._handlers: Dict[str, Handler] = {}
        self._events: Dict[str, List[Callable[..., Any]]] = {}
        self._hello: Optional[Callable[[Conn, Dict[str, Any]], Awaitable[Optional[str]]]] = None
        self._encoder: Optional[Callable[[Conn, Dict[str, Any]], Optional[Dict[str, Any]]]] = None
        self._limiter = RateLimiter()
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ 扩展

    def on(self, event: str, callback: Callable[..., Any]) -> Callable[[], None]:
        """``created``、``saved``、``deleted``：``callback(board_id, meta)``；
        ``changed``：``callback(board_id, meta, op)``。返回取消订阅的函数。"""
        self._events.setdefault(event, []).append(callback)
        return lambda: self._events.get(event, []).remove(callback) if callback in self._events.get(event, []) else None

    def _emit(self, event: str, *args: Any) -> None:
        for callback in list(self._events.get(event, [])):
            try:
                callback(*args)
            except Exception:  # noqa: BLE001 - 使用者的回调出错不能影响同步
                log.exception("事件 %s 的回调出错", event)

    def register(self, name: str, handler: Handler) -> None:
        """注册一种扩展消息。``handler(conn, msg)`` 是协程函数。"""
        from .server import CORE_MESSAGES

        if name in CORE_MESSAGES:
            raise ValueError(f"{name} 是核心消息")
        self._handlers[name] = handler

    def handler(self, name: str) -> Optional[Handler]:
        return self._handlers.get(name)

    def set_hello(self, handler: Callable[[Conn, Dict[str, Any]], Awaitable[Optional[str]]]) -> None:
        """``hello`` 带 ``follow`` 或没有 ``board`` 时，由 ``handler(conn, msg)`` 决定打开哪块白板。"""
        self._hello = handler

    @property
    def hello_handler(self):
        return self._hello

    def set_encoder(self, encoder: Callable[[Conn, Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
        """发给每个连接的每条消息先经过 ``encoder(conn, msg)``；返回 None 表示不发。"""
        self._encoder = encoder

    # ------------------------------------------------------------ 查询

    def board_meta(self, board_id: str) -> Optional[Dict[str, Any]]:
        runtime = self._runtimes.get(board_id)
        if runtime is not None:
            return _copy_meta(runtime.meta)
        return self.storage.get_meta(board_id)

    def exists(self, board_id: str) -> bool:
        return board_id not in self._deleted and self.storage.exists(board_id)

    def list_boards(self, offset: int = 0, limit: Optional[int] = 100, prefix: str = "", order: Any = "updated"):
        return self.storage.list(offset=offset, limit=limit, prefix=prefix, order=order)

    def count(self, prefix: str = "") -> int:
        return self.storage.count(prefix)

    def connections(self) -> List[Conn]:
        return list(self.conns.values())

    def runtime(self, board_id: str) -> Optional[BoardRuntime]:
        """已经在内存中的白板；没有载入时返回 None。"""
        return self._runtimes.get(board_id)

    def loaded(self) -> List[str]:
        return list(self._runtimes)

    # ------------------------------------------------------------ 载入

    def _install(self, board_id: str, result) -> BoardRuntime:
        meta, strokes, problem = result
        indexed = self.storage.get_meta(board_id)
        if indexed is not None and not self.storage.board_path(board_id).exists():
            meta = indexed  # 新建之后还没写过盘
        epoch, seq = self._retained.pop(board_id, (None, 0))
        runtime = BoardRuntime(meta, strokes, problem, epoch=epoch, seq=seq)
        self._runtimes[board_id] = runtime
        return runtime

    def board(self, board_id: str) -> BoardRuntime:
        """取内存中的白板，没有就在当前线程同步读入。扩展和测试用；协议处理用 :meth:`load`。"""
        runtime = self._runtimes.get(board_id)
        if runtime is None:
            runtime = self._install(board_id, self.storage.read_file(board_id))
        runtime.touch()
        return runtime

    async def load(self, board_id: str) -> Optional[BoardRuntime]:
        """取内存中的白板，没有就在工作线程中读入。白板不存在或已删除时返回 None。
        同一块白板同时只有一个载入任务。"""
        runtime = self._runtimes.get(board_id)
        if runtime is not None:
            runtime.touch()
            return runtime
        if not self.exists(board_id):
            return None
        pending = self._loading.get(board_id)
        if pending is not None:
            return await asyncio.shield(pending)
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[Optional[BoardRuntime]]" = loop.create_future()
        self._loading[board_id] = future
        try:
            result = await loop.run_in_executor(self._io, self.storage.read_file, board_id)
            if board_id in self._deleted or not self.storage.exists(board_id):
                runtime = None
            else:
                runtime = self._runtimes.get(board_id) or self._install(board_id, result)
                runtime.touch()
            future.set_result(runtime)
            return runtime
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            self._loading.pop(board_id, None)
            if not future.done():
                future.cancel()
            elif future.exception() is not None:
                future.exception()  # 已取走，避免「从未取出的异常」警告

    async def strokes(self, board_id: str) -> Optional[List[Dict[str, Any]]]:
        """当前全部笔画的副本，按层叠顺序；白板不存在时返回 None。"""
        runtime = await self.load(board_id)
        if runtime is None:
            return None
        return [dict(s) for s in runtime.stroke_list()]

    # ------------------------------------------------------------ 新建、修改、删除

    async def create_board(self, board_id: Optional[str], spec: Optional[Dict[str, Any]] = None,
                           meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """由服务端新建白板。``spec`` 与前端的 ``create`` 相同；已存在或不合法时抛 ValueError。

        ``meta`` 直接给出完整元数据（扩展用，例如白板应用的文档板）。"""
        if meta is None:
            if board_id is not None and not models.is_board_id(board_id):
                raise ValueError("白板 id 不合法")
            meta = models.new_board_meta(board_id, spec, self.policy.allow_src)
            if meta["data"]:
                data = self.policy.validate_data(meta["data"])
                if data is None:
                    raise ValueError("data 不合法")
                meta["data"] = models.sanitize_data(data) or {}
        else:
            meta = models.sanitize_meta(meta, self.policy.allow_src)
        board_id = meta["id"]
        if self.storage.exists(board_id):
            raise ValueError(f"白板 {board_id} 已存在")
        self._deleted.discard(board_id)
        self._retained.pop(board_id, None)
        loop = asyncio.get_running_loop()
        mtime = await loop.run_in_executor(self._io, self.storage.write_file, meta, [])
        self.storage.put_meta(meta, mtime)
        self._runtimes[board_id] = BoardRuntime(dict(meta), [])
        log.info("新建白板 %s（空间 %r）", board_id, self.name)
        self._emit("created", board_id, _copy_meta(meta))
        return _copy_meta(meta)

    async def edit_meta(self, board_id: str, patch: Dict[str, Any], touch: bool = True,
                        quiet: bool = False) -> Optional[Dict[str, Any]]:
        """服务端修改元数据：不受规则限制，记入操作并发给显示这块白板的连接。

        ``touch`` 为 False 时不改 ``updated``（例如改名、归类这类整理动作）。
        ``quiet`` 为 True 时只改元数据、不记操作也不广播，由调用方自己通知（例如白板
        应用改名之后发整份白板列表）。
        返回新的元数据；白板不存在、patch 不合法或没有改动时返回 None。"""
        runtime = await self.load(board_id)
        if runtime is None:
            return None
        if runtime.locked:
            # 锁住的白板不接受操作，自动保存也不写它：只改文件里的元数据，笔画原样保留
            merged = models.apply_meta_patch(runtime.meta, patch, self.policy.allow_src)
            if merged is None or merged == runtime.meta:
                return None
            loop = asyncio.get_running_loop()
            try:
                mtime = await loop.run_in_executor(self._io, self.storage.rewrite_meta, merged)
            except (OSError, ValueError, zlib.error) as exc:
                log.error("白板 %s 改元数据时写文件失败：%s", board_id, exc)
                return None
            runtime.meta = merged
            self.storage.put_meta(merged, mtime)
            return _copy_meta(merged)
        if quiet:
            merged = models.apply_meta_patch(runtime.meta, patch, self.policy.allow_src)
            if merged is None or merged == runtime.meta:
                return None
            runtime.meta = merged
            runtime.version += 1
            self.storage.put_meta(runtime.meta)
            return _copy_meta(runtime.meta)
        op, _reason = runtime.apply({"op": "meta", "meta": patch}, self.policy.allow_src, touch=touch)
        if op is None:
            return None
        await self._after_op(board_id, runtime, op, source=None)
        return _copy_meta(runtime.meta)

    async def delete_board(self, board_id: str) -> bool:
        """删除白板。显示它的连接收到 ``deleted``；之后针对它的操作只回执，不应用。"""
        if not self.storage.exists(board_id) or board_id in self._deleted:
            return False
        self._deleted.add(board_id)
        runtime = self._runtimes.get(board_id)
        if runtime is not None and runtime.saving is not None:
            try:
                await asyncio.wrap_future(runtime.saving)
            except Exception:  # noqa: BLE001 - 保存失败也照样删
                pass
        self._runtimes.pop(board_id, None)
        self._retained.pop(board_id, None)
        meta = self.storage.get_meta(board_id) or {"id": board_id}
        self.storage.drop(board_id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._io, self.storage.delete_file, board_id)
        log.info("删除白板 %s（空间 %r）", board_id, self.name)
        self._emit("deleted", board_id, meta)
        for conn in self.connections():
            if conn.board == board_id:
                await conn.send({"t": "deleted", "board": board_id})
        return True

    def is_deleted(self, board_id: str) -> bool:
        return board_id in self._deleted

    async def import_file(self, path: Path) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """把存储目录之外的 ``.wbz`` 复制成一块新白板，返回 ``(meta, 原元数据, 原 id)``。
        文件读不出来时抛 :class:`BoardFileError`。"""
        board_id = models.new_id()
        loop = asyncio.get_running_loop()
        meta, mtime, raw_meta, old_id = await loop.run_in_executor(self._io, self.storage.copy_in, path, board_id)
        self.storage.put_meta(meta, mtime)
        self._emit("created", board_id, _copy_meta(meta))
        return meta, raw_meta, old_id

    async def unlock(self, board_id: str) -> bool:
        """解锁一块读不全的白板：先把原文件备份，之后照常编辑、存盘。备份失败就不解锁。"""
        runtime = await self.load(board_id)
        if runtime is None or not runtime.locked:
            return False
        loop = asyncio.get_running_loop()
        try:
            backup = await loop.run_in_executor(self._io, self.storage.backup_file, board_id)
        except OSError as exc:
            log.error("白板 %s 解锁前备份失败，保持只读：%s", board_id, exc)
            return False
        log.warning("白板 %s 已解锁（%s），原文件备份在 %s", board_id, runtime.problem.get("reason"), backup)
        runtime.problem = None
        await self.broadcast_board(board_id, {"t": "locked", "board": board_id, "locked": None})
        return True

    def forget(self, board_id: str) -> None:
        """把一块白板从内存中移除（例如锁住的白板再次打开前重新读取）。有改动时不移除。"""
        runtime = self._runtimes.get(board_id)
        if runtime is not None and not runtime.dirty and runtime.saving is None:
            self._runtimes.pop(board_id, None)

    # ------------------------------------------------------------ 连接与快照

    def caps(self, conn: Conn, meta: Dict[str, Any]) -> Dict[str, bool]:
        return self.policy.caps(conn.principal, meta, readonly=conn.readonly)

    def snapshot(self, conn: Conn, runtime: BoardRuntime, reply: str = "init",
                 since: Any = None, epoch: Any = None, requested: Any = None) -> Dict[str, Any]:
        conn.caps = self.caps(conn, runtime.meta)
        common = {
            "board": _copy_meta(runtime.meta),
            "seq": runtime.seq,
            "epoch": runtime.epoch,
            "locked": runtime.problem,
            "caps": dict(conn.caps),
            "readonly": conn.readonly,
        }
        # 断线重连：白板没换、epoch 一致且历史够长时只补差量，避免整块白板重传。
        if (
            reply == "init"
            and requested == runtime.meta["id"]
            and epoch == runtime.epoch
            and isinstance(since, int)
            and not isinstance(since, bool)
            and since >= 0
        ):
            ops = runtime.ops_since(since)
            if ops is not None:
                return {"t": "sync", "ops": ops, **common}
        return {"t": reply, "strokes": runtime.stroke_list(), **common}

    async def open(self, conn: Conn, board_id: str, reply: str = "init", since: Any = None,
                   epoch: Any = None, requested: Any = None, extra: Optional[Dict[str, Any]] = None) -> bool:
        """把连接移到 ``board_id`` 并发送快照。白板不存在时返回 False。"""
        runtime = await self.load(board_id)
        if runtime is None:
            return False
        conn.board = board_id
        message = self.snapshot(conn, runtime, reply, since, epoch, requested)
        if extra:
            message.update(extra)
        await conn.send(message)
        return True

    async def admit(self, conn: Conn, board_id: str, create: Any) -> Optional[str]:
        """按规则检查能否打开（或新建）这块白板；需要时新建。允许时返回 None，否则返回原因。"""
        if not models.is_board_id(board_id):
            return "board"
        who = conn.principal
        if self.exists(board_id):
            meta = self.board_meta(board_id)
            return None if self.policy.can_open(who, board_id, meta) else "denied"
        if create is None:
            return "board"
        if not isinstance(create, dict):
            return "create"
        if not self.policy.can_create(who, board_id, create):
            return "denied"
        if not self._limiter.allow(RateLimiter.key(who), self.policy.create_limit(who)):
            log.warning("新建白板过于频繁：%s", RateLimiter.key(who))
            return "rate"
        try:
            await self.create_board(board_id, create)
        except ValueError as exc:
            log.warning("拒绝新建白板 %s：%s", board_id, exc)
            return "create"
        return None

    def attach(self, conn: Conn) -> None:
        old = self.conns.get(conn.id)
        self.conns[conn.id] = conn
        if old is not None and old is not conn:
            log.info("连接 %s 重新登记，旧连接不再接收广播", conn.id)

    def detach(self, conn: Conn) -> None:
        # 同一个客户端 id 可能已经用新连接重新登记（iOS 唤醒后旧 socket 才断开），
        # 只有登记的还是自己时才注销，否则会把新连接从广播名单里摘掉。
        if self.conns.get(conn.id) is conn:
            self.conns.pop(conn.id, None)

    # ------------------------------------------------------------ 操作

    async def handle_op(self, conn: Conn, board_id: Optional[str], raw: Any, cid: Any) -> None:
        """一条 ``op`` 消息：按规则检查、应用、回执、广播。每条消息都回执且只回执一次。"""
        board_id = board_id if isinstance(board_id, str) else conn.board
        ack: Dict[str, Any] = {"t": "ack", "cid": cid, "board": board_id, "seq": 0}

        if board_id is None or board_id in self._deleted or not self.storage.exists(board_id):
            ack["rejected"] = "deleted"
            await conn.send(ack)
            return
        runtime = await self.load(board_id)
        if runtime is None:
            ack["rejected"] = "deleted"
            await conn.send(ack)
            return
        ack["seq"] = runtime.seq
        who = conn.principal
        meta = runtime.meta
        kind = raw.get("op") if isinstance(raw, dict) else None

        denied = conn.readonly
        if not denied and board_id != conn.board:
            denied = not self.policy.can_open(who, board_id, meta)
        if not denied:
            if kind == "meta":
                patch = raw.get("meta")
                protected = self.policy.protected_data_keys
                touches = isinstance(patch, dict) and isinstance(patch.get("data"), dict) and any(
                    key in protected for key in patch["data"]
                )
                denied = touches or not self.policy.can_edit_meta(who, meta, patch if isinstance(patch, dict) else {})
            elif kind == "clear":
                denied = not (self.policy.can_write(who, meta) and self.policy.can_clear(who, meta))
            else:
                denied = not self.policy.can_write(who, meta)
        if denied:
            ack["rejected"] = "denied"
            await conn.send(ack)
            return

        op, reason = runtime.apply(raw, self.policy.allow_src, self.policy.validate_data)
        if op is None:
            if reason is not None:
                ack["rejected"] = reason
            await conn.send(ack)
            return
        ack["seq"] = op["seq"]
        ack["op"] = op
        await conn.send(ack)
        await self._after_op(board_id, runtime, op, source=conn)

    async def _after_op(self, board_id: str, runtime: BoardRuntime, op: Dict[str, Any],
                        source: Optional[Conn]) -> None:
        if op["op"] == "meta":
            self.storage.put_meta(runtime.meta)
        await self.broadcast_board(
            board_id,
            {"t": "op", "op": op, "src": source.id if source else "", "board": board_id},
            exclude=source.id if source else None,
        )
        self._emit("changed", board_id, _copy_meta(runtime.meta), op)

    # ------------------------------------------------------------ 发送

    async def send(self, conn: Conn, message: Dict[str, Any]) -> None:
        if self._encoder is not None:
            try:
                message = self._encoder(conn, message)
            except Exception:  # noqa: BLE001 - 改写出错就原样发
                log.exception("改写消息出错")
            if message is None:
                return
        try:
            await conn.ws.send_json(message)
        except (ConnectionResetError, RuntimeError, ValueError) as exc:
            log.debug("发送失败 %s：%s", conn.id, exc)

    async def broadcast_board(self, board_id: str, message: Dict[str, Any], exclude: Optional[str] = None) -> None:
        """发给所有正在显示 ``board_id`` 的连接。"""
        targets = [c for c in self.connections() if c.id != exclude and c.board == board_id]
        if targets:
            await asyncio.gather(*(self.send(c, message) for c in targets))

    async def broadcast(self, message: Dict[str, Any], where: Optional[Callable[[Conn], bool]] = None,
                        exclude: Optional[str] = None) -> None:
        targets = [c for c in self.connections() if c.id != exclude and (where is None or where(c))]
        if targets:
            await asyncio.gather(*(self.send(c, message) for c in targets))

    # ------------------------------------------------------------ 权限变化

    async def refresh(self) -> None:
        """规则的判断依据变了：重新计算每个连接的 ``caps``，有变化就通知它。"""
        for conn in self.connections():
            if conn.board is None:
                continue
            meta = self.board_meta(conn.board)
            if meta is None:
                continue
            caps = self.caps(conn, meta)
            if caps != conn.caps:
                conn.caps = caps
                await conn.send({"t": "caps", "caps": dict(caps)})

    async def reauthenticate(self, authenticate: Callable[[Any], Principal]) -> List[Conn]:
        """按保存的请求重新计算每个连接的身份，返回身份有变化的连接，然后 :meth:`refresh`。"""
        changed = []
        for conn in self.connections():
            try:
                principal = authenticate(conn.request) if conn.request is not None else conn.principal
            except Exception:  # noqa: BLE001 - 算不出来就按匿名的其他设备处理
                log.exception("重新计算 %s 的身份出错", conn.id)
                principal = Principal(id=None, local=False, address=conn.principal.address)
            if principal != conn.principal:
                conn.principal = principal
                changed.append(conn)
        await self.refresh()
        return changed

    # ------------------------------------------------------------ 保存与释放

    def _copy_for_save(self, runtime: BoardRuntime) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
        # 浅复制每个笔画字典：点列在创建后不再修改，遮罩整体赋值或删除，
        # 所以复制之后事件循环线程上的改动不会影响工作线程里的这一份。
        return runtime.version, _copy_meta(runtime.meta), [dict(s) for s in runtime.stroke_list()]

    def _start_save(self, board_id: str, runtime: BoardRuntime) -> None:
        version, meta, strokes = self._copy_for_save(runtime)
        deleted = self._deleted
        future = self._io.submit(self.storage.write_file, meta, strokes, lambda: board_id in deleted)
        runtime.saving = future
        loop = asyncio.get_running_loop()

        def done(fut: Future) -> None:
            loop.call_soon_threadsafe(self._save_done, board_id, runtime, version, fut)

        future.add_done_callback(done)

    def _save_done(self, board_id: str, runtime: BoardRuntime, version: int, fut: Future) -> None:
        if runtime.saving is fut:
            runtime.saving = None
        try:
            mtime = fut.result()
        except Exception:  # noqa: BLE001 - 失败时已保存版本不变，下一轮重试
            log.exception("白板 %s 保存失败", board_id)
            return
        if mtime is None or board_id in self._deleted:
            return
        runtime.saved_version = max(runtime.saved_version, version)
        if self._runtimes.get(board_id) is runtime:
            self.storage.put_meta(runtime.meta, mtime)
        self._emit("saved", board_id, _copy_meta(runtime.meta))

    def tick(self) -> None:
        """自动保存的一轮：开始保存有改动的白板，释放长期未用的白板。"""
        stamp = time.monotonic()
        for board_id, runtime in list(self._runtimes.items()):
            if runtime.saving is not None or runtime.locked:
                continue
            if runtime.dirty:
                try:
                    self._start_save(board_id, runtime)
                except Exception:  # noqa: BLE001
                    log.exception("白板 %s 开始保存失败", board_id)
                continue
            if stamp - runtime.last_used < self.idle_unload or board_id in self._loading:
                continue
            if any(conn.board == board_id for conn in self.conns.values()):
                continue
            self._runtimes.pop(board_id, None)
            self._retained[board_id] = (runtime.epoch, runtime.seq)
            log.debug("释放白板 %s", board_id)

    def save_all(self) -> None:
        """在当前线程同步保存所有有改动的白板（关闭前、测试中使用）。
        进行中的保存先等它写完。"""
        for board_id, runtime in list(self._runtimes.items()):
            if runtime.saving is not None:
                try:
                    runtime.saving.result()
                except Exception:  # noqa: BLE001
                    pass
            if not runtime.dirty or runtime.locked or board_id in self._deleted:
                continue
            version, meta, strokes = self._copy_for_save(runtime)
            try:
                mtime = self.storage.write_file(meta, strokes)
            except Exception:  # noqa: BLE001 - 任何一块出错都不能挡住其余几块
                log.exception("白板 %s 保存失败", board_id)
                continue
            runtime.saved_version = max(runtime.saved_version, version)
            self.storage.put_meta(runtime.meta, mtime)
            self._emit("saved", board_id, _copy_meta(runtime.meta))

    async def flush(self) -> None:
        """等待进行中的保存，再保存所有剩余改动。"""
        pending = [r.saving for r in self._runtimes.values() if r.saving is not None]
        for fut in pending:
            try:
                await asyncio.wrap_future(fut)
            except Exception:  # noqa: BLE001
                pass
        self.save_all()
        self.storage.flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.autosave)
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 自动保存不能把后台任务打死
                log.exception("自动保存出错")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    # 1.0.x 的名字
    start_autosave = start

    async def close_connections(self) -> None:
        """关服务前先把连接断干净，否则 aiohttp 会一直等这些长连接的处理协程结束。"""
        conns = self.connections()
        self.conns.clear()
        for conn in conns:
            try:
                await conn.ws.close(code=1001, message=b"shutdown")
            except (ConnectionResetError, RuntimeError, OSError) as exc:
                log.debug("关闭连接 %s 失败：%s", conn.id, exc)

    close_clients = close_connections

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    def idle(self) -> bool:
        """没有连接，内存中也没有未保存的改动。"""
        return not self.conns and not any(r.dirty or r.saving for r in self._runtimes.values())


__all__ = ["BoardRuntime", "Conn", "Hub", "OPS_HISTORY", "AUTOSAVE_INTERVAL", "BoardFileError"]

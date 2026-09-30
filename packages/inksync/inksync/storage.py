"""白板的磁盘存储（2.0）。

一个存储目录对应一个空间::

    <root>/boards/<id>.wbz   白板文件：元数据与笔画，JSON 经 zlib 压缩（docs/format.md）
    <root>/index.sqlite      索引：每块白板的元数据，可以随时从白板文件重建
    <root>/space.json        空间级的小数据（kv），不属于索引，重建索引时不受影响

方法分两类（docs/design/inksync-2.zh-CN.md 4.5 节）：

* 索引部分（``get_meta``、``list`` 等）只在事件循环线程中调用。查询只读内存；
  写入交给一个专用线程按顺序写进 SQLite，调用方不等待。
* 文件部分（``read_file``、``write_file`` 等）在工作线程中调用，只读写白板文件，
  不碰索引。结果由调用方回到事件循环线程后登记（``put_meta``）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import codec, models

log = logging.getLogger(__name__)

FILE_VERSION = 2
BOARD_SUFFIX = ".wbz"
MAX_KV_BYTES = 1024 * 1024

ConvertMeta = Callable[[Dict[str, Any]], Dict[str, Any]]


class BoardFileError(ValueError):
    """白板文件的内容不是预期的结构（能解压、能解析，但不是那个形状）。"""


def atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再替换，避免关机 / 断电时留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# 旧名字，白板应用沿用
_atomic_write = atomic_write


def _safe_id(board_id: str) -> str:
    safe = "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")
    if not safe or safe != board_id:
        raise ValueError(f"非法白板 id：{board_id!r}")
    return safe


class KeyValue:
    """空间级的小数据，存在 ``space.json``。每次修改同步写入。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: Dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except (OSError, ValueError) as exc:
                log.error("%s 读不出来，按空的处理：%s", path, exc)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        return json.loads(json.dumps(value)) if value is not None else default

    def set(self, key: str, value: Any) -> None:
        data = dict(self._data)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if len(blob) > MAX_KV_BYTES:
            raise ValueError("space.json 超过 1 MB")
        atomic_write(self.path, blob)
        self._data = data

    def keys(self) -> List[str]:
        return list(self._data)


class _Index:
    """SQLite 里的索引行：id、元数据 JSON、白板文件的修改时间。

    连接只在专用线程中建立和使用（sqlite3 的连接不能跨线程）。"""

    def __init__(self, path: Path):
        self.path = path
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inksync-index")
        self._local = threading.local()

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(str(self.path))
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS boards (id TEXT PRIMARY KEY, meta TEXT NOT NULL, mtime REAL NOT NULL)"
            )
            db.commit()
            self._local.db = db
        return db

    def call(self, fn, *args) -> Future:
        return self._pool.submit(fn, *args)

    def load(self) -> Dict[str, Tuple[Dict[str, Any], float]]:
        def run():
            rows = self._db().execute("SELECT id, meta, mtime FROM boards").fetchall()
            out = {}
            for board_id, text, mtime in rows:
                try:
                    out[board_id] = (json.loads(text), float(mtime))
                except ValueError:
                    continue
            return out

        return self.call(run).result()

    def put(self, meta: Dict[str, Any], mtime: float) -> Future:
        text = json.dumps(meta, ensure_ascii=False)

        def run():
            db = self._db()
            db.execute(
                "INSERT INTO boards (id, meta, mtime) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET meta = excluded.meta, mtime = excluded.mtime",
                (meta["id"], text, mtime),
            )
            db.commit()

        return self.call(run)

    def put_many(self, rows: Iterable[Tuple[Dict[str, Any], float]]) -> Future:
        items = [(meta["id"], json.dumps(meta, ensure_ascii=False), mtime) for meta, mtime in rows]

        def run():
            db = self._db()
            db.executemany(
                "INSERT INTO boards (id, meta, mtime) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET meta = excluded.meta, mtime = excluded.mtime",
                items,
            )
            db.commit()

        return self.call(run)

    def delete(self, board_ids: Iterable[str]) -> Future:
        ids = [(board_id,) for board_id in board_ids]

        def run():
            db = self._db()
            db.executemany("DELETE FROM boards WHERE id = ?", ids)
            db.commit()

        return self.call(run)

    def flush(self) -> None:
        self.call(lambda: None).result()

    def close(self) -> None:
        def run():
            db = getattr(self._local, "db", None)
            if db is not None:
                db.close()
                self._local.db = None

        try:
            self.call(run).result()
        finally:
            self._pool.shutdown(wait=True)


class FileStorage:
    """默认的存储实现。见模块说明。

    ``convert_meta`` 在读到 1.x 的白板文件时调用，参数是已经换成 2.0 字段的元数据，
    返回补充之后的元数据（白板应用用它为文档板生成页面图片层）。
    """

    def __init__(self, root: os.PathLike | str, convert_meta: Optional[ConvertMeta] = None,
                 allow_src: Callable[[str], bool] = models.default_allow_src):
        self.root = Path(root).expanduser()
        self.boards_dir = self.root / "boards"
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self.convert_meta = convert_meta
        self.allow_src = allow_src
        self.kv = KeyValue(self.root / "space.json")
        # 1.x 的 index.json（首次以 2.0 打开 1.x 存储目录时读出来，交给使用者迁移）
        self.legacy_index: Optional[Dict[str, Any]] = None
        self._metas: Dict[str, Dict[str, Any]] = {}
        self._mtimes: Dict[str, float] = {}
        self._index = _Index(self.root / "index.sqlite")
        self._closed = False
        self._open_index()

    # ------------------------------------------------------------ 启动

    def _open_index(self) -> None:
        legacy = self.root / "index.json"
        fresh = not (self.root / "index.sqlite").exists()
        if fresh and legacy.exists():
            try:
                raw = json.loads(legacy.read_text("utf-8"))
                self.legacy_index = raw if isinstance(raw, dict) else None
            except (OSError, ValueError) as exc:
                log.warning("1.x 的 index.json 读不出来：%s", exc)
        try:
            rows = self._index.load()
        except sqlite3.DatabaseError as exc:
            log.error("索引损坏，重建：%s", exc)
            self._index.close()
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.root / "index.sqlite") + suffix).unlink(missing_ok=True)
            self._index = _Index(self.root / "index.sqlite")
            rows = {}

        on_disk: Dict[str, float] = {}
        for path in self.boards_dir.glob(f"*{BOARD_SUFFIX}"):
            if not models.is_board_id(path.stem):
                continue
            try:
                on_disk[path.stem] = path.stat().st_mtime
            except OSError:
                continue

        stale = [board_id for board_id in rows if board_id not in on_disk]
        refreshed: List[Tuple[Dict[str, Any], float]] = []
        for board_id, mtime in on_disk.items():
            row = rows.get(board_id)
            if row is not None and abs(row[1] - mtime) < 1e-6:
                self._metas[board_id] = models.sanitize_meta(row[0], self.allow_src)
                self._mtimes[board_id] = mtime
                continue
            meta = self._read_meta(board_id)
            self._metas[board_id] = meta
            self._mtimes[board_id] = mtime
            refreshed.append((meta, mtime))
        if stale:
            self._index.delete(stale)
        if refreshed:
            self._index.put_many(refreshed)
            log.info("索引补齐 %d 块白板", len(refreshed))
        self._index.flush()

    def _read_meta(self, board_id: str) -> Dict[str, Any]:
        """启动时补索引：只要元数据。读不出来的文件也要留在列表里，打开时以只读方式提示。"""
        try:
            payload = self._read_payload(self._path(board_id))
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            return self._clean_meta({**meta, "id": board_id}, payload.get("v", 1))
        except (OSError, ValueError, zlib.error) as exc:
            log.warning("白板文件无法读取 %s：%s", board_id, exc)
            return models.sanitize_meta({"id": board_id, "updated": 0}, self.allow_src)

    def _clean_meta(self, raw: Dict[str, Any], version: Any) -> Dict[str, Any]:
        v1 = version == 1 or "canvas" not in raw
        if v1:
            raw = models.convert_v1_meta(raw)
        meta = models.sanitize_meta(raw, self.allow_src)
        if v1 and self.convert_meta is not None:
            try:
                meta = models.sanitize_meta(self.convert_meta(meta), self.allow_src)
            except Exception:  # noqa: BLE001 - 钩子出错不能让白板打不开
                log.exception("convert_meta 出错：%s", meta.get("id"))
        return meta

    # ------------------------------------------------------------ 索引（事件循环线程）

    def get_meta(self, board_id: str) -> Optional[Dict[str, Any]]:
        meta = self._metas.get(board_id)
        return json.loads(json.dumps(meta)) if meta is not None else None

    def exists(self, board_id: str) -> bool:
        return board_id in self._metas

    def ids(self) -> List[str]:
        return list(self._metas)

    def count(self, prefix: str = "") -> int:
        if not prefix:
            return len(self._metas)
        return sum(1 for board_id in self._metas if board_id.startswith(prefix))

    def list(self, offset: int = 0, limit: Optional[int] = 100, prefix: str = "",
             order: Any = "updated") -> List[Dict[str, Any]]:
        """元数据列表。``order``：``updated``、``created``（从新到旧）、``name``（升序），
        或一个白板 id 的列表（列表中的在前、按列表顺序，其余按 ``created`` 从新到旧排在前面）。"""
        metas = [m for board_id, m in self._metas.items() if not prefix or board_id.startswith(prefix)]
        if isinstance(order, list):
            position = {board_id: i for i, board_id in enumerate(order)}
            listed = sorted((m for m in metas if m["id"] in position), key=lambda m: position[m["id"]])
            rest = sorted((m for m in metas if m["id"] not in position), key=lambda m: -m["created"])
            metas = rest + listed
        elif order == "created":
            metas.sort(key=lambda m: -m["created"])
        elif order == "name":
            metas.sort(key=lambda m: (m["name"], m["id"]))
        else:
            metas.sort(key=lambda m: -m["updated"])
        end = None if limit is None else offset + max(0, int(limit))
        return json.loads(json.dumps(metas[offset:end]))

    def put_meta(self, meta: Dict[str, Any], mtime: Optional[float] = None) -> None:
        """登记（新增或更新）一块白板的元数据。``mtime`` 为白板文件的修改时间。"""
        meta = models.sanitize_meta(meta, self.allow_src)
        board_id = meta["id"]
        self._metas[board_id] = meta
        if mtime is None:
            mtime = self._mtimes.get(board_id, 0.0)
        self._mtimes[board_id] = mtime
        if not self._closed:
            self._index.put(meta, mtime)

    def drop(self, board_id: str) -> bool:
        if self._metas.pop(board_id, None) is None:
            return False
        self._mtimes.pop(board_id, None)
        if not self._closed:
            self._index.delete([board_id])
        return True

    def flush(self) -> None:
        """等待索引写完（测试和关闭时用）。"""
        self._index.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._index.close()

    # ------------------------------------------------------------ 文件（工作线程）

    def _path(self, board_id: str) -> Path:
        return self.boards_dir / f"{_safe_id(board_id)}{BOARD_SUFFIX}"

    def board_path(self, board_id: str) -> Path:
        return self._path(board_id)

    @staticmethod
    def _read_payload(path: Path) -> Dict[str, Any]:
        payload = json.loads(zlib.decompress(path.read_bytes()).decode("utf-8"))
        if not isinstance(payload, dict):
            raise BoardFileError("文件内容不是预期的结构")
        return payload

    def read_file(self, board_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """读一块白板，返回 ``(meta, strokes, problem)``。

        ``problem`` 为 None 表示文件完整读了出来；否则说明哪里不对，调用方据此
        把这块板锁成只读：

        * ``unreadable``：文件打不开（权限、外置盘没挂上……），可能只是暂时的；
        * ``corrupt``：打开了，但解压或解析失败，一笔都读不出来；
        * ``partial``：大部分读出来了，但有笔画解不开或者不合法；
        * ``newer``：文件是更新版本的程序写的，这一版可能认不全里面的内容。

        这些情况下原文件都不能被覆盖：写回去就只剩这一版读得出来的那部分。
        文件不存在时返回空白板（元数据取调用方登记的那份，由调用方补上）。
        """
        fallback = models.sanitize_meta({"id": board_id}, self.allow_src)
        path = self._path(board_id)
        if not path.exists():
            return fallback, [], None
        try:
            payload = self._read_payload(path)
        except OSError as exc:
            log.error("白板 %s 读取失败：%s", board_id, exc)
            return fallback, [], {"reason": "unreadable", "detail": str(exc)}
        except (ValueError, zlib.error) as exc:
            log.error("白板 %s 文件损坏：%s", board_id, exc)
            return fallback, [], {"reason": "corrupt", "detail": str(exc)}

        problem: Optional[Dict[str, Any]] = None
        version = payload.get("v", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version > FILE_VERSION:
            log.warning("白板 %s 由更新的版本写入（v=%r，本版 %d）", board_id, version, FILE_VERSION)
            problem = {"reason": "newer", "version": version}

        raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        meta = self._clean_meta({**raw_meta, "id": board_id}, version)
        raw_strokes = payload.get("strokes", [])
        if not isinstance(raw_strokes, list):
            raw_strokes = [raw_strokes]  # 让下面按「一条不合法的笔画」处理
        strokes: List[Dict[str, Any]] = []
        dropped = 0
        for index, raw in enumerate(raw_strokes):
            stroke = self._decode_stroke(raw)
            if stroke is None:
                dropped += 1
                continue
            stroke.setdefault("n", index)
            strokes.append(stroke)
        strokes.sort(key=lambda s: s["n"])
        if dropped and problem is None:
            log.error("白板 %s 有 %d 笔读不出来", board_id, dropped)
            problem = {"reason": "partial", "dropped": dropped}
        return meta, strokes, problem

    @staticmethod
    def _decode_stroke(raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        packed = raw.get("p")
        if isinstance(packed, str):
            try:
                raw = dict(raw, p=codec.decode_points_b64(packed))
            except (ValueError, TypeError) as exc:
                log.warning("笔画 %s 解码失败：%s", raw.get("id"), exc)
                return None
        return models.sanitize_stroke(raw)

    @staticmethod
    def encode(meta: Dict[str, Any], strokes: List[Dict[str, Any]]) -> bytes:
        payload = {
            "v": FILE_VERSION,
            "meta": meta,
            "strokes": [
                {
                    "id": s["id"],
                    "tool": s["tool"],
                    "color": s["color"],
                    "w": s["w"],
                    "n": s.get("n", i),
                    "dev": s.get("dev", ""),
                    "p": codec.encode_points_b64(s["p"]),
                    **({"cut": s["cut"]} if s.get("cut") else {}),
                    **({"m": s["m"]} if s.get("m") else {}),
                }
                for i, s in enumerate(strokes)
            ],
        }
        return zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6)

    def write_file(self, meta: Dict[str, Any], strokes: List[Dict[str, Any]],
                   skip: Optional[Callable[[], bool]] = None) -> Optional[float]:
        """写白板文件，返回文件修改时间。``skip()`` 在写入前调用，返回 True 时放弃写入
        并返回 None（白板在编码期间被删除）。"""
        blob = self.encode(meta, strokes)
        if skip is not None and skip():
            return None
        path = self._path(meta["id"])
        atomic_write(path, blob)
        return path.stat().st_mtime

    def rewrite_meta(self, meta: Dict[str, Any]) -> Optional[float]:
        """只改白板文件里的元数据，笔画原样保留（用于只读白板的改名）。

        更新版本写的文件里多出来的字段也保留。文件不存在时返回 None。"""
        path = self._path(meta["id"])
        if not path.exists():
            return None
        payload = self._read_payload(path)
        payload["meta"] = meta
        if payload.get("v", 1) < FILE_VERSION:
            payload["v"] = FILE_VERSION
        atomic_write(path, zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6))
        return path.stat().st_mtime

    def delete_file(self, board_id: str) -> None:
        self._path(board_id).unlink(missing_ok=True)

    def backup_file(self, board_id: str) -> Optional[Path]:
        """把白板文件原样复制到 ``backups/locked/``，返回副本路径；没有文件时返回 None。

        解锁一块读不全的白板之前调用：之后的存盘会用读得出来的内容覆盖原件。
        """
        path = self._path(board_id)
        if not path.exists():
            return None
        target_dir = self.root / "backups" / "locked"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{path.stem}-{stamp}{BOARD_SUFFIX}"
        counter = 1
        while target.exists():
            target = target_dir / f"{path.stem}-{stamp}-{counter}{BOARD_SUFFIX}"
            counter += 1
        shutil.copy2(path, target)
        return target

    def owns(self, path: Path) -> Optional[str]:
        """``path`` 是本存储目录里某块白板自己的文件时返回它的 id，否则返回 None。"""
        try:
            if path.resolve().parent != self.boards_dir.resolve() or path.suffix != BOARD_SUFFIX:
                return None
        except OSError:
            return None
        return path.stem if path.stem in self._metas else None

    def copy_in(self, path: Path, board_id: str) -> Tuple[Dict[str, Any], float, Dict[str, Any], str]:
        """把存储目录之外的一个 ``.wbz`` 复制成 id 为 ``board_id`` 的白板文件（工作线程）。

        文件内容原样保留，只换 id，所以读不全或由更新版本写入的文件导入后同样以只读
        白板打开。返回 ``(meta, mtime, 原文件的元数据, 原 id)``；登记由调用方完成。
        """
        try:
            payload = self._read_payload(path)
        except OSError as exc:
            raise BoardFileError(f"文件打不开：{exc}") from exc
        except (ValueError, zlib.error) as exc:
            raise BoardFileError("文件已损坏，读不出来") from exc
        raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        old_id = raw_meta.get("id") if isinstance(raw_meta.get("id"), str) else path.stem
        meta = self._clean_meta({**raw_meta, "id": board_id}, payload.get("v", 1))
        payload["meta"] = {**raw_meta, "id": board_id} if payload.get("v", 1) > FILE_VERSION else meta
        if isinstance(payload.get("v"), int) and payload["v"] < FILE_VERSION:
            payload["v"] = FILE_VERSION
        target = self._path(board_id)
        atomic_write(target, zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6))
        return meta, target.stat().st_mtime, raw_meta, old_id

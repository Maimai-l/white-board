"""白板的磁盘存储。

每块白板一个 ``<board_id>.wbz`` 文件：内容是 JSON 文本经 zlib 压缩，
其中笔画点数组已先用 :mod:`whiteboard.codec` 压成 base64 字节流。
另有一个未压缩的 ``index.json`` 保存白板列表元数据，避免列白板时
解压全部文件。索引丢失时可以从 ``.wbz`` 文件重建。

缩略图（``thumbs/<board_id>.png``）只是 Mac 端选择白板用的辅助图片，
白板内容本身始终是矢量数据。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import codec, models

log = logging.getLogger(__name__)

FILE_VERSION = 1
BOARD_SUFFIX = ".wbz"


class BoardFileError(ValueError):
    """白板文件的内容不是预期的结构（能解压、能解析，但不是那个形状）。"""


def _folder_list(raw: Any) -> List[str]:
    """索引里那份文件夹名单：只留字符串，去掉空的和重复的，顺序按存的来。"""
    names: List[str] = []
    if not isinstance(raw, list):
        return names
    for item in raw:
        name = models.sanitize_folder(item)
        if name and name not in names:
            names.append(name)
    return names


class BoardStore:
    """负责白板的持久化，不涉及任何网络 / 实时逻辑。"""

    def __init__(self, data_dir: os.PathLike | str):
        self.data_dir = Path(data_dir).expanduser()
        self.boards_dir = self.data_dir / "boards"
        self.thumbs_dir = self.data_dir / "thumbs"
        # 文档板的原件（PDF / 图片）原样放着，只读不改。
        self.docs_dir = self.data_dir / "docs"
        self.index_path = self.data_dir / "index.json"
        self.boards_dir.mkdir(parents=True, exist_ok=True)
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Any] = {"boards": [], "folders": [], "current": None}
        self._load_index()

    # ------------------------------------------------------------------ 索引

    def _load_index(self) -> None:
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text("utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("boards", []), list):
                    raise ValueError("索引不是预期的结构")
                boards = [models.sanitize_meta(m) for m in raw.get("boards", []) if isinstance(m, dict)]
                folders = _folder_list(raw.get("folders", []))
                current = raw.get("current")
                self._index = {
                    "boards": boards,
                    "folders": folders,
                    "current": current if isinstance(current, str) else None,
                }
            except (OSError, ValueError) as exc:
                log.warning("索引损坏，将重建：%s", exc)
                self._rebuild_index()
        else:
            self._rebuild_index()

        # 索引与实际文件对不上时以文件为准。
        known = {meta["id"] for meta in self._index["boards"]}
        on_disk = {path.stem for path in self.boards_dir.glob(f"*{BOARD_SUFFIX}")}
        if known - on_disk or on_disk - known:
            self._rebuild_index()

        self._sync_folders()
        if not self._index["boards"]:
            self.create_board()
        if self._index["current"] not in {m["id"] for m in self._index["boards"]}:
            self._index["current"] = self._index["boards"][0]["id"]
            self._write_index()

    def _rebuild_index(self) -> None:
        boards: List[Dict[str, Any]] = []
        for path in self.boards_dir.glob(f"*{BOARD_SUFFIX}"):
            try:
                payload = self._read_file(path)
                meta = payload.get("meta")
                meta = meta if isinstance(meta, dict) else {}
                # 文件名就是 id；meta 里的 id 读不出来时以文件名为准，否则这块板
                # 会换一个新 id 出现在列表里，却对应不到任何文件
                boards.append(models.sanitize_meta({**meta, "id": path.stem}))
            except (OSError, ValueError, zlib.error) as exc:
                # 读不出来的文件也要留在列表里：打开时以只读方式显示并提示，
                # 而不是让它从界面上消失、用户以为白板丢了
                log.warning("白板文件无法读取 %s：%s", path.name, exc)
                boards.append(models.sanitize_meta({"id": path.stem, "updated": 0}))
        boards.sort(key=lambda m: m.get("updated", 0), reverse=True)
        current = self._index.get("current") if self._index else None
        # 空文件夹只在索引里有记录，索引丢了就没了；至少把还装着白板的那些找回来。
        folders = _folder_list(self._index.get("folders", []) if self._index else [])
        self._index = {"boards": boards, "folders": folders, "current": current}
        self._sync_folders()
        if boards:
            self._write_index()

    def _sync_folders(self) -> None:
        """白板上写着的文件夹名一定要在名单里，否则那块白板会从界面上消失。"""
        known = set(self._index["folders"])
        for meta in self._index["boards"]:
            name = meta.get("folder")
            if name and name not in known:
                known.add(name)
                self._index["folders"].append(name)

    def _write_index(self) -> None:
        _atomic_write(self.index_path, json.dumps(self._index, ensure_ascii=False).encode("utf-8"))

    # ------------------------------------------------------------------ 查询

    def list_metas(self) -> List[Dict[str, Any]]:
        return [dict(meta) for meta in self._index["boards"]]

    def folders(self) -> List[str]:
        """现有的文件夹名。空文件夹也在里面，所以名单要单独存，不能从白板里现推。"""
        return list(self._index["folders"])

    def create_folder(self, name: str) -> str:
        """新建一个空文件夹，返回规整之后的名字；名字为空或者已经有了就返回空串。"""
        clean = models.sanitize_folder(name)
        if not clean or clean in self._index["folders"]:
            return ""
        self._index["folders"].append(clean)
        self._write_index()
        return clean

    def delete_folder(self, name: str) -> bool:
        """删掉文件夹本身，里面的白板移到没归类，不跟着删。"""
        clean = models.sanitize_folder(name)
        if clean not in self._index["folders"]:
            return False
        for meta in self.list_metas():
            if meta.get("folder") == clean:
                self.edit_meta(meta["id"], folder="")
        self._index["folders"] = [f for f in self._index["folders"] if f != clean]
        self._write_index()
        return True

    def rename_folder(self, name: str, to: str) -> bool:
        """给文件夹改名。名字就是身份，所以里面每块白板上记的名字都要跟着改。"""
        clean = models.sanitize_folder(name)
        target = models.sanitize_folder(to)
        if not target or clean not in self._index["folders"]:
            return False
        if target == clean:
            return False
        if target in self._index["folders"]:
            return False  # 重名会把两个文件夹并成一个，不如让界面上报错
        for meta in self.list_metas():
            if meta.get("folder") == clean:
                self.edit_meta(meta["id"], folder=target)
        self._index["folders"] = [target if f == clean else f for f in self._index["folders"]]
        self._write_index()
        return True

    def set_order(self, ids: Any) -> bool:
        """按 ``ids`` 给白板重新排序。

        列表里的白板只占用它们原来占着的那几个位置，别的白板一个都不动——界面上
        送来的是当前这一层看到的顺序（最外面那层，或者某个文件夹里那几块），
        不是全部白板。索引里的顺序就是界面上的顺序。
        """
        if not isinstance(ids, list):
            return False
        known = {meta["id"]: meta for meta in self._index["boards"]}
        wanted: List[str] = []
        for board_id in ids:
            if isinstance(board_id, str) and board_id in known and board_id not in wanted:
                wanted.append(board_id)
        if len(wanted) < 2:
            return False
        picked = set(wanted)
        slots = [i for i, meta in enumerate(self._index["boards"]) if meta["id"] in picked]
        if [self._index["boards"][i]["id"] for i in slots] == wanted:
            return False  # 顺序没变，不用写盘也不用广播
        for slot, board_id in zip(slots, wanted):
            self._index["boards"][slot] = known[board_id]
        self._write_index()
        return True

    def get_meta(self, board_id: str) -> Optional[Dict[str, Any]]:
        for meta in self._index["boards"]:
            if meta["id"] == board_id:
                return dict(meta)
        return None

    @property
    def current_id(self) -> Optional[str]:
        return self._index.get("current")

    def set_current(self, board_id: str) -> None:
        if self.get_meta(board_id):
            self._index["current"] = board_id
            self._write_index()

    # ------------------------------------------------------------- 创建 / 删除

    def create_board(self, name: str = "", **overrides: Any) -> Dict[str, Any]:
        meta = models.new_board_meta(name, **overrides)
        self._index["boards"].insert(0, meta)
        self._index["current"] = meta["id"]
        self.save_board(meta, [])
        self._write_index()
        return dict(meta)

    def delete_board(self, board_id: str) -> bool:
        meta = self.get_meta(board_id)
        if not meta:
            return False
        self._index["boards"] = [m for m in self._index["boards"] if m["id"] != board_id]
        self._board_path(board_id).unlink(missing_ok=True)
        self.thumb_path(board_id).unlink(missing_ok=True)
        doc = self.doc_path(board_id)
        if doc is not None:
            doc.unlink(missing_ok=True)
        if not self._index["boards"]:
            self.create_board()
        elif self._index["current"] == board_id:
            self._index["current"] = self._index["boards"][0]["id"]
        self._write_index()
        return True

    # ------------------------------------------------------------- 读取 / 写入

    def _board_path(self, board_id: str) -> Path:
        # board_id 已由 sanitize_meta 限制字符集，这里再兜一次底。
        safe = "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")
        if not safe:
            raise ValueError("非法白板 id")
        return self.boards_dir / f"{safe}{BOARD_SUFFIX}"

    def thumb_path(self, board_id: str) -> Path:
        safe = "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")
        return self.thumbs_dir / f"{safe}.png"

    def doc_path(self, board_id: str) -> Optional[Path]:
        """文档板的原件路径；不是文档板或文件丢了就返回 None。"""
        safe = "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")
        if not safe:
            return None
        for path in sorted(self.docs_dir.glob(f"{safe}.*")):
            if path.is_file():
                return path
        return None

    def import_doc(self, data: bytes, filename: str, name: str = "") -> Dict[str, Any]:
        """由一份 PDF / 图片新建文档板；文件原样存进 docs/。"""
        return self.register_doc(self.prepare_doc(data, filename), name)

    def prepare_doc(self, data: bytes, filename: str) -> Dict[str, Any]:
        """导入文档的前半段：把原件写进 docs/ 并读出页面信息。

        只碰 docs/ 下面一个新文件，不碰索引和任何白板，所以可以放到工作线程里跑
        （读大 PDF 要好一会儿）。后半段 :meth:`register_doc` 改索引，必须回到
        事件循环线程上做。
        """
        from . import docs  # 局部导入：没装 pypdf / pypdfium2 时其余功能照常

        suffix = Path(filename or "").suffix.lower()
        if suffix not in docs.SUFFIXES:
            raise docs.DocError(f"不支持的文件类型：{suffix or '无扩展名'}")
        if not data:
            raise docs.DocError("文件是空的")

        board_id = models.new_id()
        target = self.docs_dir / f"{board_id}{suffix}"
        _atomic_write(target, data)
        try:
            info = docs.probe(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        info["name"] = Path(filename).name  # 存的是改名后的副本，这里留原文件名
        return {"id": board_id, "info": info, "filename": filename}

    def register_doc(self, prepared: Dict[str, Any], name: str = "") -> Dict[str, Any]:
        """导入文档的后半段：为 :meth:`prepare_doc` 准备好的原件建板。"""
        filename = prepared["filename"]
        meta = self.create_board(
            name or Path(filename).stem,
            id=prepared["id"],
            kind="doc",
            background="blank",
            doc=prepared["info"],
        )
        log.info("新建文档板 %s：%s（%d 页）", meta["id"], filename, len(prepared["info"]["pages"]))
        return meta

    def board_id_of(self, path: Path) -> Optional[str]:
        """``path`` 是本存储目录里某块白板自己的文件时返回它的 id，否则返回 None。"""
        try:
            if path.resolve().parent != self.boards_dir.resolve() or path.suffix != BOARD_SUFFIX:
                return None
        except OSError:
            return None
        return path.stem if self.get_meta(path.stem) else None

    def import_board_file(self, path: Path) -> Dict[str, Any]:
        """把存储目录之外的一个 ``.wbz``（例如备份里的）复制成一块新白板。

        文件内容原样保留，只换成新的 id 并去掉文件夹（归到哪里由调用方决定），
        所以读不全或由更新版本写入的文件导入后同样以只读白板打开，不会丢内容。
        文档板还需要原件：在文件旁边的 ``docs/`` 或上一级的 ``docs/`` 里按原 id 查找。
        """
        try:
            payload = self._read_file(path)
        except OSError as exc:
            raise BoardFileError(f"文件打不开：{exc}") from exc
        except (ValueError, zlib.error) as exc:
            raise BoardFileError("文件已损坏，读不出来") from exc
        raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        old_id = raw_meta.get("id") if isinstance(raw_meta.get("id"), str) else path.stem
        board_id = models.new_id()
        raw_meta = dict(raw_meta, id=board_id)
        raw_meta.pop("folder", None)
        meta = models.sanitize_meta(raw_meta)

        original: Optional[Path] = None
        if meta["kind"] == "doc":
            from . import docs  # noqa: WPS433

            safe = "".join(ch for ch in old_id if ch.isalnum() or ch in "-_")
            for folder in (path.parent / "docs", path.parent.parent / "docs"):
                found = [
                    item for item in sorted(folder.glob(f"{safe}.*"))
                    if item.is_file() and item.suffix.lower() in docs.SUFFIXES
                ] if safe and folder.is_dir() else []
                if found:
                    original = found[0]
                    break
            if original is None:
                raise BoardFileError("这是文档板，但找不到它的原件（应在同一存储目录的 docs/ 里）")

        payload["meta"] = raw_meta
        if original is not None:
            shutil.copy2(original, self.docs_dir / f"{board_id}{original.suffix.lower()}")
        blob = zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6)
        _atomic_write(self._board_path(board_id), blob)
        self._index["boards"].insert(0, meta)
        self._index["current"] = board_id
        self._write_index()
        log.info("导入白板文件 %s 为 %s", path, board_id)
        return dict(meta)

    @staticmethod
    def _read_file(path: Path) -> Dict[str, Any]:
        blob = path.read_bytes()
        payload = json.loads(zlib.decompress(blob).decode("utf-8"))
        if not isinstance(payload, dict):
            raise BoardFileError("文件内容不是预期的结构")
        return payload

    def load_board(self, board_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """返回 ``(meta, strokes)``；文件缺失或损坏时返回读得出来的部分而不是抛错。

        要知道读得全不全，用 :meth:`open_board`。
        """
        meta, strokes, _problem = self.open_board(board_id)
        return meta, strokes

    def open_board(
        self, board_id: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """读一块白板，返回 ``(meta, strokes, problem)``。

        ``problem`` 为 None 表示文件完整读了出来；否则说明哪里不对，调用方据此
        把这块板锁成只读（见 hub.BoardRuntime）：

        * ``unreadable``：文件打不开（权限、外置盘没挂上……），可能只是暂时的；
        * ``corrupt``：打开了，但解压或解析失败，一笔都读不出来；
        * ``partial``：大部分读出来了，但有笔画解不开或者不合法；
        * ``newer``：文件是更新版本的程序写的，这一版可能认不全里面的内容。

        这些情况下原文件都不能被覆盖：写回去就只剩这一版读得出来的那部分。
        """
        meta = self.get_meta(board_id) or models.new_board_meta()
        path = self._board_path(board_id)
        if not path.exists():
            return meta, [], None
        try:
            payload = self._read_file(path)
        except OSError as exc:
            log.error("白板 %s 读取失败：%s", board_id, exc)
            return meta, [], {"reason": "unreadable", "detail": str(exc)}
        except (ValueError, zlib.error) as exc:
            log.error("白板 %s 文件损坏：%s", board_id, exc)
            return meta, [], {"reason": "corrupt", "detail": str(exc)}

        problem: Optional[Dict[str, Any]] = None
        version = payload.get("v", FILE_VERSION)
        if not isinstance(version, int) or isinstance(version, bool) or version > FILE_VERSION:
            log.warning("白板 %s 由更新的版本写入（v=%r，本版 %d）", board_id, version, FILE_VERSION)
            problem = {"reason": "newer", "version": version}

        if isinstance(payload.get("meta"), dict):
            meta = models.sanitize_meta(payload["meta"])
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

    def backup_board_file(self, board_id: str) -> Optional[Path]:
        """把白板文件原样复制到 ``backups/locked/``，返回副本路径；没有文件时返回 None。

        解锁一块读不全的白板之前调用：之后的存盘会用读得出来的内容覆盖原件。
        """
        path = self._board_path(board_id)
        if not path.exists():
            return None
        target_dir = self.data_dir / "backups" / "locked"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{path.stem}-{stamp}{BOARD_SUFFIX}"
        counter = 1
        while target.exists():
            target = target_dir / f"{path.stem}-{stamp}-{counter}{BOARD_SUFFIX}"
            counter += 1
        shutil.copy2(path, target)
        return target

    def save_board(self, meta: Dict[str, Any], strokes: List[Dict[str, Any]]) -> None:
        meta = models.sanitize_meta(meta)
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
        blob = zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6)
        _atomic_write(self._board_path(meta["id"]), blob)
        self.update_meta(meta)

    def rename_board(self, board_id: str, name: str) -> bool:
        """给白板改名。"""
        return self.edit_meta(board_id, name=name if isinstance(name, str) else "")

    def move_board(self, board_id: str, folder: str) -> bool:
        """把白板放进某个文件夹；``folder`` 是空串就是移出来。名字还不在名单里就登记进去。

        文件夹只有一层，名字本身就是身份，没有单独的文件夹 id。名单另外存在索引里
        （空文件夹只有那里有记录）；索引丢了，装着白板的文件夹能从各块白板的 meta
        重建，空文件夹找不回来。

        服务端走的是 ``Hub.move_board``（要顾及已经载入内存的白板），这个方法
        只给不经过 Hub 的地方用。
        """
        clean = models.sanitize_folder(folder)
        if not self.edit_meta(board_id, folder=clean):
            return False
        if clean and clean not in self._index["folders"]:
            self._index["folders"].append(clean)
            self._write_index()
        return True

    def edit_meta(self, board_id: str, **changes: Any) -> bool:
        """改白板的元数据。改动要同时落到索引和 ``.wbz`` 里：索引决定列表显示，
        ``.wbz`` 里那份是索引丢失后重建的依据，只改一边早晚会对不上。

        重写 ``.wbz`` 时笔画还是原来那串 base64，原样搬过去，不重新编解码。
        """
        current = self.get_meta(board_id)
        if current is None:
            return False
        meta = models.sanitize_meta(dict(current, **changes))
        if meta == current:
            return False
        path = self._board_path(board_id)
        if path.exists():
            try:
                # 其余内容原样搬过去，更新版本写的文件里多出来的字段也保留
                payload = self._read_file(path)
                payload["meta"] = meta
                blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                _atomic_write(path, zlib.compress(blob, 6))
            except (OSError, ValueError, zlib.error) as exc:
                log.error("白板 %s 改元数据时写文件失败：%s", board_id, exc)
                return False
        self.update_meta(meta)
        return True

    def update_meta(self, meta: Dict[str, Any]) -> None:
        meta = models.sanitize_meta(meta)
        boards = self._index["boards"]
        for i, existing in enumerate(boards):
            if existing["id"] == meta["id"]:
                boards[i] = meta
                break
        else:
            boards.insert(0, meta)
        self._write_index()

    def save_thumb(self, board_id: str, png: bytes) -> None:
        if not png.startswith(b"\x89PNG"):
            raise ValueError("缩略图必须是 PNG")
        _atomic_write(self.thumb_path(board_id), png)


def _atomic_write(path: Path, data: bytes) -> None:
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

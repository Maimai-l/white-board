"""白板应用的存储：inksync 的 :class:`FileStorage` 加上白板应用自己的数据。

* ``thumbs/<board_id>.png``：Mac 端选择白板用的缩略图；
* ``docs/<board_id>.<扩展名>``：文档板的原件（PDF / 图片），原样保存、只读不改；
* 文件夹名单、排序和当前白板：存在 ``space.json``（``FileStorage.kv``）；
* 至少有一块白板：白板应用总要显示一块，存储为空时新建一块空白板。

这里的方法都在事件循环线程（或测试的主线程）中同步调用，只处理没有载入内存的
白板；已经载入的白板必须经过 :class:`whiteboard.hub.Hub`，否则自动保存会拿内存里
的内容覆盖回去。首次以 2.0 打开 1.x 存储目录时的迁移也在这里（见 ``_migrate``）。
"""

from __future__ import annotations

import logging
import os
import shutil
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from inksync.storage import (  # noqa: F401 - 白板应用和测试沿用这些名字
    BOARD_SUFFIX,
    FILE_VERSION,
    BoardFileError,
    FileStorage,
    _atomic_write,
    atomic_write,
)

from . import models

log = logging.getLogger(__name__)


def _safe(board_id: str) -> str:
    return "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")


class BoardStore:
    """白板应用的用户空间。"""

    def __init__(self, data_dir: os.PathLike | str):
        root = Path(data_dir).expanduser()
        self.data_dir = root
        self.thumbs_dir = root / "thumbs"
        # 文档板的原件（PDF / 图片）原样放着，只读不改。
        self.docs_dir = root / "docs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.core = FileStorage(root, convert_meta=models.convert_v1_meta)
        self.boards_dir = self.core.boards_dir
        self.kv = self.core.kv
        self._migrate()
        self._sync_folders()
        if not self.core.count():
            self.create_board()
        if self.current_id not in self.core.ids():
            self.kv.set("current", self.list_metas()[0]["id"])

    # ------------------------------------------------------------ 迁移

    def _migrate(self) -> None:
        """首次以 2.0 打开 1.x 存储目录（docs/design/inksync-2.zh-CN.md 4.6 节）。

        备份由 ``backup.backup_if_upgraded`` 在这之前完成。"""
        legacy = self.core.legacy_index
        if legacy is None:
            return
        boards = legacy.get("boards") if isinstance(legacy.get("boards"), list) else []
        order = [m.get("id") for m in boards if isinstance(m, dict) and isinstance(m.get("id"), str)]
        folders = [models.sanitize_folder(f) for f in legacy.get("folders") or []]
        folders = [f for i, f in enumerate(folders) if f and f not in folders[:i]]
        current = legacy.get("current") if isinstance(legacy.get("current"), str) else None
        self.kv.set("order", [board_id for board_id in order if self.core.exists(board_id)])
        self.kv.set("folders", folders)
        if current:
            self.kv.set("current", current)
        self._move_app_boards()
        old = self.data_dir / "index.json"
        try:
            old.replace(self.data_dir / "index.v1.json")
        except OSError as exc:
            log.warning("index.json 改名失败：%s", exc)
        log.info("已迁移 1.x 的索引：%d 块白板，%d 个文件夹", len(order), len(folders))

    def _move_app_boards(self) -> None:
        """1.0.1 中带 app 的白板移进对应应用的空间。"""
        for meta in self.core.list(limit=None):
            app = meta.get("data", {}).get("app")
            if not isinstance(app, str) or not models.APP_RE.match(app):
                continue
            target = self.data_dir / "spaces" / app / "boards"
            target.mkdir(parents=True, exist_ok=True)
            source = self.core.board_path(meta["id"])
            try:
                shutil.move(str(source), str(target / source.name))
            except OSError as exc:
                log.error("应用白板 %s 移动失败：%s", meta["id"], exc)
                continue
            self.core.drop(meta["id"])
            self.thumb_path(meta["id"]).unlink(missing_ok=True)
            log.info("应用白板 %s 移入空间 %s", meta["id"], app)

    # ------------------------------------------------------------ 查询

    def get_meta(self, board_id: str) -> Optional[Dict[str, Any]]:
        return self.core.get_meta(board_id)

    def order(self) -> List[str]:
        order = self.kv.get("order") or []
        return [board_id for board_id in order if isinstance(board_id, str)]

    def list_metas(self) -> List[Dict[str, Any]]:
        """全部白板，按界面上的顺序：拖动排过的按排好的顺序，其余的新建的在前。"""
        return self.core.list(limit=None, order=self.order())

    def folders(self) -> List[str]:
        return list(self.kv.get("folders") or [])

    @property
    def current_id(self) -> Optional[str]:
        current = self.kv.get("current")
        return current if isinstance(current, str) else None

    def set_current(self, board_id: str) -> None:
        if self.core.exists(board_id) and board_id != self.current_id:
            self.kv.set("current", board_id)

    # ------------------------------------------------------------ 文件夹与排序

    def _sync_folders(self) -> None:
        """白板上写着的文件夹名一定要在名单里，否则那块白板会从界面上消失。"""
        known = self.folders()
        added = False
        for meta in self.core.list(limit=None):
            name = models.folder_of(meta)
            if name and name not in known:
                known.append(name)
                added = True
        if added:
            self.kv.set("folders", known)

    def create_folder(self, name: str) -> str:
        """新建一个空文件夹，返回规整之后的名字；名字为空或者已经有了就返回空串。"""
        clean = models.sanitize_folder(name)
        folders = self.folders()
        if not clean or clean in folders:
            return ""
        self.kv.set("folders", folders + [clean])
        return clean

    def forget_folder(self, name: str) -> bool:
        folders = self.folders()
        if name not in folders:
            return False
        self.kv.set("folders", [f for f in folders if f != name])
        return True

    def rename_folder_entry(self, name: str, to: str) -> None:
        self.kv.set("folders", [to if f == name else f for f in self.folders()])

    def set_order(self, ids: Any) -> bool:
        """按 ``ids`` 给白板重新排序。

        列表里的白板只占用它们原来占着的那几个位置，别的白板一个都不动——界面上
        送来的是当前这一层看到的顺序（最外面那层，或者某个文件夹里那几块），
        不是全部白板。
        """
        if not isinstance(ids, list):
            return False
        current = [m["id"] for m in self.list_metas()]
        known = set(current)
        wanted: List[str] = []
        for board_id in ids:
            if isinstance(board_id, str) and board_id in known and board_id not in wanted:
                wanted.append(board_id)
        if len(wanted) < 2:
            return False
        picked = set(wanted)
        slots = [i for i, board_id in enumerate(current) if board_id in picked]
        if [current[i] for i in slots] == wanted:
            return False  # 顺序没变，不用写盘也不用广播
        for slot, board_id in zip(slots, wanted):
            current[slot] = board_id
        self.kv.set("order", current)
        return True

    # ------------------------------------------------------------ 没有载入内存的白板

    def create_board(self, name: str = "", make_current: bool = True, kind: str = "board",
                     board_id: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
        """直接在磁盘上建一块白板（不经过 Hub）。``fields`` 可以给出 canvas、layers、data 等。"""
        spec = dict(models.spec_for_kind(kind), name=name, **fields)
        meta = models.new_board_meta(board_id, spec)
        mtime = self.core.write_file(meta, [])
        self.core.put_meta(meta, mtime)
        self.put_front(meta["id"])
        if make_current:
            self.kv.set("current", meta["id"])
        return meta

    def put_front(self, board_id: str) -> None:
        """新建或导入的白板排在最前面。"""
        order = [b for b in self.order() if b != board_id]
        self.kv.set("order", [board_id] + order)

    def edit_meta(self, board_id: str, **changes: Any) -> bool:
        """改一块没有载入内存的白板的元数据：同时改索引和白板文件。

        ``changes`` 可以是 name、background、layers、data（data 按键合并）。"""
        current = self.core.get_meta(board_id)
        if current is None:
            return False
        merged = models.apply_meta_patch(current, changes)
        if merged is None or merged == current:
            return False
        try:
            mtime = self.core.rewrite_meta(merged)
        except (OSError, ValueError, zlib.error) as exc:
            log.error("白板 %s 改元数据时写文件失败：%s", board_id, exc)
            return False
        self.core.put_meta(merged, mtime)
        return True

    def rename_board(self, board_id: str, name: str) -> bool:
        return self.edit_meta(board_id, name=name if isinstance(name, str) else "")

    def move_board(self, board_id: str, folder: str) -> bool:
        clean = models.sanitize_folder(folder)
        if not self.edit_meta(board_id, data={"folder": clean or None}):
            return False
        if clean:
            self.create_folder(clean)
        return True

    def rename_folder(self, name: str, to: str) -> bool:
        """给文件夹改名（白板都没有载入内存时用；否则走 Hub）。"""
        clean = models.sanitize_folder(name)
        target = models.sanitize_folder(to)
        folders = self.folders()
        if not target or target == clean or clean not in folders or target in folders:
            return False
        for meta in self.list_metas():
            if models.folder_of(meta) == clean:
                self.edit_meta(meta["id"], data={"folder": target})
        self.rename_folder_entry(clean, target)
        return True

    def delete_folder(self, name: str) -> bool:
        """删文件夹不删白板（白板都没有载入内存时用；否则走 Hub）。"""
        clean = models.sanitize_folder(name)
        if clean not in self.folders():
            return False
        for meta in self.list_metas():
            if models.folder_of(meta) == clean:
                self.edit_meta(meta["id"], data={"folder": None})
        return self.forget_folder(clean)

    def delete_board(self, board_id: str) -> bool:
        """删一块没有载入内存的白板；否则走 Hub。"""
        if not self.core.drop(board_id):
            return False
        self.core.delete_file(board_id)
        self.delete_files(board_id)
        self.forget_board(board_id)
        return True

    def save_board(self, meta: Dict[str, Any], strokes: List[Dict[str, Any]]) -> None:
        meta = models.sanitize_meta(meta)
        self.core.put_meta(meta, self.core.write_file(meta, strokes))

    def open_board(self, board_id: str):
        meta, strokes, problem = self.core.read_file(board_id)
        if not self.core.board_path(board_id).exists():
            meta = self.core.get_meta(board_id) or meta
        return meta, strokes, problem

    def load_board(self, board_id: str):
        meta, strokes, _problem = self.open_board(board_id)
        return meta, strokes

    def delete_files(self, board_id: str) -> None:
        """删白板时顺带删掉缩略图和文档原件。"""
        self.thumb_path(board_id).unlink(missing_ok=True)
        doc = self.doc_path(board_id)
        if doc is not None:
            doc.unlink(missing_ok=True)

    def forget_board(self, board_id: str) -> None:
        """白板删除之后：从排序中去掉；没有白板了就新建一块；当前白板被删时换成第一块。"""
        order = self.order()
        if board_id in order:
            self.kv.set("order", [b for b in order if b != board_id])
        if not self.core.count():
            self.create_board()
        elif self.current_id == board_id or self.current_id not in self.core.ids():
            self.kv.set("current", self.list_metas()[0]["id"])

    def board_id_of(self, path: Path) -> Optional[str]:
        return self.core.owns(path)

    def backup_board_file(self, board_id: str) -> Optional[Path]:
        return self.core.backup_file(board_id)

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    # ------------------------------------------------------------ 缩略图

    def thumb_path(self, board_id: str) -> Path:
        return self.thumbs_dir / f"{_safe(board_id)}.png"

    def save_thumb(self, board_id: str, png: bytes) -> None:
        if not png.startswith(b"\x89PNG"):
            raise ValueError("缩略图必须是 PNG")
        atomic_write(self.thumb_path(board_id), png)

    # ------------------------------------------------------------ 文档板

    def doc_path(self, board_id: str) -> Optional[Path]:
        """文档板的原件路径；不是文档板或文件丢了就返回 None。"""
        safe = _safe(board_id)
        if not safe:
            return None
        for path in sorted(self.docs_dir.glob(f"{safe}.*")):
            if path.is_file():
                return path
        return None

    def prepare_doc(self, data: bytes, filename: str) -> Dict[str, Any]:
        """导入文档的前半段：把原件写进 docs/ 并读出页面信息。

        只碰 docs/ 下面一个新文件，不碰索引和任何白板，所以可以放到工作线程里跑
        （读大 PDF 要好一会儿）。后半段由 Hub 在事件循环线程上建板。
        """
        from . import docs  # 局部导入：没装 pypdf / pypdfium2 时其余功能照常

        suffix = Path(filename or "").suffix.lower()
        if suffix not in docs.SUFFIXES:
            raise docs.DocError(f"不支持的文件类型：{suffix or '无扩展名'}")
        if not data:
            raise docs.DocError("文件是空的")

        board_id = models.new_id()
        target = self.docs_dir / f"{board_id}{suffix}"
        atomic_write(target, data)
        try:
            info = docs.probe(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        info["name"] = Path(filename).name  # 存的是改名后的副本，这里留原文件名
        return {"id": board_id, "info": info, "filename": filename}

    @staticmethod
    def doc_meta(prepared: Dict[str, Any], name: str = "") -> Dict[str, Any]:
        """为 :meth:`prepare_doc` 准备好的原件生成文档板的元数据。"""
        board_id = prepared["id"]
        doc = models.sanitize_doc(prepared["info"])
        meta = models.new_board_meta(board_id, {
            "name": name or Path(prepared["filename"]).stem,
            "canvas": models.doc_canvas(doc),
            "background": {"pattern": "blank"},
            "layers": models.doc_layers(board_id, doc),
            "data": {"doc": doc},
        })
        return meta

    def import_doc(self, data: bytes, filename: str, name: str = "") -> Dict[str, Any]:
        """由一份 PDF / 图片新建文档板（不经过 Hub，测试和工具用）。"""
        meta = self.doc_meta(self.prepare_doc(data, filename), name)
        self.core.put_meta(meta, self.core.write_file(meta, []))
        self.put_front(meta["id"])
        return meta

    def copy_doc_original(self, path: Path, old_id: str, board_id: str, meta: Dict[str, Any]) -> None:
        """导入 ``.wbz`` 文档板时复制原件：在文件旁边的 ``docs/`` 或上一级的 ``docs/`` 里按原 id 查找。"""
        if models.doc_of(meta) is None:
            return
        from . import docs  # noqa: WPS433

        safe = _safe(old_id)
        for folder in (path.parent / "docs", path.parent.parent / "docs"):
            found = [
                item for item in sorted(folder.glob(f"{safe}.*"))
                if item.is_file() and item.suffix.lower() in docs.SUFFIXES
            ] if safe and folder.is_dir() else []
            if found:
                shutil.copy2(found[0], self.docs_dir / f"{board_id}{found[0].suffix.lower()}")
                return
        raise BoardFileError("这是文档板，但找不到它的原件（应在同一存储目录的 docs/ 里）")

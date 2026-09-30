"""白板应用的存储：在 :class:`inksync.store.BoardStore` 上加缩略图和文档板原件。

``thumbs/<board_id>.png`` 是 Mac 端选择白板用的缩略图；``docs/<board_id>.<扩展名>`` 是
文档板的原件（PDF / 图片），原样保存、只读不改。白板本身的读写在 inksync 里。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from inksync import models
from inksync.store import (  # noqa: F401 - 白板应用和测试沿用这些名字
    BOARD_SUFFIX,
    FILE_VERSION,
    BoardFileError,
    _atomic_write,
)
from inksync.store import BoardStore as CoreStore

log = logging.getLogger(__name__)


def _safe(board_id: str) -> str:
    return "".join(ch for ch in board_id if ch.isalnum() or ch in "-_")


class BoardStore(CoreStore):
    def __init__(self, data_dir: os.PathLike | str):
        root = Path(data_dir).expanduser()
        self.thumbs_dir = root / "thumbs"
        # 文档板的原件（PDF / 图片）原样放着，只读不改。
        self.docs_dir = root / "docs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(data_dir)

    # ------------------------------------------------------------ 缩略图

    def thumb_path(self, board_id: str) -> Path:
        return self.thumbs_dir / f"{_safe(board_id)}.png"

    def save_thumb(self, board_id: str, png: bytes) -> None:
        if not png.startswith(b"\x89PNG"):
            raise ValueError("缩略图必须是 PNG")
        _atomic_write(self.thumb_path(board_id), png)

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

    # ------------------------------------------------------------ 钩子

    def _delete_extras(self, board_id: str) -> None:
        self.thumb_path(board_id).unlink(missing_ok=True)
        doc = self.doc_path(board_id)
        if doc is not None:
            doc.unlink(missing_ok=True)

    def _import_extras(self, path: Path, old_id: str, board_id: str, meta: Dict[str, Any]) -> None:
        """导入文档板时复制原件：在文件旁边的 ``docs/`` 或上一级的 ``docs/`` 里按原 id 查找。"""
        if meta.get("kind") != "doc":
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

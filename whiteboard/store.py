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
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import codec, models

log = logging.getLogger(__name__)

FILE_VERSION = 1
BOARD_SUFFIX = ".wbz"


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
        self._index: Dict[str, Any] = {"boards": [], "current": None}
        self._load_index()

    # ------------------------------------------------------------------ 索引

    def _load_index(self) -> None:
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text("utf-8"))
                boards = [models.sanitize_meta(m) for m in raw.get("boards", [])]
                self._index = {"boards": boards, "current": raw.get("current")}
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
                boards.append(models.sanitize_meta(payload.get("meta", {"id": path.stem})))
            except (OSError, ValueError, zlib.error) as exc:
                log.warning("白板文件无法读取，已跳过 %s：%s", path.name, exc)
        boards.sort(key=lambda m: m.get("updated", 0), reverse=True)
        current = self._index.get("current") if self._index else None
        self._index = {"boards": boards, "current": current}
        if boards:
            self._write_index()

    def _write_index(self) -> None:
        _atomic_write(self.index_path, json.dumps(self._index, ensure_ascii=False).encode("utf-8"))

    # ------------------------------------------------------------------ 查询

    def list_metas(self) -> List[Dict[str, Any]]:
        return [dict(meta) for meta in self._index["boards"]]

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
        meta = self.create_board(
            name or Path(filename).stem,
            id=board_id,
            kind="doc",
            background="blank",
            doc=info,
        )
        log.info("新建文档板 %s：%s（%d 页）", board_id, filename, len(info["pages"]))
        return meta

    @staticmethod
    def _read_file(path: Path) -> Dict[str, Any]:
        blob = path.read_bytes()
        return json.loads(zlib.decompress(blob).decode("utf-8"))

    def load_board(self, board_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """返回 ``(meta, strokes)``；文件缺失或损坏时返回空白板而不是抛错。"""
        meta = self.get_meta(board_id) or models.new_board_meta()
        path = self._board_path(board_id)
        if not path.exists():
            return meta, []
        try:
            payload = self._read_file(path)
        except (OSError, ValueError, zlib.error) as exc:
            log.error("白板 %s 读取失败：%s", board_id, exc)
            return meta, []

        meta = models.sanitize_meta(payload.get("meta", meta))
        strokes: List[Dict[str, Any]] = []
        for index, raw in enumerate(payload.get("strokes", [])):
            packed = raw.get("p")
            if isinstance(packed, str):
                try:
                    raw = dict(raw, p=codec.decode_points_b64(packed))
                except (ValueError, TypeError) as exc:
                    log.warning("笔画 %s 解码失败：%s", raw.get("id"), exc)
                    continue
            stroke = models.sanitize_stroke(raw)
            if stroke is None:
                continue
            stroke.setdefault("n", index)
            strokes.append(stroke)
        strokes.sort(key=lambda s: s["n"])
        return meta, strokes

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
                }
                for i, s in enumerate(strokes)
            ],
        }
        blob = zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6)
        _atomic_write(self._board_path(meta["id"]), blob)
        self.update_meta(meta)

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

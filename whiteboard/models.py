"""白板应用的数据模型：inksync 的模型，加上白板应用自己的字段。

白板应用的字段放在元数据的 ``data`` 里（docs/design/inksync-redesign.zh-CN.md 7.1、7.2 节）：

* ``data.folder``：所在的文件夹名；
* ``data.doc``：文档板的原件信息 ``{type, name, ext, pages}``；
* ``data.app``：1.0.1 中建立这块白板的应用（1.1 起应用白板在各自的空间里）。

``kind_of`` 把画布和这些字段换回界面上的三种类型：大白板、笔记、文档板。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from inksync.models import *  # noqa: F401,F403 - 白板应用和测试沿用 whiteboard.models 这个名字
from inksync.models import (  # noqa: F401
    DOC_PAGE_GAP,
    NOTE_WIDTH,
    _ID_RE,
    is_number,
    new_id,
    sanitize_canvas,
    sanitize_meta,
)

# 应用名，例如 "qb"：同时是 apps/ 下的文件夹名和空间名
APP_RE = re.compile(r"^[a-z0-9-]{1,32}$")
KINDS = ("board", "note", "doc")
DOC_TYPES = ("pdf", "image")
MAX_DOC_PAGES = 400
FOLDER_NAME_MAX = 64


def sanitize_folder(raw: Any) -> str:
    """规整文件夹名。不是字符串、去掉首尾空白之后是空串，都当作「没归类」。"""
    if not isinstance(raw, str):
        return ""
    return raw.strip()[:FOLDER_NAME_MAX]


def sanitize_doc(raw: Any) -> Optional[Dict[str, Any]]:
    """文档板的附加信息：原件类型、文件名和每页尺寸。"""
    if not isinstance(raw, dict):
        return None
    doc_type = raw.get("type")
    if doc_type not in DOC_TYPES:
        return None
    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        return None
    pages: List[List[float]] = []
    for page in pages_raw[:MAX_DOC_PAGES]:
        if not isinstance(page, (list, tuple)) or len(page) != 2:
            return None
        try:
            width, height = float(page[0]), float(page[1])
        except (TypeError, ValueError):
            return None
        if not (0 < width < 1e6 and 0 < height < 1e6):
            return None
        pages.append([round(width, 2), round(height, 2)])

    name = raw.get("name")
    name = name[:128] if isinstance(name, str) else ""
    ext = raw.get("ext")
    ext = ext[:8].lower() if isinstance(ext, str) else ""
    if ext and not re.match(r"^\.[a-z0-9]{1,7}$", ext):
        ext = ""
    return {"type": doc_type, "name": name, "ext": ext, "pages": pages}


def doc_of(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """文档板的原件信息；不是文档板时返回 None。"""
    if not isinstance(meta, dict):
        return None
    data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
    return sanitize_doc(data.get("doc"))


def folder_of(meta: Dict[str, Any]) -> str:
    data = meta.get("data") if isinstance(meta, dict) and isinstance(meta.get("data"), dict) else {}
    return sanitize_folder(data.get("folder"))


def kind_of(meta: Dict[str, Any]) -> str:
    """界面上的类型：文档板、笔记（固定宽度向下延伸）或大白板。"""
    if doc_of(meta) is not None:
        return "doc"
    canvas = meta.get("canvas") if isinstance(meta, dict) else None
    if isinstance(canvas, dict) and canvas.get("mode") == "column":
        return "note"
    return "board"


def doc_layers(board_id: str, doc: Dict[str, Any], gap: float = DOC_PAGE_GAP) -> List[Dict[str, Any]]:
    """文档板每页一个图片层：自上而下排列，横向按最宽的一页居中（与 docs.layout 相同）。"""
    pages = doc.get("pages") or []
    if not pages:
        return []
    widest = max(float(p[0]) for p in pages)
    layers = []
    y = 0.0
    for index, (width, height) in enumerate(pages):
        layers.append({
            "src": f"/api/doc/{board_id}/{index}?w={{w}}",
            "x": round((widest - float(width)) / 2, 2),
            "y": round(y, 2),
            "width": float(width),
            "height": float(height),
            "sheet": True,
        })
        y += float(height) + gap
    return layers


def doc_canvas(doc: Dict[str, Any], gap: float = DOC_PAGE_GAP) -> Dict[str, Any]:
    pages = doc.get("pages") or [[1, 1]]
    width = max(float(p[0]) for p in pages)
    height = sum(float(p[1]) for p in pages) + gap * (len(pages) - 1)
    return {"mode": "fixed", "width": width, "height": height}


def convert_v1_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """读到 1.0.x 文件时的补充（``FileStorage`` 的 ``convert_meta`` 钩子）：规整文件夹名，
    文档板加上页面图片层。"""
    data = dict(meta.get("data") or {})
    if "folder" in data:
        folder = sanitize_folder(data["folder"])
        if folder:
            data["folder"] = folder
        else:
            data.pop("folder")
        meta = dict(meta, data=data)
    doc = doc_of(meta)
    if doc is not None:
        meta = dict(meta, layers=doc_layers(meta["id"], doc), canvas=doc_canvas(doc))
        meta["background"] = dict(meta.get("background") or {}, pattern="blank")
    return meta


def spec_for_kind(kind: str) -> Dict[str, Any]:
    """「新建白板」「新建笔记」对应的 create 内容。"""
    if kind == "note":
        return {"canvas": {"mode": "column", "width": NOTE_WIDTH}}
    return {"canvas": {"mode": "infinite"}}


def to_v1(meta: Dict[str, Any]) -> Dict[str, Any]:
    """1.1 的元数据换回 1.0.x 页面认得的字段（docs/design/inksync-redesign.zh-CN.md 7.3 节）。"""
    if not isinstance(meta, dict):
        return meta
    background = meta.get("background")
    out: Dict[str, Any] = {
        "id": meta.get("id"),
        "name": meta.get("name", ""),
        "kind": kind_of(meta),
        "background": background.get("pattern", "grid") if isinstance(background, dict) else "grid",
        "created": meta.get("created"),
        "updated": meta.get("updated"),
    }
    folder = folder_of(meta)
    if folder:
        out["folder"] = folder
    doc = doc_of(meta)
    if doc is not None:
        out["doc"] = doc
    data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
    if isinstance(data.get("app"), str):
        out["app"] = data["app"]
    layers = [layer for layer in meta.get("layers") or [] if not layer.get("sheet")]
    if layers:
        out["underlay"] = {"src": layers[0]["src"], "width": layers[0]["width"]}
    return out

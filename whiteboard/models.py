"""白板应用的数据模型：inksync 的模型，加上白板应用自己的字段。

白板应用的字段放在元数据的 ``data`` 里（docs/design/inksync-redesign.zh-CN.md 7.1、7.2 节）：

* ``data.folder``：所在的文件夹名；
* ``data.doc``：文档板的原件信息 ``{type, name, ext, pages}``；
* ``data.app``：1.0.1 中建立这块白板的应用（1.1 起应用白板在各自的空间里）；
* ``data.trashed``：移进回收站的时间（Unix 秒）；没有这个键就是还在用的白板。

``kind_of`` 把画布和这些字段换回界面上的三种类型：大白板、笔记、文档板。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from inksync.models import *  # noqa: F401,F403 - 白板应用和测试沿用 whiteboard.models 这个名字
from inksync.models import (  # noqa: F401
    DOC_PAGE_GAP,
    MAX_NAME,
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
# 原件最多多少页里可以挑（导入时选页）；白板上最多还是 MAX_DOC_PAGES 页
MAX_SOURCE_PAGES = 5000
FOLDER_NAME_MAX = 64


def sanitize_folder(raw: Any) -> str:
    """规整文件夹名。不是字符串、去掉首尾空白之后是空串，都当作「没归类」。"""
    if not isinstance(raw, str):
        return ""
    return raw.strip()[:FOLDER_NAME_MAX]


def sanitize_doc(raw: Any) -> Optional[Dict[str, Any]]:
    """文档板的附加信息：原件类型、文件名、每页尺寸，以及每页来自原件的哪一页。

    ``pages`` 是白板上的页（自上而下）。``src`` 和它一样长，每一项是这一页对应的原件页码
    （从 0 起），``None`` 是用户加的空白页。没有 ``src`` 的旧文件就是原件的第 1、2、3… 页。
    """
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
        except (TypeError, ValueError):  # 忽略：校验输入，返回 None 由调用方当作不合法拒掉
            return None
        if not (0 < width < 1e6 and 0 < height < 1e6):
            return None
        pages.append([round(width, 2), round(height, 2)])

    src_raw = raw.get("src")
    if src_raw is None:
        src: List[Optional[int]] = list(range(len(pages)))
    else:
        if not isinstance(src_raw, list) or len(src_raw) < len(pages):
            return None
        src = []
        for item in src_raw[: len(pages)]:
            if item is None:
                src.append(None)
            elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item < MAX_SOURCE_PAGES:
                src.append(item)
            else:
                return None
        if all(item is None for item in src):
            return None  # 至少要有一页原件，否则就不是文档板了

    name = raw.get("name")
    name = name[:128] if isinstance(name, str) else ""
    ext = raw.get("ext")
    ext = ext[:8].lower() if isinstance(ext, str) else ""
    if ext and not re.match(r"^\.[a-z0-9]{1,7}$", ext):
        ext = ""
    out: Dict[str, Any] = {"type": doc_type, "name": name, "ext": ext, "pages": pages}
    if src != list(range(len(pages))):
        out["src"] = src  # 和原件一页对一页时不写，旧版本读出来照样对
    return out


def doc_sources(doc: Dict[str, Any]) -> List[Optional[int]]:
    """白板上每一页对应原件的第几页（从 0 起），空白页是 None。"""
    pages = doc.get("pages") or []
    src = doc.get("src")
    return list(src) if isinstance(src, list) and len(src) == len(pages) else list(range(len(pages)))


def doc_of(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """文档板的原件信息；不是文档板时返回 None。"""
    if not isinstance(meta, dict):
        return None
    data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
    return sanitize_doc(data.get("doc"))


def folder_of(meta: Dict[str, Any]) -> str:
    data = meta.get("data") if isinstance(meta, dict) and isinstance(meta.get("data"), dict) else {}
    return sanitize_folder(data.get("folder"))


def trashed_at(meta: Any) -> Optional[float]:
    """移进回收站的时间（Unix 秒）；不在回收站里时返回 None。"""
    data = meta.get("data") if isinstance(meta, dict) and isinstance(meta.get("data"), dict) else {}
    stamp = data.get("trashed")
    return float(stamp) if is_number(stamp) else None


def kind_of(meta: Dict[str, Any]) -> str:
    """界面上的类型：文档板、笔记（固定宽度向下延伸）或大白板。"""
    if doc_of(meta) is not None:
        return "doc"
    canvas = meta.get("canvas") if isinstance(meta, dict) else None
    if isinstance(canvas, dict) and canvas.get("mode") == "column":
        return "note"
    return "board"


def doc_layers(board_id: str, doc: Dict[str, Any], gap: float = DOC_PAGE_GAP) -> List[Dict[str, Any]]:
    """文档板每页一个纸张层：自上而下排列，横向按最宽的一页居中（与 docs.layout 相同）。

    原件的页带图片，地址里是原件页码（插入空白页之后地址不变，浏览器缓存照样对）；
    空白页只有纸张，没有图片。
    """
    pages = doc.get("pages") or []
    if not pages:
        return []
    sources = doc_sources(doc)
    widest = max(float(p[0]) for p in pages)
    layers = []
    y = 0.0
    for (width, height), source in zip(pages, sources):
        layer: Dict[str, Any] = {
            "x": round((widest - float(width)) / 2, 2),
            "y": round(y, 2),
            "width": float(width),
            "height": float(height),
            "sheet": True,
        }
        if source is not None:
            layer = {"src": f"/api/doc/{board_id}/{source}?w={{w}}", **layer}
        layers.append(layer)
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


# 没起名的白板在界面上显示的名字（与 static/js/ui-common.js 的 boardLabel 相同）
KIND_NAMES = {"board": "白板", "note": "笔记", "doc": "文档"}
COPY_SUFFIX = " 副本"


def display_name(meta: Dict[str, Any]) -> str:
    """界面上显示的名字：起了名就是名字；文档板退回原件的文件名（去掉扩展名）；
    否则按类型叫「白板」「笔记」「文档」。"""
    name = meta.get("name") if isinstance(meta, dict) else ""
    if isinstance(name, str) and name:
        return name
    doc = doc_of(meta)
    if doc is not None and doc["name"]:
        dot = doc["name"].rfind(".")
        return doc["name"][:dot] if dot > 0 else doc["name"]
    return KIND_NAMES[kind_of(meta)]


def copy_name(meta: Dict[str, Any]) -> str:
    """复制出来的白板的名字：显示的名字加「 副本」，总长不超过名字的上限。"""
    return display_name(meta)[: MAX_NAME - len(COPY_SUFFIX)].rstrip() + COPY_SUFFIX


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

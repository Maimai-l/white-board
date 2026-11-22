"""文档板的页面增删：插入、删除用户加的空白页（docs/backlog/014-import-pages.md）。

文档板把每一页自上而下排在一张长画布上（models.doc_layers）。插入一页空白，它后面的页
连同上面的笔迹都要往下挪一页的高度；删掉一页空白，后面的往上挪，这一页上的笔迹一起删掉。
笔迹归哪一页和导出用的是同一条规则（docs.group_by_page：按笔画中心，落在空隙里的归最近的页）。

这里只算新的元数据和笔画，不碰存储和连接；由 Hub.restructure_doc 交给 inksync 的
``replace_board`` 整块换掉。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from . import docs, models


class PageError(ValueError):
    """页面改不了：不是文档板、页码不对、要删的不是空白页、页数到上限。消息写进日志。"""


def _doc(meta: Dict[str, Any]) -> Dict[str, Any]:
    doc = models.doc_of(meta)
    if doc is None:
        raise PageError("不是文档板")
    return doc


def _with_doc(meta: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    """换上新的页面信息：图层和画布跟着重排。"""
    out = copy.deepcopy(meta)
    clean = models.sanitize_doc(doc)
    if clean is None:
        raise PageError("页面信息不合法")
    out.setdefault("data", {})["doc"] = clean
    out["layers"] = models.doc_layers(meta["id"], clean)
    out["canvas"] = models.doc_canvas(clean)
    return out


def _shift(stroke: Dict[str, Any], dy: float) -> Dict[str, Any]:
    """整笔（连同擦除遮罩）上下挪 ``dy``。"""
    moved = dict(stroke)
    points = list(stroke.get("p") or [])
    for index in range(1, len(points), 3):
        points[index] = round(points[index] + dy, 3)
    moved["p"] = points
    if stroke.get("m"):
        chains = []
        for chain in stroke["m"]:
            chain = list(chain)
            for index in range(2, len(chain), 2):
                chain[index] = round(chain[index] + dy, 3)
            chains.append(chain)
        moved["m"] = chains
    return moved


def _page_of_each(strokes: List[Dict[str, Any]], pages: List[List[float]]) -> List[int]:
    boxes = docs.layout(pages)
    groups = docs.group_by_page(strokes, boxes)
    owner: Dict[int, int] = {}
    for page, group in enumerate(groups):
        for stroke in group:
            owner[id(stroke)] = page
    return [owner.get(id(stroke), 0) for stroke in strokes]


def insert_blank(
    meta: Dict[str, Any], strokes: List[Dict[str, Any]], index: int, after: bool = True
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """在第 ``index`` 页（从 0 起）后面（或前面）插入一页空白，大小和这一页一样。"""
    doc = _doc(meta)
    pages = [list(page) for page in doc["pages"]]
    sources = models.doc_sources(doc)
    if not 0 <= index < len(pages):
        raise PageError(f"没有第 {index + 1} 页")
    if len(pages) >= models.MAX_DOC_PAGES:
        raise PageError(f"已经有 {len(pages)} 页，到上限了")
    at = index + 1 if after else index
    width, height = pages[index]
    owners = _page_of_each(strokes, pages)
    dy = height + models.DOC_PAGE_GAP
    moved = [_shift(stroke, dy) if owner >= at else stroke for stroke, owner in zip(strokes, owners)]
    pages.insert(at, [width, height])
    sources.insert(at, None)
    return _with_doc(meta, {**doc, "pages": pages, "src": sources}), moved


def delete_blank(
    meta: Dict[str, Any], strokes: List[Dict[str, Any]], index: int
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """删掉第 ``index`` 页空白：这一页上的笔迹一起删掉，后面的页往上挪。原件的页不能删。"""
    doc = _doc(meta)
    pages = [list(page) for page in doc["pages"]]
    sources = models.doc_sources(doc)
    if not 0 <= index < len(pages):
        raise PageError(f"没有第 {index + 1} 页")
    if sources[index] is not None:
        raise PageError(f"第 {index + 1} 页是原件的页，不能删")
    owners = _page_of_each(strokes, pages)
    dy = pages[index][1] + models.DOC_PAGE_GAP
    kept = []
    for stroke, owner in zip(strokes, owners):
        if owner == index:
            continue
        kept.append(_shift(stroke, -dy) if owner > index else stroke)
    del pages[index]
    del sources[index]
    return _with_doc(meta, {**doc, "pages": pages, "src": sources}), kept

"""导出分页：把一块白板切成若干张纸，给出每页的页框（白板坐标）。

纯计算，不碰文件和网络，同样的输入总是同样的输出（tests/test_pagination.py）。

一页用一个字典表示::

    {"x": 左, "y": 上, "w": 宽, "h": 高, "paper": [纸宽, 纸高]}

``x/y/w/h`` 是页框，也就是这一页要印出来的那块白板区域；``paper`` 是这页纸的大小
（pt，已经按横竖摆好）。纸张可以是固定的（A4、Letter），也可以「跟随内容」
（``CONTENT_PAPER``）：那时每页的纸就是页框加四周留白，1:1。页框等比缩放后放进纸张四周各留 ``MARGIN`` 的内容区里：
水平居中、顶端对齐（:func:`placement`）。框外的墨迹不画。

白板的一个单位按 1 pt 印：文档板的页面在白板上就是按 pt 排的，100% 缩放下看到的
大小就是印出来的大小。

三种白板各有各的分法：

* 大白板（无限画布）：把整块板当成一列从上往下读，每页的宽度取这一段墨迹自己的
  左右范围，切口落在两行之间的空白里（:func:`flow_pages`）。1.3 之前是先把笔画
  聚成组、每组按 1:1 放进一张纸：写得分散的白板会切出几十张零碎的小页（用户的
  真实白板是 83 页，现在是 7 页，见 docs/backlog/010-export-v2.md）。
* 笔记（固定宽度向下延伸）：宽度就是纸宽，按纸张长度往下切；切口尽量落在两行字
  之间的空白里（:func:`column_pages`）。
* 文档板：原件有几页就是几页（:func:`doc_pages`）。

不分页的「整块」导出是一页框住全部墨迹（:func:`whole_page`）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1
Page = Dict[str, Any]

# 纸张（pt，竖放）。A4 是 210 × 297 mm。
PAPERS = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}
DEFAULT_PAPER = "a4"
# 纸张四周留白（pt）：半英寸，打印机印得到，看着也不挤。
MARGIN = 36.0
# 笔记切页时，切口在一页的最后这一段里找空白行；找不到就硬切在纸张长度处。
NOTE_CUT_SEARCH = 0.25
# 纸张一边最长多少 pt：PDF 阅读器（Acrobat）认的页面上限，``paper_size`` 也按这个查
MAX_PAPER = 14400.0
# 「跟随内容」：不用固定的纸，每页的纸就是页框加四周留白，1:1（导出对话框的默认）
CONTENT_PAPER = "content"
# 大白板从上往下分页时：页框比墨迹左右各多留这么多（白板单位），切口往回找空白的范围是
# 一页高度的最后这一段，一页的高宽比按竖放的 A4（用户看惯的页面形状）
FLOW_PAD = 12.0
FLOW_CUT_SEARCH = 0.4
_EPS = 1e-6


# ------------------------------------------------------------------ 纸张


def paper_size(paper: Any = DEFAULT_PAPER) -> Tuple[float, float]:
    """纸张名（``a4``、``letter``）或 ``[宽, 高]``（pt）→ 竖放的 ``(宽, 高)``。

    不认识的值抛 ``ValueError``。
    """
    if isinstance(paper, str):
        key = paper.strip().lower()
        if key not in PAPERS:
            raise ValueError(f"unknown paper: {paper}")
        return PAPERS[key]
    if isinstance(paper, (list, tuple)) and len(paper) == 2:
        w, h = (float(v) for v in paper)
        if not (72.0 <= w <= MAX_PAPER and 72.0 <= h <= MAX_PAPER):
            raise ValueError("paper must be 72–14400 pt on each side")
        return (min(w, h), max(w, h))
    raise ValueError(f"unknown paper: {paper!r}")


def oriented(paper: Tuple[float, float], landscape: bool) -> Tuple[float, float]:
    short, long = min(paper), max(paper)
    return (long, short) if landscape else (short, long)


def content_area(paper: Tuple[float, float], margin: float = MARGIN) -> Tuple[float, float]:
    """纸张去掉四周留白之后能放内容的宽和高（pt）。"""
    return (max(1.0, paper[0] - 2 * margin), max(1.0, paper[1] - 2 * margin))


def placement(page: Page, margin: Optional[float] = None) -> Tuple[float, float, float]:
    """页框放到纸上的 ``(缩放, 左, 上)``：白板坐标 → 纸面 pt（左上原点、y 向下）。

    页框等比缩放到刚好放进内容区，水平居中、顶端对齐。``margin`` 留空时用页面
    自己的 ``margin``（文档板的原件页是 0），再没有就用 ``MARGIN``。
    """
    if margin is None:
        margin = float(page.get("margin", MARGIN))
    pw, ph = page["paper"]
    aw, ah = content_area((pw, ph), margin)
    scale = min(aw / page["w"], ah / page["h"])
    left = margin + (aw - page["w"] * scale) / 2
    return scale, left, margin


def _page(x: float, y: float, w: float, h: float, paper: Tuple[float, float], **extra) -> Page:
    out = {
        "x": round(x, 3),
        "y": round(y, 3),
        "w": round(w, 3),
        "h": round(h, 3),
        "paper": [round(paper[0], 2), round(paper[1], 2)],
    }
    out.update(extra)
    return out


# ------------------------------------------------------------------ 笔画


def stroke_box(stroke: Dict[str, Any]) -> Optional[Box]:
    """笔画的包围盒：中心线外扩半个笔宽（墨迹不会伸出更远）。没有点时返回 None。"""
    flat = stroke.get("p") or []
    if len(flat) < 3:
        return None
    xs = flat[0::3]
    ys = flat[1::3]
    try:
        r = max(0.3, float(stroke.get("w", 3.0)) / 2)
    except (TypeError, ValueError):
        r = 1.5
    return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)


def boxes_of(strokes: Iterable[Dict[str, Any]]) -> List[Box]:
    return [box for box in (stroke_box(s) for s in strokes) if box is not None]


def union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def intersects(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def blank_page(paper: Tuple[float, float] = PAPERS[DEFAULT_PAPER], margin: float = MARGIN) -> Page:
    """空白板的那一页：以原点为中心的一张竖放的纸。"""
    sheet = oriented(paper, False)
    aw, ah = content_area(sheet, margin)
    return _page(-aw / 2, -ah / 2, aw, ah, sheet)


# ------------------------------------------------------------------ 笔记


def free_gaps(boxes: Sequence[Box]) -> List[Tuple[float, float]]:
    """纵向上没有任何笔画经过的空白段（相邻两段墨迹之间），按从上到下排。"""
    spans = sorted((box[1], box[3]) for box in boxes)
    gaps: List[Tuple[float, float]] = []
    if not spans:
        return gaps
    end = spans[0][1]
    for start, stop in spans[1:]:
        if start > end:
            gaps.append((end, start))
        end = max(end, stop)
    gaps.append((end, math.inf))
    return gaps


def column_pages(width: float, boxes: Sequence[Box],
                 paper: Tuple[float, float] = PAPERS[DEFAULT_PAPER],
                 margin: float = MARGIN, top: float = 0.0) -> List[Page]:
    """笔记（固定宽度、向下延伸）的分页：页宽就是笔记宽，竖放的纸，按纸张长度往下切。

    每页的切口在这页最后 ``NOTE_CUT_SEARCH`` 那一段里找：有空白行就切在离纸底最近的
    那段空白的中间，没有就切在纸张长度处。最后一页总是完整的一张纸。
    """
    sheet = oriented(paper, False)
    aw, ah = content_area(sheet, margin)
    full = width * ah / aw  # 一页能放多高的白板
    bottom = max((box[3] for box in boxes), default=top)
    gaps = free_gaps(boxes)
    pages: List[Page] = []
    y = min([top] + [box[1] for box in boxes])
    while True:
        end = y + full
        if end >= bottom - _EPS:
            pages.append(_page(0.0, y, width, full, sheet))
            return pages
        lowest = y + full * (1 - NOTE_CUT_SEARCH)
        cut = end
        best = None
        for start, stop in gaps:
            lo, hi = max(start, lowest), min(stop, end)
            if hi < lo or start >= end:
                continue
            candidate = min(hi, (start + stop) / 2) if stop != math.inf else hi
            candidate = max(lo, candidate)
            if best is None or candidate > best:
                best = candidate
        if best is not None and best > y + _EPS:
            cut = best
        pages.append(_page(0.0, y, width, cut - y, sheet))
        y = cut


# ------------------------------------------------------------------ 大白板：从上往下按段


def is_content_paper(paper: Any) -> bool:
    return isinstance(paper, str) and paper.strip().lower() == CONTENT_PAPER


def _ink_range(boxes: Sequence[Box], top: float, bottom: float) -> Optional[Tuple[float, float]]:
    """纵向落在 ``[top, bottom)`` 里（哪怕只沾一点）的笔画，左右的范围。"""
    xs = [(box[0], box[2]) for box in boxes if box[3] > top and box[1] < bottom]
    if not xs:
        return None
    return min(a for a, _ in xs), max(b for _, b in xs)


def content_sheet(w: float, h: float, margin: float) -> Tuple[Tuple[float, float], float]:
    """「跟随内容」的纸：页框加四周留白；超过 ``MAX_PAPER`` 时连留白一起等比缩小。"""
    pw, ph = w + 2 * margin, h + 2 * margin
    shrink = min(1.0, MAX_PAPER / max(pw, ph))
    return (pw * shrink, ph * shrink), margin * shrink


def flow_pages(boxes: Sequence[Box], paper: Any = CONTENT_PAPER, margin: float = MARGIN) -> List[Page]:
    """大白板（无限画布）的自动分页：把整块板当成一列，从上往下读。

    每页从下一段墨迹的顶上开始；页宽取这一段墨迹自己的左右范围（不是整块板最宽的
    地方，零星几笔写在远处只影响它所在的那一页），页高按竖放 A4 的高宽比；页宽和
    页高互相决定，算几轮就稳定了。切口在这页最后 ``FLOW_CUT_SEARCH`` 那一段里找
    两行之间的空白，找不到才硬切。

    ``paper`` 是 ``"content"`` 时每页的纸就是页框加留白（1:1，字不缩小）；给了纸张
    （名字或 ``(宽, 高)``）就用那张竖放的纸，页框等比放进去（宽的段字会变小）。
    """
    content = is_content_paper(paper)
    sheet = oriented(PAPERS[DEFAULT_PAPER] if content else paper_size(paper), False)
    aw, ah = content_area(sheet, margin)
    ratio = ah / aw
    if not boxes:
        return [blank_page(sheet, margin)]
    bottom = max(box[3] for box in boxes)
    gaps = free_gaps(boxes)
    pages: List[Page] = []
    y = min(box[1] for box in boxes) - FLOW_PAD
    while y < bottom:
        width = aw
        for _ in range(8):
            span = _ink_range(boxes, y, y + width * ratio)
            grown = max(aw, span[1] - span[0] + 2 * FLOW_PAD) if span else aw
            if abs(grown - width) < 0.5:
                break
            width = grown
        end = y + width * ratio
        if end >= bottom:
            cut = bottom + FLOW_PAD
        else:
            lowest = y + width * ratio * (1 - FLOW_CUT_SEARCH)
            best = None
            for start, stop in gaps:
                lo, hi = max(start, lowest), min(stop, end)
                if hi < lo or start >= end:
                    continue
                candidate = max(lo, min(hi, (start + stop) / 2)) if stop != math.inf else hi
                best = candidate if best is None else max(best, candidate)
            cut = best if best is not None and best > y + _EPS else end
        span = _ink_range(boxes, y, cut)
        left, right = span if span else (0.0, 0.0)
        w = max(width, right - left + 2 * FLOW_PAD)
        x = (left + right) / 2 - w / 2
        h = cut - y
        if content:
            size, edge = content_sheet(w, h, margin)
            pages.append(_page(x, y, w, h, size, margin=round(edge, 3)))
        else:
            pages.append(_page(x, y, w, h, sheet))
        below = [box[1] for box in boxes if box[1] >= cut]
        # 下一页从下一段墨迹的顶上开始，但不往回盖住这一页
        y = max(cut, min(below) - FLOW_PAD) if below else bottom
    return pages


# ------------------------------------------------------------------ 文档板


def doc_pages(boxes: Sequence[Dict[str, float]]) -> List[Page]:
    """文档板：原件的每一页就是一页，纸张就是原页面的大小，不留白。"""
    return [
        _page(box["x"], box["y"], box["w"], box["h"], (box["w"], box["h"]), margin=0.0)
        for box in boxes
    ]


# ------------------------------------------------------------------ 整块


def whole_page(meta: Dict[str, Any], strokes: Sequence[Dict[str, Any]], margin: float = MARGIN) -> Page:
    """整块白板一页：页框是全部墨迹的包围盒（取整到整数），纸张就是页框加四周 ``margin``，1:1。

    笔记的页框横向至少是整个笔记宽。纸张任一边超过 ``MAX_PAPER`` 时整页等比缩小
    （留白一起缩），PDF 阅读器才打得开。空白板和自动分页一样，给以原点为中心的一张纸。
    文档板不用这个（导出的是原件的页）。
    """
    from . import models

    boxes = boxes_of(strokes)
    if not boxes:
        return blank_page(PAPERS[DEFAULT_PAPER], margin)
    box = boxes[0]
    for other in boxes[1:]:
        box = union(box, other)
    canvas = meta.get("canvas") if isinstance(meta, dict) else None
    if isinstance(canvas, dict) and canvas.get("mode") in ("column", "fixed"):
        width = float(canvas.get("width") or models.NOTE_WIDTH)
        box = (min(0.0, box[0]), box[1], max(width, box[2]), box[3])
    x0, y0 = math.floor(box[0]), math.floor(box[1])
    w, h = math.ceil(box[2]) - x0, math.ceil(box[3]) - y0
    pw, ph = w + 2 * margin, h + 2 * margin
    shrink = min(1.0, MAX_PAPER / max(pw, ph))
    return _page(x0, y0, w, h, (pw * shrink, ph * shrink), margin=round(margin * shrink, 3))


# ------------------------------------------------------------------ 入口


def auto_pages(meta: Dict[str, Any], strokes: Sequence[Dict[str, Any]], paper: Any = DEFAULT_PAPER) -> List[Page]:
    """按白板类型自动分页。``meta`` 是白板元数据，``strokes`` 是全部笔画。"""
    from . import docs, models

    doc = models.doc_of(meta) if isinstance(meta, dict) else None
    if doc is None:
        doc = docs.doc_of(meta)
    if doc is not None:
        return doc_pages(docs.layout(doc["pages"]))
    boxes = boxes_of(strokes)
    canvas = meta.get("canvas") if isinstance(meta, dict) else None
    if isinstance(canvas, dict) and canvas.get("mode") in ("column", "fixed"):
        width = float(canvas.get("width") or models.NOTE_WIDTH)
        if not is_content_paper(paper):
            return column_pages(width, boxes, paper_size(paper))
        # 跟随内容：页框照 A4 的形状切，纸张换成页框加留白
        pages = column_pages(width, boxes, PAPERS[DEFAULT_PAPER])
        out = []
        for page in pages:
            size, edge = content_sheet(page["w"], page["h"], MARGIN)
            out.append(_page(page["x"], page["y"], page["w"], page["h"], size, margin=round(edge, 3)))
        return out
    return flow_pages(boxes, paper)

"""导出分页：把一块白板切成若干张纸，给出每页的页框（白板坐标）。

纯计算，不碰文件和网络，同样的输入总是同样的输出（tests/test_pagination.py）。

一页用一个字典表示::

    {"x": 左, "y": 上, "w": 宽, "h": 高, "paper": [纸宽, 纸高]}

``x/y/w/h`` 是页框，也就是这一页要印出来的那块白板区域；``paper`` 是这页纸的大小
（pt，已经按横竖摆好）。页框等比缩放后放进纸张四周各留 ``MARGIN`` 的内容区里：
水平居中、顶端对齐（:func:`placement`）。框外的墨迹不画。

白板的一个单位按 1 pt 印：文档板的页面在白板上就是按 pt 排的，100% 缩放下看到的
大小就是印出来的大小。

三种白板各有各的分法：

* 大白板（无限画布）：先把离得近的笔画聚成组（:func:`cluster`），能同放一页的组再
  合并（:func:`pack`）；每组按 1:1 放进一张纸，横竖取放得下的那个；放不下时最多缩到
  ``MIN_FIT_SCALE``，再大就按 1:1 的网格切成几页（:func:`fit_group`）。
* 笔记（固定宽度向下延伸）：宽度就是纸宽，按纸张长度往下切；切口尽量落在两行字
  之间的空白里（:func:`column_pages`）。
* 文档板：原件有几页就是几页（:func:`doc_pages`）。
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
# 两笔的包围盒在横向和纵向上都离得不超过这么多（白板单位），就算同一组。
# 手写的字距、行距在 10～60 之间，两块各自独立的内容通常隔着几百。
CLUSTER_GAP = 72.0
# 一组按 1:1 放不进一页时，允许缩到这个比例来保住整组；再小就切页。
# 缩一点比把一张图切成两半好，缩太多字就小得没法看了。
MIN_FIT_SCALE = 0.7
# 笔记切页时，切口在一页的最后这一段里找空白行；找不到就硬切在纸张长度处。
NOTE_CUT_SEARCH = 0.25
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
        if not (72.0 <= w <= 14400.0 and 72.0 <= h <= 14400.0):
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


def gap_between(a: Box, b: Box) -> float:
    """两个盒子在横、纵两个方向上离得较远的那个距离；相交时为 0。"""
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return max(dx, dy)


def overlap_area(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def intersects(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _Sets:
    """并查集；合并时小编号当根，结果和遍历顺序无关。"""

    def __init__(self, count: int):
        self.parent = list(range(count))

    def find(self, i: int) -> int:
        root = i
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[i] != root:
            self.parent[i], i = root, self.parent[i]
        return root

    def join(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        root, child = (ra, rb) if ra < rb else (rb, ra)
        self.parent[child] = root
        return root


def _near_pairs(boxes: Sequence[Box], reach: float) -> List[Tuple[int, int]]:
    """所有 ``gap_between <= reach`` 的盒子对 ``(i, j)``，``i < j``，按编号排好。

    用格子哈希避免两两比较：每个盒子往外扩 ``reach / 2`` 后登记到它盖住的格子里，
    两个盒子离得够近当且仅当扩过之后相交，相交就一定共享某个格子。
    """
    if not boxes:
        return []
    cell = max(reach, 256.0)
    half = reach / 2
    grid: Dict[Tuple[int, int], List[int]] = {}
    for index, (x0, y0, x1, y1) in enumerate(boxes):
        for gx in range(math.floor((x0 - half) / cell), math.floor((x1 + half) / cell) + 1):
            for gy in range(math.floor((y0 - half) / cell), math.floor((y1 + half) / cell) + 1):
                grid.setdefault((gx, gy), []).append(index)
    pairs = set()
    for members in grid.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if (i, j) not in pairs and gap_between(boxes[i], boxes[j]) <= reach + _EPS:
                    pairs.add((i, j))
    return sorted(pairs)


# ------------------------------------------------------------------ 大白板


def cluster(boxes: Sequence[Box], gap: float = CLUSTER_GAP) -> List[List[int]]:
    """离得近的笔画聚成组：两笔的包围盒在两个方向上都相距不超过 ``gap`` 就连起来，
    连通的算一组。返回每组的笔画编号，组按最小编号排序。"""
    sets = _Sets(len(boxes))
    for i, j in _near_pairs(boxes, gap):
        sets.join(i, j)
    groups: Dict[int, List[int]] = {}
    for index in range(len(boxes)):
        groups.setdefault(sets.find(index), []).append(index)
    return [groups[root] for root in sorted(groups)]


def bbox_of(boxes: Sequence[Box], members: Sequence[int]) -> Box:
    out = boxes[members[0]]
    for index in members[1:]:
        out = union(out, boxes[index])
    return out


def fits_one_to_one(box: Box, paper: Tuple[float, float], margin: float = MARGIN) -> bool:
    """按 1:1 能不能放进一张纸（横竖任一）。"""
    w, h = box[2] - box[0], box[3] - box[1]
    for landscape in (False, True):
        aw, ah = content_area(oriented(paper, landscape), margin)
        if w <= aw + _EPS and h <= ah + _EPS:
            return True
    return False


def pack(boxes: Sequence[Box], groups: Sequence[Sequence[int]], paper: Tuple[float, float],
         margin: float = MARGIN) -> List[List[int]]:
    """把能按 1:1 同放一页的组合并起来，免得零散的几个字各占一页。

    从离得最近的两组开始试，合并后的外框还放得进一页就合并（类似 Kruskal）。
    只有离得比纸的长边还近的两组才可能放进同一页，所以只看这些组对。
    """
    if len(groups) < 2:
        return [list(g) for g in groups]
    group_boxes = [bbox_of(boxes, g) for g in groups]
    reach = max(paper) - 2 * margin
    candidates = sorted(
        (gap_between(group_boxes[i], group_boxes[j]), i, j)
        for i, j in _near_pairs(group_boxes, reach)
    )
    sets = _Sets(len(groups))
    merged = {i: group_boxes[i] for i in range(len(groups))}
    for _, i, j in candidates:
        ri, rj = sets.find(i), sets.find(j)
        if ri == rj:
            continue
        combined = union(merged[ri], merged[rj])
        if fits_one_to_one(combined, paper, margin):
            root = sets.join(ri, rj)
            merged[root] = combined
    out: Dict[int, List[int]] = {}
    for index, members in enumerate(groups):
        out.setdefault(sets.find(index), []).extend(members)
    return [sorted(out[root]) for root in sorted(out)]


def best_fit(box: Box, paper: Tuple[float, float], margin: float = MARGIN):
    """一组放进一张纸的最大缩放（不超过 1）和对应的纸张方向。

    返回 ``(scale, paper)``。两个方向一样大时，看组的形状：宽的横放，否则竖放。
    """
    w, h = max(1.0, box[2] - box[0]), max(1.0, box[3] - box[1])
    options = []
    for landscape in (False, True):
        sheet = oriented(paper, landscape)
        aw, ah = content_area(sheet, margin)
        scale = min(1.0, aw / w, ah / h)
        preferred = landscape == (w > h)
        options.append((round(scale, 9), preferred, sheet))
    options.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return options[0][0], options[0][2]


def fit_group(box: Box, paper: Tuple[float, float], margin: float = MARGIN) -> List[Page]:
    """一组墨迹的页框：一页放得下（缩放不小于 ``MIN_FIT_SCALE``）就一页，页框以组的
    中心为中心；否则按 1:1 切成网格，页数少的方向优先。"""
    scale, sheet = best_fit(box, paper, margin)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if scale >= MIN_FIT_SCALE:
        aw, ah = content_area(sheet, margin)
        w, h = aw / scale, ah / scale
        return [_page(cx - w / 2, cy - h / 2, w, h, sheet)]

    gw, gh = box[2] - box[0], box[3] - box[1]
    options = []
    for landscape in (False, True):
        candidate = oriented(paper, landscape)
        aw, ah = content_area(candidate, margin)
        cols, rows = math.ceil(gw / aw - _EPS), math.ceil(gh / ah - _EPS)
        preferred = landscape == (gw > gh)
        options.append((cols * rows, not preferred, landscape, cols, rows, candidate))
    options.sort(key=lambda item: item[:3])
    _, _, _, cols, rows, sheet = options[0]
    aw, ah = content_area(sheet, margin)
    left, top = cx - cols * aw / 2, cy - rows * ah / 2
    return [
        _page(left + col * aw, top + row * ah, aw, ah, sheet)
        for row in range(rows)
        for col in range(cols)
    ]


def _frame(page: Page) -> Box:
    return (page["x"], page["y"], page["x"] + page["w"], page["y"] + page["h"])


def settle(page: Page, own: Box, others: Sequence[Box]) -> Page:
    """页框比组大的时候，在「组整个留在框里」的范围内挪一挪，尽量别切到旁边别的墨迹。

    候选位置是：居中、贴两边、刚好让开某一笔；挑框里外来墨迹面积最小的，
    一样小就挑离居中最近的。
    """
    x, y, w, h = page["x"], page["y"], page["w"], page["h"]
    lo_x, hi_x = own[2] - w, own[0]
    lo_y, hi_y = own[3] - h, own[1]
    if hi_x - lo_x < _EPS and hi_y - lo_y < _EPS:
        return page
    reach = (lo_x, lo_y, hi_x + w, hi_y + h)
    near = [box for box in others if intersects(box, reach)]
    if not near or not any(intersects(box, _frame(page)) for box in near):
        return page

    def options(lo, hi, centre, size, edges):
        values = {centre, lo, hi}
        for start, end in edges:
            values.add(end)  # 框的起边贴着这一笔的末端
            values.add(start - size)  # 框的末边贴着这一笔的起端
        return sorted(min(hi, max(lo, v)) for v in values) if hi >= lo else [centre]

    xs = options(lo_x, hi_x, x, w, [(b[0], b[2]) for b in near])
    ys = options(lo_y, hi_y, y, h, [(b[1], b[3]) for b in near])
    best = None
    for cx in xs:
        for cy in ys:
            frame = (cx, cy, cx + w, cy + h)
            cost = sum(overlap_area(frame, box) for box in near)
            key = (round(cost, 6), round(abs(cx - x) + abs(cy - y), 6), cx, cy)
            if best is None or key < best[0]:
                best = (key, cx, cy)
    return _page(best[1], best[2], w, h, tuple(page["paper"]))


def reading_order(units: Sequence[Box]) -> List[int]:
    """按阅读顺序排：从上往下分成几行，行内从左往右。

    一个单元的上沿高过当前行第一个单元的中线，就算同一行。
    """
    order = sorted(range(len(units)), key=lambda i: (units[i][1], units[i][0], i))
    rows: List[List[int]] = []
    middle = -math.inf
    for index in order:
        box = units[index]
        if rows and box[1] < middle:
            rows[-1].append(index)
        else:
            rows.append([index])
            middle = (box[1] + box[3]) / 2
    out: List[int] = []
    for row in rows:
        out.extend(sorted(row, key=lambda i: (units[i][0], units[i][1], i)))
    return out


def board_pages(boxes: Sequence[Box], paper: Tuple[float, float] = PAPERS[DEFAULT_PAPER],
                margin: float = MARGIN) -> List[Page]:
    """大白板（无限画布）的自动分页。空白板也给一页：以原点为中心的一张竖放的纸。"""
    if not boxes:
        aw, ah = content_area(paper, margin)
        return [_page(-aw / 2, -ah / 2, aw, ah, paper)]
    clusters = cluster(boxes)
    groups = pack(boxes, clusters, paper, margin)
    owner = {}
    for number, members in enumerate(groups):
        for index in members:
            owner[index] = number
    # 挪页框时拿聚组之后（合并之前）的各组外框当「别人的墨迹」：比逐笔算快得多，
    # 组内笔画彼此离得不超过 CLUSTER_GAP，外框里空着的地方不多
    cluster_boxes = [(owner[members[0]], bbox_of(boxes, members)) for members in clusters]
    units: List[List[Page]] = []
    for number, members in enumerate(groups):
        own = bbox_of(boxes, members)
        pages = fit_group(own, paper, margin)
        if len(pages) == 1:
            others = [box for group, box in cluster_boxes if group != number]
            pages = [settle(pages[0], own, others)]
        units.append(pages)
    extents = []
    for pages in units:
        extent = _frame(pages[0])
        for page in pages[1:]:
            extent = union(extent, _frame(page))
        extents.append(extent)
    return [page for index in reading_order(extents) for page in units[index]]


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


# ------------------------------------------------------------------ 文档板


def doc_pages(boxes: Sequence[Dict[str, float]]) -> List[Page]:
    """文档板：原件的每一页就是一页，纸张就是原页面的大小，不留白。"""
    return [
        _page(box["x"], box["y"], box["w"], box["h"], (box["w"], box["h"]), margin=0.0)
        for box in boxes
    ]


# ------------------------------------------------------------------ 入口


def auto_pages(meta: Dict[str, Any], strokes: Sequence[Dict[str, Any]], paper: Any = DEFAULT_PAPER) -> List[Page]:
    """按白板类型自动分页。``meta`` 是白板元数据，``strokes`` 是全部笔画。"""
    from . import docs, models

    doc = models.doc_of(meta) if isinstance(meta, dict) else None
    if doc is None:
        doc = docs.doc_of(meta)
    if doc is not None:
        return doc_pages(docs.layout(doc["pages"]))
    sheet = paper_size(paper)
    boxes = boxes_of(strokes)
    canvas = meta.get("canvas") if isinstance(meta, dict) else None
    if isinstance(canvas, dict) and canvas.get("mode") in ("column", "fixed"):
        width = float(canvas.get("width") or models.NOTE_WIDTH)
        return column_pages(width, boxes, sheet)
    return board_pages(boxes, sheet)

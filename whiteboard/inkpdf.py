"""把白板笔迹写成 PDF 内容流。

导出 PDF 时不重排原文件：新内容流以 ``/Contents`` 数组的形式追加在原有
内容流后面，原始那几个流对象一个字节都不动，所以体积基本只增加笔迹本身。

笔迹的编码方式经过测量后定成这样（详见 docs/format.md）：

* 先做一次 Ramer–Douglas–Peucker 抽稀（240Hz 采样的笔画有大量冗余点）；
* 坐标按 1/4 pt 取整，靠 ``cm`` 缩放回去，整数比小数好压得多；
* 每一笔都是「一次填充闭合轮廓」，和屏幕上的画法逐段对应：两侧是穿过中点的
  曲线，笔尖是半圆。一次填充还顺带解决了半透明笔重叠处变深的问题。

结果是每笔几百字节，和自家 .wbz 的矢量存储在同一量级。
"""

from __future__ import annotations

import math
import zlib
from typing import Dict, Iterable, List, Sequence, Set, Tuple

# 与前端 stroke.js 的 TOOLS 保持一致。这里只取透明度：笔宽在落笔时就已经
# 乘过工具倍数了（input.js 里 `w: tool.width * scale`），再乘一次就会粗一大圈。
ALPHA = {"pen": 1.0, "marker": 1.0, "highlighter": 0.3}

QUANT = 4  # 坐标网格：1/4 pt
SIMPLIFY = 0.35  # 抽稀阈值下限（pt）
SIMPLIFY_MAX = 1.5  # 抽稀阈值上限（pt）
# 抽稀允许的偏差取笔宽的这个比例：细笔差半点就看得见，马克笔差一点半也看不出来，
# 而马克笔恰好是点最多、最占体积的那一类。
SIMPLIFY_RATIO = 0.05
# 二次曲线离弦不到这个距离就退化成直线：量化到 1/4 pt 之后本来也看不出来。
FLAT = 0.18
# 四分之一圆弧用一段三次贝塞尔近似时的控制点长度（误差约万分之二）
ARC_K = 0.5522847498307936


def epsilon(width: float) -> float:
    """按笔宽定抽稀阈值，夹在 ``SIMPLIFY``～``SIMPLIFY_MAX`` 之间。"""
    return min(SIMPLIFY_MAX, max(SIMPLIFY, width * SIMPLIFY_RATIO))


def radius(tool: str, width: float, pressure: float) -> float:
    """单点半径，公式与 stroke.js 的 strokeRadius 相同。"""
    half = max(0.3, width / 2)
    if tool != "pen":
        return half
    p = 0.0 if pressure < 0 else (1.0 if pressure > 1 else pressure)
    return half * (0.42 + 0.58 * p**0.8)


def points_of(stroke: Dict) -> List[Tuple[float, float, float]]:
    flat = stroke.get("p") or []
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat) - 2, 3)]


def simplify(points: Sequence[Tuple[float, float, float]], eps: float):
    """Ramer–Douglas–Peucker；压感跟着保留下来的点一起走。"""
    if len(points) < 3 or eps <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a][0], points[a][1]
        dx, dy = points[b][0] - ax, points[b][1] - ay
        span = dx * dx + dy * dy
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i][0], points[i][1]
            t = 0.0 if span <= 0 else ((px - ax) * dx + (py - ay) * dy) / span
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            d = math.hypot(px - (ax + dx * t), py - (ay + dy * t))
            if d > best:
                best, best_i = d, i
        if best_i > 0 and best > eps:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [p for p, k in zip(points, keep) if k]


def outline_path(
    points: Sequence[Tuple[float, float, float]], tool: str, width: float, cut: int = 0
):
    """笔画的闭合轮廓，和 stroke.js 的 buildPath 逐段对应。

    返回一串路径指令：``('m', x, y)`` / ``('l', x, y)`` /
    ``('c', x1, y1, x2, y2, x, y)``。两侧的曲线和屏幕上一样是穿过中点的二次
    曲线（这里换算成三次贝塞尔，PDF 只有 ``c``），笔尖是半圆。

    以前这里为了省体积把不透明的笔画成「按线宽分段的折线」，线宽一变就断一段，
    每段两头还各有一个圆头——笔一粗就变成一串大小不一的圆饼。现在一律按轮廓填充，
    屏幕上什么样导出就什么样。
    """
    count = len(points)
    pts = [(x, y, max(0.35, radius(tool, width, pr))) for x, y, pr in points]
    if count == 1:
        x, y, r = pts[0]
        cmds = [("m", x + r, y)]
        _arc(cmds, x, y, r, 0.0, 4)  # 一个点就画个整圆
        return cmds

    left: List[Tuple[float, float]] = []
    right: List[Tuple[float, float]] = []
    nx, ny = 0.0, -1.0
    first = last = 0.0
    for i in range(count):
        px, py, _ = pts[max(0, i - 1)]
        qx, qy, _ = pts[min(count - 1, i + 1)]
        dx, dy = qx - px, qy - py
        length = math.hypot(dx, dy)
        if length > 1e-6:
            nx, ny = -dy / length, dx / length
        x, y, r = pts[i]
        left.append((x + nx * r, y + ny * r))
        right.append((x - nx * r, y - ny * r))
        if i == 0:
            first = math.atan2(ny, nx)
        last = math.atan2(ny, nx)

    cmds: List[Tuple] = []
    cursor = (0.0, 0.0)

    def side(side_points: List[Tuple[float, float]], start_here: bool) -> None:
        """一侧的轮廓线：Catmull-Rom 转三次贝塞尔，曲线穿过每一个点。

        屏幕上用的是「穿过相邻两点中点」的二次曲线，点密的时候贴着折线走，
        看不出差别；但导出前会先抽稀，点一疏，那套画法就开始切角——笔画拐弯
        的地方会被削平。这里改成插值曲线，抽稀之后形状仍然跟得住。
        """
        nonlocal cursor
        cmds.append(("m" if start_here else "l", side_points[0][0], side_points[0][1]))
        cursor = side_points[0]
        count = len(side_points)
        for i in range(count - 1):
            p0 = side_points[i - 1] if i > 0 else side_points[0]
            p1 = side_points[i]
            p2 = side_points[i + 1]
            p3 = side_points[i + 2] if i + 2 < count else side_points[-1]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            # 两个控制点都贴着弦时，画直线就够了
            flat = max(
                _line_gap(p1, p2, c1),
                _line_gap(p1, p2, c2),
            )
            if flat <= FLAT:
                cmds.append(("l", p2[0], p2[1]))
            else:
                cmds.append(("c", c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))
            cursor = p2

    def cap(cx: float, cy: float, r: float, angle: float) -> None:
        nonlocal cursor
        cursor = _arc(cmds, cx, cy, r, angle, 2)

    # cut 的两位标出哪一头是橡皮切出来的，切口画平口而不是半圆笔尖，与 stroke.js 一致
    side(left, True)
    if not cut & 2:
        cap(pts[-1][0], pts[-1][1], pts[-1][2], last)
    side(list(reversed(right)), False)
    if not cut & 1:
        cap(pts[0][0], pts[0][1], pts[0][2], first + math.pi)
    return cmds


def _line_gap(a: Tuple[float, float], b: Tuple[float, float], p: Tuple[float, float]) -> float:
    """点到线段所在直线的距离，用来判断这一段值不值得画成曲线。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / length


def _arc(cmds: List[Tuple], cx: float, cy: float, r: float, start: float, quarters: int):
    """顺时针画 ``quarters`` 个四分之一圆弧，每段一条三次贝塞尔。

    笔尖以前是十二段折线，光是两个笔尖就要二十四条指令；换成圆弧之后
    两条指令搞定，还更圆。
    """
    angle = start
    x = cx + math.cos(angle) * r
    y = cy + math.sin(angle) * r
    for _ in range(quarters):
        nxt = angle - math.pi / 2
        ex = cx + math.cos(nxt) * r
        ey = cy + math.sin(nxt) * r
        k = ARC_K * r
        cmds.append((
            "c",
            x + math.sin(angle) * k, y - math.cos(angle) * k,
            ex - math.sin(nxt) * k, ey + math.cos(nxt) * k,
            ex, ey,
        ))
        angle, x, y = nxt, ex, ey
    return (x, y)


def flatten(cmds: Sequence[Tuple], steps: int = 8) -> List[Tuple[float, float]]:
    """把路径指令摊成多边形，给栅格化（图片导出）用。"""
    poly: List[Tuple[float, float]] = []
    cursor = (0.0, 0.0)
    for cmd in cmds:
        if cmd[0] == "c":
            x1, y1, x2, y2, x3, y3 = cmd[1:]
            x0, y0 = cursor
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                poly.append((
                    u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
                    u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
                ))
            cursor = (x3, y3)
        else:
            cursor = (cmd[1], cmd[2])
            poly.append(cursor)
    return poly


def _num(value: float) -> bytes:
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return (text or "0").encode("ascii")


def rgb(color: str) -> Tuple[float, float, float]:
    try:
        return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return (0.1, 0.1, 0.12)


class _Writer:
    """按 1/QUANT pt 的网格输出整数坐标的路径。"""

    def __init__(self, quant: int = QUANT):
        self.quant = quant
        self.parts: List[bytes] = []
        self.color: str = ""
        self.alpha: float = -1.0
        self.width: float = -1.0
        self.alphas: Set[int] = set()

    def _c(self, value: float) -> bytes:
        return b"%d" % round(value * self.quant)

    def _style(self, color: str, alpha: float) -> None:
        if alpha != self.alpha:
            key = round(alpha * 100)
            self.alphas.add(key)
            self.parts.append(b"/WBa%d gs\n" % key)
            self.alpha = alpha
        if color != self.color:
            r, g, b = rgb(color)
            self.parts.append(
                b"%s %s %s rg %s %s %s RG\n"
                % (_num(r), _num(g), _num(b), _num(r), _num(g), _num(b))
            )
            self.color = color

    def fill(self, cmds: Sequence[Tuple], color: str, alpha: float) -> None:
        self._style(color, alpha)
        out: List[bytes] = []
        last: bytes | None = None
        for cmd in cmds:
            if cmd[0] == "c":
                out.append(b" ".join(self._c(v) for v in cmd[1:]) + b" c\n")
                last = None
                continue
            token = self._c(cmd[1]) + b" " + self._c(cmd[2])
            if token == last:
                continue
            out.append(token + (b" m\n" if cmd[0] == "m" else b" l\n"))
            last = token
        if len(out) < 2:
            return
        self.parts.append(b"".join(out) + b"h f\n")

    def data(self) -> bytes:
        return b"".join(self.parts)


def content_stream(
    strokes: Iterable[Dict],
    origin: Tuple[float, float] = (0.0, 0.0),
    matrix: Sequence[float] | None = None,
    quant: int = QUANT,
    eps: float | None = None,
) -> Tuple[bytes, Set[int]]:
    """把若干笔画编成一段 PDF 内容流，返回 ``(未压缩字节, 用到的透明度)``。

    笔迹用的是世界坐标，``origin`` 是这一页左上角在世界里的位置；
    ``matrix`` 则把页面显示坐标（左上原点、y 向下）换算成 PDF 用户坐标，
    顺带处理 /Rotate 和 CropBox 的偏移。``eps`` 留空就按笔宽自动取。
    """
    ox, oy = origin
    writer = _Writer(quant)
    for stroke in strokes:
        points = points_of(stroke)
        if not points:
            continue
        tool = stroke.get("tool", "pen")
        alpha = ALPHA.get(tool, 1.0)
        width = float(stroke.get("w", 3.0))
        points = simplify(points, epsilon(width) if eps is None else eps)
        color = stroke.get("color", "#1b1b1f")
        cmds = [
            (cmd[0],) + tuple(v - (ox if i % 2 == 0 else oy) for i, v in enumerate(cmd[1:]))
            for cmd in outline_path(points, tool, width, int(stroke.get("cut") or 0))
        ]
        writer.fill(cmds, color, alpha)

    body = writer.data()
    if not body:
        return b"", set()
    head = b"q "
    if matrix:
        head += b" ".join(_num(v) for v in matrix) + b" cm "
    scale = _num(1.0 / quant)
    head += b"%s 0 0 %s 0 0 cm\n" % (scale, scale)
    return head + body + b"Q\n", writer.alphas


def inherited(page, key: str, depth: int = 32):
    """取页面属性，顺着 /Parent 往上找。

    /Rotate、/MediaBox、/CropBox 都是可继承属性，pypdf 的 ``page.rotation`` 和
    ``page.cropbox`` 只看页面自己那一层，写在 Pages 节点上的值会被漏掉，
    漏掉就会让笔迹落到旋转前的坐标上。
    """
    node = page
    for _ in range(depth):
        if node is None:
            break
        value = node.get(key)
        if value is not None:
            return value.get_object() if hasattr(value, "get_object") else value
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return None


def page_geometry(page) -> Tuple[List[float], float, float]:
    """页面的 ``(cm 矩阵, 显示宽, 显示高)``，考虑继承来的 /Rotate 与页面框。"""
    from pypdf.generic import RectangleObject  # 局部导入：没装 pypdf 时其余功能照常

    box = inherited(page, "/CropBox")
    if box is None:
        box = inherited(page, "/MediaBox")
    if box is None:
        box = RectangleObject([0, 0, 612, 792])
    elif not isinstance(box, RectangleObject):
        box = RectangleObject(list(box)[:4])
    rotation = inherited(page, "/Rotate") or 0
    try:
        rotation = int(rotation)
    except (TypeError, ValueError):
        rotation = 0
    return page_matrix(rotation, box)


def page_matrix(rotation: int, box) -> Tuple[List[float], float, float]:
    """页面显示坐标（左上原点、y 向下）→ PDF 用户坐标的 cm 矩阵。

    返回 ``(matrix, 显示宽, 显示高)``；``box`` 是 pypdf 的 CropBox / MediaBox。
    """
    left, bottom = float(box.left), float(box.bottom)
    width, height = float(box.width), float(box.height)
    rotation = int(rotation or 0) % 360
    if rotation == 90:
        matrix = [0.0, 1.0, 1.0, 0.0, left, bottom]
        shown = (height, width)
    elif rotation == 180:
        matrix = [-1.0, 0.0, 0.0, 1.0, left + width, bottom]
        shown = (width, height)
    elif rotation == 270:
        matrix = [0.0, -1.0, -1.0, 0.0, left + width, bottom + height]
        shown = (height, width)
    else:
        matrix = [1.0, 0.0, 0.0, -1.0, left, bottom + height]
        shown = (width, height)
    return matrix, shown[0], shown[1]


def flate(data: bytes) -> bytes:
    return zlib.compress(data, 9)

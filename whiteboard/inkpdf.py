"""把白板笔迹写成 PDF 内容流。

导出 PDF 时不重排原文件：新内容流以 ``/Contents`` 数组的形式追加在原有
内容流后面，原始那几个流对象一个字节都不动，所以体积基本只增加笔迹本身。

笔迹的编码方式经过测量后定成这样（详见 docs/format.md）：

* 先做一次 Ramer–Douglas–Peucker 抽稀（240Hz 采样的笔画有大量冗余点）；
* 坐标按 1/4 pt 取整，靠 ``cm`` 缩放回去，整数比小数好压得多；
* 不透明的笔（钢笔 / 马克笔）走「描边折线」，压感按 0.5pt 分桶成几段；
* 半透明的荧光笔仍然走「一次填充闭合轮廓」，重叠处才不会变深。

结果是每笔大约 110 字节，跟自家 .wbz 的矢量存储在同一量级。
"""

from __future__ import annotations

import math
import zlib
from typing import Dict, Iterable, List, Sequence, Set, Tuple

# 与前端 stroke.js 的 TOOLS 保持一致：(alpha, 宽度倍数)
TOOLS = {
    "pen": (1.0, 1.0),
    "marker": (1.0, 2.6),
    "highlighter": (0.3, 6.0),
}

QUANT = 4  # 坐标网格：1/4 pt
SIMPLIFY = 0.35  # 抽稀阈值（pt）
WIDTH_STEP = 0.5  # 线宽分桶（pt）
CAP_STEPS = 8


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


def outline(points: Sequence[Tuple[float, float, float]], tool: str, width: float):
    """笔画的闭合轮廓，与 stroke.js 的 buildPath 同一套几何（折线近似曲线）。"""
    count = len(points)
    pts = [(x, y, max(0.35, radius(tool, width, pr))) for x, y, pr in points]
    if count == 1:
        x, y, r = pts[0]
        steps = 16
        return [
            (x + r * math.cos(i * 2 * math.pi / steps), y + r * math.sin(i * 2 * math.pi / steps))
            for i in range(steps)
        ]

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

    def cap(cx: float, cy: float, r: float, angle: float):
        return [
            (cx + math.cos(angle - math.pi * i / CAP_STEPS) * r,
             cy + math.sin(angle - math.pi * i / CAP_STEPS) * r)
            for i in range(1, CAP_STEPS + 1)
        ]

    poly = list(left)
    poly += cap(pts[-1][0], pts[-1][1], pts[-1][2], last)
    poly += list(reversed(right))
    poly += cap(pts[0][0], pts[0][1], pts[0][2], first + math.pi)
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

    def _path(self, pts: Iterable[Tuple[float, float]]) -> List[bytes]:
        out: List[bytes] = []
        last = None
        for x, y in pts:
            token = self._c(x) + b" " + self._c(y)
            if token == last:
                continue
            out.append(token + (b" m\n" if last is None else b" l\n"))
            last = token
        return out

    def fill(self, pts: Sequence[Tuple[float, float]], color: str, alpha: float) -> None:
        self._style(color, alpha)
        body = self._path(pts)
        if len(body) < 2:
            return
        self.parts.append(b"".join(body) + b"f\n")

    def strokes(self, pts: Sequence[Tuple[float, float, float]], color: str, alpha: float) -> None:
        """按线宽分段描边：同一宽度的连续线段合成一条子路径。"""
        self._style(color, alpha)
        if len(pts) == 1:
            x, y, w = pts[0]
            self._width(w)
            self.parts.append(b"%s %s m %s %s l S\n" % (self._c(x), self._c(y), self._c(x), self._c(y)))
            return
        i = 0
        while i < len(pts) - 1:
            width = pts[i + 1][2]
            run = [pts[i]]
            while i < len(pts) - 1 and pts[i + 1][2] == width:
                run.append(pts[i + 1])
                i += 1
            self._width(width)
            body = self._path([(p[0], p[1]) for p in run])
            if len(body) < 2:
                continue
            self.parts.append(b"".join(body) + b"S\n")

    def _width(self, width: float) -> None:
        if width != self.width:
            self.parts.append(b"%s w\n" % _num(width * self.quant))
            self.width = width

    def data(self) -> bytes:
        return b"".join(self.parts)


def content_stream(
    strokes: Iterable[Dict],
    origin: Tuple[float, float] = (0.0, 0.0),
    matrix: Sequence[float] | None = None,
    quant: int = QUANT,
    eps: float = SIMPLIFY,
) -> Tuple[bytes, Set[int]]:
    """把若干笔画编成一段 PDF 内容流，返回 ``(未压缩字节, 用到的透明度)``。

    笔迹用的是世界坐标，``origin`` 是这一页左上角在世界里的位置；
    ``matrix`` 则把页面显示坐标（左上原点、y 向下）换算成 PDF 用户坐标，
    顺带处理 /Rotate 和 CropBox 的偏移。
    """
    ox, oy = origin
    writer = _Writer(quant)
    for stroke in strokes:
        points = points_of(stroke)
        if not points:
            continue
        points = simplify(points, eps)
        tool = stroke.get("tool", "pen")
        alpha, scale = TOOLS.get(tool, TOOLS["pen"])
        width = float(stroke.get("w", 3.0)) * scale
        color = stroke.get("color", "#1b1b1f")
        if alpha < 1.0 or len(points) == 1:
            poly = [(x - ox, y - oy) for x, y in outline(points, tool, width)]
            writer.fill(poly, color, alpha)
            continue
        shaped = []
        for x, y, pressure in points:
            w = max(0.7, round(2 * radius(tool, width, pressure) / WIDTH_STEP) * WIDTH_STEP)
            shaped.append((x - ox, y - oy, w))
        writer.strokes(shaped, color, alpha)

    body = writer.data()
    if not body:
        return b"", set()
    head = b"q "
    if matrix:
        head += b" ".join(_num(v) for v in matrix) + b" cm "
    scale = _num(1.0 / quant)
    head += b"%s 0 0 %s 0 0 cm 1 J 1 j\n" % (scale, scale)
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

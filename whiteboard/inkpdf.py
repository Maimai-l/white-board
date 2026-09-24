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
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

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
# 折角阈值，和 stroke.js 的 CORNER 一致：转角超过它就断开轮廓、转一段圆弧
CORNER = math.radians(45)


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
    ``('c', x1, y1, x2, y2, x, y)``。

    两侧是穿过每一个偏移点的三次贝塞尔（Catmull-Rom），偏移量用斜接的
    ``r / cos(转角/2)``；转角超过 ``CORNER`` 的点当折角处理，曲线在那里收尾、
    沿笔尖圆转过去、再重新起头。``cut`` 标出哪一头是橡皮切出来的，切口画平口。

    以前这里为了省体积把不透明的笔画画成「按线宽分段的折线」，线宽一变就断一段，
    每段两头还各有一个圆头——笔一粗就变成一串大小不一的圆饼。现在一律按轮廓填充，
    屏幕上什么样导出就什么样。
    """
    pts = []
    for x, y, pr in points:
        r = max(0.35, radius(tool, width, pr))
        # 去掉重合点：方向角要靠相邻点算，两点重合会得到无意义的角度
        if pts and abs(x - pts[-1][0]) < 1e-7 and abs(y - pts[-1][1]) < 1e-7:
            continue
        pts.append((x, y, r))

    cmds: List[Tuple] = []
    if not pts:
        return cmds
    if len(pts) == 1:
        x, y, r = pts[0]
        cmds.append(("m", x + r, y))
        _arc(cmds, x, y, r, 0.0, 2 * math.pi)
        return cmds

    count = len(pts)
    dirs = [
        math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
        for i in range(count - 1)
    ]

    # 每个采样点在两侧各给一项：普通点是一个斜接偏移点，折角是一段圆弧
    left: List[Any] = []
    right: List[Any] = []
    for i in range(count):
        before = dirs[i - 1] if i > 0 else dirs[0]
        after = dirs[i] if i < count - 1 else dirs[-1]
        turn = _turn(before, after)
        x, y, r = pts[i]
        if abs(turn) > CORNER and 0 < i < count - 1:
            left.append(((x, y, r), before + math.pi / 2, after + math.pi / 2))
            right.append(((x, y, r), before - math.pi / 2, after - math.pi / 2))
            continue
        half = turn / 2
        reach = r / math.cos(half)
        normal = before + half + math.pi / 2
        dx, dy = math.cos(normal) * reach, math.sin(normal) * reach
        left.append((x + dx, y + dy))
        right.append((x - dx, y - dy))

    started = False

    def step(point: Tuple[float, float]) -> None:
        nonlocal started
        cmds.append(("l" if started else "m", point[0], point[1]))
        started = True

    def curve(run: List[Tuple[float, float]]) -> None:
        """一段连续偏移点：Catmull-Rom 转三次贝塞尔，曲线穿过每一个点。"""
        if not run:
            return
        step(run[0])
        for i in range(len(run) - 1):
            p0 = run[i - 1] if i > 0 else run[0]
            p1, p2 = run[i], run[i + 1]
            p3 = run[i + 2] if i + 2 < len(run) else run[-1]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            # 两个控制点都贴着弦时，画直线就够了
            if max(_line_gap(p1, p2, c1), _line_gap(p1, p2, c2)) <= FLAT:
                cmds.append(("l", p2[0], p2[1]))
            else:
                cmds.append(("c", c1[0], c1[1], c2[0], c2[1], p2[0], p2[1]))

    def side(items: Sequence[Any]) -> None:
        run: List[Tuple[float, float]] = []
        for item in items:
            if len(item) == 2:
                run.append(item)
                continue
            curve(run)
            run = []
            (cx, cy, r), a0, a1 = item
            if not started:
                step((cx + math.cos(a0) * r, cy + math.sin(a0) * r))
            else:
                cmds.append(("l", cx + math.cos(a0) * r, cy + math.sin(a0) * r))
            _arc(cmds, cx, cy, r, a0, _turn(a0, a1))
        curve(run)

    first = dirs[0] + math.pi / 2
    last = dirs[-1] + math.pi / 2
    tx, ty, tr = pts[-1]
    hx, hy, hr = pts[0]

    side(left)
    if not cut & 2:
        _arc(cmds, tx, ty, tr, last, -math.pi)
    # 反着走另一侧：到达和离开的角度对调
    side([
        (item[0], item[2], item[1]) if len(item) == 3 else item
        for item in reversed(right)
    ])
    if not cut & 1:
        _arc(cmds, hx, hy, hr, first + math.pi, -math.pi)
    return cmds


def _capsules(chains: Sequence[Sequence[float]]) -> List[Tuple[float, float, float, float, float]]:
    """把几条胶囊链摊成 ``(x0, y0, x1, y1, r)`` 的列表，顺带抽稀。

    一次擦除拖动会产生上百段，而相邻几段在近乎笔直的一段上几乎完全重合。
    按 RDP 抽稀到 ``r / 6``，形状看不出变化，段数少一个量级。
    """
    out = []
    for chain in chains:
        r = chain[0]
        pts = [(chain[i], chain[i + 1]) for i in range(1, len(chain) - 1, 2)]
        if len(pts) == 1:
            out.append((pts[0][0], pts[0][1], pts[0][0], pts[0][1], r))
            continue
        thin = simplify([(x, y, 1.0) for x, y in pts], r / 6)
        for i in range(len(thin) - 1):
            out.append((thin[i][0], thin[i][1], thin[i + 1][0], thin[i + 1][1], r))
    return out


def _disjoint_groups(caps) -> List[List]:
    """把胶囊分成几组，每组内部互不重叠。

    ``W*`` 是 even-odd：同一条裁剪路径里两段胶囊一旦重叠，重叠处就被算了两次、
    判定成「不裁」——而一次拖动里相邻两段在共用的圆端点处必然重叠，结果是擦痕
    每隔一段就留一块没擦掉。所以不能把它们塞进同一条路径。

    好在裁剪是可以叠加的：`W* n` 连着来几次就是几个区域求交，而
    「补集的交 = 并集的补集」，正是要的结果。组内不重叠就不会互相抵消，
    组数通常只有两三组。
    """
    groups: List[List] = []
    for cap in caps:
        for group in groups:
            if all(not _caps_overlap(cap, other) for other in group):
                group.append(cap)
                break
        else:
            groups.append([cap])
    return groups


def _caps_overlap(a, b) -> bool:
    return _segment_distance(a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3]) <= a[4] + b[4]


def _segment_distance(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    def point_seg(px, py, qx, qy, rx, ry):
        ux, uy = rx - qx, ry - qy
        span = ux * ux + uy * uy
        t = 0.0 if span <= 0 else ((px - qx) * ux + (py - qy) * uy) / span
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        return math.hypot(px - (qx + ux * t), py - (qy + uy * t))

    return min(
        point_seg(ax, ay, cx, cy, dx, dy), point_seg(bx, by, cx, cy, dx, dy),
        point_seg(cx, cy, ax, ay, bx, by), point_seg(dx, dy, ax, ay, bx, by),
    )


def mask_clips(chains: Sequence[Sequence[float]], bbox: Tuple[float, float, float, float]):
    """啃掉的那几块对应的裁剪路径，一组一条，按顺序求交。

    每一条是「外框减去这一组胶囊」，用 even-odd；组内的胶囊互不重叠，所以不会
    互相抵消。几条依次 `W* n` 叠加，得到的就是「框内、所有胶囊之外」那一块。
    缺口在导出里因此仍然是真矢量，不会退化成一张栅格图。
    """
    x0, y0, x1, y1 = bbox
    clips = []
    for group in _disjoint_groups(_capsules(chains)):
        cmds: List[Tuple] = [
            ("m", x0, y0), ("l", x1, y0), ("l", x1, y1), ("l", x0, y1), ("l", x0, y0),
        ]
        for ax, ay, bx, by, r in group:
            if abs(bx - ax) < 1e-9 and abs(by - ay) < 1e-9:
                cmds.append(("m", ax + r, ay))
                _arc(cmds, ax, ay, r, 0.0, 2 * math.pi)
                continue
            a = math.atan2(by - ay, bx - ax) + math.pi / 2
            cmds.append(("m", ax + math.cos(a) * r, ay + math.sin(a) * r))
            cmds.append(("l", bx + math.cos(a) * r, by + math.sin(a) * r))
            _arc(cmds, bx, by, r, a, -math.pi)
            cmds.append(("l", ax + math.cos(a - math.pi) * r, ay + math.sin(a - math.pi) * r))
            _arc(cmds, ax, ay, r, a + math.pi, -math.pi)
        clips.append(cmds)
    return clips


def _line_gap(a: Tuple[float, float], b: Tuple[float, float], p: Tuple[float, float]) -> float:
    """点到线段所在直线的距离，用来判断这一段值不值得画成曲线。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / length


def _turn(before: float, after: float) -> float:
    """角差折算到 (-π, π]，和 stroke.js 的 turnOf 一致。"""
    delta = after - before
    while delta > math.pi:
        delta -= 2 * math.pi
    while delta <= -math.pi:
        delta += 2 * math.pi
    return delta


def _arc(cmds: List[Tuple], cx: float, cy: float, r: float, start: float, sweep: float):
    """从 ``start`` 转过 ``sweep``（带符号）的圆弧，按 90° 切段，每段一条三次贝塞尔。

    笔尖以前是十二段折线，光是两个笔尖就要二十四条指令；换成圆弧之后
    半个笔尖两条指令就够，还更圆。折角接头用的也是它。
    """
    angle = start
    x = cx + math.cos(angle) * r
    y = cy + math.sin(angle) * r
    if abs(sweep) < 1e-9:
        return (x, y)
    steps = max(1, math.ceil(abs(sweep) / (math.pi / 2) - 1e-9))
    piece = sweep / steps
    # 控制点长度按张角算，90° 时退化成 ARC_K
    k = (4 / 3) * math.tan(piece / 4) * r
    for _ in range(steps):
        nxt = angle + piece
        ex = cx + math.cos(nxt) * r
        ey = cy + math.sin(nxt) * r
        cmds.append((
            "c",
            x - math.sin(angle) * k, y + math.cos(angle) * k,
            ex + math.sin(nxt) * k, ey - math.cos(nxt) * k,
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


def _bounds(cmds: Sequence[Tuple]) -> Tuple[float, float, float, float]:
    """路径的包围盒，外扩一点，给裁剪用的外框。"""
    xs = [v for cmd in cmds for v in cmd[1::2]]
    ys = [v for cmd in cmds for v in cmd[2::2]]
    return (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)


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

    def fill_clipped(
        self, cmds: Sequence[Tuple], clips: Sequence[Sequence[Tuple]], color: str, alpha: float
    ) -> None:
        """依次叠加几条 even-odd 裁剪，再填充。裁剪只在 q/Q 之间生效。"""
        self.parts.append(b"q\n")
        # 裁剪路径本身不画出来：W* 之后跟 n（什么都不画）
        for clip in clips:
            clip_out: List[bytes] = []
            for cmd in clip:
                if cmd[0] == "c":
                    clip_out.append(b" ".join(self._c(v) for v in cmd[1:]) + b" c\n")
                else:
                    clip_out.append(
                        self._c(cmd[1]) + b" " + self._c(cmd[2])
                        + (b" m\n" if cmd[0] == "m" else b" l\n")
                    )
            self.parts.append(b"".join(clip_out) + b"h W* n\n")
        # q 会把图形状态一起存下来，Q 之后颜色和透明度都要重新设
        self.color = ""
        self.alpha = -1.0
        self.fill(cmds, color, alpha)
        self.parts.append(b"Q\n")
        self.color = ""
        self.alpha = -1.0

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
        shift = lambda path: [
            (cmd[0],) + tuple(v - (ox if i % 2 == 0 else oy) for i, v in enumerate(cmd[1:]))
            for cmd in path
        ]
        cmds = shift(outline_path(points, tool, width, int(stroke.get("cut") or 0)))
        chains = stroke.get("m") or []
        if chains:
            clips = [shift(clip) for clip in mask_clips(chains, _bounds(cmds))]
            writer.fill_clipped(cmds, clips, color, alpha)
        else:
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

"""perfect-freehand 1.2.3 的 Python 移植，只为导出用。

屏幕那边直接用了原版（``web/static/js/vendor/perfect-freehand.js``）。导出走的是
另一条路：``/api/export/{board_id}`` 在服务端从存下来的白板生成 PDF，用不上浏览器，
所以这一份是照着它的 ``getStrokePoints`` / ``getStrokeOutlinePoints`` 一行一行搬过来的。

两份实现必须给出**完全一样**的点列，``tests/test_docs.py`` 里有用例拿真机录的笔画
逐点比对，差一个点就会红。所以这里刻意保持和原版一样的写法：同样的常数、同样的
循环累加方式（浮点累加的次数要一模一样，不能改写成整数循环）、同样的跳过条件。
读起来不像 Python 的地方基本都是这个原因，改之前先看那条用例。

没有搬 ``simulatePressure``：鼠标和手指没有压感读数，但输入层已经把速度折算进压感
通道了（``input.js`` 的 ``pressureFor``），导出时一律按 ``simulatePressure=False`` 走。
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]

RATE_OF_PRESSURE_CHANGE = 0.275
FIXED_PI = math.pi + 0.0001
START_CAP_SEGMENTS = 13
END_CAP_SEGMENTS = 29
CORNER_CAP_SEGMENTS = 13
END_NOISE_THRESHOLD = 3
MIN_STREAMLINE_T = 0.15
STREAMLINE_T_RANGE = 0.85
MIN_RADIUS = 0.01
DEFAULT_FIRST_PRESSURE = 0.25
DEFAULT_PRESSURE = 0.5
UNIT_OFFSET: Vec2 = (1.0, 1.0)


def _add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def _sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _mul(a: Vec2, n: float) -> Vec2:
    return (a[0] * n, a[1] * n)


def _per(a: Vec2) -> Vec2:
    return (a[1], -a[0])


def _neg(a: Vec2) -> Vec2:
    return (-a[0], -a[1])


def _dpr(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _dist(a: Vec2, b: Vec2) -> float:
    return math.hypot(a[1] - b[1], a[0] - b[0])


def _dist2(a: Vec2, b: Vec2) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def _uni(a: Vec2) -> Vec2:
    n = math.hypot(a[0], a[1])
    return (a[0] / n, a[1] / n)


def _lrp(a: Vec2, b: Vec2, t: float) -> Vec2:
    return _add(a, _mul(_sub(b, a), t))


def _prj(a: Vec2, b: Vec2, c: float) -> Vec2:
    return _add(a, _mul(b, c))


def _rot_around(a: Vec2, c: Vec2, r: float) -> Vec2:
    s = math.sin(r)
    k = math.cos(r)
    px = a[0] - c[0]
    py = a[1] - c[1]
    return (px * k - py * s + c[0], px * s + py * k + c[1])


def stroke_radius(size: float, thinning: float, pressure: float,
                  easing: Callable[[float], float] = lambda t: t) -> float:
    return size * easing(0.5 - thinning * (0.5 - pressure))


class StrokePoint:
    __slots__ = ("point", "pressure", "vector", "distance", "running_length")

    def __init__(self, point, pressure, vector, distance, running_length):
        self.point = point
        self.pressure = pressure
        self.vector = vector
        self.distance = distance
        self.running_length = running_length


def get_stroke_points(points: Sequence[Sequence[float]], size: float = 16.0,
                      streamline: float = 0.5, last: bool = False) -> List[StrokePoint]:
    """和 JS 的 getStrokePoints 一一对应。"""
    if not points:
        return []

    t = MIN_STREAMLINE_T + (1 - streamline) * STREAMLINE_T_RANGE
    pts = [tuple(p) for p in points]

    # 两个点的时候中间补几个，避免带收尾的笔画画成一段一段
    if len(pts) == 2:
        last_pt = pts[1]
        pts = pts[:-1]
        for i in range(1, 5):
            pts.append(_lrp(pts[0][:2], last_pt[:2], i / 4))
    if len(pts) == 1:
        pts = [pts[0], tuple(_add(pts[0][:2], UNIT_OFFSET)) + tuple(pts[0][2:])]

    def pressure_of(p, default):
        if len(p) > 2 and p[2] is not None and p[2] >= 0:
            return p[2]
        return default

    out = [StrokePoint((pts[0][0], pts[0][1]),
                       pressure_of(pts[0], DEFAULT_FIRST_PRESSURE),
                       UNIT_OFFSET, 0.0, 0.0)]

    reached_min = False
    running = 0.0
    prev = out[0]
    top = len(pts) - 1

    for i in range(1, len(pts)):
        if last and i == top:
            point = (pts[i][0], pts[i][1])
        else:
            point = _lrp(prev.point, (pts[i][0], pts[i][1]), t)
        if point[0] == prev.point[0] and point[1] == prev.point[1]:
            continue
        distance = _dist(point, prev.point)
        running += distance
        if i < top and not reached_min:
            if running < size:
                continue
            reached_min = True
        prev = StrokePoint(point, pressure_of(pts[i], DEFAULT_PRESSURE),
                           _uni(_sub(prev.point, point)), distance, running)
        out.append(prev)

    out[0].vector = out[1].vector if len(out) > 1 else (0.0, 0.0)
    return out


def _draw_dot(center: Vec2, radius: float) -> List[Vec2]:
    offset_point = _add(center, (1.0, 1.0))
    start = _prj(center, _uni(_per(_sub(center, offset_point))), -radius)
    dot: List[Vec2] = []
    step = 1 / START_CAP_SEGMENTS
    t = step
    while t <= 1:
        dot.append(_rot_around(start, center, FIXED_PI * 2 * t))
        t += step
    return dot


def _draw_round_start_cap(center: Vec2, right_point: Vec2, segments: int) -> List[Vec2]:
    cap: List[Vec2] = []
    step = 1 / segments
    t = step
    while t <= 1:
        cap.append(_rot_around(right_point, center, FIXED_PI * t))
        t += step
    return cap


def _draw_flat_start_cap(center: Vec2, left_point: Vec2, right_point: Vec2) -> List[Vec2]:
    corners = _sub(left_point, right_point)
    a = _mul(corners, 0.5)
    b = _mul(corners, 0.51)
    return [_sub(center, a), _sub(center, b), _add(center, b), _add(center, a)]


def _draw_round_end_cap(center: Vec2, direction: Vec2, radius: float,
                        segments: int) -> List[Vec2]:
    cap: List[Vec2] = []
    start = _prj(center, direction, radius)
    step = 1 / segments
    t = step
    while t < 1:
        cap.append(_rot_around(start, center, FIXED_PI * 3 * t))
        t += step
    return cap


def _draw_flat_end_cap(center: Vec2, direction: Vec2, radius: float) -> List[Vec2]:
    return [
        _add(center, _mul(direction, radius)),
        _add(center, _mul(direction, radius * 0.99)),
        _sub(center, _mul(direction, radius * 0.99)),
        _sub(center, _mul(direction, radius)),
    ]


def get_stroke_outline_points(
    points: List[StrokePoint],
    size: float = 16.0,
    thinning: float = 0.5,
    smoothing: float = 0.5,
    easing: Callable[[float], float] = lambda t: t,
    cap_start: bool = True,
    cap_end: bool = True,
    last: bool = False,
) -> List[Vec2]:
    """和 JS 的 getStrokeOutlinePoints 一一对应（不含 taper 和 simulatePressure）。"""
    if not points or size <= 0:
        return []

    total_length = points[-1].running_length
    min_distance = (size * smoothing) ** 2

    left: List[Vec2] = []
    right: List[Vec2] = []

    # 开头几个点的压感取平均，免得起笔处鼓一块
    prev_pressure = points[0].pressure
    for p in points[:10]:
        prev_pressure = (prev_pressure + p.pressure) / 2

    radius = stroke_radius(size, thinning, points[-1].pressure, easing)
    first_radius: Optional[float] = None
    prev_vector = points[0].vector
    prev_left = points[0].point
    prev_right = prev_left
    temp_left = prev_left
    temp_right = prev_right
    prev_is_sharp = False

    n = len(points)
    for i in range(n):
        sp = points[i]
        pressure = sp.pressure
        point = sp.point
        vector = sp.vector
        is_last = i == n - 1

        # 掐掉末尾那一小段噪声
        if not is_last and total_length - sp.running_length < END_NOISE_THRESHOLD:
            continue

        if thinning:
            radius = stroke_radius(size, thinning, pressure, easing)
        else:
            radius = size / 2
        if first_radius is None:
            first_radius = radius
        radius = max(MIN_RADIUS, radius)

        next_vector = (points[i] if is_last else points[i + 1]).vector
        next_dpr = 1.0 if is_last else _dpr(vector, next_vector)
        prev_dpr = _dpr(vector, prev_vector)

        is_sharp = prev_dpr < 0 and not prev_is_sharp
        next_is_sharp = next_dpr < 0

        if is_sharp or next_is_sharp:
            # 急转弯：在这个点上画一个圆帽，然后跳到下一个点
            offset = _mul(_per(prev_vector), radius)
            step = 1 / CORNER_CAP_SEGMENTS
            t = 0.0
            while t <= 1:
                temp_left = _rot_around(_sub(point, offset), point, FIXED_PI * t)
                left.append(temp_left)
                temp_right = _rot_around(_add(point, offset), point, FIXED_PI * -t)
                right.append(temp_right)
                t += step
            prev_left = temp_left
            prev_right = temp_right
            if next_is_sharp:
                prev_is_sharp = True
            continue

        prev_is_sharp = False

        if is_last:
            offset = _mul(_per(vector), radius)
            left.append(_sub(point, offset))
            right.append(_add(point, offset))
            continue

        offset = _mul(_per(_lrp(next_vector, vector, next_dpr)), radius)

        temp_left = _sub(point, offset)
        if i <= 1 or _dist2(prev_left, temp_left) > min_distance:
            left.append(temp_left)
            prev_left = temp_left

        temp_right = _add(point, offset)
        if i <= 1 or _dist2(prev_right, temp_right) > min_distance:
            right.append(temp_right)
            prev_right = temp_right

        prev_pressure = pressure
        prev_vector = vector

    first_point = points[0].point
    last_point = points[-1].point if n > 1 else _add(points[0].point, (1.0, 1.0))

    start_cap: List[Vec2] = []
    end_cap: List[Vec2] = []

    if n == 1:
        if last:
            return _draw_dot(first_point, first_radius if first_radius is not None else radius)
    else:
        if cap_start:
            start_cap.extend(_draw_round_start_cap(first_point, right[0], START_CAP_SEGMENTS))
        else:
            start_cap.extend(_draw_flat_start_cap(first_point, left[0], right[0]))

        direction = _per(_neg(points[-1].vector))
        if cap_end:
            end_cap.extend(_draw_round_end_cap(last_point, direction, radius, END_CAP_SEGMENTS))
        else:
            end_cap.extend(_draw_flat_end_cap(last_point, direction, radius))

    return left + end_cap + list(reversed(right)) + start_cap


def get_stroke(points: Sequence[Sequence[float]], size: float = 16.0,
               thinning: float = 0.5, smoothing: float = 0.5,
               streamline: float = 0.5, cap_start: bool = True,
               cap_end: bool = True, last: bool = False) -> List[Vec2]:
    """和 JS 的 getStroke 一样：先 getStrokePoints 再 getStrokeOutlinePoints。"""
    sp = get_stroke_points(points, size=size, streamline=streamline, last=last)
    return get_stroke_outline_points(sp, size=size, thinning=thinning,
                                     smoothing=smoothing, cap_start=cap_start,
                                     cap_end=cap_end, last=last)

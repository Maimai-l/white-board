"""把笔迹栅格化到 Pillow 图上：文档板的图片导出和分页导出的 PNG 共用。

形状和 PDF 导出是同一个（``inkpdf.outline_path``），只是摊成多边形再画；每笔只在
自己的包围盒里按 ``SUPERSAMPLE`` 倍超采样，缩回来就是抗锯齿。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence, Tuple

from . import inkpdf

SUPERSAMPLE = 3
# 一笔的超采样图比这个还大（像素数）就不超采样了，免得一笔吃掉几百 MB 内存
MAX_TILE_PIXELS = 64_000_000


def _pil():
    from PIL import Image  # noqa: WPS433 - 调用方已经确认装了 Pillow

    return Image


def paint_strokes(
    canvas,
    strokes: Sequence[Dict[str, Any]],
    origin: Tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
) -> None:
    """把笔画按顺序叠到 RGBA 的 ``canvas`` 上。

    白板坐标 ``(x, y)`` 落在画布的 ``((x - ox) * scale, (y - oy) * scale)``；
    画布之外的部分直接裁掉。
    """
    Image = _pil()
    from PIL import ImageDraw  # noqa: WPS433

    ox, oy = origin
    for stroke in strokes:
        points = inkpdf.points_of(stroke)
        if not points:
            continue
        tool = stroke.get("tool", "pen")
        alpha = inkpdf.ALPHA.get(tool, 1.0)
        width = float(stroke.get("w", 3.0))
        points = inkpdf.simplify(points, inkpdf.epsilon(width))
        cut = int(stroke.get("cut") or 0)
        polygon = inkpdf.flatten(inkpdf.outline_path(points, tool, width, cut))
        if not polygon:
            continue
        if ox or oy or scale != 1.0:
            polygon = [((x - ox) * scale, (y - oy) * scale) for x, y in polygon]
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        x0, y0 = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1
        x1, y1 = math.ceil(max(xs)) + 1, math.ceil(max(ys)) + 1
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(canvas.width, x1), min(canvas.height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        tile_size = ((x1 - x0) * SUPERSAMPLE, (y1 - y0) * SUPERSAMPLE)
        if tile_size[0] * tile_size[1] > MAX_TILE_PIXELS:  # 超大笔画退回不抗锯齿
            tile = Image.new("L", (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(tile).polygon([(x - x0, y - y0) for x, y in polygon], fill=255)
        else:
            tile = Image.new("L", tile_size, 0)
            ImageDraw.Draw(tile).polygon(
                [((x - x0) * SUPERSAMPLE, (y - y0) * SUPERSAMPLE) for x, y in polygon], fill=255
            )
            tile = tile.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        if stroke.get("m"):
            tile = erase_mask(tile, stroke["m"], x0, y0, origin, scale)
        if alpha < 1.0:
            tile = tile.point(lambda v: int(v * alpha))
        color = inkpdf.rgb(stroke.get("color", "#1b1b1f"))
        patch = Image.new("RGBA", tile.size, tuple(round(c * 255) for c in color) + (255,))
        patch.putalpha(tile)
        canvas.alpha_composite(patch, (x0, y0))


def erase_mask(
    tile,
    chains: Sequence[Sequence[float]],
    x0: int,
    y0: int,
    origin: Tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
):
    """从一笔的覆盖图里减去像素橡皮擦过的胶囊链（笔画的 ``m``），与 PDF 导出的裁剪一致。

    ``(x0, y0)`` 是这块覆盖图在画布上的左上角，``origin`` / ``scale`` 同 :func:`paint_strokes`。
    """
    Image = _pil()
    from PIL import ImageChops, ImageDraw  # noqa: WPS433

    s = SUPERSAMPLE
    ox, oy = origin
    hole = Image.new("L", (tile.width * s, tile.height * s), 0)
    draw = ImageDraw.Draw(hole)
    for chain in chains:
        r = chain[0] * scale * s
        pts = [
            (((chain[i] - ox) * scale - x0) * s, ((chain[i + 1] - oy) * scale - y0) * s)
            for i in range(1, len(chain) - 1, 2)
        ]
        for x, y in pts:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            length = math.hypot(bx - ax, by - ay)
            if length < 1e-9:
                continue
            nx, ny = -(by - ay) / length * r, (bx - ax) / length * r
            draw.polygon([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)], fill=255)
    hole = hole.resize(tile.size, Image.LANCZOS)
    return ImageChops.subtract(tile, hole)

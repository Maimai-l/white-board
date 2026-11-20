"""分页导出：把一块白板按页框导出成 PDF、PNG 压缩包或单张 PNG。

页框从哪里来见 ``pagination``；这里只管把选中的那几页画出来。

* 大白板和笔记：每页是一张新纸（默认 A4），墨迹是矢量（``inkpdf.content_stream``，
  和文档板导出同一套轮廓），所以 PDF 放多大都清楚；PNG 按 ``dpi`` 栅格化。
  纸色和背景纹理（格子、横线、点）也画上，可以用 ``background: false`` 关掉。
* 文档板：PDF 原件走 ``docs.export_pdf``（笔迹追加到原页面上，原内容流不动），
  然后只留下选中的页；图片原件做成一页 PDF，或者按原分辨率出 PNG。

* 整块（``frames: "whole"``）：不分页，一页框住全部墨迹（``pagination.whole_page``），
  纸张就是墨迹的大小加四周留白，1:1；PNG 太大时按 ``MAX_PAGE_PIXELS`` 降清晰度。
* 白板文件（``format: "wbz"``）：原样的 ``.wbz``，由服务端先存盘再读文件
  （``server.handle_page_export``），这里只给文件名（:func:`raw_name`）。

入口是 :func:`export`；请求参数的校验在 :func:`parse_options`，出错抛
:class:`ExportError`，消息直接给调用方看。
"""

from __future__ import annotations

import io
import logging
import math
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from . import docs, inkpdf, inkraster, pagination

log = logging.getLogger(__name__)

FORMATS = ("pdf", "zip", "png")
# 白板文件本身：不经过 export()，由服务端存盘后原样读出来
RAW_FORMAT = "wbz"
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "zip": "application/zip",
    "png": "image/png",
    RAW_FORMAT: "application/octet-stream",
}
DEFAULT_DPI = 144.0
MIN_DPI = 36.0
MAX_DPI = 600.0
# 一次最多导出、最多接受的页数（文档板原件的上限也是 400 页）
MAX_PAGES = 400
# 单页 PNG 的像素上限：A4 在 600 dpi 是 3500 万像素，再大就按比例降 dpi
MAX_PAGE_PIXELS = 40_000_000
MAX_MARGIN = 144.0
# 背景纹理，与 renderer.js 的 GRID_STEP / LINE 一致；线宽和点的半径按 100% 缩放时的样子
PATTERN_STEP = 40.0
PATTERN_COLOR = "#d7dbe6"
PATTERN_LINE = 0.75
MIN_PATTERN_PT = 10.0
# PNG 里格子至少隔这么多像素：整块导出的大白板降了清晰度以后，格子不能糊成一片灰
MIN_PATTERN_PX = 8.0
# 点阵一页最多这么多个点，再多就把点距加倍：页框很大时 PDF 不至于有几百万个点
MAX_PATTERN_DOTS = 40_000
DOT_RADIUS = 1.2
PAPER_COLOR = "#ffffff"
PATTERNS = ("blank", "grid", "lines", "dots")


class ExportError(ValueError):
    """请求不合法或者导不出来；消息给调用方看。"""


@dataclass
class Options:
    format: str
    frames: Any = None  # None：自动分页；"whole"：整块一页；否则是页框列表
    select: Optional[List[int]] = None  # None：全部页
    dpi: Optional[float] = None  # None：默认（图片原件按原分辨率）
    # 纸张 (宽, 高) pt；或者 ``pagination.CONTENT_PAPER``：每页的纸跟随页框
    paper: Any = pagination.PAPERS[pagination.DEFAULT_PAPER]
    background: bool = True


# ------------------------------------------------------------------ 参数


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExportError(f"{name} must be a finite number")
    return float(value)


def _frame(raw: Any, default_paper: Any) -> Dict[str, Any]:
    """校验一个页框：``{x, y, w, h}``，可选 ``paper``（[宽, 高] pt 或纸张名）和 ``margin``。

    没给 ``paper`` 时用请求的纸张，横竖跟页框的形状走；请求的纸张是「跟随内容」时，
    纸就是页框加四周留白。
    """
    if not isinstance(raw, Mapping):
        raise ExportError("each frame must be an object")
    x, y = _number(raw.get("x"), "x"), _number(raw.get("y"), "y")
    w, h = _number(raw.get("w"), "w"), _number(raw.get("h"), "h")
    if not (0 < w <= 1e6 and 0 < h <= 1e6) or abs(x) > 1e7 or abs(y) > 1e7:
        raise ExportError("frame size out of range")
    if raw.get("paper") is None and pagination.is_content_paper(default_paper):
        paper = pagination.content_sheet(w, h, pagination.MARGIN)[0]
    elif raw.get("paper") is None:
        paper = pagination.oriented(default_paper, w > h)
    else:
        try:
            short, long = pagination.paper_size(raw["paper"])
        except (TypeError, ValueError) as exc:
            raise ExportError(str(exc)) from exc
        given = raw["paper"]
        if isinstance(given, (list, tuple)):
            landscape = float(given[0]) > float(given[1])
        else:
            landscape = w > h
        paper = pagination.oriented((short, long), landscape)
    frame = {"x": x, "y": y, "w": w, "h": h, "paper": [paper[0], paper[1]]}
    if raw.get("margin") is not None:
        margin = _number(raw["margin"], "margin")
        if not 0 <= margin <= min(MAX_MARGIN, min(paper) / 4):
            raise ExportError("margin out of range")
        frame["margin"] = margin
    return frame


def parse_options(raw: Mapping[str, Any]) -> Options:
    """导出请求的参数（POST 的 JSON，或 GET 的查询参数换成的同样结构）。

    * ``format``：``pdf``、``zip``（每页一张 PNG）、``png``（只能选一页）或 ``wbz``
      （白板文件本身，其余参数都不看）；
    * ``frames``：``"auto"``（默认）、``"whole"``（整块一页）或页框列表，见 :func:`_frame`；
    * ``select``：要导出的页（从 0 数），默认全部；
    * ``dpi``：PNG 的清晰度，36～600，默认 144；
    * ``paper``：``a4``（默认）、``letter``、``content``（每页的纸跟随页框）或 ``[宽, 高]``（pt）；
    * ``background``：画不画纸色和背景纹理，默认画。
    """
    if not isinstance(raw, Mapping):
        raise ExportError("the request must be a JSON object")
    fmt = raw.get("format")
    if fmt == RAW_FORMAT:
        return Options(format=RAW_FORMAT)
    if fmt not in FORMATS:
        raise ExportError("format must be one of pdf, zip, png, wbz")
    try:
        given = raw.get("paper") or pagination.DEFAULT_PAPER
        paper = pagination.CONTENT_PAPER if pagination.is_content_paper(given) else pagination.paper_size(given)
    except (TypeError, ValueError) as exc:
        raise ExportError(str(exc)) from exc

    frames = raw.get("frames", "auto")
    if frames is None or frames == "auto":
        frames = None
    elif frames == "whole":
        pass
    elif isinstance(frames, list):
        if not frames or len(frames) > MAX_PAGES:
            raise ExportError(f"frames must list 1–{MAX_PAGES} pages")
        frames = [_frame(item, paper) for item in frames]
    else:
        raise ExportError('frames must be "auto", "whole" or a list')

    select = raw.get("select")
    if select is not None:
        if not isinstance(select, list) or not select:
            raise ExportError("select must be a non-empty list of page indices")
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in select):
            raise ExportError("select must hold page indices counted from 0")
        select = sorted(set(select))
        if len(select) > MAX_PAGES:
            raise ExportError(f"at most {MAX_PAGES} pages can be exported at once")

    dpi = raw.get("dpi")
    if dpi is not None:
        dpi = _number(dpi, "dpi")
        if not MIN_DPI <= dpi <= MAX_DPI:
            raise ExportError(f"dpi must be {MIN_DPI:g}–{MAX_DPI:g}")

    background = raw.get("background", True)
    if not isinstance(background, bool):
        raise ExportError("background must be true or false")
    return Options(format=fmt, frames=frames, select=select, dpi=dpi, paper=paper, background=background)


def options_from_query(query: Mapping[str, str]) -> Options:
    """GET 的查询参数：``format``、``frames=whole``、``select=1,3``、``dpi``、``paper``、
    ``background=0|1``。

    页框只能自动分或整块一页；要自己给页框用 POST。
    """
    raw: Dict[str, Any] = {"format": query.get("format")}
    if query.get("frames"):
        if query["frames"] not in ("auto", "whole"):
            raise ExportError('frames must be "auto" or "whole" in a query')
        raw["frames"] = query["frames"]
    if query.get("select"):
        try:
            raw["select"] = [int(part) for part in query["select"].split(",") if part.strip()]
        except ValueError as exc:
            raise ExportError("select must be page indices separated by commas") from exc
    if query.get("dpi"):
        try:
            raw["dpi"] = float(query["dpi"])
        except ValueError as exc:
            raise ExportError("dpi must be a number") from exc
    if query.get("paper"):
        raw["paper"] = query["paper"]
    if "background" in query:
        raw["background"] = query["background"] not in ("0", "false", "no")
    return parse_options(raw)


# ------------------------------------------------------------------ 页面


def pages_for(meta: Dict[str, Any], strokes: Sequence[Dict[str, Any]], options: Options) -> List[Dict[str, Any]]:
    """这次导出的全部页（选页之前）。文档板只能用原件的页。"""
    if docs.doc_of(meta) is not None:
        if options.frames is not None:
            raise ExportError("document boards export the pages of their original")
        return pagination.auto_pages(meta, strokes)
    if options.frames == "whole":
        return [pagination.whole_page(meta, strokes)]
    if options.frames is not None:
        return options.frames
    paper = options.paper if pagination.is_content_paper(options.paper) else list(options.paper)
    return pagination.auto_pages(meta, strokes, paper)


def selected(pages: Sequence[Dict[str, Any]], options: Options) -> List[int]:
    indices = list(range(len(pages))) if options.select is None else options.select
    if indices and indices[-1] >= len(pages):
        raise ExportError(f"the board has {len(pages)} pages; page index {indices[-1]} does not exist")
    if len(indices) > MAX_PAGES:
        raise ExportError(f"at most {MAX_PAGES} pages can be exported at once")
    if options.format == "png" and len(indices) != 1:
        raise ExportError("png exports exactly one page; choose zip for several")
    return indices


def background_of(meta: Dict[str, Any]) -> Tuple[str, str]:
    """``(纹理, 纸色)``，规则与 renderer.js 的 backgroundOf 相同。"""
    background = meta.get("background") if isinstance(meta, dict) else None
    if isinstance(background, str):
        pattern, paper = background, PAPER_COLOR
    elif isinstance(background, dict):
        pattern = background.get("pattern") or "grid"
        paper = background.get("paper") or PAPER_COLOR
    else:
        pattern, paper = "grid", PAPER_COLOR
    if pattern not in PATTERNS:
        pattern = "grid"
    if not re.match(r"^#[0-9a-fA-F]{6}$", str(paper)):
        paper = PAPER_COLOR
    return pattern, paper.lower()


def boxed(strokes: Sequence[Dict[str, Any]]) -> List[Tuple[Any, Dict[str, Any]]]:
    """每笔配上包围盒，按层叠顺序；没有点的笔画丢掉。一次导出只算一次。"""
    pairs = []
    for stroke in strokes:
        box = pagination.stroke_box(stroke)
        if box is not None:
            pairs.append((box, stroke))
    return pairs


def strokes_in(pairs: Sequence[Tuple[Any, Dict[str, Any]]], page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """和页框有交叠的笔画（``pairs`` 来自 :func:`boxed`），保持原来的层叠顺序。"""
    frame = (page["x"], page["y"], page["x"] + page["w"], page["y"] + page["h"])
    return [stroke for box, stroke in pairs if pagination.intersects(box, frame)]


def _pattern_lines(page: Dict[str, Any], pattern: str, scale: float, pixels: Optional[float] = None):
    """页框里的背景纹理（白板坐标）：返回 ``(竖线 x 列表, 横线 y 列表, 点列表)``。

    ``scale`` 是每个白板单位在纸上多少 pt。和 renderer.js 的 patternStep 一样，
    格子在纸上小于 ``MIN_PATTERN_PT`` 时逐级合并，页框再大也不会糊成一片灰。
    PNG 再给 ``pixels``（每个白板单位多少像素）：格子也不能小于 ``MIN_PATTERN_PX``。
    点阵的点数不超过 ``MAX_PATTERN_DOTS``。
    """
    step = PATTERN_STEP
    while step * scale < MIN_PATTERN_PT or (pixels is not None and step * pixels < MIN_PATTERN_PX):
        step *= 2
    x0, y0 = page["x"], page["y"]
    x1, y1 = x0 + page["w"], y0 + page["h"]
    if pattern == "dots":
        while (page["w"] / step + 1) * (page["h"] / step + 1) > MAX_PATTERN_DOTS:
            step *= 2
    xs = [k * step for k in range(math.ceil(x0 / step), math.floor(x1 / step) + 1)]
    ys = [k * step for k in range(math.ceil(y0 / step), math.floor(y1 / step) + 1)]
    if pattern == "grid":
        return xs, ys, []
    if pattern == "lines":
        return [], ys, []
    if pattern == "dots":
        return [], [], [(x, y) for y in ys for x in xs]
    return [], [], []


# ------------------------------------------------------------------ PDF


def _pdf_page_stream(page: Dict[str, Any], strokes, pattern: str, paper_color: str, background: bool):
    """一张新纸的内容流：纸色、裁到页框里的纹理和墨迹。返回 ``(字节, 透明度集合)``。"""
    pw, ph = page["paper"]
    scale, left, top = pagination.placement(page)
    width, height = page["w"] * scale, page["h"] * scale
    bottom = ph - top - height
    num = inkpdf._num
    parts: List[bytes] = []
    if background and paper_color != PAPER_COLOR:
        r, g, b = inkpdf.rgb(paper_color)
        parts.append(b"%s %s %s rg 0 0 %s %s re f\n" % (num(r), num(g), num(b), num(pw), num(ph)))
    parts.append(b"q %s %s %s %s re W n\n" % (num(left), num(bottom), num(width), num(height)))
    if background:
        xs, ys, dots = _pattern_lines(page, pattern, scale)
        to_x = lambda x: left + (x - page["x"]) * scale  # noqa: E731
        to_y = lambda y: ph - top - (y - page["y"]) * scale  # noqa: E731
        r, g, b = inkpdf.rgb(PATTERN_COLOR)
        if xs or ys:
            lines = [b"%s %s %s RG %s w\n" % (num(r), num(g), num(b), num(PATTERN_LINE * scale))]
            for x in xs:
                lines.append(b"%s %s m %s %s l\n" % (num(to_x(x)), num(bottom), num(to_x(x)), num(bottom + height)))
            for y in ys:
                lines.append(b"%s %s m %s %s l\n" % (num(left), num(to_y(y)), num(left + width), num(to_y(y))))
            parts.append(b"".join(lines) + b"S\n")
        if dots:
            radius = DOT_RADIUS * scale
            cells = [b"%s %s %s rg\n" % (num(r), num(g), num(b))]
            for x, y in dots:
                cx, cy = to_x(x), to_y(y)
                arc: List[Tuple] = []
                inkpdf._arc(arc, cx, cy, radius, 0.0, 2 * math.pi)
                cells.append(b"%s %s m\n" % (num(cx + radius), num(cy)))
                cells.extend(b"%s c\n" % b" ".join(num(v) for v in cmd[1:]) for cmd in arc)
            parts.append(b"".join(cells) + b"f\n")
    matrix = [scale, 0.0, 0.0, -scale, left, ph - top]
    ink, alphas = inkpdf.content_stream(strokes, origin=(page["x"], page["y"]), matrix=matrix)
    parts.append(ink)
    parts.append(b"Q\n")
    return b"".join(parts), alphas


class _PdfBuilder:
    """用 pypdf 从零拼一份 PDF：每页一个内容流，透明度放 ExtGState，可选一张底图。"""

    def __init__(self):
        pypdf = docs._pypdf()
        self.writer = pypdf.PdfWriter()

    def add_page(self, width: float, height: float, content: bytes, alphas, image: Optional[Dict[str, Any]] = None):
        from pypdf.generic import DictionaryObject, FloatObject, NameObject, StreamObject  # noqa: WPS433

        page = self.writer.add_blank_page(width=width, height=height)
        resources = DictionaryObject()
        if alphas:
            states = DictionaryObject()
            for alpha in sorted(alphas):
                state = DictionaryObject()
                state[NameObject("/Type")] = NameObject("/ExtGState")
                state[NameObject("/ca")] = FloatObject(alpha / 100.0)
                state[NameObject("/CA")] = FloatObject(alpha / 100.0)
                states[NameObject("/WBa%d" % alpha)] = self.writer._add_object(state)
            resources[NameObject("/ExtGState")] = states
        if image is not None:
            objects = DictionaryObject()
            objects[NameObject("/WBimg")] = self._image(image)
            resources[NameObject("/XObject")] = objects
            content = b"q %s 0 0 %s 0 0 cm /WBimg Do Q\n" % (inkpdf._num(width), inkpdf._num(height)) + content
        page[NameObject("/Resources")] = resources
        stream = StreamObject()
        stream._data = inkpdf.flate(content)
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        page[NameObject("/Contents")] = self.writer._add_object(stream)

    def _image(self, image: Dict[str, Any]):
        from pypdf.generic import NameObject, NumberObject, StreamObject  # noqa: WPS433

        stream = StreamObject()
        stream._data = image["data"]
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Image")
        stream[NameObject("/Width")] = NumberObject(image["width"])
        stream[NameObject("/Height")] = NumberObject(image["height"])
        stream[NameObject("/ColorSpace")] = NameObject(image["colorspace"])
        stream[NameObject("/BitsPerComponent")] = NumberObject(8)
        stream[NameObject("/Filter")] = NameObject(image["filter"])
        if image.get("smask") is not None:
            stream[NameObject("/SMask")] = self._image(image["smask"])
        return self.writer._add_object(stream)

    def data(self) -> bytes:
        buffer = io.BytesIO()
        self.writer.write(buffer)
        return buffer.getvalue()


def _image_xobject(path: Path) -> Dict[str, Any]:
    """图片原件做成 PDF 的图片对象：JPEG 原样嵌进去（不重新压缩），其余无损压缩。"""
    Image = docs._pil()
    with Image.open(path) as source:
        source.load()
        if source.format == "JPEG" and source.mode in ("RGB", "L"):
            return {
                "data": path.read_bytes(),
                "width": source.width,
                "height": source.height,
                "colorspace": "/DeviceRGB" if source.mode == "RGB" else "/DeviceGray",
                "filter": "/DCTDecode",
            }
        has_alpha = source.mode in ("RGBA", "LA", "PA") or "transparency" in source.info
        rgba = source.convert("RGBA" if has_alpha else "RGB")
    out = {
        "data": inkpdf.flate(rgba.convert("RGB").tobytes()),
        "width": rgba.width,
        "height": rgba.height,
        "colorspace": "/DeviceRGB",
        "filter": "/FlateDecode",
    }
    if has_alpha:
        out["smask"] = {
            "data": inkpdf.flate(rgba.getchannel("A").tobytes()),
            "width": rgba.width,
            "height": rgba.height,
            "colorspace": "/DeviceGray",
            "filter": "/FlateDecode",
        }
    return out


def _board_pdf(meta, strokes, pages, indices, background: bool) -> bytes:
    pattern, paper_color = background_of(meta)
    builder = _PdfBuilder()
    pairs = boxed(strokes)
    for index in indices:
        page = pages[index]
        content, alphas = _pdf_page_stream(page, strokes_in(pairs, page), pattern, paper_color, background)
        builder.add_page(page["paper"][0], page["paper"][1], content, alphas)
    return builder.data()


def _doc_pdf(strokes, pages, indices, path: Path) -> bytes:
    if docs.kind_of(path) == "image":
        page = pages[0]
        groups = docs.group_by_page(strokes, [{"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]} for p in pages])
        content, alphas = inkpdf.content_stream(
            groups[0], origin=(page["x"], page["y"]), matrix=[1.0, 0.0, 0.0, -1.0, 0.0, page["h"]]
        )
        builder = _PdfBuilder()
        builder.add_page(page["w"], page["h"], content, alphas, image=_image_xobject(path))
        return builder.data()

    with tempfile.TemporaryDirectory(prefix="whiteboard-export-") as folder:
        merged = Path(folder) / "merged.pdf"
        docs.export_pdf(path, strokes, merged)
        if indices == list(range(len(pages))):
            return merged.read_bytes()
        pypdf = docs._pypdf()
        reader = pypdf.PdfReader(str(merged))
        writer = pypdf.PdfWriter()
        for index in indices:
            writer.add_page(reader.pages[index])
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()


# ------------------------------------------------------------------ PNG


def _pixels_per_pt(page: Dict[str, Any], dpi: float) -> float:
    k = dpi / 72.0
    pw, ph = page["paper"]
    if pw * ph * k * k > MAX_PAGE_PIXELS:
        lower = math.sqrt(MAX_PAGE_PIXELS / (pw * ph))
        while round(pw * lower) * round(ph * lower) > MAX_PAGE_PIXELS:  # 四舍五入别把像素数又推过上限
            lower *= 0.999
        log.info("export._pixels_per_pt：%.0f dpi 超过单页像素上限，降到 %.0f dpi", dpi, lower * 72)
        k = lower
    return k


def _hex_rgb(color: str) -> Tuple[int, int, int]:
    return tuple(round(c * 255) for c in inkpdf.rgb(color))  # type: ignore[return-value]


def _board_png(meta, pairs, page, dpi: float, background: bool):
    """一张新纸的 PNG，返回 ``(图, 实际的每 pt 像素数)``。"""
    Image = docs._pil()
    from PIL import ImageDraw  # noqa: WPS433

    pattern, paper_color = background_of(meta)
    k = _pixels_per_pt(page, dpi)
    pw, ph = page["paper"]
    size = (max(1, round(pw * k)), max(1, round(ph * k)))
    fill = _hex_rgb(paper_color) + (255,) if background else (0, 0, 0, 0)
    canvas = Image.new("RGBA", size, fill)

    scale, left, top = pagination.placement(page)
    unit = scale * k  # 每个白板单位多少像素
    x0, y0 = round(left * k), round(top * k)
    frame = Image.new("RGBA", (max(1, round(page["w"] * unit)), max(1, round(page["h"] * unit))), (0, 0, 0, 0))
    if background:
        xs, ys, dots = _pattern_lines(page, pattern, scale, pixels=unit)
        draw = ImageDraw.Draw(frame)
        color = _hex_rgb(PATTERN_COLOR) + (255,)
        line = max(1, round(PATTERN_LINE * unit))
        for x in xs:
            px = (x - page["x"]) * unit
            draw.line([(px, 0), (px, frame.height)], fill=color, width=line)
        for y in ys:
            py = (y - page["y"]) * unit
            draw.line([(0, py), (frame.width, py)], fill=color, width=line)
        radius = max(0.8, DOT_RADIUS * unit)
        for x, y in dots:
            px, py = (x - page["x"]) * unit, (y - page["y"]) * unit
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color)
    inkraster.paint_strokes(frame, strokes_in(pairs, page), origin=(page["x"], page["y"]), scale=unit)
    canvas.alpha_composite(frame, (x0, y0))
    return (canvas.convert("RGB") if background else canvas), k


def _doc_png(strokes, pages, index: int, path: Path, dpi: Optional[float]):
    """文档板的一页 PNG，返回 ``(图, 实际的每 pt 像素数)``。"""
    Image = docs._pil()
    page = pages[index]
    groups = docs.group_by_page(strokes, [{"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]} for p in pages])
    if docs.kind_of(path) == "image":
        k = 1.0 if dpi is None else _pixels_per_pt(page, dpi)
        with Image.open(path) as source:
            source.load()
            canvas = source.convert("RGBA")
        if abs(k - 1.0) > 1e-9:
            canvas = canvas.resize((max(1, round(canvas.width * k)), max(1, round(canvas.height * k))), Image.LANCZOS)
    else:
        k = _pixels_per_pt(page, DEFAULT_DPI if dpi is None else dpi)
        pdfium = docs._pdfium()
        with docs._PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                source = pdf[index]
                try:
                    bitmap = source.render(scale=k)
                    try:
                        # 和 docs.render_page 一样：位图和 PIL 图共享内存，复制一份再放掉
                        canvas = bitmap.to_pil().convert("RGBA").copy()
                    finally:
                        bitmap.close()
                finally:
                    source.close()
            finally:
                pdf.close()
    inkraster.paint_strokes(canvas, groups[index], origin=(page["x"], page["y"]), scale=k)
    return canvas, k


def _png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


# ------------------------------------------------------------------ 入口


def safe_name(name: Any) -> str:
    """白板名做成文件名：去掉文件系统不认的字符，最长 80 字；没有名字时是 ``whiteboard-日期``。"""
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        name = "whiteboard-" + time.strftime("%Y-%m-%d")
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "-", name).strip(" .") or "whiteboard"
    return name[:80]


def base_name(meta: Dict[str, Any]) -> str:
    """导出文件的主名（不带扩展名）：文档板是「原件名-批注」，其他白板用白板名。"""
    if docs.doc_of(meta) is not None:
        return Path(docs.default_export_name(meta)).stem
    return safe_name(meta.get("name") if isinstance(meta, dict) else "")


def raw_name(meta: Dict[str, Any]) -> str:
    """白板文件下载时的文件名：任何白板（文档板也是）都是 ``<白板名>.wbz``。"""
    return safe_name(meta.get("name") if isinstance(meta, dict) else "") + ".wbz"


def page_name(base: str, index: int, total: int) -> str:
    digits = max(2, len(str(total)))
    return f"{base}-{index + 1:0{digits}d}.png"


class Exported(NamedTuple):
    body: bytes
    content_type: str
    name: str
    dpi: Optional[float] = None  # PNG 实际用的清晰度（几页里最低的）；超过像素上限时比要的低


def export(
    meta: Dict[str, Any],
    strokes: Sequence[Dict[str, Any]],
    options: Options,
    doc_path: Optional[Path] = None,
) -> Exported:
    """按 ``options`` 导出，返回 :class:`Exported`（内容、Content-Type、文件名、PNG 的清晰度）。

    文档板要给 ``doc_path``（原件）。参数不对抛 :class:`ExportError`；原件读不了抛
    ``docs.DocError``。``wbz`` 不在这里导出（见 :func:`raw_name`）。
    """
    if options.format not in FORMATS:
        raise ExportError(f"{options.format} is not exported here")
    is_doc = docs.doc_of(meta) is not None
    if is_doc and doc_path is None:
        raise ExportError("the original of this document board is missing")
    pages = pages_for(meta, strokes, options)
    indices = selected(pages, options)
    base = base_name(meta)

    if options.format == "pdf":
        if is_doc:
            body = _doc_pdf(strokes, pages, indices, doc_path)
        else:
            body = _board_pdf(meta, strokes, pages, indices, options.background)
        return Exported(body, CONTENT_TYPES["pdf"], f"{base}.pdf")

    pairs = None if is_doc else boxed(strokes)
    used: List[float] = []  # 每页实际的每 pt 像素数

    def render(index: int) -> bytes:
        if is_doc:
            image, k = _doc_png(strokes, pages, index, doc_path, options.dpi)
        else:
            image, k = _board_png(meta, pairs, pages[index], options.dpi or DEFAULT_DPI, options.background)
        used.append(k)
        return _png_bytes(image)

    if options.format == "png":
        index = indices[0]
        name = f"{base}.png" if len(pages) == 1 else page_name(base, index, len(pages))
        body = render(index)
        return Exported(body, CONTENT_TYPES["png"], name, round(min(used) * 72, 2))

    buffer = io.BytesIO()
    # PNG 本身已经压缩过，压缩包里只存不压
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for index in indices:
            archive.writestr(page_name(base, index, len(pages)), render(index))
    return Exported(buffer.getvalue(), CONTENT_TYPES["zip"], f"{base}.zip", round(min(used) * 72, 2))

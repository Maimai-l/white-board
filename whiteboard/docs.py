"""文档板（beta）：在 PDF / 图片上直接写批注。

原件按位复制进数据目录的 ``docs/`` 里，之后只读不改；笔迹照旧是矢量，
存在白板自己的 ``.wbz`` 里。导出时才把两者合到一起：

* PDF —— 笔迹编成新的内容流追加到原页面上，原始内容流不重写，
  所以体积只增加笔迹本身（实测每笔约 110 字节）。
* 图片 —— 笔迹按原分辨率栅格化后叠上去，JPEG 复用原来的量化表重编码，
  体积同样不会明显变大。

页面在世界坐标里自上而下排列，页宽居中对齐，页与页之间留 ``PAGE_GAP``。
"""

from __future__ import annotations

import io
import logging
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import inkpdf

log = logging.getLogger(__name__)

# pdfium 不是线程安全的：两个线程同时开 / 渲染文档会直接把进程带走
# （表现是 "bad_variant_access was thrown in -fno-exceptions mode"）。
# 所有 pdfium 调用都串在这把锁后面。
_PDFIUM_LOCK = threading.RLock()

PAGE_GAP = 24.0
MAX_PAGES = 400
MIN_RENDER_WIDTH = 160
MAX_RENDER_WIDTH = 2600
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES


class DocError(RuntimeError):
    """打不开文档时抛出，调用方负责转成给用户看的提示。"""


def _pdfium():
    try:
        import pypdfium2  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise DocError("缺少 pypdfium2，无法打开 PDF") from exc
    return pypdfium2


def _pil():
    try:
        from PIL import Image  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise DocError("缺少 Pillow，无法打开图片") from exc
    return Image


def _pypdf():
    try:
        import pypdf  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise DocError("缺少 pypdf，无法导出 PDF") from exc
    return pypdf


def kind_of(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    raise DocError(f"不支持的文件类型：{suffix or '无扩展名'}")


# ---------------------------------------------------------------- 读取信息


def probe(path: str | Path) -> Dict[str, Any]:
    """读出页数与每页尺寸（PDF 用 pt，图片用像素）。"""
    path = Path(path)
    if not path.exists():
        raise DocError("文件不存在")
    doc_type = kind_of(path)
    if doc_type == "pdf":
        pdfium = _pdfium()
        with _PDFIUM_LOCK:
            try:
                pdf = pdfium.PdfDocument(str(path))
            except Exception as exc:  # pdfium 的异常类型不稳定
                raise DocError("PDF 打不开，可能已加密或损坏") from exc
            try:
                count = len(pdf)
                if count < 1:
                    raise DocError("PDF 里没有页面")
                pages = []
                for index in range(min(count, MAX_PAGES)):
                    width, height = pdf[index].get_size()
                    pages.append([round(float(width), 2), round(float(height), 2)])
            finally:
                pdf.close()
    else:
        Image = _pil()
        try:
            with Image.open(path) as img:
                pages = [[float(img.width), float(img.height)]]
        except Exception as exc:
            raise DocError("图片打不开，可能格式不支持") from exc
    return {"type": doc_type, "name": path.name, "ext": path.suffix.lower(), "pages": pages}


def layout(pages: Sequence[Sequence[float]], gap: float = PAGE_GAP) -> List[Dict[str, float]]:
    """把页面自上而下排好，横向按最宽的一页居中。"""
    if not pages:
        return []
    widest = max(float(p[0]) for p in pages)
    boxes: List[Dict[str, float]] = []
    y = 0.0
    for page in pages:
        width, height = float(page[0]), float(page[1])
        boxes.append({"x": (widest - width) / 2, "y": y, "w": width, "h": height})
        y += height + gap
    return boxes


def doc_of(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    doc = meta.get("doc") if isinstance(meta, dict) else None
    return doc if isinstance(doc, dict) and doc.get("pages") else None


def bounds(meta: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """文档板的可书写范围（世界坐标）。"""
    doc = doc_of(meta)
    if not doc:
        return None
    boxes = layout(doc["pages"])
    widest = max(box["w"] for box in boxes)
    last = boxes[-1]
    return {"x0": 0.0, "x1": widest, "y0": 0.0, "y1": last["y"] + last["h"]}


# ------------------------------------------------------------------ 渲染


def render_page(path: str | Path, index: int, width: int) -> Tuple[bytes, str]:
    """渲染某一页成位图，返回 ``(字节, content-type)``。"""
    path = Path(path)
    width = max(MIN_RENDER_WIDTH, min(MAX_RENDER_WIDTH, int(width)))
    Image = _pil()
    if kind_of(path) == "pdf":
        pdfium = _pdfium()
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                if index < 0 or index >= len(pdf):
                    raise DocError("页码超出范围")
                page = pdf[index]
                scale = width / max(1.0, float(page.get_size()[0]))
                # to_pil() 和 pdfium 的位图共享内存，必须复制一份再放掉文档
                image = page.render(scale=scale).to_pil().convert("RGB").copy()
            finally:
                pdf.close()
        return _encode(image, "jpeg")

    if index != 0:
        raise DocError("页码超出范围")
    with Image.open(path) as source:
        source.load()
        has_alpha = source.mode in ("RGBA", "LA", "P") and "transparency" in source.info
        image = source.convert("RGBA" if has_alpha else "RGB")
        if image.width != width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.LANCZOS)
    return _encode(image, "png" if has_alpha else "jpeg")


def _encode(image, fmt: str) -> Tuple[bytes, str]:
    buffer = io.BytesIO()
    if fmt == "png":
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    image.save(buffer, "JPEG", quality=82, optimize=True, progressive=False)
    return buffer.getvalue(), "image/jpeg"


# ------------------------------------------------------------------ 导出


def _stroke_center(stroke: Dict[str, Any]) -> Tuple[float, float]:
    flat = stroke.get("p") or []
    if not flat:
        return (0.0, 0.0)
    xs = flat[0::3]
    ys = flat[1::3]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def group_by_page(
    strokes: Sequence[Dict[str, Any]], boxes: Sequence[Dict[str, float]]
) -> List[List[Dict[str, Any]]]:
    """按笔画中心归页；落在页间空隙里的归给最近的一页。"""
    groups: List[List[Dict[str, Any]]] = [[] for _ in boxes]
    if not boxes:
        return groups
    for stroke in strokes:
        _, cy = _stroke_center(stroke)
        best, best_distance = 0, math.inf
        for index, box in enumerate(boxes):
            if box["y"] <= cy <= box["y"] + box["h"]:
                best = index
                break
            distance = min(abs(cy - box["y"]), abs(cy - (box["y"] + box["h"])))
            if distance < best_distance:
                best, best_distance = index, distance
        groups[best].append(stroke)
    return groups


def export_pdf(src: str | Path, strokes: Sequence[Dict[str, Any]], out: str | Path) -> Path:
    """把笔迹追加到原 PDF 的每一页上。原有的内容流不做任何重写。"""
    pypdf = _pypdf()
    from pypdf.generic import (  # noqa: WPS433
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        StreamObject,
    )

    info = probe(src)
    boxes = layout(info["pages"])
    groups = group_by_page(strokes, boxes)

    reader = pypdf.PdfReader(str(src))
    if reader.is_encrypted:
        # pdfium 打得开空密码的加密文件，pypdf 得自己先解一次
        try:
            if not reader.decrypt(""):
                raise DocError("这份 PDF 有密码，导不出来")
        except DocError:
            raise
        except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型不稳定
            raise DocError("这份 PDF 有密码，导不出来") from exc
    writer = pypdf.PdfWriter(clone_from=reader)

    def add_stream(raw: bytes):
        stream = StreamObject()
        stream._data = inkpdf.flate(raw)
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        return writer._add_object(stream)

    for index, page in enumerate(writer.pages):
        if index >= len(boxes) or not groups[index]:
            continue
        matrix, _, _ = inkpdf.page_geometry(page)
        raw, alphas = inkpdf.content_stream(
            groups[index], origin=(boxes[index]["x"], boxes[index]["y"]), matrix=matrix
        )
        if not raw:
            continue

        contents = ArrayObject()
        contents.append(add_stream(b"q\n"))
        existing = page.get(NameObject("/Contents"))
        if existing is not None:
            resolved = existing.get_object()
            if isinstance(resolved, ArrayObject):
                contents.extend(resolved)
            else:
                contents.append(existing)
        contents.append(add_stream(b"Q\n"))
        contents.append(add_stream(raw))
        page[NameObject("/Contents")] = contents

        resources = page.get(NameObject("/Resources"))
        if resources is None:
            resources = DictionaryObject()
            page[NameObject("/Resources")] = resources
        resources = resources.get_object()
        states = resources.get(NameObject("/ExtGState"))
        if states is None:
            states = DictionaryObject()
            resources[NameObject("/ExtGState")] = states
        states = states.get_object()
        for alpha in alphas:
            name = NameObject("/WBa%d" % alpha)
            if name in states:
                continue
            state = DictionaryObject()
            state[NameObject("/Type")] = NameObject("/ExtGState")
            state[NameObject("/ca")] = FloatObject(alpha / 100.0)
            state[NameObject("/CA")] = FloatObject(alpha / 100.0)
            states[name] = writer._add_object(state)

    out = Path(out)
    with open(out, "wb") as handle:
        writer.write(handle)
    return out


SUPERSAMPLE = 3


def export_image(src: str | Path, strokes: Sequence[Dict[str, Any]], out: str | Path) -> Path:
    """把笔迹按原分辨率画到图片上；每笔只在自己的包围盒里栅格化。"""
    Image = _pil()
    from PIL import ImageDraw  # noqa: WPS433

    with Image.open(src) as source:
        source.load()
        quantization = getattr(source, "quantization", None)
        subsampling = None
        if source.format == "JPEG":
            try:
                from PIL import JpegImagePlugin  # noqa: WPS433

                subsampling = JpegImagePlugin.get_sampling(source)
            except Exception:  # pragma: no cover - 取不到就用默认
                subsampling = None
        canvas = source.convert("RGBA")

    for stroke in strokes:
        points = inkpdf.points_of(stroke)
        if not points:
            continue
        points = inkpdf.simplify(points, inkpdf.SIMPLIFY)
        tool = stroke.get("tool", "pen")
        alpha, scale = inkpdf.TOOLS.get(tool, inkpdf.TOOLS["pen"])
        polygon = inkpdf.outline(points, tool, float(stroke.get("w", 3.0)) * scale)
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        x0, y0 = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1
        x1, y1 = math.ceil(max(xs)) + 1, math.ceil(max(ys)) + 1
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(canvas.width, x1), min(canvas.height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        tile_size = ((x1 - x0) * SUPERSAMPLE, (y1 - y0) * SUPERSAMPLE)
        if tile_size[0] * tile_size[1] > 64_000_000:  # 超大笔画退回不抗锯齿
            tile = Image.new("L", (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(tile).polygon([(x - x0, y - y0) for x, y in polygon], fill=255)
        else:
            tile = Image.new("L", tile_size, 0)
            ImageDraw.Draw(tile).polygon(
                [((x - x0) * SUPERSAMPLE, (y - y0) * SUPERSAMPLE) for x, y in polygon], fill=255
            )
            tile = tile.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        if alpha < 1.0:
            tile = tile.point(lambda v: int(v * alpha))
        color = inkpdf.rgb(stroke.get("color", "#1b1b1f"))
        patch = Image.new("RGBA", tile.size, tuple(round(c * 255) for c in color) + (255,))
        patch.putalpha(tile)
        canvas.alpha_composite(patch, (x0, y0))

    out = Path(out)
    suffix = out.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        options: Dict[str, Any] = {"optimize": True}
        if quantization:
            options["qtables"] = quantization
        else:
            options["quality"] = 90
        if subsampling is not None and subsampling >= 0:
            options["subsampling"] = subsampling
        canvas.convert("RGB").save(out, "JPEG", **options)
    else:
        canvas.save(out, "PNG", optimize=True)
    return out


def export(src: str | Path, strokes: Sequence[Dict[str, Any]], out: str | Path) -> Path:
    if kind_of(src) == "pdf":
        return export_pdf(src, strokes, out)
    return export_image(src, strokes, out)


def default_export_name(meta: Dict[str, Any]) -> str:
    doc = doc_of(meta) or {}
    stem = Path(doc.get("name") or "document").stem or "document"
    ext = doc.get("ext") or ".pdf"
    if ext in (".gif", ".bmp", ".webp", ".tif", ".tiff"):
        ext = ".png"
    return f"{stem}-批注{ext}"

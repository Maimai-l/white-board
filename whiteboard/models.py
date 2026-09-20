"""白板数据模型与线上数据的校验。

内存 / WebSocket / 磁盘三处共用同一套字段命名，区别只在于落盘时 ``p``
（点数组）会被 :mod:`whiteboard.codec` 压成 base64 字符串。

笔画（stroke）::

    {"id": "c3f1-17", "tool": "pen", "color": "#1b1b1f", "w": 3.0,
     "p": [x, y, pressure, ...], "n": 42, "dev": "ipad"}

``n`` 是服务端分配的层叠序号，客户端按 ``n`` 升序绘制，撤销「擦除」时
用原始 ``n`` 复原，保证前后关系不会错乱。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional

# 白板的延伸方式，创建时选定、之后不可更改：
#   board —— 四个方向都无限，是一块大白板；
#   note  —— 宽度固定成一页，只向下无限延伸，像笔记本；
#   doc   —— 由一份 PDF / 图片生成，页面自上而下排好，只能在页面上写。
KINDS = ("board", "note", "doc")

BACKGROUNDS = ("blank", "grid", "lines", "dots")
DOC_TYPES = ("pdf", "image")
MAX_DOC_PAGES = 400
TOOLS = ("pen", "marker", "highlighter")

MAX_POINTS_PER_STROKE = 20000
MIN_WIDTH = 0.5
MAX_WIDTH = 96.0

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def new_board_meta(name: str = "", **overrides: Any) -> Dict[str, Any]:
    meta = {
        "id": new_id(),
        "name": name,
        "kind": "board",
        "background": "grid",
        "created": now(),
        "updated": now(),
    }
    meta.update(overrides)
    return sanitize_meta(meta)


def sanitize_doc(raw: Any) -> Optional[Dict[str, Any]]:
    """文档板的附加信息：原件类型、文件名和每页尺寸。"""
    if not isinstance(raw, dict):
        return None
    doc_type = raw.get("type")
    if doc_type not in DOC_TYPES:
        return None
    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        return None
    pages: List[List[float]] = []
    for page in pages_raw[:MAX_DOC_PAGES]:
        if not isinstance(page, (list, tuple)) or len(page) != 2:
            return None
        try:
            width, height = float(page[0]), float(page[1])
        except (TypeError, ValueError):
            return None
        if not (0 < width < 1e6 and 0 < height < 1e6):
            return None
        pages.append([round(width, 2), round(height, 2)])

    name = raw.get("name")
    name = name[:128] if isinstance(name, str) else ""
    ext = raw.get("ext")
    ext = ext[:8].lower() if isinstance(ext, str) else ""
    if ext and not re.match(r"^\.[a-z0-9]{1,7}$", ext):
        ext = ""
    return {"type": doc_type, "name": name, "ext": ext, "pages": pages}


def sanitize_meta(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把（可能来自局域网客户端的）白板元数据收敛到合法范围。

    老版本的文件里可能还带着 cols / rows / unit，直接丢掉即可：画布已经是无限的。
    """
    background = raw.get("background", "grid")
    if background not in BACKGROUNDS:
        background = "grid"

    kind = raw.get("kind", "board")
    if kind not in KINDS:
        kind = "board"

    # 文档板离了原件就没有意义，信息不全时按普通白板处理。
    doc = sanitize_doc(raw.get("doc"))
    if kind == "doc" and doc is None:
        kind = "board"
    if kind != "doc":
        doc = None

    name = raw.get("name", "")
    if not isinstance(name, str):
        name = ""

    board_id = raw.get("id", "")
    if not isinstance(board_id, str) or not _ID_RE.match(board_id):
        board_id = new_id()

    try:
        created = float(raw.get("created", now()))
    except (TypeError, ValueError):
        created = now()
    try:
        updated = float(raw.get("updated", created))
    except (TypeError, ValueError):
        updated = created

    meta = {
        "id": board_id,
        "name": name[:64],
        "kind": kind,
        "background": background,
        "created": created,
        "updated": updated,
    }
    if doc is not None:
        meta["doc"] = doc
    return meta


def sanitize_stroke(raw: Any) -> Optional[Dict[str, Any]]:
    """校验单个笔画，非法数据返回 ``None`` 而不是抛错（一条坏数据不该断开连接）。"""
    if not isinstance(raw, dict):
        return None
    stroke_id = raw.get("id")
    if not isinstance(stroke_id, str) or not _ID_RE.match(stroke_id):
        return None

    points_raw = raw.get("p")
    if not isinstance(points_raw, list) or len(points_raw) < 3:
        return None
    if len(points_raw) % 3 != 0 or len(points_raw) > MAX_POINTS_PER_STROKE * 3:
        return None
    points: List[float] = []
    for value in points_raw:
        if not isinstance(value, (int, float)) or value != value:  # NaN 检查
            return None
        points.append(float(value))

    tool = raw.get("tool", "pen")
    if tool not in TOOLS:
        tool = "pen"
    color = raw.get("color", "#1b1b1f")
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        color = "#1b1b1f"
    try:
        width = clamp(float(raw.get("w", 3.0)), MIN_WIDTH, MAX_WIDTH)
    except (TypeError, ValueError):
        width = 3.0
    device = raw.get("dev", "")
    if not isinstance(device, str):
        device = ""

    stroke = {
        "id": stroke_id,
        "tool": tool,
        "color": color,
        "w": width,
        "p": points,
        "dev": device[:16],
    }
    n = raw.get("n")
    if isinstance(n, int) and 0 <= n < 1 << 40:
        stroke["n"] = n
    return stroke


def sanitize_ids(raw: Any, limit: int = 5000) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for value in raw[:limit]:
        if isinstance(value, str) and _ID_RE.match(value):
            out.append(value)
    return out

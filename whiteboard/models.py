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

# 单屏基准尺寸，取 11 英寸 iPad 横屏的逻辑分辨率。
UNIT_W = 1180
UNIT_H = 820

# 伪无限画布：默认九宫格，即主屏 + 周围 8 个方向各扩展一屏。
DEFAULT_COLS = 3
DEFAULT_ROWS = 3
MIN_GRID = 1
MAX_GRID = 7

BACKGROUNDS = ("blank", "grid", "lines", "dots")
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
        "cols": DEFAULT_COLS,
        "rows": DEFAULT_ROWS,
        "unit": [UNIT_W, UNIT_H],
        "background": "grid",
        "created": now(),
        "updated": now(),
    }
    meta.update(overrides)
    return sanitize_meta(meta)


def sanitize_meta(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把（可能来自局域网客户端的）白板元数据收敛到合法范围。"""
    unit = raw.get("unit") or [UNIT_W, UNIT_H]
    try:
        unit_w = int(clamp(float(unit[0]), 320, 4096))
        unit_h = int(clamp(float(unit[1]), 320, 4096))
    except (TypeError, ValueError, IndexError):
        unit_w, unit_h = UNIT_W, UNIT_H

    def _int(key: str, default: int) -> int:
        try:
            return int(clamp(int(raw.get(key, default)), MIN_GRID, MAX_GRID))
        except (TypeError, ValueError):
            return default

    background = raw.get("background", "grid")
    if background not in BACKGROUNDS:
        background = "grid"

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

    return {
        "id": board_id,
        "name": name[:64],
        "cols": _int("cols", DEFAULT_COLS),
        "rows": _int("rows", DEFAULT_ROWS),
        "unit": [unit_w, unit_h],
        "background": background,
        "created": created,
        "updated": updated,
    }


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


def board_size(meta: Dict[str, Any]) -> tuple[int, int]:
    unit_w, unit_h = meta["unit"]
    return meta["cols"] * unit_w, meta["rows"] * unit_h

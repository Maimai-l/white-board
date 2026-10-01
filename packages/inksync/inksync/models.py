"""白板数据模型与线上数据的校验。

内存 / WebSocket / 磁盘三处共用同一套字段命名，区别只在于落盘时 ``p``
（点数组）会被 :mod:`inksync.codec` 压成 base64 字符串。

笔画（stroke）::

    {"id": "c3f1-17", "tool": "pen", "color": "#1b1b1f", "w": 3.0,
     "p": [x, y, pressure, ...], "n": 42, "dev": "ipad"}

``n`` 是服务端分配的层叠序号，客户端按 ``n`` 升序绘制，撤销「擦除」时
用原始 ``n`` 复原，保证前后关系不会错乱。

白板元数据（文件格式 2，见 docs/design/inksync-interface.zh-CN.md 第 5 节）::

    {"id", "name", "created", "updated",
     "canvas": {"mode": "infinite" | "column" | "fixed", "width", "height"},
     "background": {"pattern": "grid", "paper": "#ffffff"},
     "layers": [{"src", "x", "y", "width", "height"?, "z"?, "sheet"?}],
     "data": {...}}
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

CANVAS_MODES = ("infinite", "column", "fixed")
PATTERNS = ("blank", "grid", "lines", "dots")
TOOLS = ("pen", "marker", "highlighter")

MAX_POINTS_PER_STROKE = 20000
# 遮罩的上限，按**总胶囊段数**算，和前端一个口径。
#
# 以前这里是「最多 64 条链、每条链最多 256 个点」，而前端管的是总段数
# （stroke.js 的 MASK_LIMIT，400 段，超了就抽稀或落实成切分）。两个口径对不上，
# 前端合法的遮罩到这里会被悄悄截断：真机录像里一次擦除攒出 113 条链，截到 64，
# 43% 的擦除就这么没了。截断的结果既广播给对端，也顺着回执盖回发送端自己，
# 所以那边刚擦掉的墨过一会儿自己又回来一部分——看上去像随机，其实是这一刀。
#
# 现在按总段数算，数值放在前端上限之上留出版本差的余量；
# tests/test_models.py 里有用例把两边钉在一起，防止再次跑偏。
MAX_MASK_SEGMENTS = 1024
MIN_WIDTH = 0.5
MAX_WIDTH = 96.0

MAX_NAME = 64
MAX_CANVAS = 100000.0
MAX_LAYERS = 1000
MAX_SRC = 512
MAX_DATA_BYTES = 16 * 1024
# 1.0.x 笔记的页宽，现在是 column 画布的宽度
NOTE_WIDTH = 1000.0
# 1.0.x 文档板的页间距，和 whiteboard/docs.py 的 PAGE_GAP 相同
DOC_PAGE_GAP = 24.0

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# 笔画 id：客户端生成，允许 . 和 :
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# 白板 id：同时是文件名，只允许字母、数字、- 和 _
BOARD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SPACE_RE = re.compile(r"^[a-z0-9-]{0,32}$")
# 1.0.x 固定连接用的名字，保留给白板应用的兼容层
PINNED_ID_RE = BOARD_ID_RE

# 可以通过 meta 操作修改的字段
EDITABLE = ("name", "background", "layers", "data")


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def is_number(value: Any) -> bool:
    """有限的数。布尔值在 Python 里是 int 的子类，要单独排除；NaN 和无穷大
    存盘编码时会出错（codec 要把它们转成整数），发给浏览器时 JSON.parse 也读不了。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_board_id(value: Any) -> bool:
    return isinstance(value, str) and bool(BOARD_ID_RE.match(value))


# ------------------------------------------------------------------ 元数据的各部分


def sanitize_canvas(raw: Any) -> Optional[Dict[str, Any]]:
    """画布；不合法时返回 None（调用方决定是报错还是用默认值）。"""
    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode", "infinite")
    if mode == "infinite":
        return {"mode": "infinite"}

    def size(key: str) -> Optional[float]:
        value = raw.get(key)
        if not is_number(value) or not 1 <= float(value) <= MAX_CANVAS:
            return None
        return round(float(value), 2)

    if mode == "column":
        width = size("width")
        return None if width is None else {"mode": "column", "width": width}
    if mode == "fixed":
        width, height = size("width"), size("height")
        if width is None or height is None:
            return None
        return {"mode": "fixed", "width": width, "height": height}
    return None


def sanitize_background(raw: Any) -> Dict[str, Any]:
    """背景。1.0.x 的字符串形式（"grid"）照样接受。"""
    if isinstance(raw, str):
        raw = {"pattern": raw}
    if not isinstance(raw, dict):
        raw = {}
    pattern = raw.get("pattern", "grid")
    if pattern not in PATTERNS:
        pattern = "grid"
    out: Dict[str, Any] = {"pattern": pattern}
    paper = raw.get("paper")
    if isinstance(paper, str) and _COLOR_RE.match(paper):
        out["paper"] = paper.lower()
    return out


def default_allow_src(src: str) -> bool:
    """同源的绝对路径：以一个 / 开头，不含 ..、反斜杠和协议名。"""
    if not src.startswith("/") or src.startswith("//"):
        return False
    if "\\" in src or ":" in src.split("?", 1)[0]:
        return False
    path = src.split("?", 1)[0]
    return not any(part in (".", "..") for part in path.split("/"))


def sanitize_layer(raw: Any, allow_src: Callable[[str], bool] = default_allow_src) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    src = raw.get("src")
    if not isinstance(src, str) or not src or len(src) > MAX_SRC:
        return None
    if any(ord(ch) < 32 for ch in src) or not allow_src(src):
        return None
    out: Dict[str, Any] = {"src": src}
    for key in ("x", "y"):
        value = raw.get(key, 0)
        if not is_number(value) or abs(float(value)) > MAX_CANVAS:
            return None
        out[key] = round(float(value), 2)
    width = raw.get("width")
    if not is_number(width) or not 1 <= float(width) <= MAX_CANVAS:
        return None
    out["width"] = round(float(width), 2)
    height = raw.get("height")
    if height is not None:
        if not is_number(height) or not 1 <= float(height) <= MAX_CANVAS:
            return None
        out["height"] = round(float(height), 2)
    if raw.get("z") == "above":
        out["z"] = "above"
    if raw.get("sheet") is True:
        out["sheet"] = True
    return out


def sanitize_layers(raw: Any, allow_src: Callable[[str], bool] = default_allow_src) -> Optional[List[Dict[str, Any]]]:
    """图片层；任何一项不合法时整份返回 None，不悄悄丢掉其中几项。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_LAYERS:
        return None
    out: List[Dict[str, Any]] = []
    for item in raw:
        layer = sanitize_layer(item, allow_src)
        if layer is None:
            return None
        out.append(layer)
    return out


def sanitize_data(raw: Any) -> Optional[Dict[str, Any]]:
    """使用者自己的字段：JSON 对象，序列化后不超过 16 KB。不合法时返回 None。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    try:
        text = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if len(text.encode("utf-8")) > MAX_DATA_BYTES:
        return None
    return json.loads(text)


def sanitize_name(raw: Any) -> str:
    return raw.strip()[:MAX_NAME] if isinstance(raw, str) else ""


def merge_data(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """``data`` 按键合并：出现的键替换，值为 None 的键删除，其余不变。"""
    merged = dict(current)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


# ------------------------------------------------------------------ 元数据


def sanitize_meta(raw: Dict[str, Any], allow_src: Callable[[str], bool] = default_allow_src) -> Dict[str, Any]:
    """把磁盘或索引里的元数据收敛到合法范围。不合法的部分换成默认值，不抛错：
    文件里的元数据坏了一项，白板照样要能打开。"""
    if not isinstance(raw, dict):
        raw = {}
    if "canvas" not in raw and ("kind" in raw or "underlay" in raw or isinstance(raw.get("background"), str)):
        raw = convert_v1_meta(raw)

    board_id = raw.get("id")
    if not is_board_id(board_id):
        board_id = new_id()
    try:
        created = float(raw.get("created", now()))
    except (TypeError, ValueError):
        created = now()
    try:
        updated = float(raw.get("updated", created))
    except (TypeError, ValueError):
        updated = created

    meta: Dict[str, Any] = {
        "id": board_id,
        "name": sanitize_name(raw.get("name")),
        "created": created,
        "updated": updated,
        "canvas": sanitize_canvas(raw.get("canvas", {"mode": "infinite"})) or {"mode": "infinite"},
        "background": sanitize_background(raw.get("background")),
        "layers": sanitize_layers(raw.get("layers"), allow_src) or [],
        "data": sanitize_data(raw.get("data")) or {},
    }
    return meta


def new_board_meta(board_id: Optional[str] = None, spec: Optional[Dict[str, Any]] = None,
                   allow_src: Callable[[str], bool] = default_allow_src) -> Dict[str, Any]:
    """按 ``create`` 的内容建一份新元数据；内容不合法时抛 ValueError。"""
    spec = spec or {}
    if not isinstance(spec, dict):
        raise ValueError("create 必须是对象")
    canvas = sanitize_canvas(spec.get("canvas", {"mode": "infinite"}))
    if canvas is None:
        raise ValueError("canvas 不合法")
    layers = sanitize_layers(spec.get("layers"), allow_src)
    if layers is None:
        raise ValueError("layers 不合法")
    data = sanitize_data(spec.get("data"))
    if data is None:
        raise ValueError("data 不合法")
    stamp = now()
    return {
        "id": board_id or new_id(),
        "name": sanitize_name(spec.get("name")),
        "created": stamp,
        "updated": stamp,
        "canvas": canvas,
        "background": sanitize_background(spec.get("background")),
        "layers": layers,
        "data": data,
    }


def apply_meta_patch(meta: Dict[str, Any], patch: Any,
                     allow_src: Callable[[str], bool] = default_allow_src) -> Optional[Dict[str, Any]]:
    """把 meta 操作的 ``patch`` 合进 ``meta``，返回新的元数据；patch 不合法时返回 None。

    只处理可修改的字段（:data:`EDITABLE`），其余键忽略。"""
    if not isinstance(patch, dict):
        return None
    merged = dict(meta)
    if "name" in patch:
        if not isinstance(patch["name"], str):
            return None
        merged["name"] = sanitize_name(patch["name"])
    if "background" in patch:
        merged["background"] = sanitize_background(patch["background"])
    if "layers" in patch:
        layers = sanitize_layers(patch["layers"], allow_src)
        if layers is None:
            return None
        merged["layers"] = layers
    if "data" in patch:
        if not isinstance(patch["data"], dict):
            return None
        data = sanitize_data(merge_data(meta.get("data") or {}, patch["data"]))
        if data is None:
            return None
        merged["data"] = data
    return merged


# ------------------------------------------------------------------ 1.0.x 元数据


def doc_extent(pages: Any, gap: float = DOC_PAGE_GAP) -> Optional[Dict[str, float]]:
    """1.0.x 文档板各页自上而下排列（横向按最宽的一页居中）之后的外框。"""
    if not isinstance(pages, list) or not pages:
        return None
    sizes = []
    for page in pages:
        if not isinstance(page, (list, tuple)) or len(page) != 2 or not all(is_number(v) for v in page):
            return None
        if page[0] <= 0 or page[1] <= 0:
            return None
        sizes.append((float(page[0]), float(page[1])))
    width = max(w for w, _ in sizes)
    height = sum(h for _, h in sizes) + gap * (len(sizes) - 1)
    return {"width": width, "height": height}


def convert_v1_meta(raw: Dict[str, Any]) -> Dict[str, Any]:
    """1.0.x 的元数据换成现在的字段（见 docs/design/inksync-redesign.zh-CN.md 4.6 节）。

    白板应用自己的字段（folder、app、doc）移进 ``data``；文档页的图片层由白板应用
    的 ``convert_meta`` 钩子补上，这里只定画布。
    """
    out: Dict[str, Any] = {
        key: raw[key] for key in ("id", "name", "created", "updated") if key in raw
    }
    kind = raw.get("kind", "board")
    data: Dict[str, Any] = {}
    if kind == "note":
        out["canvas"] = {"mode": "column", "width": NOTE_WIDTH}
    elif kind == "doc" and isinstance(raw.get("doc"), dict):
        extent = doc_extent(raw["doc"].get("pages"))
        out["canvas"] = {"mode": "fixed", **extent} if extent else {"mode": "infinite"}
        data["doc"] = raw["doc"]
    else:
        out["canvas"] = {"mode": "infinite"}
    out["background"] = raw.get("background", "grid")
    underlay = raw.get("underlay")
    if isinstance(underlay, dict) and "src" in underlay:
        out["layers"] = [{"src": underlay.get("src"), "x": 0, "y": 0, "width": underlay.get("width")}]
    for key in ("folder", "app"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            data[key] = value
    if isinstance(raw.get("data"), dict):
        data = {**raw["data"], **data}
    out["data"] = data
    return out


# ------------------------------------------------------------------ 笔画


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
        if not is_number(value):
            return None
        points.append(float(value))

    tool = raw.get("tool", "pen")
    if tool not in TOOLS:
        tool = "pen"
    color = raw.get("color", "#1b1b1f")
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        color = "#1b1b1f"
    try:
        width = float(raw.get("w", 3.0))
    except (TypeError, ValueError):
        width = 3.0
    if math.isnan(width):
        width = 3.0  # NaN 比较全是 False，clamp 会原样放行
    width = clamp(width, MIN_WIDTH, MAX_WIDTH)
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
    mask = sanitize_mask(raw.get("m"))
    if mask:
        stroke["m"] = mask
    # 橡皮切出来的端头：1 = 起点是切口，2 = 终点是切口，画平口而不是圆笔尖
    cut = raw.get("cut")
    if isinstance(cut, int) and not isinstance(cut, bool) and 1 <= cut <= 3:
        stroke["cut"] = cut
    n = raw.get("n")
    if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 1 << 40:
        stroke["n"] = n
    return stroke


def sanitize_mask(raw: Any) -> List[List[float]]:
    """遮罩：``[[半径, x0, y0, x1, y1, ...], ...]``，橡皮啃掉的那几块。

    比笔细的橡皮切不断截面，只能啃；啃出来的形状用胶囊记下来，渲染和导出时
    从轮廓里裁掉。非法数据整条丢掉而不是抛错，和 sanitize_stroke 一个原则。
    """
    if not isinstance(raw, list):
        return []
    out: List[List[float]] = []
    budget = MAX_MASK_SEGMENTS
    for chain in raw:
        if budget <= 0:
            break
        if not isinstance(chain, list) or len(chain) < 5 or len(chain) % 2 == 0:
            continue
        # 一条链 [r, x0, y0, x1, y1, ...]：点数是 (len-1)/2，段数比点数少一个
        values: List[float] = []
        for value in chain[: budget * 2 + 3]:
            if not is_number(value):
                break
            values.append(float(value))
        else:
            if values[0] > 0:
                out.append(values)
                budget -= max(1, (len(values) - 3) // 2)
    return out


def sanitize_ids(raw: Any, limit: int = 5000) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for value in raw[:limit]:
        if isinstance(value, str) and _ID_RE.match(value):
            out.append(value)
    return out

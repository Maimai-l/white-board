"""生成分发给 iPad 的描述文件（Web Clip）与图标。

iPad 在 Safari 里打开 ``/profile.mobileconfig``，安装后主屏会多出一个
图标，点开就是全屏白板（无 Safari 界面），地址指向 Mac 的 ``.local``
主机名，因此 Mac 的局域网 IP 变化也不影响。

图标用纯 Python 生成 PNG，不引入图像库、也不往仓库里塞二进制文件。
"""

from __future__ import annotations

import bisect
import functools
import plistlib
import struct
import uuid
import zlib
from typing import List, Tuple

PROFILE_ID_PREFIX = "local.whiteboard"
DISPLAY_NAME = "白板"


# --------------------------------------------------------------------- 图标

def _png(width: int, height: int, rows: List[bytearray]) -> bytes:
    raw = bytearray()
    for row in rows:
        raw.append(0)  # 每行的 filter type
        raw.extend(row)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _blend(dst: Tuple[int, int, int, int], color: Tuple[int, int, int], alpha: float):
    alpha = max(0.0, min(1.0, alpha))
    return (
        round(dst[0] * (1 - alpha) + color[0] * alpha),
        round(dst[1] * (1 - alpha) + color[1] * alpha),
        round(dst[2] * (1 - alpha) + color[2] * alpha),
        max(dst[3], round(255 * alpha)),
    )


@functools.lru_cache(maxsize=4)
def icon_png(size: int = 180) -> bytes:
    """画一个 Material 风格的圆角方块 + 一笔两端收笔的手写曲线。"""
    import math

    radius = size * 0.23
    # 曲线采样：一条带压感收笔的波浪线。
    steps = 200
    curve = []
    for i in range(steps + 1):
        t = i / steps
        x = (0.17 + 0.66 * t) * size
        y = (0.52 - 0.17 * math.sin(2 * math.pi * t)) * size
        taper = math.sin(math.pi * min(1.0, max(0.0, t))) ** 0.45
        half = size * 0.052 * (0.35 + 0.65 * taper)
        curve.append((x, y, half))

    curve_x = [point[0] for point in curve]
    max_half = max(point[2] for point in curve)

    rows: List[bytearray] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            # 圆角矩形的有向距离，用来做抗锯齿边缘
            dx = max(radius - px, px - (size - 1 - radius), 0.0)
            dy = max(radius - py, py - (size - 1 - radius), 0.0)
            corner = math.hypot(dx, dy) - radius
            inside = 1.0 - max(0.0, min(1.0, corner + 0.5))
            if inside <= 0:
                row.extend((0, 0, 0, 0))
                continue

            # 竖向渐变，取 Material 3 的主色调
            t = py / (size - 1)
            base = (round(66 + 30 * t), round(97 + 20 * t), round(183 - 20 * t))
            pixel = _blend((0, 0, 0, 0), base, inside)

            # 笔画：到曲线的最近「带宽距离」。曲线按 x 单调，只看附近那一段，
            # 否则 1024 像素的图标要跑上几分钟。
            lo = bisect.bisect_left(curve_x, px - max_half - 1)
            hi = bisect.bisect_right(curve_x, px + max_half + 1)
            best = 1e9
            for index in range(lo, hi):
                cx, cy, half = curve[index]
                d = math.hypot(cx - px, cy - py) - half
                if d < best:
                    best = d
            ink = max(0.0, min(1.0, 0.5 - best))
            if ink > 0:
                pixel = _blend(pixel, (255, 255, 255), ink * inside)
            row.extend(pixel)
        rows.append(row)
    return _png(size, size, rows)


# ------------------------------------------------------------- 描述文件

def build_profile(url: str, label: str = DISPLAY_NAME, icon: bytes | None = None) -> bytes:
    """生成 ``.mobileconfig``（未签名，安装时 iPadOS 会提示「未签名」，属正常）。"""
    if icon is None:
        icon = icon_png()
    # 同一地址生成稳定的 UUID，重装描述文件会覆盖而不是留下两个图标。
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, url)
    payload = {
        "PayloadType": "com.apple.webClip.managed",
        "PayloadIdentifier": f"{PROFILE_ID_PREFIX}.webclip",
        "PayloadUUID": str(uuid.uuid5(namespace, "webclip")),
        "PayloadVersion": 1,
        "PayloadDisplayName": label,
        "URL": url,
        "Label": label,
        "Icon": icon,
        "IsRemovable": True,
        "FullScreen": True,
        "Precomposed": True,
        "IgnoreManifestScope": True,
    }
    profile = {
        "PayloadType": "Configuration",
        "PayloadIdentifier": PROFILE_ID_PREFIX,
        "PayloadUUID": str(uuid.uuid5(namespace, "profile")),
        "PayloadVersion": 1,
        "PayloadDisplayName": f"{label} · {url}",
        "PayloadDescription": f"在主屏添加白板图标，指向 {url}",
        "PayloadOrganization": "Whiteboard",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [payload],
    }
    return plistlib.dumps(profile)

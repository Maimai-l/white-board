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


# Apple 的 macOS 图标模板（Big Sur 起）：1024 画布里只画 824 的圆角方块，
# 四周各留 100 像素透明边，圆角半径 185.4。不留边的话在程序坞里会比别的图标大一圈。
MAC_TILE_RATIO = 824 / 1024
MAC_RADIUS_RATIO = 185.4 / 824
IOS_RADIUS_RATIO = 0.23


@functools.lru_cache(maxsize=16)
def icon_png(size: int = 180, mac: bool = False) -> bytes:
    """画图标。

    ``mac=True`` 按 Apple 的 macOS 模板留出透明边并带投影，用于 .icns；
    ``mac=False`` 是铺满整张画布的圆角方块，用于 iPad 主屏图标（系统自己会裁）。
    """
    import math

    tile = size * MAC_TILE_RATIO if mac else float(size)
    margin = (size - tile) / 2
    radius = tile * (MAC_RADIUS_RATIO if mac else IOS_RADIUS_RATIO)
    def tile_coverage(px: float, py: float) -> float:
        """圆角方块的覆盖度（带一像素的抗锯齿过渡）。"""
        left = margin
        top = margin
        right = margin + tile - 1
        bottom = margin + tile - 1
        dx_out = max(left + radius - px, px - (right - radius), 0.0)
        dy_out = max(top + radius - py, py - (bottom - radius), 0.0)
        distance = math.hypot(dx_out, dy_out) - radius
        return 1.0 - max(0.0, min(1.0, distance + 0.5))

    # 曲线采样：一条带压感收笔的波浪线，按方块本身定位
    steps = 200
    curve = []
    for i in range(steps + 1):
        t = i / steps
        x = margin + (0.17 + 0.66 * t) * tile
        y = margin + (0.52 - 0.17 * math.sin(2 * math.pi * t)) * tile
        taper = math.sin(math.pi * min(1.0, max(0.0, t))) ** 0.45
        half = tile * 0.052 * (0.35 + 0.65 * taper)
        curve.append((x, y, half))
    curve_x = [point[0] for point in curve]
    max_half = max(point[2] for point in curve)

    rows: List[bytearray] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            inside = tile_coverage(px, py)
            if inside <= 0:
                row.extend((0, 0, 0, 0))
                continue
            pixel = (0, 0, 0, 0)

            if inside > 0:
                # 竖向渐变，取 Material 3 的主色调
                t = (py - margin) / max(1.0, tile - 1)
                t = min(1.0, max(0.0, t))
                base = (round(66 + 30 * t), round(97 + 20 * t), round(183 - 20 * t))
                pixel = _blend(pixel, base, inside)

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

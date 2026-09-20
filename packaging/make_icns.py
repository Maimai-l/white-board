#!/usr/bin/env python3
"""用纯 Python 画出图标并打成 .icns（需要 macOS 的 iconutil）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whiteboard.profile import icon_png  # noqa: E402

# iconutil 要求的一整套尺寸
ENTRIES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "whiteboard.icns").resolve()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "whiteboard.iconset"
        iconset.mkdir()
        cache: dict[int, bytes] = {}
        for name, size in ENTRIES:
            if size not in cache:
                cache[size] = icon_png(size)
            (iconset / name).write_bytes(cache[size])
        try:
            result = subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=False
            )
        except FileNotFoundError:
            print("找不到 iconutil：只有 macOS 能生成 .icns", file=sys.stderr)
            return 1
        if result.returncode != 0:
            print("iconutil 执行失败", file=sys.stderr)
            return result.returncode
    print(f"已生成 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

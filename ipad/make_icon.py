#!/usr/bin/env python3
"""生成外壳的图标：和 iPad 主屏上 Web Clip 的图标是同一张（whiteboard/profile.py）。

仓库里不放二进制文件，构建前跑一次：python3 ipad/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whiteboard import profile  # noqa: E402

OUT = ROOT / "ipad" / "Whiteboard" / "Assets.xcassets" / "AppIcon.appiconset" / "icon.png"


def main() -> int:
    OUT.write_bytes(profile.icon_png(1024))
    print(f"已生成 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

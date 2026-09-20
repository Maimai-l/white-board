#!/usr/bin/env python3
"""把版本号写进 whiteboard/__init__.py（打包前由 CI 调用）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "whiteboard" / "__init__.py"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：set_version.py <版本号>", file=sys.stderr)
        return 2
    version = argv[1].lstrip("vV")
    text = INIT.read_text("utf-8")
    updated, count = re.subn(
        r'^__version__ = ".*"$', f'__version__ = "{version}"', text, flags=re.M
    )
    if not count:
        print("没找到 __version__", file=sys.stderr)
        return 1
    INIT.write_text(updated, "utf-8")
    print(f"版本号已设为 {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

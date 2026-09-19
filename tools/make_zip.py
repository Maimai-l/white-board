#!/usr/bin/env python3
"""打一个可以直接解压使用的 zip（保留 .command 的可执行权限）。

    python tools/make_zip.py [输出路径]
"""

from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = "white-board"

INCLUDE_FILES = [
    "run.py",
    "requirements.txt",
    "README.md",
    "使用说明.txt",
    "启动白板.command",
    "浏览器模式.command",
]
INCLUDE_DIRS = ["whiteboard", "docs", "tests"]
SKIP_PARTS = {"__pycache__", ".git", ".venv", "data", ".pytest_cache"}


def iter_files():
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.exists():
            yield path
    for name in INCLUDE_DIRS:
        for path in sorted((ROOT / name).rglob("*")):
            if path.is_file() and not SKIP_PARTS & set(path.parts):
                yield path


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "dist" / "white-board-mac.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in iter_files():
            arcname = f"{TOP}/{path.relative_to(ROOT).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
            executable = path.suffix == ".command"
            mode = 0o755 if executable else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    print(f"{out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

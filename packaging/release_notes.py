#!/usr/bin/env python3
"""从 CHANGELOG.md 里取出某个版本的那一节，写到 stdout（发 Release 时用）。

    python packaging/release_notes.py 1.0.0-rc.1

预发布版（带连字符）找不到自己那一节时用正式版那一节。找不到就返回 1，
由调用方退回 GitHub 自动生成的说明。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(text: str, version: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^##\s+v?{re.escape(version)}\s*$", line):
            body = []
            for rest in lines[i + 1:]:
                if re.match(r"^##\s", rest):
                    break
                body.append(rest)
            return "\n".join(body).strip() + "\n"
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：release_notes.py <版本号>", file=sys.stderr)
        return 2
    version = argv[1].lstrip("vV")
    text = CHANGELOG.read_text("utf-8") if CHANGELOG.exists() else ""
    notes = section(text, version) or section(text, version.split("-", 1)[0])
    if not notes or not notes.strip():
        print(f"CHANGELOG.md 里没有 {version} 这一节", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

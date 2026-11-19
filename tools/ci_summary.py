#!/usr/bin/env python3
"""把 pytest 的 JUnit XML 写成运行摘要：通过多少、哪些失败、失败的第一行原因。

    python tools/ci_summary.py "端到端（Chromium）" reports/e2e.xml >> "$GITHUB_STEP_SUMMARY"

没有报告文件（比如装依赖那一步就失败了）时也写一行说明，而不是让摘要空着。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Sequence


def summarize(title: str, paths: Sequence[str]) -> str:
    total = failed = skipped = 0
    failures: List[str] = []
    missing = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            missing.append(raw)
            continue
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            total += 1
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is not None:
                failed += 1
                name = f"{case.get('classname', '')}::{case.get('name', '')}"
                reason = (problem.get("message") or problem.text or "").strip().splitlines()
                failures.append(f"- `{name}` — {reason[0][:200] if reason else '（没有说明）'}")
            elif case.find("skipped") is not None:
                skipped += 1

    lines = [f"### {title}", ""]
    if missing and not total:
        lines.append(f"没有测试报告（{', '.join(missing)}）：测试之前的步骤失败了，看这个 job 的日志。")
        return "\n".join(lines) + "\n"
    passed = total - failed - skipped
    lines.append(f"{'❌' if failed else '✅'} 通过 {passed}，失败 {failed}，跳过 {skipped}")
    if failures:
        lines += ["", *failures[:30]]
        if len(failures) > 30:
            lines.append(f"- ……另外 {len(failures) - 30} 个")
        lines += ["", "失败时打开的页面截图在这次运行的产物里（test-results-*）。"]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(summarize(argv[1], argv[2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

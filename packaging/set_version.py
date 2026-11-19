#!/usr/bin/env python3
"""把版本号、构建号和更新通道写进 whiteboard/__init__.py（打包前由 CI 调用）。

    set_version.py 1.3.0-alpha.20261119.153012 --build 20261119.153012 --channel alpha

版本号的规则见 docs/design/ci-and-updates.zh-CN.md 第 3.2 节。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "whiteboard" / "__init__.py"


def apply(text: str, version: str, build: str | None = None, channel: str | None = None) -> str:
    values = {"__version__": version.lstrip("vV")}
    if build is not None:
        values["__build__"] = build
    if channel is not None:
        values["__channel__"] = channel
    for name, value in values.items():
        text, count = re.subn(rf'^{name} = ".*"$', f'{name} = "{value}"', text, flags=re.M)
        if not count:
            raise ValueError(f"没找到 {name}")
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="写入版本号")
    parser.add_argument("version")
    parser.add_argument("--build")
    parser.add_argument("--channel", choices=["alpha", "stable"])
    args = parser.parse_args(argv[1:])
    try:
        INIT.write_text(apply(INIT.read_text("utf-8"), args.version, args.build, args.channel), "utf-8")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"版本号 {args.version}，构建 {args.build or '（不变）'}，通道 {args.channel or '（不变）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

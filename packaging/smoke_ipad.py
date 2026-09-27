#!/usr/bin/env python3
"""冒烟测试：打包后的应用带着同版本的 iPad 外壳，安装页和下载接口都能用。

用法：smoke_ipad.py http://127.0.0.1:8877 packaging/ipad/Whiteboard.ipa
"""

from __future__ import annotations

import json
import sys
import urllib.request


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def main(argv: list[str]) -> int:
    base, ipa = argv[1].rstrip("/"), argv[2]
    info = json.loads(fetch(f"{base}/ipad/version"))
    print("外壳版本信息：", info)
    if info.get("ipa") is not True:
        print("应用里没有 IPA", file=sys.stderr)
        return 1
    with open(ipa, "rb") as handle:
        expected = handle.read()
    if fetch(f"{base}/ipad/Whiteboard.ipa") != expected:
        print("下载到的 IPA 和构建出来的不是同一个", file=sys.stderr)
        return 1
    page = fetch(f"{base}/ipad").decode("utf-8")
    if "apple-magnifier://install?url=" not in page or "whiteboard-shell://connect?" not in page:
        print("安装页缺按钮", file=sys.stderr)
        return 1
    print("iPad 外壳可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

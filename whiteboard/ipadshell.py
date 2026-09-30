"""iPad 外壳的分发：安装页、版本信息、IPA 下载（docs/ipad-shell.md 第 8 节）。

外壳是一个装着 WKWebView 的原生应用，通过 TrollStore 安装未签名的 IPA。每个版本
的 Mac 应用都自带同版本的 IPA（CI 构建时放进资源目录），iPad 从这台 Mac 上安装和
更新，不需要连外网。

这里的三个接口和 ``/profile.mobileconfig`` 一样不需要任何权限：iPad 在安装外壳
之前什么权限都没有，而这几样东西本来就是给它的。
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__, resources

# 网页认得的接口版本范围，必须与 inksync 前端 input.js（packages/inksync/inksync/web）的 SHELL_BRIDGE 一致
# （tests/test_ipadshell.py 会核对）。外壳据此判断自己要不要提示更新。
BRIDGE = (1, 1)

URL_SCHEME = "whiteboard-shell"
IPA_NAME = "Whiteboard.ipa"
# TrollStore 1.3 起提供的 URL 安装接口，需要在 TrollStore 的设置里打开 URL Scheme
TROLLSTORE_INSTALL = "apple-magnifier://install?url="


def ipa_path() -> Optional[Path]:
    """Mac 应用自带的 IPA；从源码运行时没有。"""
    path = resources.resource_root() / "ipad" / IPA_NAME
    return path if path.is_file() else None


def version_info() -> Dict[str, Any]:
    return {"version": __version__, "ipa": ipa_path() is not None, "bridge": list(BRIDGE)}


def base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def install_url(host: str, port: int) -> str:
    return TROLLSTORE_INSTALL + f"{base_url(host, port)}/ipad/{IPA_NAME}"


def connect_url(host: str, port: int) -> str:
    return f"{URL_SCHEME}://connect?host={host}&port={port}"


def install_page(host: str, port: int, releases: str) -> str:
    """``GET /ipad``：两个按钮，「安装白板外壳」和「打开外壳」。

    全程不需要输入文字：安装交给 TrollStore，「打开外壳」把这台 Mac 的地址直接交给
    外壳，路由器屏蔽 Bonjour 时就靠它。
    """
    esc = lambda text: html.escape(str(text), quote=True)  # noqa: E731
    if ipa_path() is not None:
        install = (
            f'<a class="button primary" href="{esc(install_url(host, port))}">安装白板外壳</a>'
            '<p class="note">由 TrollStore 安装。点了没有反应的话，先在 TrollStore 的设置里'
            "打开 URL Scheme。</p>"
        )
    else:
        install = (
            '<p class="note">这个版本没有附带外壳，请从 Release 下载：'
            f'<a href="{esc(releases)}">{esc(releases)}</a></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>白板外壳</title>
<style>
  :root {{ --bg: #f2f2f7; --card: #fff; --text: #1c1c1e; --muted: #6e6e73; --primary: #0a66ff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1c1c1e; --card: #2c2c2e; --text: #f2f2f7; --muted: #aeaeb2; --primary: #409cff; }}
  }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 17px/1.5 -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }}
  main {{ max-width: 520px; margin: 0 auto; padding: 48px 16px; }}
  h1 {{ font-size: 28px; margin: 0 0 8px; }}
  section {{ background: var(--card); border-radius: 14px; padding: 20px; margin-top: 20px; }}
  .button {{ display: block; text-align: center; padding: 14px; border-radius: 12px;
            text-decoration: none; font-weight: 600; color: var(--primary);
            border: 1px solid var(--primary); }}
  .button.primary {{ background: var(--primary); color: #fff; }}
  .note {{ color: var(--muted); font-size: 15px; margin: 10px 0 0; word-break: break-all; }}
  .note a {{ color: var(--primary); }}
</style>
</head>
<body>
<main>
  <h1>白板外壳</h1>
  <p class="note">版本 {esc(__version__)} · {esc(host)}:{esc(port)}</p>
  <section>{install}</section>
  <section>
    <a class="button" href="{esc(connect_url(host, port))}">打开外壳</a>
    <p class="note">已经装好外壳时点这里，外壳会记住这台 Mac 并直接进入白板。</p>
  </section>
</main>
</body>
</html>
"""

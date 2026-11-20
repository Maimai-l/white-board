#!/usr/bin/env python3
"""发布 Sparkle 更新时 generate_appcast 前后的几步（docs/design/ci-and-updates.zh-CN.md 第 3.3、3.4 节）。

    appcast.py fetch  <站点> <目录>          取线上的 appcast.xml 和它列出的完整包
    appcast.py notes  <目录> <包名> <文件>   把更新说明放到包旁边（generate_appcast 会嵌进 appcast）
    appcast.py prune  <目录>                删掉 appcast 不再引用的文件；去掉文件已不在的增量包条目
    appcast.py index  <目录>                写一个列出各通道最新版本下载链接的 index.html
    appcast.py keygen <目录>                演练用：生成一对临时 EdDSA 密钥（private.key / public.key）
    appcast.py verify <目录> <公钥>          核对 appcast 里每个包和增量包的 EdDSA 签名
    appcast.py treediff <甲> <乙>           演练用：两个应用包逐项比较（不跟随符号链接）

站点上的文件名只接受字母、数字、点、- 和 _（不以点开头），其余一律跳过：文件名来自网络。
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
NS = {"sparkle": SPARKLE_NS}
KEEP = {"appcast.xml", "index.html", ".nojekyll"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")  # 不以点开头：排除 . 和 ..


@dataclass
class Enclosure:
    url: str
    signature: str
    length: int


@dataclass
class Item:
    version: str
    display: str
    channel: str
    archive: Optional[Enclosure]
    deltas: List[Enclosure] = field(default_factory=list)


def _enclosure(node: ET.Element) -> Enclosure:
    return Enclosure(
        url=node.get("url", ""),
        signature=node.get(f"{{{SPARKLE_NS}}}edSignature", ""),
        length=int(node.get("length", "0") or 0),
    )


def parse(text: str) -> List[Item]:
    root = ET.fromstring(text)
    items = []
    for node in root.iter("item"):
        enclosure = node.find("enclosure")
        version = node.findtext("sparkle:version", default="", namespaces=NS)
        if not version and enclosure is not None:
            version = enclosure.get(f"{{{SPARKLE_NS}}}version", "")
        display = node.findtext("sparkle:shortVersionString", default="", namespaces=NS)
        if not display and enclosure is not None:
            display = enclosure.get(f"{{{SPARKLE_NS}}}shortVersionString", "")
        deltas = [_enclosure(delta) for delta in node.findall("sparkle:deltas/enclosure", NS)]
        items.append(
            Item(
                version=version,
                display=display or version,
                channel=node.findtext("sparkle:channel", default="", namespaces=NS) or "stable",
                archive=_enclosure(enclosure) if enclosure is not None else None,
                deltas=deltas,
            )
        )
    return items


def file_name(url: str) -> Optional[str]:
    path = urllib.parse.urlparse(url).path
    if path.endswith("/"):
        return None
    name = Path(path).name
    return name if SAFE_NAME.match(name or "") else None


def referenced(items: Sequence[Item]) -> set:
    names = set()
    for item in items:
        for enclosure in ([item.archive] if item.archive else []) + item.deltas:
            name = file_name(enclosure.url)
            if name:
                names.add(name)
    return names


# ---------------------------------------------------------------- 子命令


def _get(url: str, timeout: float = 60.0) -> Optional[bytes]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "whiteboard-ci"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def fetch(site: str, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    site = site.rstrip("/")
    # 带时间戳绕过 Pages 的 CDN 缓存：读到旧的 appcast 会把上一次发布的条目弄丢
    data = _get(f"{site}/appcast.xml?t={int(time.time())}")
    if data is None:
        print("线上还没有 appcast.xml：这是第一次发布")
        return 0
    (folder / "appcast.xml").write_bytes(data)
    items = parse(data.decode("utf-8"))
    print(f"线上的 appcast 有 {len(items)} 个版本")
    # 完整包和增量包都要取：generate_appcast 会保留旧条目里的增量包，文件不在就发布不了
    for item in items:
        enclosures = ([item.archive] if item.archive else []) + item.deltas
        for enclosure in enclosures:
            name = file_name(enclosure.url)
            if name is None:
                print(f"跳过文件名不合规的文件：{enclosure.url}")
                continue
            target = folder / name
            if target.exists():
                continue
            body = _get(enclosure.url, timeout=300)
            if body is None:
                # 完整包丢了，这个版本没法做增量包，generate_appcast 会把它从 appcast 里去掉；
                # 增量包丢了，verify 会报出来
                print(f"::warning::{enclosure.url} 不存在")
                continue
            target.write_bytes(body)
            print(f"取回 {name}（{len(body) / 1e6:.1f} MB，{item.channel} {item.display}）")
    return 0


def notes(folder: Path, archive: str, source: Path) -> int:
    stem = archive[: -len(".zip")] if archive.endswith(".zip") else archive
    text = source.read_text("utf-8").strip()
    (folder / f"{stem}.md").write_text((text or "- 没有更新说明。") + "\n", "utf-8")
    return 0


# generate_appcast 写出的增量包条目：一行一个自闭合的 <enclosure>，带 sparkle:deltaFrom
_DELTA_ENTRY = re.compile(r'\n?[ \t]*<enclosure\b[^>]*\burl="([^"]+)"[^>]*\bsparkle:deltaFrom="[^"]*"[^>]*/>')
_EMPTY_DELTAS = re.compile(r"\n?[ \t]*<sparkle:deltas>\s*</sparkle:deltas>")


def drop_missing_deltas(text: str, folder: Path) -> str:
    """去掉文件已经不在目录里的增量包条目。

    generate_appcast 会删掉从已不保留的版本（超出 --maximum-versions）出发的增量包文件，
    但更早条目里列着的这些增量包还留在 appcast 里，发布出去就是指向不存在的文件
    （2026-11-20 dev 上的 updates 因此失败）。客户端在这些版本上时照样能用完整包更新。
    完整包缺了不在这里处理：那是真问题，交给 verify 报出来。
    """

    def keep(match: "re.Match[str]") -> str:
        name = file_name(match.group(1))
        if name and (folder / name).exists():
            return match.group(0)
        print(f"去掉文件已不在的增量包条目：{match.group(1)}")
        return ""

    return _EMPTY_DELTAS.sub("", _DELTA_ENTRY.sub(keep, text))


def prune(folder: Path) -> int:
    appcast = folder / "appcast.xml"
    if not appcast.exists():
        print("没有 appcast.xml，不清理", file=sys.stderr)
        return 1
    text = appcast.read_text("utf-8")
    tidy = drop_missing_deltas(text, folder)
    if tidy != text:
        appcast.write_text(tidy, "utf-8")
    keep = referenced(parse(appcast.read_text("utf-8"))) | KEEP
    for path in sorted(folder.iterdir()):
        if path.name in keep:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"删除 {path.name}")
    return 0


def index(folder: Path) -> int:
    items = parse((folder / "appcast.xml").read_text("utf-8"))
    latest: Dict[str, Item] = {}
    for item in items:
        key = tuple(int(part) for part in re.findall(r"\d+", item.version))
        best = latest.get(item.channel)
        if best is None or key > tuple(int(part) for part in re.findall(r"\d+", best.version)):
            latest[item.channel] = item
    rows = []
    for channel in ("stable", "alpha"):
        item = latest.get(channel)
        if item and item.archive:
            name = html.escape(file_name(item.archive.url) or "")
            rows.append(
                f'<li>{channel}：<a href="{name}">{html.escape(item.display)}</a>'
                f"（{item.archive.length / 1e6:.0f} MB）</li>"
            )
    page = (
        '<!doctype html><meta charset="utf-8"><title>白板更新</title>'
        "<h1>白板</h1><p>已安装的应用会自动更新。第一次安装从这里下载：</p>"
        f"<ul>{''.join(rows) or '<li>还没有发布</li>'}</ul>"
        '<p><a href="appcast.xml">appcast.xml</a></p>\n'
    )
    (folder / "index.html").write_text(page, "utf-8")
    (folder / ".nojekyll").write_text("", "utf-8")
    return 0


def keygen(folder: Path) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    folder.mkdir(parents=True, exist_ok=True)
    seed = os.urandom(32)  # Sparkle 新格式的私钥就是 32 字节种子的 base64（common_cli/Secret.swift）
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    (folder / "private.key").write_text(base64.b64encode(seed).decode() + "\n")
    (folder / "public.key").write_text(base64.b64encode(public).decode() + "\n")
    return 0


def verify(folder: Path, public_key: str) -> int:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key.strip()))
    items = parse((folder / "appcast.xml").read_text("utf-8"))
    checked = problems = 0
    for item in items:
        for enclosure in ([item.archive] if item.archive else []) + item.deltas:
            name = file_name(enclosure.url)
            path = folder / name if name else None
            if path is None or not path.exists():
                print(f"缺文件：{enclosure.url}")
                problems += 1
                continue
            data = path.read_bytes()
            if enclosure.length and enclosure.length != len(data):
                print(f"{name}：长度 {len(data)}，appcast 里写的是 {enclosure.length}")
                problems += 1
            if not enclosure.signature:
                # generate_appcast 不给 Info.plist 里没有 SUPublicEDKey（或公钥和私钥不配对）的包签名
                print(f"{name}：没有 EdDSA 签名（应用的 SUPublicEDKey 缺失，或与签名用的私钥不配对）")
                problems += 1
                continue
            try:
                key.verify(base64.b64decode(enclosure.signature), data)
                checked += 1
            except (InvalidSignature, ValueError):
                print(f"{name}：EdDSA 签名不对")
                problems += 1
    deltas = sum(len(item.deltas) for item in items)
    print(f"核对了 {checked} 个文件（其中增量包 {deltas} 个），问题 {problems} 个")
    return 1 if problems or not checked else 0


def _tree(root: Path) -> Dict[str, str]:
    """目录树里每一项的样子：符号链接记指向，文件记可执行位和 SHA-256，目录只记存在。不跟随链接。"""
    entries: Dict[str, str] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirs + files:
            path = base / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries[rel] = f"link -> {os.readlink(path)}"
            elif path.is_dir():
                entries[rel] = "dir"
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                mode = "x" if os.access(path, os.X_OK) else "-"
                entries[rel] = f"file {mode} {digest}"
    return entries


def treediff(a: Path, b: Path) -> int:
    """增量包应用之后和新版本逐项比较。不用 diff -r：macOS 26 的 diff 遇到指向包内的目录链接
    报「Directory loop detected」后跳过，退出码却是 0，应用包里大半内容没比到。"""
    left, right = _tree(a), _tree(b)
    problems = []
    for rel in sorted(set(left) | set(right)):
        if left.get(rel) != right.get(rel):
            problems.append(f"{rel}：{left.get(rel, '没有')} ≠ {right.get(rel, '没有')}")
    for line in problems[:40]:
        print(line)
    if problems:
        print(f"不一致 {len(problems)} 项（共 {len(left)} 项）")
        return 1
    print(f"逐项一致：{len(left)} 项")
    return 0


def main(argv: Sequence[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[1], argv[2:]
    if command == "fetch" and len(args) == 2:
        return fetch(args[0], Path(args[1]))
    if command == "notes" and len(args) == 3:
        return notes(Path(args[0]), args[1], Path(args[2]))
    if command == "prune" and len(args) == 1:
        return prune(Path(args[0]))
    if command == "index" and len(args) == 1:
        return index(Path(args[0]))
    if command == "keygen" and len(args) == 1:
        return keygen(Path(args[0]))
    if command == "treediff" and len(args) == 2:
        return treediff(Path(args[0]), Path(args[1]))
    if command == "verify" and len(args) == 2:
        return verify(Path(args[0]), Path(args[1]).read_text() if Path(args[1]).exists() else args[1])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

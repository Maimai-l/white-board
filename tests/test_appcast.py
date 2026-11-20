"""packaging/appcast.py：发布 Sparkle 更新前后的整理（docs/design/ci-and-updates.zh-CN.md 第 3.3 节）。"""

import base64
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("appcast", ROOT / "packaging" / "appcast.py")
appcast = importlib.util.module_from_spec(spec)
sys.modules["appcast"] = appcast  # dataclass 要在 sys.modules 里找到所在的模块
spec.loader.exec_module(appcast)

SITE = "https://example.github.io/white-board"

# generate_appcast 写出来的样子：alpha 条目带 channel，最新的带增量包
FEED = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Whiteboard</title>
    <item>
      <title>1.3.0-alpha.20261120.090000</title>
      <sparkle:version>20261120.090000</sparkle:version>
      <sparkle:shortVersionString>1.3.0-alpha.20261120.090000</sparkle:shortVersionString>
      <sparkle:channel>alpha</sparkle:channel>
      <enclosure url="{SITE}/Whiteboard-1.3.0-alpha.20261120.090000.zip" length="3" type="application/octet-stream" sparkle:edSignature="SIG1"/>
      <sparkle:deltas>
        <enclosure url="{SITE}/Whiteboard20261120.090000-20261119.153012.delta" sparkle:deltaFrom="20261119.153012" length="2" type="application/octet-stream" sparkle:edSignature="SIG2"/>
      </sparkle:deltas>
    </item>
    <item>
      <title>1.2.0</title>
      <sparkle:version>20261119.153012</sparkle:version>
      <sparkle:shortVersionString>1.2.0</sparkle:shortVersionString>
      <enclosure url="{SITE}/Whiteboard-1.2.0.zip" length="3" type="application/octet-stream" sparkle:edSignature="SIG3"/>
    </item>
  </channel>
</rss>
"""


def test_parse_reads_versions_channels_and_deltas():
    alpha, stable = appcast.parse(FEED)
    assert alpha.version == "20261120.090000" and alpha.channel == "alpha"
    assert alpha.display == "1.3.0-alpha.20261120.090000"
    assert len(alpha.deltas) == 1 and alpha.deltas[0].signature == "SIG2"
    assert stable.channel == "stable" and stable.deltas == []
    assert stable.archive.url.endswith("Whiteboard-1.2.0.zip")


def test_file_names_from_the_network_are_checked():
    assert appcast.file_name(f"{SITE}/Whiteboard-1.2.0.zip") == "Whiteboard-1.2.0.zip"
    assert appcast.file_name(f"{SITE}/a%20b.zip") is None
    assert appcast.file_name(f"{SITE}/..") is None
    assert appcast.file_name(f"{SITE}/") is None


def test_prune_keeps_only_what_the_appcast_names(tmp_path):
    (tmp_path / "appcast.xml").write_text(FEED)
    keep = [
        "Whiteboard-1.3.0-alpha.20261120.090000.zip",
        "Whiteboard20261120.090000-20261119.153012.delta",
        "Whiteboard-1.2.0.zip",
        "index.html",
    ]
    for name in keep + ["Whiteboard-1.1.0.zip", "Whiteboard-1.3.0-alpha.20261120.090000.md"]:
        (tmp_path / name).write_text("x")
    (tmp_path / "old_updates").mkdir()
    (tmp_path / "old_updates" / "Whiteboard-1.0.0.zip").write_text("x")

    assert appcast.prune(tmp_path) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(keep + ["appcast.xml"])


def test_index_links_the_newest_build_of_each_channel(tmp_path):
    (tmp_path / "appcast.xml").write_text(FEED)
    appcast.index(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert 'href="Whiteboard-1.2.0.zip"' in page
    assert 'href="Whiteboard-1.3.0-alpha.20261120.090000.zip"' in page
    assert (tmp_path / ".nojekyll").exists()


def test_notes_sit_next_to_the_archive(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("- Fix the eraser\n")
    appcast.notes(tmp_path, "Whiteboard-1.3.0-alpha.1.zip", source)
    assert (tmp_path / "Whiteboard-1.3.0-alpha.1.md").read_text() == "- Fix the eraser\n"


def test_signatures_round_trip_with_a_generated_key(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    appcast.keygen(tmp_path / "keys")
    seed = base64.b64decode((tmp_path / "keys" / "private.key").read_text())
    assert len(seed) == 32  # generate_appcast 认的新格式：32 字节种子
    key = Ed25519PrivateKey.from_private_bytes(seed)

    archive = tmp_path / "Whiteboard-1.2.0.zip"
    archive.write_bytes(b"zip")
    signature = base64.b64encode(key.sign(b"zip")).decode()
    feed = FEED.split("<item>")[0] + "<item>" + FEED.split("<item>")[2].replace("SIG3", signature)
    (tmp_path / "appcast.xml").write_text(feed)
    public = (tmp_path / "keys" / "public.key").read_text()
    assert appcast.verify(tmp_path, public) == 0

    archive.write_bytes(b"zap")  # 内容被换掉：签名对不上
    assert appcast.verify(tmp_path, public) == 1


def test_a_missing_signature_is_reported_as_missing(tmp_path, capsys):
    """generate_appcast 遇到没有 SUPublicEDKey 的包时不签名：要说清是没签，而不是签错。"""
    pytest.importorskip("cryptography")
    appcast.keygen(tmp_path / "keys")
    (tmp_path / "Whiteboard-1.2.0.zip").write_bytes(b"zip")
    feed = FEED.split("<item>")[0] + "<item>" + FEED.split("<item>")[2].replace(' sparkle:edSignature="SIG3"', "")
    (tmp_path / "appcast.xml").write_text(feed)
    assert appcast.verify(tmp_path, (tmp_path / "keys" / "public.key").read_text()) == 1
    assert "没有 EdDSA 签名" in capsys.readouterr().out


def _bundle(root):
    """一个带符号链接目录的小应用包：Resources/lib 指回 Frameworks/lib，和 PyInstaller 打的包一样。"""
    (root / "Contents" / "Frameworks" / "lib").mkdir(parents=True)
    (root / "Contents" / "Resources").mkdir(parents=True)
    (root / "Contents" / "Frameworks" / "lib" / "a.py").write_text("print(1)\n")
    (root / "Contents" / "Resources" / "lib").symlink_to("../Frameworks/lib")
    exe = root / "Contents" / "MacOS" / "app"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return root


def test_tree_diff_passes_identical_bundles_with_linked_directories(tmp_path, capsys):
    a, b = _bundle(tmp_path / "a"), _bundle(tmp_path / "b")
    assert appcast.main(["appcast.py", "treediff", str(a), str(b)]) == 0
    assert "一致" in capsys.readouterr().out


def test_tree_diff_reports_content_link_and_mode_changes(tmp_path, capsys):
    a, b = _bundle(tmp_path / "a"), _bundle(tmp_path / "b")
    (b / "Contents" / "Frameworks" / "lib" / "a.py").write_text("print(2)\n")
    (b / "Contents" / "Resources" / "lib").unlink()
    (b / "Contents" / "Resources" / "lib").symlink_to("../Frameworks")
    (b / "Contents" / "MacOS" / "app").chmod(0o644)
    (b / "Contents" / "extra.txt").write_text("x")
    assert appcast.main(["appcast.py", "treediff", str(a), str(b)]) == 1
    out = capsys.readouterr().out
    for name in ("Contents/Frameworks/lib/a.py", "Contents/Resources/lib", "Contents/MacOS/app", "Contents/extra.txt"):
        assert name in out


def test_fetch_keeps_every_file_the_live_appcast_names(tmp_path, monkeypatch):
    """线上 appcast 列出的完整包和增量包都要取回来：漏了增量包，重新生成的 appcast 仍然列着它，
    verify 报缺文件，发布失败（2026-11-20 dev 上的 updates job）。"""
    files = {
        "Whiteboard-1.3.0-alpha.20261120.090000.zip": b"new",
        "Whiteboard20261120.090000-20261119.153012.delta": b"dd",
        "Whiteboard-1.2.0.zip": b"old",
    }

    def fake_get(url, timeout=60.0):
        if "/appcast.xml" in url:
            return FEED.encode()
        return files.get(url.rsplit("/", 1)[1])

    monkeypatch.setattr(appcast, "_get", fake_get)
    assert appcast.fetch(SITE, tmp_path) == 0
    for name, body in files.items():
        assert (tmp_path / name).read_bytes() == body


def test_prune_drops_delta_entries_whose_files_generate_appcast_removed(tmp_path):
    """generate_appcast 删掉了从已不保留的版本出发的增量包文件，旧条目却还列着它们
    （2026-11-20 dev 上的 updates 报缺文件）。这种条目去掉；客户端照样能用完整包更新。"""
    (tmp_path / "appcast.xml").write_text(FEED, "utf-8")
    for name in ("Whiteboard-1.3.0-alpha.20261120.090000.zip", "Whiteboard-1.2.0.zip"):
        (tmp_path / name).write_bytes(b"zip")
    assert appcast.prune(tmp_path) == 0
    text = (tmp_path / "appcast.xml").read_text("utf-8")
    assert ".delta" not in text and "<sparkle:deltas>" not in text
    alpha, stable = appcast.parse(text)
    assert alpha.deltas == [] and alpha.archive.signature == "SIG1" and stable.archive is not None


def test_prune_keeps_delta_entries_whose_files_are_there(tmp_path):
    (tmp_path / "appcast.xml").write_text(FEED, "utf-8")
    for name in (
        "Whiteboard-1.3.0-alpha.20261120.090000.zip",
        "Whiteboard-1.2.0.zip",
        "Whiteboard20261120.090000-20261119.153012.delta",
    ):
        (tmp_path / name).write_bytes(b"x")
    assert appcast.prune(tmp_path) == 0
    assert (tmp_path / "appcast.xml").read_text("utf-8") == FEED

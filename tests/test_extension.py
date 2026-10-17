"""Edge / Chrome 插件（extensions/sketch）：在真实的 Chromium 中加载插件。

插件是一个工具栏弹出页（popup.html），嵌入白板应用的速写页面。工具栏弹出页本身无法用自动化
打开，这里在标签页中打开 popup.html：页面内容与弹出时相同。检查：清单文件合法、插件能加载；
弹出页自动找到白板应用的端口并嵌入速写页面；嵌入的页面连接、iPad 跟随、在嵌入的页面中复制；
白板应用没有运行时显示提示，运行之后自动连上。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from test_browser import chromium_path, draw, free_port, open_pages  # noqa: E402

from whiteboard.config import Config  # noqa: E402
from whiteboard.runner import ServerThread  # noqa: E402

EXTENSION = Path(__file__).resolve().parent.parent / "extensions" / "sketch"


def test_manifest_is_complete():
    manifest = json.loads((EXTENSION / "manifest.json").read_text("utf-8"))
    assert manifest["manifest_version"] == 3
    assert "side_panel" not in manifest and "background" not in manifest
    assert manifest["action"]["default_popup"] == "popup.html"
    assert set(manifest["permissions"]) == {"storage"}
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert manifest["commands"]["_execute_action"]["suggested_key"]["default"] == "Alt+Shift+S"
    for path in [*manifest["icons"].values(), "popup.html", "popup.js", "popup.css"]:
        assert (EXTENSION / path).is_file(), path
    # 弹出页只嵌白板应用的页面，不加载任何远程脚本（Manifest V3 也不允许）
    html = (EXTENSION / "popup.html").read_text("utf-8")
    assert "http" not in html
    assert 'allow="clipboard-write"' in html


@pytest.fixture
def server(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = free_port()
    thread = ServerThread(config, advertise=False)
    thread.start()
    yield thread
    thread.stop()


def extension_id():
    """清单里的 key 固定了插件的 id：公钥 SHA-256 的前 32 个十六进制位，0-f 换成 a-p。"""
    key = json.loads((EXTENSION / "manifest.json").read_text("utf-8"))["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


@pytest.fixture
def extension_context(tmp_path):
    """加载了插件的浏览器（Chromium 的新无头模式支持插件），以及插件的 id。"""
    with sync_playwright() as pw:
        path = chromium_path()
        # 无头模式加载插件需要完整的 Chromium（不是 headless shell）：本机用 /opt/pw-browsers 里的，
        # CI 上用 Playwright 自带的那一版（channel="chromium"）
        browser_args = {"executable_path": path} if path else {"channel": "chromium"}
        try:
            context = pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"),
                **browser_args,
                headless=True,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", "--headless=new"],
                viewport={"width": 420, "height": 360},
            )
        except Exception as exc:  # pragma: no cover - 环境没有浏览器
            if os.environ.get("CI"):
                raise  # CI 上必须真的跑，不能悄悄跳过
            pytest.skip(f"没有可用的 Chromium：{exc}")
        ext = extension_id()
        page = context.new_page()
        loaded = False
        for _ in range(40):
            try:
                page.goto(f"chrome-extension://{ext}/popup.html")
                loaded = page.evaluate("() => typeof chrome !== 'undefined' && !!chrome.storage")
            except Exception:  # noqa: BLE001 - 插件还没加载完
                loaded = False
            if loaded:
                break
            page.wait_for_timeout(250)
        page.close()
        if not loaded:
            context.close()
            if os.environ.get("CI"):
                raise AssertionError("插件没有加载")
            pytest.skip("这个 Chromium 不支持在无头模式下加载插件")
        yield pw, context, ext
        context.close()


def open_popup(context, ext, port):
    """在标签页里打开弹出页；先把端口记进插件的存储，让它第一个试这个端口（测试服务不在 8848）。"""
    page = context.new_page()
    page.goto(f"chrome-extension://{ext}/popup.html")
    page.evaluate("(port) => chrome.storage.local.set({ port })", port)
    page.reload()
    return page


def sketch_frame(page):
    page.wait_for_selector("#sketch:not([hidden])", timeout=15000)
    handle = page.query_selector("#sketch").content_frame()
    handle.wait_for_function("() => window.sketchPad && sketchPad.status === 'online'", timeout=10000)
    return handle


def test_popup_embeds_the_sketch_and_copies_from_inside(extension_context, server):
    pw, context, ext = extension_context
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{server.port}")
    server.hub.sketch_grace = 0.3
    path = chromium_path()
    browser = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
    try:
        mac, ipad = open_pages(browser, server.port)
        original = ipad.evaluate("() => whiteboard.state.id")
        popup = open_popup(context, ext, server.port)
        frame = sketch_frame(popup)
        ipad.wait_for_function("() => whiteboard.state.id === 'sketch'")
        assert frame.evaluate("() => Array.from(document.querySelectorAll('button')).map((b) => b.id)") == ["clear", "copy"]

        draw(ipad, [(300, 300), (420, 360), (540, 300)])
        frame.wait_for_function("() => sketchPad.snapshot().strokes.length === 1")
        frame.evaluate("() => { window.copied = null; addEventListener('sketch-copied', (e) => { window.copied = e.detail; }); }")
        frame.click("#copy")
        frame.wait_for_function("() => document.getElementById('notice').textContent.length > 0")
        notice = frame.evaluate("() => document.getElementById('notice').textContent")
        assert "已复制" in notice, notice
        # 剪贴板写入成功之后页面才发出 sketch-copied（带写入的 PNG 大小）。这里不读回剪贴板：
        # 较新的 Chromium 不允许嵌入的其他来源的页面读剪贴板；读回像素的检查在 test_browser.py 中
        copied = frame.evaluate("() => window.copied")
        assert copied is not None and copied["size"] > 100, copied

        # 弹出页关闭（点到别处时浏览器会自动关掉它）：iPad 回到原来的白板
        popup.close()
        ipad.wait_for_function(f"() => whiteboard.state.id === {original!r}", timeout=10000)
    finally:
        browser.close()


def test_popup_finds_the_app_once_it_runs(extension_context, tmp_path):
    """白板应用没有运行：显示提示；之后白板应用启动（这里在记下的端口上），弹出页自动连上。"""
    _pw, context, ext = extension_context
    port = free_port()
    page = open_popup(context, ext, port)
    page.wait_for_selector("#offline:not([hidden])")
    assert page.evaluate("() => document.getElementById('sketch').hidden")

    config = Config(path=tmp_path / "late.json")
    config.data_dir = tmp_path / "late-data"
    config.port = port
    late = ServerThread(config, advertise=False)
    late.start()
    try:
        sketch_frame(page)
        assert page.evaluate("() => document.getElementById('offline').hidden")
        assert page.evaluate("async () => (await chrome.storage.local.get('port')).port") == port
    finally:
        late.stop()

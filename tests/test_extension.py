"""Edge / Chrome 侧边栏插件（extensions/sketch）：在真实的 Chromium 中加载插件。

侧边栏本身无法用自动化打开，这里直接在标签页中打开插件的 panel.html：页面内容与侧边栏中
完全相同。检查：清单文件合法、插件能加载；面板找到白板应用并嵌入速写页面；嵌入的页面
连接、iPad 跟随、在嵌入的页面中复制图片；端口不对时显示「白板应用没有运行」，改端口后连上。
"""

from __future__ import annotations

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
    assert manifest["side_panel"]["default_path"] == "panel.html"
    assert set(manifest["permissions"]) == {"sidePanel", "storage"}
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert manifest["commands"]["_execute_action"]["suggested_key"]["default"] == "Alt+Shift+S"
    for path in [*manifest["icons"].values(), manifest["background"]["service_worker"], "panel.html", "panel.js", "panel.css"]:
        assert (EXTENSION / path).is_file(), path
    # 面板只嵌白板应用的页面，不加载任何远程脚本（Manifest V3 也不允许）
    html = (EXTENSION / "panel.html").read_text("utf-8")
    assert "http" not in html.replace('allow="clipboard-write; clipboard-read"', "")
    assert 'allow="clipboard-write; clipboard-read"' in html


@pytest.fixture
def server(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = free_port()
    thread = ServerThread(config, advertise=False)
    thread.start()
    yield thread
    thread.stop()


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
                viewport={"width": 380, "height": 760},
            )
        except Exception as exc:  # pragma: no cover - 环境没有浏览器
            if os.environ.get("CI"):
                raise  # CI 上必须真的跑，不能悄悄跳过
            pytest.skip(f"没有可用的 Chromium：{exc}")
        try:
            workers = context.service_workers or [context.wait_for_event("serviceworker", timeout=10000)]
        except Exception:  # pragma: no cover
            context.close()
            if os.environ.get("CI"):
                raise
            pytest.skip("这个 Chromium 不支持在无头模式下加载插件")
        extension_id = workers[0].url.split("/")[2]
        yield pw, context, extension_id
        context.close()


def open_panel(context, extension_id, port):
    page = context.new_page()
    page.goto(f"chrome-extension://{extension_id}/panel.html")
    page.evaluate("(port) => chrome.storage.local.set({ port })", port)
    page.reload()
    return page


def sketch_frame(page):
    page.wait_for_selector("#sketch:not([hidden])", timeout=10000)
    frame = page.frame_locator("#sketch")
    frame.locator("#status").wait_for()
    handle = page.query_selector("#sketch").content_frame()
    handle.wait_for_function("() => window.sketchPad && sketchPad.status === 'online'", timeout=10000)
    return handle


def test_panel_embeds_the_sketch_and_copies_from_inside(extension_context, server):
    pw, context, extension_id = extension_context
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{server.port}")
    server.hub.sketch_grace = 0.3
    path = chromium_path()
    browser = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
    try:
        mac, ipad = open_pages(browser, server.port)
        original = ipad.evaluate("() => whiteboard.state.id")
        panel = open_panel(context, extension_id, server.port)
        frame = sketch_frame(panel)
        ipad.wait_for_function("() => whiteboard.state.id === 'sketch'")

        draw(ipad, [(300, 300), (420, 360), (540, 300)])
        frame.wait_for_function("() => sketchPad.snapshot().strokes.length === 1")
        frame.click("#copy")
        frame.wait_for_function("() => document.getElementById('notice').textContent.length > 0")
        notice = frame.evaluate("() => document.getElementById('notice').textContent")
        assert "已复制" in notice, notice
        types = frame.evaluate("async () => (await navigator.clipboard.read()).flatMap((i) => i.types)")
        assert "image/png" in types

        # 关掉侧边栏（这里是关掉面板页面）：iPad 回到原来的白板
        panel.close()
        ipad.wait_for_function(f"() => whiteboard.state.id === {original!r}", timeout=10000)
    finally:
        browser.close()


def test_panel_waits_for_the_app_and_remembers_the_port(extension_context, server):
    _pw, context, extension_id = extension_context
    wrong = free_port()
    page = open_panel(context, extension_id, wrong)
    page.wait_for_selector("#offline:not([hidden])")
    assert f"127.0.0.1:{wrong}" in page.inner_text("#address")
    assert page.evaluate("() => document.getElementById('sketch').hidden")

    page.click("#settings summary")
    page.fill("#port", str(server.port))
    page.click("#save")
    sketch_frame(page)
    assert page.evaluate("async () => (await chrome.storage.local.get('port')).port") == server.port
    page.reload()
    sketch_frame(page)

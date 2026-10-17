"""Edge / Chrome 插件（extensions/sketch）：在真实的 Chromium 中加载插件。

插件打开一个速写小窗口（float.html）。检查：清单文件合法、插件能加载；点图标（这里直接调用
后台的 toggleFloat）打开小窗口，再点一次不重复打开；小窗口找到白板应用并嵌入速写页面；嵌入的
页面连接、iPad 跟随、在嵌入的页面中复制图片；展开 / 收起改变窗口大小；端口不对时显示「白板
应用没有运行」，改端口后连上。置顶（文档画中画）在无头浏览器中打不开，需要在真机上检查。
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
    assert "side_panel" not in manifest
    assert set(manifest["permissions"]) == {"storage"}
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert manifest["commands"]["_execute_action"]["suggested_key"]["default"] == "Alt+Shift+S"
    for path in [*manifest["icons"].values(), manifest["background"]["service_worker"], "float.html", "float.js", "float.css"]:
        assert (EXTENSION / path).is_file(), path
    # 小窗口只嵌白板应用的页面，不加载任何远程脚本（Manifest V3 也不允许）
    html = (EXTENSION / "float.html").read_text("utf-8")
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
    page.goto(f"chrome-extension://{extension_id}/float.html")
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
        frame.evaluate("() => { window.copied = null; addEventListener('sketch-copied', (e) => { window.copied = e.detail; }); }")
        frame.click("#copy")
        frame.wait_for_function("() => document.getElementById('notice').textContent.length > 0")
        notice = frame.evaluate("() => document.getElementById('notice').textContent")
        assert "已复制" in notice, notice
        # 剪贴板写入成功之后页面才发出 sketch-copied（带写入的 PNG 大小）。这里不读回剪贴板：
        # 较新的 Chromium 不允许嵌入的其他来源的页面读剪贴板；读回像素的检查在 test_browser.py 中
        copied = frame.evaluate("() => window.copied")
        assert copied is not None and copied["size"] > 100, copied

        # 关掉速写小窗口：iPad 回到原来的白板
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

    page.click("#gear")
    page.fill("#port", str(server.port))
    page.click("#save")
    sketch_frame(page)
    assert page.evaluate("async () => (await chrome.storage.local.get('port')).port") == server.port
    page.reload()
    sketch_frame(page)


def test_icon_opens_one_small_window_that_can_expand(extension_context, server):
    """点图标打开速写小窗口（右上角、iPad 横屏的比例），再点一次切回同一个窗口而不是再开一个；
    展开之后变大，收起之后回到原来的大小。"""
    _pw, context, extension_id = extension_context
    worker = context.service_workers[0]
    page = context.new_page()
    page.goto(f"chrome-extension://{extension_id}/float.html")
    page.evaluate("(port) => chrome.storage.local.set({ port })", server.port)
    page.close()

    first = worker.evaluate("() => toggleFloat()")
    second = worker.evaluate("() => toggleFloat()")
    assert first == second
    floats = [p for p in context.pages if p.url.endswith("/float.html")]
    assert len(floats) == 1
    window = floats[0]
    sketch_frame(window)
    info = worker.evaluate("(id) => chrome.windows.get(id)", first)
    assert info["type"] == "popup" and info["width"] < 500, info

    window.click("#expand")
    window.wait_for_function("() => document.getElementById('expand').textContent === '收起'")
    expanded = worker.evaluate("(id) => chrome.windows.get(id)", first)
    assert expanded["width"] > info["width"] + 200, expanded
    window.click("#expand")
    window.wait_for_function("() => document.getElementById('expand').textContent === '展开'")
    back = worker.evaluate("(id) => chrome.windows.get(id)", first)
    assert abs(back["width"] - info["width"]) <= 20, back

    # 关掉之后再点图标，开一个新的
    worker.evaluate("(id) => chrome.windows.remove(id)", first)
    window.wait_for_event("close", timeout=5000)
    third = worker.evaluate("() => toggleFloat()")
    assert third != first


def test_pinning_moves_the_sketch_into_an_always_on_top_window(extension_context, server):
    """置顶：速写页面移进文档画中画浮窗（始终在其他窗口上方），本窗口不再连接；
    iPad 照常写进去；关闭浮窗后本窗口重新嵌入速写页面。整个过程速写不中断。"""
    pw, context, extension_id = extension_context
    server.hub.sketch_grace = 1.0
    path = chromium_path()
    browser = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
    try:
        _mac, ipad = open_pages(browser, server.port)
        page = open_panel(context, extension_id, server.port)
        sketch_frame(page)
        ipad.wait_for_function("() => whiteboard.state.id === 'sketch'")
        assert page.evaluate("() => !document.getElementById('pin').hidden")

        page.click("#pin")
        page.wait_for_function("() => documentPictureInPicture.window && document.getElementById('pin').textContent === '取消置顶'")
        assert page.evaluate("() => !document.getElementById('sketch').getAttribute('src')"), "本窗口的速写页面已卸掉"
        assert page.evaluate("() => documentPictureInPicture.window.document.querySelector('iframe').src").endswith("/sketch")
        hub = server.hub
        page.wait_for_timeout(1500)  # 本窗口那个速写页面的连接断开、宽限期过去
        assert hub.sketch_active
        assert len(hub.sketch_owners()) == 1, "同一时间只有浮窗里的那个速写页面"
        assert ipad.evaluate("() => whiteboard.state.id") == "sketch"

        draw(ipad, [(300, 300), (420, 360)])
        page.wait_for_timeout(500)
        assert len(hub.core.board("sketch").stroke_list()) == 1

        page.evaluate("() => documentPictureInPicture.window.close()")
        page.wait_for_function("() => document.getElementById('pin').textContent === '置顶'")
        frame = sketch_frame(page)
        frame.wait_for_function("() => sketchPad.snapshot().strokes.length === 1")
        assert hub.sketch_active and ipad.evaluate("() => whiteboard.state.id") == "sketch"
    finally:
        browser.close()

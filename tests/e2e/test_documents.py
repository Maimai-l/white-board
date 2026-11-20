"""导出 PNG、文档板（PDF 底图上书写、导出时把笔迹合进 PDF）。"""

from __future__ import annotations

import urllib.request

import pytest

from e2e.support import draw, open_mac, open_pages, wait_strokes

HAS_DOC = "() => !!(whiteboard.state.meta && whiteboard.state.meta.data && whiteboard.state.meta.data.doc)"


def test_export_png_has_content(browser, server):
    mac = open_mac(browser, server.port)
    draw(mac, [(300, 300), (400, 360), (500, 300)], pointer_type="mouse")
    wait_strokes(mac, 1)
    data_url = mac.evaluate(
        "async () => { const m = await import('/inksync/exporter.js');return m.exportDataURL(whiteboard.state); }"
    )
    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 5000


def test_the_export_button_downloads_a_png(browser, server):
    """导出按钮打开导出对话框；选 PNG、只有一页时下载一张 PNG（对话框本身见 test_export_dialog.py）。"""
    mac = open_mac(browser, server.port)
    draw(mac, [(200, 200), (300, 260)], pointer_type="mouse")
    wait_strokes(mac, 1)
    mac.click('#topright button[title="导出"]')
    mac.wait_for_selector(".export-card")
    mac.click('.export-seg button:has-text("PNG")')
    with mac.expect_download() as info:
        mac.click('.export-footer button:has-text("导出 PNG")')
    download = info.value
    assert download.suggested_filename.endswith(".png")
    with open(download.path(), "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def make_doc(tmp_path, pages=((595, 842), (595, 842))):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    writer = pypdf.PdfWriter()
    for width, height in pages:
        writer.add_blank_page(width=width, height=height)
    path = tmp_path / "讲义.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def open_doc_board(mac, path):
    """走完整的界面路径：白板列表 → 新建 → 选文件。"""
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    with mac.expect_file_chooser() as chooser:
        mac.click(".dialog.kinds .kind-tile:nth-child(3)")
    chooser.value.set_files(str(path))
    mac.wait_for_function(HAS_DOC, timeout=15000)


def test_a_file_that_cannot_be_opened_says_which_without_details(browser, server, tmp_path):
    """打不开的文件只说打不开哪个，原因（服务端的回复）不显示在界面上（docs/ui-text.md）。"""
    bad = tmp_path / "坏的.pdf"
    bad.write_bytes(b"not a pdf")
    mac = open_mac(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    with mac.expect_file_chooser() as chooser:
        mac.click('.dialog.kinds .kind-tile[title="打开 PDF 或图片"]')
    with mac.expect_response(lambda r: "/api/doc" in r.url) as response:
        chooser.value.set_files(str(bad))
    assert response.value.status == 400
    notice = mac.locator(".notice", has_text="打不开")
    notice.wait_for(timeout=5000)
    assert notice.inner_text() == "打不开 坏的.pdf"


def test_doc_board_created_from_file_and_synced(browser, server, tmp_path):
    """拖 / 选一份 PDF：两端都切过去，页面底图取得回来，笔只能写在文档范围内。"""
    path = make_doc(tmp_path)
    mac, ipad = open_pages(browser, server.port)
    open_doc_board(mac, path)
    ipad.wait_for_function(HAS_DOC, timeout=15000)

    pages = mac.evaluate(
        "() => whiteboard.state.layers.filter((l) => l.sheet).map((l) => ({x: l.x, y: l.y, w: l.width, h: l.height}))"
    )
    assert len(pages) == 2
    assert pages[1]["y"] == pytest.approx(842 + 24)
    limits = mac.evaluate("() => whiteboard.state.limits")
    assert limits["y1"] == pytest.approx(842 * 2 + 24)

    # 首页底图由服务端渲染后送过来
    mac.wait_for_function("() => whiteboard.renderer.images.get(0) !== null", timeout=15000)

    # 从末页里落笔，一路划到文档下方：超出的部分被夹回最后一页里
    start = ipad.evaluate(
        "() => { const l = whiteboard.state.limits;"
        " whiteboard.viewport.scale = 0.4;"
        " whiteboard.viewport.centerOn(l.x1 / 2, l.y1 - 120, 1180, 820);"
        " whiteboard.renderer.requestFull();"
        " return whiteboard.viewport.toScreen(l.x1 / 2, l.y1 - 60); }"
    )
    draw(ipad, [(start[0], start[1]), (start[0] + 40, start[1] + 120), (start[0] + 80, start[1] + 240)])
    wait_strokes(mac, 1)
    ys = mac.evaluate("() => whiteboard.state.strokes[0].p.filter((_, i) => i % 3 === 1)")
    assert max(ys) <= limits["y1"] + 0.01


def test_doc_board_export_merges_ink(browser, server, tmp_path):
    path = make_doc(tmp_path)
    mac, ipad = open_pages(browser, server.port)
    open_doc_board(mac, path)
    draw(mac, [(300, 300), (380, 360), (460, 300)], pointer_type="mouse")
    wait_strokes(ipad, 1)

    board_id = mac.evaluate("() => whiteboard.state.id")
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/export/{board_id}") as out:
        body = out.read()
    assert body.startswith(b"%PDF")
    # 笔迹进去了，但体积没涨多少
    assert len(body) > path.stat().st_size
    assert len(body) < path.stat().st_size + 4096

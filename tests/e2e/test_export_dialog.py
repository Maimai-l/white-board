"""导出对话框（docs/backlog/001-export-redesign.md）：像用户一样点，只看页面和下载下来的文件。

页框怎么分、文件怎么出在 tests/test_export.py 和 tests/test_pagination.py 里测。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from e2e.support import draw, open_mac, serve_with_perms, wait_strokes

pypdf = pytest.importorskip("pypdf")
pdfium = pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")

EXPORT = '#topright button[title="导出"]'
CARDS = ".export-card"


def zoomed_out(page, times=6):
    """点「缩小」几次（每次 0.8 倍）：屏幕上隔开一段，白板上就隔得很远。"""
    for _ in range(times):
        page.click('#zoombar button[title="缩小"]')


def scribble(page, x, y, rows=3):
    """在屏幕 (x, y) 附近写几行短横线。"""
    for row in range(rows):
        yy = y + row * 14
        draw(page, [(x + i * 8, yy + (3 if i % 2 else -3)) for i in range(8)], pointer_type="mouse")


def board_with_groups(browser, server, spots):
    """一块大白板，每个屏幕位置写一组字（缩小着写，组与组在白板上隔得很远）。"""
    mac = open_mac(browser, server.port)
    zoomed_out(mac)
    for x, y in spots:
        scribble(mac, x, y)
    wait_strokes(mac, 3 * len(spots))
    return mac


def open_dialog(page, pages):
    page.click(EXPORT)
    page.wait_for_function(f"() => document.querySelectorAll('{CARDS}').length === {pages}")


def download(page, label):
    with page.expect_download() as info:
        page.click(f'.export-footer button:has-text("{label}")')
    path = info.value.path()
    with open(path, "rb") as handle:
        return info.value.suggested_filename, handle.read()


def pdf_pages(body):
    return len(pypdf.PdfReader(io.BytesIO(body)).pages)


def dark_pixels(body):
    """PDF 第一页上深色像素的个数（墨迹；背景格子是浅色的）。"""
    pdf = pdfium.PdfDocument(body)
    try:
        page = pdf[0]
        bitmap = page.render(scale=0.5)
        image = bitmap.to_pil().convert("L").copy()
        bitmap.close()
        page.close()
    finally:
        pdf.close()
    return image.point(lambda v: 255 if v < 100 else 0).histogram()[255]


def test_the_export_button_opens_the_dialog_with_the_auto_pages(browser, server):
    mac = board_with_groups(browser, server, [(150, 200), (1000, 600)])
    open_dialog(mac, 2)
    assert mac.locator(".export-title").inner_text() == "导出"
    assert mac.locator(".export-count").inner_text() == "已选 2 页"
    assert mac.locator(".export-caption").all_inner_texts() == ["第 1 页", "第 2 页"]
    assert mac.locator(".export-paging").inner_text().startswith("自动分成 2 页")
    assert mac.locator(".export-footer .primary").inner_text() == "导出 PDF"
    # 取消就关掉，什么都不下载
    mac.click('.export-footer button:has-text("取消")')
    assert mac.locator(".export-dialog").count() == 0


def test_deselected_pages_are_left_out_of_the_pdf(browser, server):
    mac = board_with_groups(browser, server, [(550, 100), (550, 400), (550, 700)])
    open_dialog(mac, 3)
    mac.locator(CARDS).nth(1).click()
    assert mac.locator(".export-count").inner_text() == "已选 2 页，共 3 页"
    name, body = download(mac, "导出 PDF")
    assert name.endswith(".pdf")
    assert pdf_pages(body) == 2
    mac.wait_for_selector(".toast")
    assert mac.locator(".export-dialog").count() == 0

    # 一页都不选时导出按钮是灰的；「全选」又全选上
    open_dialog(mac, 3)
    for index in range(3):
        mac.locator(CARDS).nth(index).click()
    assert mac.locator(".export-footer .primary").is_disabled()
    mac.click('.text-btn:has-text("全选")')
    assert mac.locator(".export-count").inner_text() == "已选 3 页"


def test_png_gives_one_image_for_one_page_and_a_zip_for_several(browser, server):
    mac = board_with_groups(browser, server, [(150, 200), (1000, 600)])
    open_dialog(mac, 2)
    mac.select_option(".export-popup select", "a4")
    mac.click('.export-seg button:has-text("PNG")')
    assert mac.locator(".export-footer .primary").inner_text() == "导出图片"
    # 清晰度默认「高」（300 dpi），A4 横放就是 3508 × 2480
    assert mac.locator(".export-hint").inner_text() in ("每页 3508 × 2480 像素", "每页 2480 × 3508 像素")
    mac.click('.export-seg button:has-text("标准")')
    assert mac.locator(".export-hint").inner_text() in ("每页 1754 × 1240 像素", "每页 1240 × 1754 像素")

    mac.locator(CARDS).nth(1).click()
    name, body = download(mac, "导出图片")
    assert name.endswith(".png")
    image = Image.open(io.BytesIO(body))
    assert image.format == "PNG" and sorted(image.size) == [1240, 1754]

    open_dialog(mac, 2)
    mac.click('.export-seg button:has-text("PNG")')
    name, body = download(mac, "导出图片")
    assert name.endswith(".zip")
    assert len(zipfile.ZipFile(io.BytesIO(body)).namelist()) == 2


def test_changing_the_paper_fetches_new_pages(browser, server):
    mac = board_with_groups(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    mac.click('.export-seg button:has-text("PNG")')
    assert mac.locator(".export-popup > span").first.inner_text() == "跟随内容"
    mac.select_option(".export-popup select", "letter")
    assert mac.locator(".export-popup > span").first.inner_text() == "Letter"
    # Letter 是 8.5 × 11 英寸：300 dpi 下 2550 × 3300
    mac.wait_for_function(
        "() => ['每页 2550 × 3300 像素', '每页 3300 × 2550 像素']"
        ".includes(document.querySelector('.export-hint').textContent)"
    )
    _, body = download(mac, "导出图片")
    assert sorted(Image.open(io.BytesIO(body)).size) == [2550, 3300]


def test_current_page_selects_the_page_in_the_middle_of_the_view(browser, server):
    mac = board_with_groups(browser, server, [(150, 200), (1000, 600)])
    # 视口中心移到第二组上
    mac.evaluate(
        "() => { const s = whiteboard.state.strokes.at(-1);"
        " whiteboard.viewport.centerOn(s.p[0], s.p[1], whiteboard.renderer.viewW, whiteboard.renderer.viewH);"
        " whiteboard.renderer.requestFull(); }"
    )
    open_dialog(mac, 2)
    mac.click('.text-btn:has-text("当前页")')
    assert mac.locator(".export-count").inner_text() == "已选 1 页，共 2 页"
    _, body = download(mac, "导出 PDF")
    assert pdf_pages(body) == 1


def test_dragging_a_page_frame_changes_what_the_page_shows(browser, server):
    """调整页框：把页框从墨迹上拖开，导出的那一页就空了。"""
    mac = board_with_groups(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    _, before = download(mac, "导出 PDF")
    assert dark_pixels(before) > 20

    open_dialog(mac, 1)
    mac.click('.export-paging button:has-text("调整")')
    frame = mac.locator(".export-frame").first
    frame.wait_for()
    assert mac.locator(".export-dialog").is_hidden()
    assert mac.locator('.export-bar button:has-text("完成")').is_visible()
    box = frame.bounding_box()
    # 按住页框靠左边的地方往右拖一个多页框宽：页框整个越过墨迹，鼠标还在窗口里松开
    x, y = box["x"] + 20, box["y"] + box["height"] / 2
    end = x + box["width"] * 1.05
    assert end < mac.viewport_size["width"]
    mac.mouse.move(x, y)
    mac.mouse.down()
    for step in range(1, 11):
        mac.mouse.move(x + (end - x) * step / 10, y)
    mac.mouse.up()
    # 拖的是页框，不是白板，也没落笔
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 3
    mac.click('.export-bar button:has-text("完成")')
    mac.wait_for_selector(".export-dialog")
    assert mac.locator(".export-paging").inner_text().startswith("手动调整过，1 页")
    _, after = download(mac, "导出 PDF")
    assert pdf_pages(after) == 1
    assert dark_pixels(after) == 0


def test_the_adjust_view_shows_the_frames_in_place_right_away(browser, server):
    """点「调整」的那一刻页框就在最终的位置上，不会先在别处闪一下再跳过去。"""
    mac = board_with_groups(browser, server, [(150, 200)])
    open_dialog(mac, 1)
    first = mac.evaluate(
        """() => {
            [...document.querySelectorAll('.export-paging button')].find((b) => b.textContent === '调整').click();
            const r = document.querySelector('.export-frame').getBoundingClientRect();
            return [r.x, r.y, r.width, r.height];
        }"""
    )
    settled = mac.evaluate(
        """() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const r = document.querySelector('.export-frame').getBoundingClientRect();
            done([r.x, r.y, r.width, r.height]);
        })))"""
    )
    assert first == pytest.approx(settled, abs=0.5)


def test_a_frame_released_outside_the_window_stops_following_the_mouse(browser, server):
    """在窗口外松开鼠标：页面收不到松开，回到窗口时鼠标键已经松了，页框不能再跟着鼠标走。"""
    mac = board_with_groups(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    mac.click('.export-paging button:has-text("调整")')
    frame = mac.locator(".export-frame").first
    frame.wait_for()
    box = frame.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    mac.mouse.move(x, y)
    mac.mouse.down()
    mac.mouse.move(x + 200, y)
    dragged = frame.bounding_box()
    assert dragged["x"] == pytest.approx(box["x"] + 200, abs=1)
    # 松开发生在窗口外，页面只看到下一次移动：位置回到窗口里，按键已经是松开的
    cdp = mac.context.new_cdp_session(mac)
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y + 150, "button": "none", "buttons": 0})
    mac.wait_for_function("() => new Promise((done) => requestAnimationFrame(() => done(true)))")
    assert frame.bounding_box() == dragged
    mac.mouse.up()
    mac.mouse.move(x - 300, y)
    assert frame.bounding_box() == dragged


def test_adjust_view_adds_and_deletes_pages(browser, server):
    mac = board_with_groups(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    mac.click('.export-paging button:has-text("调整")')
    mac.wait_for_selector(".export-frame")
    mac.click('.export-bar button:has-text("添加一页")')
    assert mac.locator(".export-frame").count() == 2
    assert mac.locator(".export-frame.is-selected .export-handle").count() == 4
    mac.click(".export-frame.is-selected .export-delete")
    assert mac.locator(".export-frame").count() == 1
    mac.click('.export-bar button:has-text("添加一页")')
    mac.click('.export-bar button:has-text("自动分页")')
    mac.wait_for_function("() => document.querySelectorAll('.export-frame').length === 1")
    mac.click('.export-bar button:has-text("完成")')
    assert mac.locator(".export-paging").inner_text().startswith("自动分成 1 页")
    # 回到对话框之后，工具栏又回来了，白板可以照常写字
    mac.click('.export-footer button:has-text("取消")')
    assert mac.locator("#toolbar").is_visible()
    draw(mac, [(200, 700), (260, 720)], pointer_type="mouse")
    wait_strokes(mac, 4)


def test_document_boards_have_no_paging_row(browser, server, tmp_path):
    from e2e.test_documents import make_doc, open_doc_board

    mac = open_mac(browser, server.port)
    open_doc_board(mac, make_doc(tmp_path, pages=((595, 842), (595, 842), (595, 842))))
    open_dialog(mac, 3)
    assert mac.locator(".export-paging").count() == 0
    assert mac.locator(".export-popup").count() == 0
    mac.locator(CARDS).nth(0).click()
    _, body = download(mac, "导出 PDF")
    assert pdf_pages(body) == 2


def test_no_export_entry_without_the_permission(browser, server):
    mac = open_mac(browser, server.port)
    serve_with_perms(mac, "manage settings clear")
    assert mac.locator(EXPORT).count() == 0
    assert mac.locator(".export-dialog").count() == 0


def test_scattered_notes_down_the_board_are_not_cut_into_many_small_pages(browser, server):
    """用户的反馈（backlog 010）：东一块西一块往下写的大白板，第一版每块切一页、又碎又小。
    现在从上往下按段分：一屏写下来的六块字放进同一页。"""
    spots = [(300, 100), (700, 200), (400, 300), (800, 400), (350, 500), (650, 600)]
    mac = board_with_groups(browser, server, spots)
    open_dialog(mac, 1)
    assert mac.locator(".export-paging").inner_text().startswith("自动分成 1 页")
    _, body = download(mac, "导出 PDF")
    assert pdf_pages(body) == 1

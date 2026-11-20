"""导出第二版的对话框（docs/backlog/010-export-v2.md）：白板文件、整块、纸张跟随内容、
页码输入框、在调整视图里清空并拖出页框。像用户一样点，只看页面、发出的请求和下载到的文件。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import re

import pytest

from e2e.support import draw, fire, open_mac, view, wait_strokes
from e2e.test_export_dialog import (
    CARDS,
    dark_pixels,
    download,
    open_dialog,
    pdf_pages,
    scribble,
    zoomed_out,
)

pypdf = pytest.importorskip("pypdf")
Image = pytest.importorskip("PIL.Image")

FIELD = 'input[aria-label="页码"]'
ALL_RADIO = 'input[type="radio"][value="all"]'
CUSTOM_RADIO = 'input[type="radio"][value="custom"]'
PICKED_RADIO = 'input[type="radio"][value="picked"]'
PRIMARY = ".export-footer .primary"
ERROR = ".export-range-error"


def board(browser, server, spots, times=6):
    """一块大白板：先缩小 ``times`` 次，再在每个屏幕位置写一组字。"""
    mac = open_mac(browser, server.port)
    zoomed_out(mac, times)
    for x, y in spots:
        scribble(mac, x, y)
    wait_strokes(mac, 3 * len(spots))
    return mac


def four_pages(browser, server):
    """竖着隔得很远的四组字：自动分成四页。"""
    mac = board(browser, server, [(550, 80), (550, 280), (550, 480), (550, 680)], times=8)
    open_dialog(mac, 4)
    return mac


def pick(page, label):
    page.click(f'.export-seg button:has-text("{label}")')


def selected_cards(page):
    return page.evaluate(
        f"() => [...document.querySelectorAll('{CARDS}')]"
        ".flatMap((c, i) => c.getAttribute('aria-pressed') === 'true' ? [i + 1] : [])"
    )


def auto_pages(page, paper):
    return page.evaluate(
        "async (paper) => (await (await fetch(`/api/export/${whiteboard.state.id}/pages?paper=${paper}`)).json())",
        paper,
    )


def page_sizes(body):
    return [(float(p.mediabox.width), float(p.mediabox.height)) for p in pypdf.PdfReader(io.BytesIO(body)).pages]


def export_with_request(page, label):
    """点导出，返回 (下载到的文件内容, 发给服务端的请求体)。"""
    with page.expect_request(lambda r: r.method == "POST" and "/api/export/" in r.url) as request:
        _, body = download(page, label)
    return body, json.loads(request.value.post_data)


def visible(page, selector):
    return page.locator(selector).first.is_visible()


# ------------------------------------------------------------------ 白板文件


def test_the_board_file_downloads_and_opens_as_the_same_board(browser, server, tmp_path):
    from whiteboard.hub import Hub
    from whiteboard.server import open_local_file
    from whiteboard.store import BoardStore

    mac = board(browser, server, [(300, 300), (800, 500)])
    open_dialog(mac, 2)
    pick(mac, "WBZ")
    card = mac.locator(".export-file-name").inner_text()
    assert mac.locator(".export-note").inner_text() == "可以在白板应用中重新打开"
    assert mac.locator(PRIMARY).inner_text() == "导出 WBZ"
    # 只剩格式那一行：缩略图、范围、页面、纸张、分页都不显示
    assert not visible(mac, CARDS)
    assert not visible(mac, FIELD)
    assert not visible(mac, ".export-paging")
    assert not visible(mac, '.export-seg button:has-text("整张长图")')

    filename, body = download(mac, "导出 WBZ")
    # 卡片上写的就是下载到的文件名（没起名的白板是 whiteboard-日期.wbz）
    assert filename == card
    assert filename.endswith(".wbz")
    saved = tmp_path / filename
    saved.write_bytes(body)

    # 在另一份存储目录里打开（Mac 上双击 .wbz 走的就是这条路）：笔画一模一样
    hub = Hub(BoardStore(tmp_path / "other"))

    async def reopen():
        meta = await open_local_file(hub, saved)
        return await hub.strokes_of(meta["id"])

    # Playwright 的同步接口在这个线程里开着事件循环，导入放到另一个线程里跑
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        strokes = pool.submit(asyncio.run, reopen()).result()
    ours = mac.evaluate("() => whiteboard.state.strokes.map((s) => [s.id, s.p])")
    theirs = [[s["id"], s["p"]] for s in strokes]
    assert len(theirs) == len(ours) == 6
    assert [t[0] for t in theirs] == [o[0] for o in ours]
    for (_, a), (_, b) in zip(theirs, ours):
        assert a == pytest.approx(b, abs=0.07)  # 存盘时坐标按 1/8 单位取整


# ------------------------------------------------------------------ 整块


def test_whole_board_png_is_one_image_of_the_size_shown(browser, server):
    mac = board(browser, server, [(150, 150), (1000, 650)])
    open_dialog(mac, 2)
    pick(mac, "PNG")
    pick(mac, "整张长图")
    assert mac.locator(".export-whole-caption").inner_text() == "整块白板"
    assert not visible(mac, CARDS)
    assert not visible(mac, FIELD)
    assert not visible(mac, ".export-popup")
    assert not visible(mac, ".export-paging")
    assert mac.locator(PRIMARY).inner_text() == "导出 PNG"
    hint = mac.locator(".export-hint").inner_text()
    match = re.fullmatch(r"(\d+) × (\d+) 像素", hint)
    assert match, hint
    _, body = download(mac, "导出 PNG")
    image = Image.open(io.BytesIO(body))
    assert image.format == "PNG"
    assert image.size == (int(match.group(1)), int(match.group(2)))


def test_whole_board_pdf_is_one_page(browser, server):
    mac = board(browser, server, [(150, 150), (1000, 650)])
    open_dialog(mac, 2)
    pick(mac, "整张长图")
    assert not visible(mac, ".export-hint")
    assert mac.locator(PRIMARY).inner_text() == "导出 PDF"
    body, request = export_with_request(mac, "导出 PDF")
    assert request["frames"] == "whole"
    assert pdf_pages(body) == 1
    whole = auto_pages(mac, "content")["whole"]
    assert page_sizes(body)[0] == pytest.approx(tuple(whole["paper"]), abs=0.01)
    assert dark_pixels(body) > 20


# ------------------------------------------------------------------ 纸张跟随内容


def test_paper_follows_the_content_by_default(browser, server):
    """上面一小段、下面一大段：两页的纸各是各的大小，和自动分页给的一致。"""
    mac = board(browser, server, [(550, 100), (150, 650), (1000, 650)])
    open_dialog(mac, 2)
    assert mac.locator(".export-popup > span").first.inner_text() == "跟随内容"
    body, request = export_with_request(mac, "导出 PDF")
    assert request["paper"] == "content"
    sizes = page_sizes(body)
    expected = [tuple(page["paper"]) for page in auto_pages(mac, "content")["pages"]]
    assert len(sizes) == 2
    assert sizes[0] != pytest.approx(sizes[1], abs=1)
    for got, want in zip(sizes, expected):
        assert got == pytest.approx(want, abs=0.01)

    # 换成 A4：两页都是竖放的 A4
    open_dialog(mac, 2)
    mac.select_option(".export-popup select", "a4")
    assert mac.locator(".export-popup > span").first.inner_text() == "A4"
    mac.wait_for_function(f"() => document.querySelectorAll('{CARDS}').length > 0")
    body, _ = export_with_request(mac, "导出 PDF")
    for size in page_sizes(body):
        assert size == pytest.approx((595.28, 841.89), abs=0.01)


# ------------------------------------------------------------------ 页码


def test_typing_page_numbers_selects_those_pages(browser, server):
    mac = four_pages(browser, server)
    assert mac.locator(ALL_RADIO).is_checked()
    assert mac.locator(FIELD).get_attribute("placeholder") == "例如 1-3, 5"
    mac.fill(FIELD, "1-2、4")
    assert mac.locator(CUSTOM_RADIO).is_checked()
    assert selected_cards(mac) == [1, 2, 4]
    assert mac.locator(".export-count").inner_text() == "已选 3 页"
    body, request = export_with_request(mac, "导出 PDF")
    assert request["select"] == [0, 1, 3]
    assert pdf_pages(body) == 3


def test_clicking_pages_in_the_sidebar_exports_the_selected_pages(browser, server):
    """点边栏里的页：页面换到所选的页，后面写着几页；页码框跟着改写。
    全选和取消全选是同一个链接。"""
    mac = four_pages(browser, server)
    labels = mac.locator(".export-range .export-radio").all_inner_texts()
    assert [text.split("\n")[0] for text in labels] == ["全部 4 页", "范围", "所选的页"]
    assert mac.locator(f"{CARDS} .export-caption").all_inner_texts() == ["1 / 4", "2 / 4", "3 / 4", "4 / 4"]

    mac.locator(CARDS).nth(2).click()
    assert mac.locator(PICKED_RADIO).is_checked()
    assert "所选的 3 页" in mac.locator(".export-range").inner_text()
    assert mac.locator(FIELD).input_value() == "1-2, 4"
    mac.locator(CARDS).nth(0).click()
    assert mac.locator(FIELD).input_value() == "2, 4"
    body, request = export_with_request(mac, "导出 PDF")
    assert request["select"] == [1, 3]
    assert pdf_pages(body) == 2

    mac.click('.text-btn:has-text("全选")')
    assert mac.locator(ALL_RADIO).is_checked()
    assert mac.locator(".export-count").inner_text() == "已选 4 页"
    mac.click('.text-btn:has-text("当前页")')
    assert mac.locator(PICKED_RADIO).is_checked()
    assert len(selected_cards(mac)) == 1
    mac.click('.text-btn:has-text("全选")')
    mac.click('.text-btn:has-text("取消全选")')
    assert selected_cards(mac) == []
    assert mac.locator(PRIMARY).is_disabled()
    # 点全部又是全部
    mac.click(ALL_RADIO)
    assert selected_cards(mac) == [1, 2, 3, 4]


def test_a_page_number_out_of_range_shows_a_message_and_disables_export(browser, server):
    mac = four_pages(browser, server)
    mac.fill(FIELD, "1-9")
    assert mac.locator(ERROR).inner_text() == "只有 4 页"
    assert mac.locator(PRIMARY).is_disabled()
    mac.fill(FIELD, "1、x")
    assert mac.locator(ERROR).inner_text() == "页码格式不对"
    assert mac.locator(PRIMARY).is_disabled()
    mac.fill(FIELD, " 2 ，3 ")
    assert not visible(mac, ERROR)
    assert selected_cards(mac) == [2, 3]
    assert mac.locator(PRIMARY).is_enabled()


def test_the_page_field_works_on_document_boards(browser, server, tmp_path):
    from e2e.test_documents import make_doc, open_doc_board

    mac = open_mac(browser, server.port)
    open_doc_board(mac, make_doc(tmp_path, pages=((595, 842),) * 3))
    open_dialog(mac, 3)
    assert mac.locator('.export-seg button:has-text("整张长图")').count() == 0
    assert mac.locator(FIELD).get_attribute("placeholder") == "例如 1-5, 8, 11-13"
    mac.fill(FIELD, "2-3")
    assert selected_cards(mac) == [2, 3]
    assert mac.locator(".export-count").inner_text() == "已选 2 / 3 页"
    _, body = download(mac, "导出 PDF")
    assert pdf_pages(body) == 2


# ------------------------------------------------------------------ 调整：清空、拖出页框


def adjust(page):
    page.click('.export-paging button:has-text("调整")')
    page.locator(".export-bar").wait_for()


def stroke_box_on_screen(page, index):
    """第 ``index`` 条笔画在屏幕上的包围盒 [x0, y0, x1, y1]（视口坐标）。"""
    return page.evaluate(
        """(i) => {
            const s = whiteboard.state.strokes[i];
            const r = document.getElementById('stage').getBoundingClientRect();
            let box = [Infinity, Infinity, -Infinity, -Infinity];
            for (let k = 0; k < s.p.length; k += 3) {
                const [x, y] = whiteboard.viewport.toScreen(s.p[k], s.p[k + 1]);
                box = [Math.min(box[0], x + r.left), Math.min(box[1], y + r.top),
                       Math.max(box[2], x + r.left), Math.max(box[3], y + r.top)];
            }
            return box;
        }""",
        index,
    )


def mouse_drag(page, x0, y0, x1, y1, steps=8):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    page.mouse.up()


def test_clearing_the_frames_and_drawing_one_exports_exactly_that_region(browser, server):
    mac = board(browser, server, [(300, 250), (800, 550)], times=2)
    open_dialog(mac, 1)
    adjust(mac)
    bar = mac.locator(".export-bar button")
    assert bar.first.inner_text() == "清空"
    mac.click('.export-bar button:has-text("清空")')
    assert mac.locator(".export-frame").count() == 0
    assert mac.locator(".export-adjust-hint").inner_text() == "在白板上拖出页框"

    # 一个页框都没有就完成：0 页，导出按钮是灰的
    mac.click('.export-bar button:has-text("完成")')
    assert mac.locator(".export-count").inner_text() == "已选 0 页"
    assert mac.locator(PRIMARY).is_disabled()

    adjust(mac)
    assert mac.locator(".export-frame").count() == 0
    before = view(mac)
    # 框住第一组（前三笔），不碰第二组
    boxes = [stroke_box_on_screen(mac, i) for i in range(3)]
    x0 = min(b[0] for b in boxes) - 20
    y0 = min(b[1] for b in boxes) - 20
    x1 = max(b[2] for b in boxes) + 20
    y1 = max(b[3] for b in boxes) + 20
    # 拖到一半：正在拖出的框已经画出来，提示收起，不压在框的页码上
    mac.mouse.move(x0, y0)
    mac.mouse.down()
    mac.mouse.move((x0 + x1) / 2, (y0 + y1) / 2)
    mac.wait_for_selector(".export-frame.is-drawing")
    assert not visible(mac, ".export-adjust-hint")
    mac.mouse.up()
    mac.click('.export-bar button:has-text("清空")')
    mouse_drag(mac, x0, y0, x1, y1)
    assert view(mac) == before  # 拖的是页框，不是白板
    assert mac.locator(".export-frame").count() == 1
    assert not visible(mac, ".export-adjust-hint")
    frame = mac.locator(".export-frame").bounding_box()
    assert frame["x"] == pytest.approx(x0, abs=2)
    assert frame["y"] == pytest.approx(y0, abs=2)
    assert frame["width"] == pytest.approx(x1 - x0, abs=2)
    assert frame["height"] == pytest.approx(y1 - y0, abs=2)
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 6

    mac.click('.export-bar button:has-text("完成")')
    assert mac.locator(".export-count").inner_text() == "已选 1 页"
    body, request = export_with_request(mac, "导出 PDF")
    assert pdf_pages(body) == 1
    assert dark_pixels(body) > 20
    (sent,) = request["frames"]
    points = mac.evaluate("() => whiteboard.state.strokes.map((s) => s.p)")

    def inside(p):
        xs, ys = p[0::3], p[1::3]
        return all(
            sent["x"] <= x <= sent["x"] + sent["w"] and sent["y"] <= y <= sent["y"] + sent["h"] for x, y in zip(xs, ys)
        )

    def outside(p):
        xs, ys = p[0::3], p[1::3]
        return all(
            not (sent["x"] <= x <= sent["x"] + sent["w"] and sent["y"] <= y <= sent["y"] + sent["h"])
            for x, y in zip(xs, ys)
        )

    assert all(inside(p) for p in points[:3])
    assert all(outside(p) for p in points[3:])


def test_a_drawn_frame_keeps_the_paper_proportions_with_a4(browser, server):
    mac = board(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    mac.select_option(".export-popup select", "a4")
    mac.wait_for_function(f"() => document.querySelectorAll('{CARDS}').length === 1")
    adjust(mac)
    mac.click('.export-bar button:has-text("清空")')
    # 拖得横一点：页框是横放的 A4 内容区
    mouse_drag(mac, 300, 300, 700, 480)
    frame = mac.locator(".export-frame").bounding_box()
    assert frame["x"] == pytest.approx(300, abs=2)
    assert frame["y"] == pytest.approx(300, abs=2)
    assert frame["width"] / frame["height"] == pytest.approx((841.89 - 72) / (595.28 - 72), rel=0.02)
    # 太短的拖动当成点一下：不加页框
    mouse_drag(mac, 900, 650, 903, 652, steps=2)
    assert mac.locator(".export-frame").count() == 1


def test_one_finger_draws_a_frame_while_two_fingers_and_the_wheel_pan(browser, server):
    mac = board(browser, server, [(550, 400)])
    open_dialog(mac, 1)
    adjust(mac)
    mac.click('.export-bar button:has-text("清空")')

    # 两根手指一起挪：平移白板，不加页框
    before = view(mac)
    for i in range(6):
        kind = "down" if i == 0 else "move"
        fire(mac, kind, 300 + i * 20, 600, "touch", 41, 0.5)
        fire(mac, kind, 500 + i * 20, 600, "touch", 42, 0.5)
    fire(mac, "up", 400, 600, "touch", 41, 0)
    fire(mac, "up", 600, 600, "touch", 42, 0)
    assert view(mac) != before
    assert mac.locator(".export-frame").count() == 0

    # 滚轮 / 触控板：平移
    before = view(mac)
    mac.mouse.move(600, 500)
    mac.mouse.wheel(0, 120)
    mac.wait_for_function(f"(b) => JSON.stringify(__t.view()) !== JSON.stringify(b)", arg=before)
    assert mac.locator(".export-frame").count() == 0

    # 一根手指：拖出一个页框，白板不动
    before = view(mac)
    draw(mac, [(200 + i * 40, 200 + i * 30) for i in range(8)], pointer_type="touch", pointer_id=43)
    assert view(mac) == before
    assert mac.locator(".export-frame").count() == 1

    # 一根手指按下之后第二根也落下：变成双指平移，正在拖的页框作废
    before = view(mac)
    fire(mac, "down", 700, 300, "touch", 44, 0.5)
    fire(mac, "move", 760, 340, "touch", 44, 0.5)
    fire(mac, "down", 900, 300, "touch", 45, 0.5)
    for i in range(1, 5):
        fire(mac, "move", 760 + i * 20, 340, "touch", 44, 0.5)
        fire(mac, "move", 900 + i * 20, 300, "touch", 45, 0.5)
    fire(mac, "up", 840, 340, "touch", 44, 0)
    fire(mac, "up", 980, 300, "touch", 45, 0)
    assert view(mac) != before
    assert mac.locator(".export-frame").count() == 1
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 3

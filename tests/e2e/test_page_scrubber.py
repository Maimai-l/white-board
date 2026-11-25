"""文档白板右边的页码滑条（docs/backlog/015-page-scrubber.md）。

用户在 iPad 和 Mac 上读几百页的 PDF，要翻到后面某一页只能一路滚。右边一条滑条：滚动时出现、
停下淡出；指针移到右边或按住滑块出现轨道；拖动时页面跟着走、旁边显示页码；点轨道跳过去。
这里像用户一样滚、指、拖、点，只看屏幕上的滑条、页码和停在哪一页。
"""

from __future__ import annotations

from e2e.support import draw, enable_pk_picker, finger_drag, open_mac, open_touch, wait_strokes
from e2e.test_doc_pages import DOC, drop_pdf, wait_doc

PAGES = 30
SCRUBBER = ".scrubber"
STATE = "() => document.querySelector('.scrubber').dataset.state"
# 屏幕正中那一页（从 0 起）
CURRENT = "() => whiteboard.currentDocPage().index"
# 某一页上沿在屏幕上的 y（相对画布）
PAGE_TOP = "(i) => { const l = whiteboard.state.layers.filter((x) => x.sheet)[i]; return l.y * whiteboard.viewport.scale + whiteboard.viewport.y; }"


def doc_board(mac, pages=PAGES):
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "高等数学.pdf", pages)
    if pages > 1:
        mac.click(".import-dialog .lib-btn.primary")
        mac.locator(".import-dialog").wait_for(state="detached")
    wait_doc(mac, before)


def box(page, selector):
    return page.locator(selector).bounding_box()


def wait_state(page, state, timeout=5000):
    page.wait_for_function(f"(want) => ({STATE})() === want", arg=state, timeout=timeout)


def test_scrolling_shows_the_scrubber_and_it_fades_when_the_view_stops(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    wait_state(mac, "hidden", timeout=4000)  # 刚打开时视野摆好也算动了一下，滑块亮一下就淡出
    mac.mouse.move(600, 400)
    mac.mouse.wheel(0, 600)
    wait_state(mac, "scroll")
    assert mac.locator(".scrubber-thumb").evaluate("(n) => getComputedStyle(n).opacity") != "0"
    wait_state(mac, "hidden", timeout=4000)


def test_dragging_the_thumb_on_the_mac_turns_to_that_page(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    track = box(mac, SCRUBBER)
    mac.mouse.move(1200 - 6, track["y"] + 40)
    wait_state(mac, "active")
    thumb = box(mac, ".scrubber-hit")
    mac.mouse.move(thumb["x"] + thumb["width"] / 2, thumb["y"] + thumb["height"] / 2)
    mac.mouse.down()
    middle = track["y"] + track["height"] / 2
    mac.mouse.move(thumb["x"] + thumb["width"] / 2, middle, steps=10)
    bubble = mac.locator(".scrubber-bubble")
    page_label = bubble.inner_text().replace("\n", "").split("/")
    shown = int(page_label[0].strip())
    assert page_label[1].strip() == str(PAGES)
    assert 13 <= shown <= 18
    assert mac.evaluate(CURRENT) == shown - 1  # 页面跟着走：屏幕正中就是气泡上那一页
    mac.mouse.up()
    # 松手停在那一页的顶上
    mac.wait_for_function(f"(i) => Math.abs(({PAGE_TOP})(i) - 16) < 2", arg=shown - 1)
    assert mac.locator(".scrubber.dragging").count() == 0


def test_clicking_the_track_jumps_to_the_last_page(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    track = box(mac, SCRUBBER)
    mac.mouse.move(1200 - 6, track["y"] + 40)
    wait_state(mac, "active")
    mac.mouse.click(track["x"] + track["width"] / 2, track["y"] + track["height"] - 4)
    mac.wait_for_function(f"(i) => ({CURRENT})() === i || Math.abs(({PAGE_TOP})(i) - 16) < 2", arg=PAGES - 1)


def test_a_finger_drags_the_scrubber_on_the_ipad_without_writing(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    # 手指滚一下：细滑块出现
    finger_drag(ipad, (600, 500), (600, 380), steps=6)
    wait_state(ipad, "scroll")
    thumb = box(ipad, ".scrubber-hit")
    track = box(ipad, SCRUBBER)
    cx = thumb["x"] + thumb["width"] / 2
    finger_drag(ipad, (cx, thumb["y"] + thumb["height"] / 2), (cx, track["y"] + track["height"] * 0.75), steps=12)
    ipad.wait_for_function(f"() => ({CURRENT})() >= 20")
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 0
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 0


def test_the_pencil_writes_at_the_right_edge_while_the_scrubber_is_hidden(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    ipad.evaluate("() => { whiteboard.viewport.scale = 3; whiteboard.clampView(); whiteboard.renderer.requestFull(); }")
    wait_state(ipad, "hidden", timeout=4000)
    track = box(ipad, SCRUBBER)
    x = track["x"] + track["width"] / 2
    y = track["y"] + track["height"] / 2
    draw(ipad, [(x, y), (x - 4, y + 30), (x, y + 60)])
    wait_strokes(mac, 1)


def test_only_document_boards_of_several_pages_have_a_scrubber(browser, server):
    mac = open_mac(browser, server.port)
    assert mac.locator(SCRUBBER).is_hidden()  # 大白板
    doc_board(mac, pages=1)
    assert mac.locator(SCRUBBER).is_hidden()
    doc_board(mac, pages=3)
    mac.wait_for_selector(SCRUBBER, state="attached")
    mac.wait_for_function("() => !document.querySelector('.scrubber').hidden")

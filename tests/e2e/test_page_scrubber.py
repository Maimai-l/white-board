"""文档白板右边的页码滑条（docs/backlog/015-page-scrubber.md）。

用户在 iPad 和 Mac 上读几百页的 PDF，要翻到后面某一页只能一路滚。右边一条滑条：滚动时出现、
停下淡出；指针移到右边或按住滑块出现轨道；拖动时页面跟着走、旁边显示页码；点轨道跳过去。
这里像用户一样滚、指、拖、点，只看屏幕上的滑条、页码和停在哪一页。
"""

from __future__ import annotations

from e2e.support import draw, enable_pk_picker, finger_drag, open_mac, open_touch, pencil_stroke, wait_strokes
from e2e.test_doc_pages import DOC, drop_pdf, wait_doc

PAGES = 30
SCRUBBER = ".scrubber"
STATE = "() => document.querySelector('.scrubber').dataset.state"
# 屏幕正中那一页（从 0 起）
CURRENT = "() => whiteboard.currentDocPage().index"
# 某一页上沿在屏幕上的 y（相对画布）
# 视野停住了：连着几帧没动（手指松开后的惯性滑完）
STILL = """() => {
  const v = whiteboard.viewport;
  const now = `${v.x} ${v.y} ${v.scale}`;
  const still = window.__lastView === now;
  window.__lastView = now;
  return still && !whiteboard.input.momentum;
}"""
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


def test_the_pencil_writes_over_the_visible_scrubber_instead_of_turning_pages(browser, server):
    """用户反馈：iPad 上 Pencil 会误触右边的滑条，很烦。滑块刚滚出来时，贴着右边写字的 Pencil
    落在滑块上，原来会拖动翻页。现在滑条只认手指，Pencil 落在上面照常写字，页面不动。"""
    mac = open_mac(browser, server.port)
    doc_board(mac)
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    finger_drag(ipad, (600, 500), (600, 380), steps=6)  # 手指滚一下：细滑块出现
    wait_state(ipad, "scroll")
    ipad.wait_for_function(STILL)  # 手指松开后的惯性滑完
    view = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    thumb = box(ipad, ".scrubber-hit")
    x = thumb["x"] + thumb["width"] / 2
    y = thumb["y"] + thumb["height"] / 2
    pencil_stroke(ipad, [(x, y + i * 12) for i in range(12)])  # 从滑块上往下写一笔
    wait_strokes(mac, 1)
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == view
    assert ipad.locator(".scrubber.dragging").count() == 0


MENU_ITEM = '.menu-layer:not(.leaving) .menu-item:has-text("页码滑条")'


def test_the_more_menu_turns_the_scrubber_off_and_the_mac_remembers(browser, server):
    """用户：加一个开关，可以关闭这个滑动条。和用手指绘图、形状识别一样在更多菜单里，只管这台设备，记住设置。"""
    mac = open_mac(browser, server.port)
    doc_board(mac)
    mac.click('#toolbar button[title="更多"]')
    item = mac.locator(MENU_ITEM)
    assert item.get_attribute("aria-checked") == "true"  # 默认打开
    item.click()
    mac.wait_for_function("() => document.querySelector('.scrubber').hidden")
    # 关了之后滚动、指到右边都不出现
    mac.mouse.move(600, 400)
    mac.mouse.wheel(0, 600)
    mac.mouse.move(1200 - 6, 300)
    assert mac.locator(SCRUBBER).is_hidden()
    # 这台设备记住：重新打开还是关着
    mac.reload()
    mac.wait_for_function(f"() => !!({DOC})()")
    assert mac.locator(SCRUBBER).is_hidden()
    mac.click('#toolbar button[title="更多"]')
    assert mac.locator(MENU_ITEM).get_attribute("aria-checked") == "false"
    mac.locator(MENU_ITEM).click()
    mac.wait_for_function("() => !document.querySelector('.scrubber').hidden")


def test_the_ipad_picker_turns_the_scrubber_off_for_this_device_only(browser, server):
    mac = open_mac(browser, server.port)
    doc_board(mac)
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    ipad.click("#pk-host button[data-act='more']")
    row = ipad.locator("#pk-host .pk-pop [data-toggle='scrubber']")
    assert row.locator(".pk-switch").get_attribute("aria-checked") == "true"
    row.click()
    ipad.wait_for_function("() => document.querySelector('.scrubber').hidden")
    finger_drag(ipad, (600, 500), (600, 380), steps=6)  # 滚一下也不出现
    ipad.wait_for_timeout(300)  # 滑块要出现的话，滚动之后的这段时间里就出现了
    assert ipad.locator(SCRUBBER).is_hidden()
    assert mac.evaluate("() => !document.querySelector('.scrubber').hidden")  # Mac 上不受影响


def test_boards_without_pages_have_no_scrubber_switch(browser, server):
    mac = open_mac(browser, server.port)
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu-layer:not(.leaving) .menu")
    assert mac.locator(MENU_ITEM).count() == 0

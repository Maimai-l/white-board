"""导入 PDF 时选页、文档白板里插入和删除空白页（docs/backlog/014-import-pages.md）。

用户要批注几百页教材里的一段：拖进来先选页（例如 3-15），批注写不下时在两页之间加一页空白。
这里像用户一样拖文件、在对话框里选、右键（平板上长按）一页，只看页面上显示的和白板的内容。
"""

from __future__ import annotations

import base64
import io

import pytest

from e2e.support import TABLET_UA, enable_pk_picker, finger_hold, gone, new_page, open_mac, open_touch, wait_online

DROP_FILE = """([name, type, base64]) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, { type }));
  for (const kind of ["dragover", "drop"]) {
    window.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true }));
  }
}"""

DOC = "() => whiteboard.state.meta && whiteboard.state.meta.data && whiteboard.state.meta.data.doc"
SOURCES = "() => { const d = whiteboard.state.meta.data.doc; return d.src || d.pages.map((_p, i) => i); }"


def pdf_base64(pages):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


def drop_pdf(page, name, pages):
    page.evaluate(DROP_FILE, [name, "application/pdf", pdf_base64(pages)])


def wait_doc(page, before):
    page.wait_for_function(f"(was) => whiteboard.state.id !== was && !!({DOC})()", arg=before, timeout=20000)


def page_point(page, index, fraction=0.3):
    """第 ``index`` 页上一点的窗口坐标（先把那一页滚到屏幕中间）。"""
    return page.evaluate(
        """([index, fraction]) => {
          const sheets = whiteboard.state.layers.filter((l) => l.sheet);
          const layer = sheets[index];
          const y = layer.y + layer.height * fraction;
          const x = layer.x + layer.width / 2;
          const v = whiteboard.viewport;
          v.x = whiteboard.renderer.viewW / 2 - x * v.scale;
          v.y = whiteboard.renderer.viewH / 2 - y * v.scale;
          whiteboard.renderer.requestFull();
          const rect = whiteboard.input.stage.getBoundingClientRect();
          return [rect.left + whiteboard.renderer.viewW / 2, rect.top + whiteboard.renderer.viewH / 2];
        }""",
        [index, fraction],
    )


def test_dropping_a_long_pdf_lets_you_choose_pages_and_add_blank_pages(browser, server):
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "教材.pdf", 6)
    dialog = mac.wait_for_selector(".import-dialog")
    assert mac.locator(".import-dialog .export-card").count() == 6
    assert mac.locator(".import-dialog .lib-btn.primary").inner_text() == "导入 6 页"
    mac.wait_for_function("() => document.querySelector('.import-dialog .export-thumb-image')?.naturalWidth > 0")

    mac.fill(".import-dialog .export-range-field", "3-5")
    assert mac.locator(".import-dialog .export-count").inner_text() == "已选 3 页"
    mac.check('.import-dialog .export-radio:has-text("每页后面加一页空白") input')
    mac.click('.import-dialog .lib-btn.primary:has-text("导入 3 页")')
    wait_doc(mac, before)
    assert mac.evaluate(SOURCES) == [2, None, 3, None, 4, None]
    gone(mac, ".import-dialog")
    assert dialog is not None


def test_cancelling_the_import_dialog_creates_nothing(browser, server):
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    count = mac.evaluate("() => whiteboard.ui.boards.length")
    drop_pdf(mac, "教材.pdf", 3)
    mac.wait_for_selector(".import-dialog")
    mac.keyboard.press("Escape")
    gone(mac, ".import-dialog")
    assert mac.evaluate("() => whiteboard.state.id") == before
    assert mac.evaluate("() => whiteboard.ui.boards.length") == count


def test_a_one_page_pdf_needs_no_dialog(browser, server):
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "单页.pdf", 1)
    wait_doc(mac, before)
    assert mac.locator(".import-dialog").count() == 0


def test_right_clicking_a_page_inserts_and_deletes_blank_pages(browser, server):
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "讲义.pdf", 2)
    mac.wait_for_selector(".import-dialog")
    mac.click(".import-dialog .lib-btn.primary")
    wait_doc(mac, before)

    x, y = page_point(mac, 0)
    mac.mouse.click(x, y, button="right")
    menu = mac.wait_for_selector(".menu-layer:not(.leaving) .menu")
    labels = mac.locator(".menu-layer:not(.leaving) .menu-label").all_inner_texts()
    assert labels == ["在后面插入空白页", "在前面插入空白页"]  # 原件的页不能删
    mac.click('.menu-layer:not(.leaving) .menu-item:has-text("在后面插入空白页")')
    mac.wait_for_function(f"() => JSON.stringify(({SOURCES})()) === '[0,null,1]'")
    assert menu is not None

    # 空白页上铺的是白板的背景纹理：白板设置里有空白页的背景一段
    mac.click('#topbar button[title="白板设置"]')
    mac.wait_for_selector('.panel-settings .group-title:text-is("空白页的背景")')
    mac.keyboard.press("Escape")
    gone(mac, ".panel")

    x, y = page_point(mac, 1)
    mac.mouse.click(x, y, button="right")
    mac.wait_for_selector(".menu-layer:not(.leaving) .menu")
    mac.click('.menu-layer:not(.leaving) .menu-item:has-text("删除这一空白页")')
    mac.wait_for_selector('.dialog.ask:has-text("删除这一空白页")')
    mac.click('.dialog.ask button[title="删除"]')
    mac.wait_for_function(f"() => JSON.stringify(({SOURCES})()) === '[0,1]'")


def test_the_more_menu_adds_a_blank_page_after_the_page_in_view(browser, server):
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "讲义.pdf", 3)
    mac.wait_for_selector(".import-dialog")
    mac.click(".import-dialog .lib-btn.primary")
    wait_doc(mac, before)
    page_point(mac, 1)  # 第 2 页在屏幕中间
    mac.click('#toolbar button[title="更多"]')
    mac.click('.menu-layer:not(.leaving) .menu-item:has-text("在这一页后面插入空白页")')
    mac.wait_for_function(f"() => JSON.stringify(({SOURCES})()) === '[0,1,null,2]'")


def test_a_finger_held_on_a_page_opens_the_page_menu_on_a_tablet(browser, server):
    """用户反馈：iPad 上长按没法加页。真手指的触摸落在 #stage 里面的画布上，以前只认 #stage
    本身，所以真机上从来不弹（原来的测试直接往 #stage 上发事件，测不出来）。这里用真触摸事件。"""
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "讲义.pdf", 2)
    mac.wait_for_selector(".import-dialog")
    mac.click(".import-dialog .lib-btn.primary")
    wait_doc(mac, before)

    tablet = new_page(browser, user_agent=TABLET_UA, touch=True)
    tablet.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(tablet)
    tablet.wait_for_function(f"() => !!({DOC})()")
    x, y = page_point(tablet, 0)
    item = '.menu-layer:not(.leaving) .menu-item:has-text("在后面插入空白页")'
    finger_hold(tablet, x, y, 900, during=lambda: tablet.wait_for_selector(item, timeout=1000))
    # 松手之后菜单还在，页没有变：不能因为松手就选了菜单里的一项
    tablet.wait_for_timeout(400)  # 给补发的 click 留出时间，它要是会选中菜单项，就在这段时间里
    assert tablet.locator(item).count() == 1
    assert mac.evaluate(SOURCES) == [0, 1]
    tablet.tap(item)
    mac.wait_for_function(f"() => JSON.stringify(({SOURCES})()) === '[0,null,1]'")


def test_the_ipad_picker_more_menu_adds_a_blank_page(browser, server):
    """iPad 上工具栏是笔具盘：它的更多菜单里也有这一项。"""
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "讲义.pdf", 2)
    mac.wait_for_selector(".import-dialog")
    mac.click(".import-dialog .lib-btn.primary")
    wait_doc(mac, before)

    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    page_point(ipad, 0)
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-wb='blank']")
    mac.wait_for_function(f"() => JSON.stringify(({SOURCES})()) === '[0,null,1]'")


def test_the_ipad_settings_button_opens_on_a_document_board(browser, server):
    """用户反馈：iPad 右上角的设置按钮点了没有任何反应。文档板没有空白页时面板里一段也没有，
    按钮就什么都不做。现在文档板上有空白页的背景这一段（加空白页之前就能选好）。"""
    mac = open_mac(browser, server.port)
    before = mac.evaluate("() => whiteboard.state.id")
    drop_pdf(mac, "讲义.pdf", 1)
    wait_doc(mac, before)

    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_function(f"() => !!({DOC})()")
    ipad.tap('#topright button[title="白板设置"]')
    ipad.wait_for_selector('.panel-settings .group-title:text-is("空白页的背景")')
    ipad.tap('.panel-settings .bg-opt[title="方格"]')
    mac.wait_for_function("() => whiteboard.state.meta.background.pattern === 'grid'")

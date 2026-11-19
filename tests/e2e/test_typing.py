"""输入框：iPad 上打得了字；在输入框里打字、按快捷键、用输入法都碰不到白板。"""

from __future__ import annotations

from e2e.support import draw, open_gallery, open_mac, open_touch, stroke_count, wait_strokes

TEXT_FIELDS_SELECTABLE = """
() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=file]), textarea')]
  .map((el) => {
    const style = getComputedStyle(el);
    return { key: el.className || el.type, select: style.webkitUserSelect || style.userSelect };
  })
"""

COMPOSING_ENTER = """
(selector) => {
  const field = document.querySelector(selector);
  field.focus();
  field.value = 'xian';
  field.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter', keyCode: 229, isComposing: true, bubbles: true, cancelable: true,
  }));
  return document.activeElement === field;
}
"""


def open_chooser_with_a_named_board(page):
    open_gallery(page)
    return page.wait_for_selector(".board-name")


def test_text_fields_can_take_typing_on_the_ipad(browser, server):
    """iPadOS 的 Safari 里，-webkit-user-select: none 的输入框弹得出键盘，却打不进字。

    页面为了挡住选择 / 放大镜手势，给所有元素一刀切设了 user-select: none，
    输入框也跟着被关掉了——白板选择界面的搜索框、改名框在 iPad 上都打不了字。
    Chromium 和桌面 WebKit 不按这条规则办事，所以只能检查样式本身。
    """
    ipad = open_touch(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    open_gallery(ipad)
    ipad.wait_for_selector(".board-search")
    fields = ipad.evaluate(TEXT_FIELDS_SELECTABLE)
    assert fields, "白板选择界面里应该有输入框"
    blocked = [field for field in fields if field["select"] == "none"]
    assert not blocked, f"这些输入框在 iPad 上打不了字：{blocked}"

    # 照用户的做法点一下再打字，而不是 fill() 直接塞值
    ipad.click(".board-search")
    ipad.keyboard.type("查无此板")
    ipad.wait_for_selector(".gallery-empty")
    assert ipad.evaluate("() => document.querySelector('.board-search').value") == "查无此板"


def test_shortcuts_do_not_reach_the_board_while_typing(browser, server):
    """在改名框里按 ⌘Z 撤销的是打的字，不能把白板上的上一笔也撤掉；⌘+ ⌘- 也不能缩放白板。"""
    mac = open_mac(browser, server.port)
    draw(mac, [(200, 200), (260, 260), (320, 210)], pointer_type="mouse")
    wait_strokes(mac, 1)
    scale = mac.evaluate("() => whiteboard.viewport.scale")

    field = open_chooser_with_a_named_board(mac)
    field.click()
    mac.keyboard.type("第三章")
    # 一个一个按、一个一个查：连着按的话 ⌘Z 和 ⌘Y 会互相抵消，看不出问题
    for combo in ("Control+z", "Meta+z", "Control+y", "Control+Shift+z", "Control+=", "Control+-", "Control+0"):
        mac.keyboard.press(combo)
        assert stroke_count(mac) == 1, combo
        assert mac.evaluate("() => whiteboard.undoStack.length") == 1, combo
        assert mac.evaluate("() => whiteboard.viewport.scale") == scale, combo

    # 输入框外面照旧生效
    mac.keyboard.press("Escape")
    mac.click('.gallery-head button[title="关闭"]')
    mac.keyboard.press("Control+z")
    wait_strokes(mac, 0)


def test_space_typed_into_a_field_does_not_turn_the_mouse_into_a_pan(browser, server):
    """在输入框里按空格不能让画布进入「按住空格拖动」的状态。"""
    mac = open_mac(browser, server.port)
    field = open_chooser_with_a_named_board(mac)
    field.click()
    mac.keyboard.down(" ")
    assert mac.evaluate("() => whiteboard.input.spaceHeld") is False
    mac.keyboard.up(" ")


def test_enter_that_picks_an_ime_candidate_does_not_commit_the_name(browser, server):
    """用拼音输入法时，回车是选候选词，不能把打了一半的名字当成最终结果提交。"""
    mac = open_mac(browser, server.port)
    open_chooser_with_a_named_board(mac)
    assert mac.evaluate(COMPOSING_ENTER, ".board-name") is True
    mac.click(".board-folder")
    mac.wait_for_selector(".folder-new")
    assert mac.evaluate(COMPOSING_ENTER, ".folder-new") is True
    assert mac.query_selector(".dialog.folders") is not None
    assert mac.evaluate("() => whiteboard.ui.folders") == []

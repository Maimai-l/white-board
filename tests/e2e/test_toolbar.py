"""普通工具栏、弹窗、背景设置，以及触摸设备上的样式。笔具盘在 test_picker.py。"""

from __future__ import annotations

from e2e.support import (
    board_menu,
    draw,
    menu_pick,
    more_pick,
    new_board,
    open_gallery,
    open_mac,
    open_pages,
    open_touch,
    reload,
    stroke_count,
    wait_strokes,
)


def test_toolbar_still_responds_to_taps(browser, server):
    """挡掉触摸默认行为之后，iOS 仍然要能在按钮上合成 click。"""
    ipad = open_touch(browser, server.port)
    ipad.tap('button[title="橡皮擦"]')
    assert ipad.evaluate("() => whiteboard.tool.tool") == "eraser"
    ipad.tap('button[title="钢笔"]')
    assert ipad.evaluate("() => whiteboard.tool.tool") == "pen"
    ipad.tap('#toolbar button[title="更多"]')
    ipad.tap('.menu .menu-item:has-text("手指书写")')
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is True


def test_the_toolbar_can_move_to_the_left(browser, server):
    """底部那条够不着时可以竖到左边（更多菜单里），并且记在本机。"""
    ipad = open_touch(browser, server.port)

    def dock():
        return ipad.evaluate("() => document.documentElement.dataset.toolbar")

    def toolbar_box():
        return ipad.locator("#toolbar").bounding_box()

    assert dock() == "bottom"
    more_pick(ipad, "工具栏移到左侧")
    assert dock() == "left"
    box = toolbar_box()
    assert box["x"] < 40 and box["height"] > box["width"]

    # 颜色面板改成往右开，不会盖住工具栏，也不会跑到屏幕外面
    ipad.click('button[title="颜色与粗细"]')
    left, bottom = ipad.evaluate(
        "() => { const r = document.querySelector('.popover').getBoundingClientRect(); return [r.left, r.bottom]; }"
    )
    assert left >= box["x"] + box["width"]
    assert bottom <= ipad.evaluate("() => innerHeight")

    reload(ipad)
    assert dock() == "left"  # 换个位置之后记得住

    more_pick(ipad, "工具栏移到底部")
    assert dock() == "bottom"


def test_the_old_top_position_becomes_the_left_side(browser, server):
    """老版本存的是贴上边：顶上现在是标题栏，升级上来换到左边。"""
    ipad = open_touch(browser, server.port)
    ipad.evaluate("() => localStorage.setItem('whiteboard.toolbar', 'top')")
    reload(ipad)
    assert ipad.evaluate("() => document.documentElement.dataset.toolbar") == "left"


def test_classic_toolbar_still_shares_one_colour(browser, server):
    """没开笔具盘时保持老行为：改颜色是所有笔一起改。"""
    ipad = open_touch(browser, server.port)
    ipad.click('button[title="颜色与粗细"]')
    ipad.click(".popover .swatch:nth-child(4)")
    picked = ipad.evaluate("() => whiteboard.tool.color")
    ipad.click('button[title="马克笔"]')
    assert ipad.evaluate("() => whiteboard.tool.color") == picked


def test_classic_toolbar_width_changes_the_pen(browser, server):
    ipad = open_touch(browser, server.port)
    before = ipad.evaluate("() => whiteboard.tool.width")
    ipad.click('button[title="颜色与粗细"]')
    options = ipad.query_selector_all(".popover .width-opt")
    assert len(options) >= 2
    target = next(o for o in options if "active" not in (o.get_attribute("class") or ""))
    target.click()
    assert ipad.evaluate("() => whiteboard.tool.width") != before
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    wait_strokes(ipad, 1)
    assert ipad.evaluate("() => whiteboard.state.strokes[0].w") > 0


def test_clear_asks_before_it_wipes_the_board(browser, server):
    """垃圾桶点开要说清楚问的是什么，别让人对着一个图标猜。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 340)])
    wait_strokes(mac, 1)

    more_pick(ipad, "清空白板…")
    ipad.wait_for_selector(".dialog.ask")
    assert ipad.locator(".dialog.ask .ask-text").inner_text() == "清空这块白板"
    ipad.click('.dialog button[title="取消"]')
    assert stroke_count(ipad) == 1  # 取消就是什么都没发生

    more_pick(ipad, "清空白板…")
    ipad.click('.dialog button[title="清空"]')
    wait_strokes(mac, 0)

    # 白板库里移到回收站不用问：回收站里还能恢复
    new_board(mac, "笔记")
    open_gallery(mac)
    board_menu(mac)
    menu_pick(mac, "移到回收站")
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.trashed)")
    assert mac.query_selector(".dialog.ask") is None


def test_dialog_stays_solid_while_the_scrim_darkens(browser, server):
    """弹窗不能跟着那层暗底一起变透明，否则暗底直接透过来，看着像弹窗自己也黑了。

    暗底一旦用 opacity 淡入，它就成了子元素的 backdrop root：弹窗既跟着它一起
    半透明，磨砂又只采样得到这层暗底。所以暗底只淡背景色，弹窗的透明度在开头
    那一小段里就走完。这里把动画拉长，在中途去量。
    """
    mac = open_mac(browser, server.port)
    values = mac.evaluate(
        """async () => {
          document.documentElement.style.setProperty('--motion', '1200ms linear');
          whiteboard.ui.confirmClear();
          await new Promise(r => setTimeout(r, 600));  // 动画正中间
          const scrim = document.querySelector('.scrim');
          const dialog = document.querySelector('.dialog.ask');
          return {
            scrim: getComputedStyle(scrim).opacity,
            dialog: getComputedStyle(dialog).opacity,
            shade: getComputedStyle(scrim).backgroundColor,
          };
        }"""
    )
    assert values["scrim"] == "1"  # 暗底自己不透明，只是背景色在变深
    assert values["dialog"] == "1"  # 弹窗早就到位了
    assert values["shade"] not in ("rgba(0, 0, 0, 0)", "transparent")  # 确实在变深


def test_touch_devices_get_no_hover_styling(browser, server):
    """iPadOS 上点过的元素会一直挂着 :hover，看着像被按住。所有 hover 都关在
    `@media (hover: hover)` 里，触摸设备不给这套反馈。"""
    ipad = open_touch(browser, server.port)
    ipad.wait_for_selector("#topright")
    rules = ipad.evaluate(
        """() => {
          const bare = [];
          for (const sheet of document.styleSheets) {
            let list;
            try { list = sheet.cssRules; } catch (err) { continue; }
            for (const rule of list) {
              if (rule.selectorText && rule.selectorText.includes(':hover')) bare.push(rule.selectorText);
            }
          }
          return bare;
        }"""
    )
    assert rules == [], f"这些 :hover 没有关在 hover 媒体查询里：{rules}"


def test_background_can_be_changed_from_the_ipad(browser, server):
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.tap('#topright button[title="白板设置"]')
    ipad.tap('.bg-opt[title="点阵"]')
    for page in (ipad, mac):
        page.wait_for_function("() => whiteboard.state.meta.background.pattern === 'dots'")

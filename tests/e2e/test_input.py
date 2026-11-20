"""输入：Pencil 和手指的分工、手掌屏蔽、系统打断、触控板和双指手势、平移缩放。"""

from __future__ import annotations

from e2e.support import (
    draw,
    fire,
    open_mac,
    open_pages,
    open_touch,
    stroke_count,
    view,
    wait_strokes,
)


# 手掌屏蔽的时间窗（抬笔后 PALM_GRACE）过去了
PALM_GONE = "() => !whiteboard.input.penActive()"


def test_touch_pans_by_default_and_only_pencil_draws(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (360, 340), (420, 300)], pointer_type="pen", pressure=0.8)
    wait_strokes(ipad, 1)

    # 默认只认 Pencil：手指只平移，不留笔迹（等过了手掌屏蔽的时间窗）
    ipad.wait_for_function(PALM_GONE)
    before = view(ipad)
    draw(ipad, [(500, 500), (560, 560), (620, 600)], pointer_type="touch", pointer_id=7)
    # 指针事件是同步处理的：手指要是画了线，抬起时就已经提交了
    assert stroke_count(ipad) == 1
    assert view(ipad) != before, "手指应该把画布拖动了"

    # 打开「手指书写」之后手指才画线，并且这个选择会被记住
    ipad.click('button[title="手指书写"]')
    ipad.wait_for_function("() => whiteboard.input.fingerDraw === true")
    draw(ipad, [(300, 600), (380, 640), (460, 600)], pointer_type="touch", pointer_id=8)
    wait_strokes(ipad, 2)
    wait_strokes(mac, 2)
    assert ipad.evaluate("() => localStorage.getItem('whiteboard.fingerDraw')") == "1"

    ipad.click('button[title="手指书写"]')
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is False


def test_palm_is_ignored_while_pencil_writes(browser, server):
    """Pencil 写字时手掌落在屏幕上，既不能画线也不能把画布拖走。"""
    ipad = open_touch(browser, server.port)
    before = view(ipad)

    fire(ipad, "down", 400, 400, "pen", 1, 0.7)
    for x in range(410, 520, 10):
        fire(ipad, "move", x, 420, "pen", 1, 0.7)
    # 手掌：落下并滑动
    for step, x in enumerate(range(300, 500, 40)):
        fire(ipad, "down" if step == 0 else "move", x, 700, "touch", 9, 0.5)
    fire(ipad, "up", 500, 700, "touch", 9, 0)
    fire(ipad, "up", 520, 420, "pen", 1, 0)
    wait_strokes(ipad, 1)

    assert view(ipad) == before
    assert stroke_count(ipad) == 1

    # 抬笔一会儿之后，手指恢复平移
    ipad.wait_for_function(PALM_GONE)
    draw(ipad, [(300, 600), (420, 640)], pointer_type="touch", pointer_id=11)
    assert view(ipad) != before


def test_palm_landing_before_the_pencil_does_not_shift_the_canvas(browser, server):
    """手掌通常比笔尖先落屏：它拖出来的位移要在笔落下时撤回去。"""
    ipad = open_touch(browser, server.port)
    before = view(ipad)

    fire(ipad, "down", 300, 700, "touch", 21, 0.5)
    for x in range(320, 440, 20):
        fire(ipad, "move", x, 700, "touch", 21, 0.5)
    assert view(ipad) != before, "手掌确实先把画布拖动了"

    fire(ipad, "down", 500, 400, "pen", 1, 0.7)
    assert view(ipad) == before
    for x in range(510, 600, 10):
        fire(ipad, "move", x, 420, "pen", 1, 0.7)
    fire(ipad, "up", 600, 420, "pen", 1, 0)
    wait_strokes(ipad, 1)
    assert view(ipad) == before


def test_stroke_resumes_after_a_system_cancel(browser, server):
    """系统中途取消指针、但笔还按着时，后半截不能丢。"""
    mac, ipad = open_pages(browser, server.port)
    fire(ipad, "down", 300, 300, "pen", 5)
    for x in (320, 340, 360):
        fire(ipad, "move", x, 320, "pen", 5)
    fire(ipad, "cancel", 360, 320, "pen", 5)
    # 笔仍然按在屏幕上，继续移动
    for x in (380, 400, 420, 440):
        fire(ipad, "move", x, 330, "pen", 5)
    fire(ipad, "up", 440, 330, "pen", 5, 0)

    wait_strokes(ipad, 2)
    tail = ipad.evaluate("() => whiteboard.state.strokes[1].p.length / 3")
    assert tail >= 3, "被取消之后的那一段应该接着画出来"
    assert ipad.evaluate("() => whiteboard.input.stats.cancel") == 1
    wait_strokes(mac, 2)


def test_repeated_pen_interruptions_show_a_hint(browser, server):
    """页面拦不住系统级的随手写，只能在连续被打断时提示用户去关掉它。"""
    ipad = open_touch(browser, server.port)
    for i in range(3):
        pointer = 30 + i
        fire(ipad, "down", 300, 300 + i * 40, "pen", pointer)
        fire(ipad, "move", 340, 320 + i * 40, "pen", pointer)
        fire(ipad, "move", 380, 320 + i * 40, "pen", pointer)
        fire(ipad, "cancel", 380, 320 + i * 40, "pen", pointer)

    notice = ipad.locator(".notice")
    notice.wait_for(timeout=3000)
    # 两句各占一行：出了什么事，怎么办
    assert notice.inner_text().split("\n") == ["笔迹被随手写打断", "在 iPad 设置的 Apple Pencil 中关闭随手写"]
    assert ipad.evaluate("() => whiteboard.input.stats.penCancel") == 3
    notice.click()
    assert notice.count() == 0


def test_stage_blocks_ios_selection_gestures(browser, server):
    """iPadOS 的选择 / 查词手势必须被挡掉，否则写快了会丢笔。"""
    ipad = open_touch(browser, server.port)
    blocked = ipad.evaluate(
        """() => {
          const stage = document.getElementById('stage');
          const results = {};
          for (const type of ['touchstart', 'touchmove', 'selectstart', 'dragstart']) {
            const event = new Event(type, { bubbles: true, cancelable: true });
            stage.dispatchEvent(event);
            results[type] = event.defaultPrevented;
          }
          results.touchAction = getComputedStyle(stage).touchAction;
          return results;
        }"""
    )
    assert blocked["touchstart"] and blocked["touchmove"]
    assert blocked["selectstart"] and blocked["dragstart"]
    assert blocked["touchAction"] == "none"

    # -webkit-touch-callout 只有 Safari 认，这里只能确认样式确实发出去了
    css = ipad.evaluate("async () => (await fetch('/static/css/app.css')).text()")
    assert "-webkit-touch-callout: none" in css


GESTURE = """([where, scales]) => {
  const node = document.querySelector(where);
  const fire = (type, scale) => node.dispatchEvent(Object.assign(
    new Event(type, { bubbles: true, cancelable: true }),
    { scale, rotation: 0, clientX: 600, clientY: 400 }));
  fire('gesturestart', 1);
  for (const s of scales) fire('gesturechange', s);
  fire('gestureend', scales[scales.length - 1]);
}"""

CTRL_WHEEL = """(deltaY) => document.getElementById('stage').dispatchEvent(new WheelEvent('wheel', {
  deltaY, ctrlKey: true, clientX: 600, clientY: 400, bubbles: true, cancelable: true }))"""


def test_trackpad_pinch_zooms(browser, server):
    """触控板捏合：WebKit 发的是 gesture 事件，Chrome 发的是 ctrl + wheel，两条都要认。"""
    mac, ipad = open_pages(browser, server.port)
    read = "() => whiteboard.viewport.scale"
    reset = "() => { whiteboard.viewport.scale = 1; whiteboard.renderer.requestFull(); }"

    # WebKit 那套：scale 是从手势开始算起的累计倍数
    mac.evaluate(reset)
    mac.evaluate(GESTURE, ["#stage", [1.5, 2]])
    assert abs(mac.evaluate(read) - 2) < 0.05

    # Chrome 那套：ctrl + wheel，一格鼠标滚轮（deltaY 100）会被夹住，不会一下缩掉三分之二
    mac.evaluate(reset)
    mac.evaluate(CTRL_WHEEL, 100)
    after = mac.evaluate(read)
    assert 0.7 < after < 0.85, after

    mac.evaluate(reset)
    mac.evaluate(CTRL_WHEEL, -6)
    assert mac.evaluate(read) > 1  # 捏开就是放大

    # 指针停在工具栏上时不动画布（浏览器的整页缩放仍然被拦下）
    mac.evaluate(reset)
    mac.evaluate(GESTURE, ["#toolbar", [2]])
    assert mac.evaluate(read) == 1

    # iPad 上双指是自己用 pointer 事件做的，WebKit 还会同时发 gesture，别缩两次
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; whiteboard.input.gesture = {}; }")
    ipad.evaluate(GESTURE, ["#stage", [2]])
    assert ipad.evaluate(read) == 1
    ipad.evaluate("() => { whiteboard.input.gesture = null; }")


def test_two_fingers_pinch_and_pan_on_a_touch_device(browser, server):
    """两根手指分开是放大、一起挪是平移，都不留笔迹。"""
    ipad = open_touch(browser, server.port)
    scale = "() => whiteboard.viewport.scale"
    start = ipad.evaluate(scale)

    def fingers(steps):
        (a0, b0) = steps[0]
        fire(ipad, "down", *a0, "touch", 21, 0.5)
        fire(ipad, "down", *b0, "touch", 22, 0.5)
        for a, b in steps[1:]:
            fire(ipad, "move", *a, "touch", 21, 0.5)
            fire(ipad, "move", *b, "touch", 22, 0.5)
        a, b = steps[-1]
        fire(ipad, "up", *a, "touch", 21, 0)
        fire(ipad, "up", *b, "touch", 22, 0)

    # 分开：放大
    fingers([((500 - d, 400), (600 + d, 400)) for d in range(0, 121, 20)])
    zoomed = ipad.evaluate(scale)
    assert zoomed > start * 1.3

    # 一起往右下挪：平移，比例不变
    before = view(ipad)
    fingers([((400 + d, 400 + d), (500 + d, 400 + d)) for d in range(0, 101, 20)])
    assert view(ipad) != before
    assert abs(ipad.evaluate(scale) - zoomed) < zoomed * 0.05
    assert stroke_count(ipad) == 0


def test_mac_pans_with_the_middle_button_space_and_the_wheel(browser, server):
    mac = open_mac(browser, server.port)

    def drag(button="left"):
        mac.mouse.move(400, 400)
        mac.mouse.down(button=button)
        for i in range(1, 6):
            mac.mouse.move(400 + i * 20, 400 + i * 10)
        mac.mouse.up(button=button)

    before = view(mac)
    drag("middle")
    after_middle = view(mac)
    assert after_middle != before

    mac.keyboard.down(" ")
    drag("left")
    mac.keyboard.up(" ")
    after_space = view(mac)
    assert after_space != after_middle

    mac.mouse.move(500, 500)
    mac.mouse.wheel(0, 120)
    mac.wait_for_function(
        "(was) => JSON.stringify(__t.view()) !== JSON.stringify(was)", arg=after_space
    )
    assert stroke_count(mac) == 0  # 上面这些都只是平移，一笔都没画


def test_zoom_buttons_and_shortcuts(browser, server):
    mac = open_mac(browser, server.port)
    draw(mac, [(200, 200), (300, 260)], pointer_type="mouse")
    wait_strokes(mac, 1)
    scale = "() => whiteboard.viewport.scale"
    start = mac.evaluate(scale)
    mac.click('button[title="放大"]')
    bigger = mac.evaluate(scale)
    assert bigger > start
    mac.click('button[title="缩小"]')
    assert mac.evaluate(scale) < bigger
    before_key = mac.evaluate(scale)
    mac.keyboard.press("Control+=")
    zoomed = mac.evaluate(scale)
    assert zoomed > before_key
    mac.keyboard.press("Control+-")
    assert mac.evaluate(scale) < zoomed
    mac.keyboard.press("Control+=")
    mac.keyboard.press("Control+=")
    mac.keyboard.press("Control+0")  # 适应内容：回到把笔迹放进窗口的那个比例
    fitted = mac.evaluate(scale)
    mac.click('button[title="放大"]')
    mac.click('button[title="回到内容"]')
    assert abs(mac.evaluate(scale) - fitted) < 1e-6

    # ⌘Y 重做
    mac.keyboard.press("Control+z")
    wait_strokes(mac, 0)
    mac.keyboard.press("Control+y")
    wait_strokes(mac, 1)

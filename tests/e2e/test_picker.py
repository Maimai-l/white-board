"""iPad 上的笔具盘：只在 iPad 上出现、驱动白板、停靠、收起、拖动和甩的手感。"""

from __future__ import annotations

from e2e.support import (
    DESKTOP_SAFARI_UA,
    GRIP,
    IPAD_UA,
    PICKER,
    TABLET_UA,
    center,
    draw,
    drag,
    enable_pk_picker,
    fling,
    gallery_with_folder,
    hold_still,
    minimize_picker,
    new_page,
    nudge,
    open_pages,
    open_touch,
    picker_top,
    pk_form,
    reload,
    settled_center,
    wait_online,
    wait_still,
    wait_strokes,
)

# 手指拖动时每一步之间隔多久：模拟手慢慢移动的节奏
HAND_STEP_MS = 60

UNDO = "#pk-host button[data-act='undo']"
REDO = "#pk-host button[data-act='redo']"
MORE = "#pk-host button[data-act='more']"


def pk_tool(page):
    return page.evaluate("() => whiteboard.tool")


def wait_state(page, state, timeout=4000):
    page.wait_for_function("(s) => whiteboard.ui.pk.state === s", arg=state, timeout=timeout)


# ----------------------------------------------------------------- 谁有笔具盘


def test_picker_is_only_on_ipad(browser, server):
    """笔具盘只给 iPad；别的设备（Mac 窗口、安卓平板）一律是普通工具栏，两条之间没有开关。"""

    def open_as(user_agent, touch, role, legacy_off=False, max_touch=False):
        page = new_page(browser, user_agent=user_agent, touch=touch)
        if legacy_off:
            # 早先版本里关掉过笔具盘的机器，本机还留着这个记录
            page.add_init_script("try { localStorage.setItem('whiteboard.picker', '0'); } catch (e) {}")
        if max_touch:
            page.add_init_script("Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 })")
        page.goto(f"http://127.0.0.1:{server.port}/?role={role}")
        wait_online(page)
        return page

    # iPad：默认就是笔具盘，以前关掉过的记录不再起作用
    for ipad in (open_as(IPAD_UA, True, "ipad"), open_as(IPAD_UA, True, "ipad", legacy_off=True)):
        ipad.wait_for_selector(PICKER, timeout=20000)
        assert ipad.is_hidden("#toolbar")
        ipad.click(MORE)
        assert ipad.query_selector("#pk-host .pk-pop [data-wb='leave']") is None

    # iPadOS 的 Safari 默认报桌面版 UA（Macintosh），靠有触摸点认出来
    open_as(DESKTOP_SAFARI_UA, True, "ipad", max_touch=True).wait_for_selector(PICKER, timeout=20000)

    # 安卓平板和 Mac 窗口：普通工具栏，颜色面板里也没有切换的开关
    for page in (open_as(TABLET_UA, True, "ipad"), open_as(DESKTOP_SAFARI_UA, False, "mac")):
        page.wait_for_selector("#toolbar", state="visible")
        assert page.evaluate("() => whiteboard.ui.picker") is False
        assert page.query_selector("#pk-host") is None
        page.click('button[title="颜色与粗细"]')
        assert page.locator(".popover input[type=checkbox]").count() == 0


def test_ipad_keeps_the_board_entries_when_the_picker_hides_the_toolbar(browser, server):
    """iPad 开了笔具盘之后，管理白板、白板设置、导出仍然要有入口。

    笔具盘一挂上，普通工具栏整条 `.hidden`。清空在笔具盘的「更多」里，连接状态点
    在左上角、不属于工具栏，这两项不受影响；剩下三项只剩右上角这一组。样式照
    iOS 的做法：同一条磨砂底，图标用强调色。
    """
    ipad = open_touch(browser, server.port, picker=True)
    # 笔具盘是动态载入的，挂上之后才会把普通工具栏收起来
    ipad.wait_for_selector(PICKER, timeout=20000)
    out = ipad.evaluate("""() => {
      const group = document.getElementById('topright');
      const toolbar = document.getElementById('toolbar');
      const dot = document.getElementById('status');
      return {
        条目: group ? [...group.querySelectorAll('button')].map((b) => b.title) : null,
        类名: group ? group.className : null,
        图标颜色: group ? getComputedStyle(group.querySelector('.icon-btn')).color : null,
        工具栏隐藏: toolbar.classList.contains('hidden'),
        状态点可见: !!dot && getComputedStyle(dot).display !== 'none',
      };
    }""")
    assert out["工具栏隐藏"], out           # 笔具盘模式，普通工具栏收起来了
    assert out["条目"] == ["白板", "白板设置", "导出"], out
    assert "tinted" in out["类名"], out
    assert out["状态点可见"], out           # 诊断和录制的入口还在
    # 图标用强调色，不是默认的灰色
    r, g, b = [int(v) for v in out["图标颜色"].replace("rgb(", "").rstrip(")").split(",")]
    assert (r, g, b) == (0, 122, 255), out


def test_the_corner_group_gets_out_of_the_pickers_way(browser, server):
    """笔具盘停到右上角那一组底下时，那一组让开；挪走再回来。"""
    ipad = open_touch(browser, server.port, picker=True)
    ipad.wait_for_selector(PICKER)
    ipad.wait_for_selector("#topright")
    shy = "() => document.querySelector('#topright').classList.contains('shy')"
    assert ipad.evaluate(shy) is False

    # 直接把笔具盘摆到右上角（拖动的手感由那份实现自己管，这里只看让位这件事）
    ipad.evaluate(
        """() => {
          const pk = document.querySelector('#pk-host .pk-picker');
          const box = document.querySelector('#topright').getBoundingClientRect();
          pk.style.left = box.left + 'px';
          pk.style.top = box.top + 'px';
          pk.style.width = '80px';
          pk.style.height = '80px';
        }"""
    )
    ipad.wait_for_function(shy)

    ipad.evaluate("() => { document.querySelector('#pk-host .pk-picker').style.top = '600px'; }")
    ipad.wait_for_function(f"() => !({shy})()")


def test_the_picker_does_not_float_over_the_board_chooser(browser, server):
    """笔具盘自带 z-index，白板选择界面必须压在它上面，否则一进来它还浮着。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector(PICKER)
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".gallery")

    # 选择界面是不透明的，盖住就等于看不见：量的是同一个点上谁在上面
    top = ipad.evaluate(
        """() => {
          const [x, y] = __t.center('#pk-host .pk-picker');
          const node = document.elementFromPoint(x, y);
          return !!(node && node.closest('.gallery'));
        }"""
    )
    assert top, "笔具盘还浮在白板选择界面上面"
    # 而且整条收起来了：它每帧都在画自己那层，留在上面就是一直闪
    host_display = "() => getComputedStyle(document.querySelector('#pk-host')).display"
    assert ipad.evaluate(host_display) == "none"

    # 列表被广播刷新时不重放淡入，否则别处一改名这边就闪一下
    gallery_with_folder(mac)
    ipad.wait_for_function("() => whiteboard.ui.folders.length === 1")
    assert ipad.evaluate("() => !!document.querySelector('.gallery.opening')") is False

    ipad.click('button[title="关闭"]')
    ipad.wait_for_function(f"() => ({host_display})() !== 'none'")


# ----------------------------------------------------------------- 驱动白板


def test_pencilkit_picker_drives_the_board(browser, server):
    """笔具盘（beta）：选工具、选颜色、改粗细都落到白板上，画布照常能写。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)

    assert ipad.is_hidden("#toolbar")  # 原来那条收起来了
    # 套索去掉了；尺子按钮是那份实现自己的，开关的是白板的尺子，见 test_ruler.py
    assert ipad.query_selector('#pk-host [data-tool="lasso"]') is None
    assert ipad.get_attribute('#pk-host [data-tool="ruler"]', "title") == "尺子"

    # 画布在工具盘下面，笔迹照常同步
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    wait_strokes(mac, 1)

    # 换成荧光笔（用的是那份实现里的「马克笔」造型）
    ipad.click('#pk-host [data-tool="marker"]')
    assert pk_tool(ipad)["tool"] == "highlighter"

    # 选一个预置色
    ipad.click('#pk-host .pk-sw[data-color="#157efa"]')
    assert pk_tool(ipad)["color"] == "#157efa"

    # 点已经选中的工具 → 粗细面板；换一档粗细
    ipad.click('#pk-host [data-tool="marker"]')
    ipad.wait_for_selector("#pk-host .pk-pop[data-open] .pk-tips")
    ipad.click("#pk-host .pk-tips button[data-size='4']")
    assert pk_tool(ipad)["width"] > 8

    # 每支笔各记各的：换回钢笔，颜色不是刚才那支的
    ipad.click('#pk-host [data-tool="pen"]')
    assert pk_tool(ipad)["tool"] == "pen"
    assert pk_tool(ipad)["color"] != "#157efa"


def test_pencilkit_picker_undo_and_clear(browser, server):
    """撤销按钮跟着白板的撤销栈亮灭，清屏在更多菜单里。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    assert ipad.is_disabled(UNDO)

    draw(ipad, [(320, 320), (400, 360), (480, 320)])
    wait_strokes(mac, 1)
    ipad.wait_for_function(f"() => !document.querySelector(\"{UNDO}\").disabled")
    ipad.click(UNDO)
    wait_strokes(mac, 0)

    # 更多菜单里的清屏
    draw(ipad, [(320, 420), (400, 460)])
    wait_strokes(mac, 1)
    ipad.click(MORE)
    ipad.click("#pk-host .pk-pop [data-wb='clear']")
    ipad.click('.dialog button[title="确定"]')
    wait_strokes(mac, 0)

    # 触摸设备上这条笔具盘就是唯一的工具栏，更多菜单里没有换回普通工具栏那一行
    ipad.click(MORE)
    assert ipad.query_selector("#pk-host .pk-pop [data-wb='leave']") is None


def test_picker_redo_and_the_more_menu_switches(browser, server):
    """笔具盘：撤销之后重做；「更多」里的用手指绘图和自动最小化都真的起作用。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    draw(ipad, [(320, 320), (400, 360), (480, 320)])
    wait_strokes(mac, 1)
    ipad.wait_for_function(f"() => !document.querySelector(\"{UNDO}\").disabled")
    ipad.click(UNDO)
    wait_strokes(mac, 0)
    ipad.wait_for_function(f"() => !document.querySelector(\"{REDO}\").disabled")
    ipad.click(REDO)
    wait_strokes(mac, 1)

    # 用手指绘图：打开之后手指落笔就是画线
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is False
    ipad.click(MORE)
    ipad.click("#pk-host .pk-pop [data-toggle='fingerDraws']")
    ipad.wait_for_function("() => whiteboard.input.fingerDraw === true")
    # 抬笔后 0.5 秒内手指一律当手掌不理（手掌屏蔽），等这个窗口过去再用手指写
    ipad.wait_for_function("() => !whiteboard.input.penActive()")
    draw(ipad, [(300, 520), (380, 560), (460, 520)], pointer_type="touch", pointer_id=5)
    wait_strokes(mac, 2)

    # 自动最小化：打开之后一落笔工具盘就收起来。点的必须是打开着的菜单里的开关：
    # 关上的面板只是透明，不能再接点击（见 test_a_closed_picker_panel_does_not_block_the_pencil）
    if not ipad.query_selector("#pk-host .pk-pop[data-open]"):
        ipad.click(MORE)
        ipad.wait_for_selector("#pk-host .pk-pop[data-open]")
    ipad.click("#pk-host .pk-pop[data-open] [data-toggle='autoMin']")
    ipad.wait_for_function("() => whiteboard.ui.pk.autoMin === true")
    ipad.click(MORE)
    ipad.wait_for_function("() => !document.querySelector('#pk-host .pk-pop[data-open]')")
    draw(ipad, [(300, 620), (380, 660)])
    wait_state(ipad, "minimized")


def test_picker_switches_eraser_mode(browser, server):
    """笔具盘里橡皮那个面板的「对象 / 像素」二选一：选了就生效，记在本机。"""
    ipad = open_touch(browser, server.port, picker=True)
    ipad.wait_for_selector(PICKER)
    ipad.click('#pk-host [data-tool="eraser"]')
    ipad.wait_for_function("() => whiteboard.tool.tool === 'eraser'")
    ipad.click('#pk-host [data-tool="eraser"]')  # 再点一次弹出面板
    ipad.wait_for_selector('#pk-host [data-emode="pixel"]')
    ipad.click('#pk-host [data-emode="pixel"]')
    ipad.wait_for_function("() => whiteboard.tool.eraserMode === 'pixel'")

    # 重新打开页面，选择还在
    reload(ipad)
    assert ipad.evaluate("() => whiteboard.tool.eraserMode") == "pixel"


# ----------------------------------------------------------------- 停靠和收起


def test_pencilkit_picker_docks_and_minimizes(browser, server):
    """拖握把换边，丢进角落缩成圆，点圆展开；位置本身由那份实现自己管。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    dataset = "() => { const d = document.querySelector('#pk-host .pk-picker').dataset; return [d.state, d.dock]; }"

    assert ipad.evaluate(dataset) == ["docked", "bottom"]
    drag(ipad, *settled_center(ipad), 60, 400)
    ipad.wait_for_function(f"() => ({dataset})()[1] === 'left'")

    drag(ipad, *settled_center(ipad), 1130, 770)
    ipad.wait_for_function(f"() => ({dataset})()[0] === 'minimized'")
    # 用手指点那个圆（鼠标会先悬停，一靠近就自己展开了，见 test_picker_expands_when_the_pointer_comes_close）。
    # 收起来是带动画的，等它停稳再点，否则点的是半路上的位置。
    wait_still(ipad)
    ipad.touchscreen.tap(*center(ipad, PICKER))
    ipad.wait_for_function(f"() => ({dataset})()[0] === 'docked'")


def test_picker_docks_to_the_top(browser, server):
    """原实现只有下 / 左 / 右，顶部停靠是加的：笔要转过来，面板要往下开。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)

    drag(ipad, *settled_center(ipad), 590, 24)
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'top'")
    # 贴边有动画，等它停下来再量
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top < 80",
        timeout=4000,
    )
    bar = picker_top(ipad)

    # 笔照旧立着，笔尖朝上（不翻转）
    assert "rotate" not in ipad.evaluate("() => whiteboard.ui.pk.el.pen.style.transform")
    # 握把还在上边缘（工具是往下沉的，下边让给它们）
    grip_y = ipad.evaluate(
        "() => { const p = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();"
        " const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
        " return (g.top - p.top) / p.height; }"
    )
    assert grip_y < 0.2

    # 粗细面板开在工具盘下面，箭头朝上
    ipad.click('#pk-host [data-tool="pen"]')
    ipad.wait_for_selector("#pk-host .pk-pop[data-open]")
    top, side = ipad.evaluate(
        "() => { const p = document.querySelector('#pk-host .pk-pop');"
        " return [p.getBoundingClientRect().top, p.dataset.side]; }"
    )
    assert side == "bottom" and top > bar


def test_picker_expands_while_dragging_to_an_edge(browser, server):
    """在触发区里停够 DOCK_DWELL_MS 就当场展开成那条边的样子，不用等松手；出了触发区立刻变回圆。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    wait_still(ipad)

    ipad.mouse.move(*center(ipad, GRIP))
    ipad.mouse.down()
    ipad.mouse.move(560, 410)  # 先拖到中间：不在任何触发区，立刻变成圆
    assert pk_form(ipad)[0] == "moving"

    ipad.mouse.move(560, 120)  # 进顶部触发区，但还没停够
    assert pk_form(ipad)[0] == "moving"
    wait_state(ipad, "docked", timeout=2000)
    assert pk_form(ipad) == ["docked", "top"]

    # 但它只是展开，没有自己跑到边上去：整条栏还跟在手底下（形态变化是带动画的，等它铺开）
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().width > 400",
        timeout=2000,
    )
    assert picker_top(ipad) > 40  # 还没贴到顶（贴上去是 20）

    ipad.mouse.move(60, 410)  # 换到左边触发区，重新计时
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'left'", timeout=2000)

    ipad.mouse.move(590, 410)  # 回到中间：出了触发区立刻缩成圆，不等
    assert pk_form(ipad)[0] == "moving"
    ipad.mouse.move(1130, 780)  # 角落：松手收起来
    hold_still(ipad)
    ipad.mouse.up()
    wait_state(ipad, "minimized")


def test_picker_only_snaps_to_the_edge_after_release(browser, server):
    """松手才真的贴到边上，拖的过程里它一直跟着手。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    wait_still(ipad)
    ipad.mouse.move(*center(ipad, GRIP))
    ipad.mouse.down()
    for spot in [(560, 410), (500, 200), (460, 150)]:
        ipad.mouse.move(*spot)
        # 这是手移动的节奏，不是在等什么：每步隔开一点，松手速度才低于 FLING_SPEED，算慢慢拖过去
        ipad.wait_for_timeout(HAND_STEP_MS)
    before = picker_top(ipad)
    ipad.mouse.up()
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top < 40",
        timeout=4000,
    )
    assert before > 60  # 松手之前没贴上去


def test_picker_fling_carries_past_the_release_point(browser, server):
    """甩出去有惯性：同一个松手点，慢放落到最近的底边，甩出去要按推算的停点落到顶边。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    wait_still(ipad)

    # 慢放：松手点略偏下半屏，落到底边
    drag(ipad, *settled_center(ipad), 590, 420)
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'bottom'", timeout=4000)

    # 同一个松手点，一路向上甩：惯性把停点推过顶部触发区
    fling(ipad, *settled_center(ipad), 590, 420)
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'top'", timeout=4000)


def test_picker_takes_a_tap_right_after_a_drag(browser, server):
    """拖完立刻点就得生效（vendor 是松手后 400ms 内一律吞掉），但轻轻一蹭不能误选工具。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    wait_still(ipad)

    # 在底边触发区里原地小幅拖一下，长条贴回原位；松手后马上点马克笔（白板里是荧光笔）
    nudge(ipad, *center(ipad, GRIP), [(0, step * 4) for step in range(1, 7)])
    ipad.touchscreen.tap(*center(ipad, '#pk-host [data-tool="marker"]'))
    ipad.wait_for_function("() => whiteboard.tool.tool === 'highlighter'", timeout=2000)

    # 但松手当下那一下要挡住：长条以手指为中心跟手，手指正压在橡皮那一格上
    ipad.evaluate("() => whiteboard.ui.pk.applyState({tool: 'pen', color: '#000000', sizeIndex: 1})")
    nudge(ipad, *center(ipad, GRIP), [(0, -8), (0, -16)], pause=HAND_STEP_MS // 2)
    wait_still(ipad)  # 长条贴回原位，这次松手连同它派生的点击都处理完了
    assert ipad.evaluate("() => whiteboard.tool.tool") == "pen"

    # 圆也一样：蹭一下不算点一下，不该展开
    minimize_picker(ipad, "br")
    wait_still(ipad)
    nudge(ipad, *center(ipad, PICKER), [(-8, 0), (-16, 0)], pause=HAND_STEP_MS // 2)
    wait_still(ipad)  # 误展开的话这时已经展开完了
    assert ipad.evaluate("() => whiteboard.ui.pk.state") == "minimized"


def test_picker_expands_when_the_pointer_comes_close(browser, server):
    """收进角落之后，指针靠近就展开，不用非得点中那个圆。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    minimize_picker(ipad, "br")

    ipad.mouse.move(600, 400)  # 离得远，不动
    # 收进角落是带动画的，等它停稳再看、再量，否则量到的是半路上的坐标
    wait_still(ipad)
    assert ipad.evaluate("() => whiteboard.ui.pk.state") == "minimized"

    left, top = ipad.evaluate(
        "() => { const r = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();"
        " return [r.left, r.top]; }"
    )
    ipad.mouse.move(left - 40, top - 30)
    wait_state(ipad, "docked")

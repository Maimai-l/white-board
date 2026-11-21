"""编辑界面的外框（docs/backlog/011-ui-redesign.md）：Mac 的顶栏、工具栏的更多菜单、
缩放百分比、白板设置和连接 iPad 两个面板、关于，以及 iPad 右上角的连接状态。"""

from __future__ import annotations

from e2e.support import (
    IPAD_UA,
    as_native,
    draw,
    menu_pick,
    new_page,
    open_gallery,
    open_mac,
    open_touch,
    reload,
    stroke_count,
    wait_online,
    wait_strokes,
)

ZOOM_TEXT = "() => document.querySelector('#zoombar .zoom-level').textContent"

# 本地进程的接口，多给一份地址：连接 iPad 的二维码要用
NATIVE_WITH_URL = """
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.3.0', packaged: true, hostname: 'MacBook.local',
      port: Number(location.port), urls: [location.origin + '/'], data_dir: '/Users/me/Documents/白板',
      remote_permissions: { manage: true, settings: false, clear: false, export: true }, apps: [] }),
    set_remote_permission: async () => ({}),
  }};
"""


def native_mac(browser, port):
    mac = open_mac(browser, port)
    mac.add_init_script(NATIVE_WITH_URL)
    reload(mac)
    mac.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000)
    return mac


def remote_ipad(browser, port):
    """局域网里的一台 iPad：请求带上转发头，服务端就不把它当本机（netinfo.is_local_request）。"""
    page = new_page(browser, user_agent=IPAD_UA, touch=True)
    page.set_extra_http_headers({"X-Forwarded-For": "192.168.1.20"})
    page.goto(f"http://127.0.0.1:{port}/?role=ipad")
    wait_online(page)
    return page


def test_the_title_bar_shows_the_board_name_and_folder(browser, server):
    """顶栏左边是白板库按钮和白板的名字，名字下面是所在的文件夹；改名、归类都跟着变。"""
    mac = open_mac(browser, server.port)
    assert mac.locator("#topbar .topbar-name").inner_text() == "白板"
    assert mac.locator("#topbar .topbar-folder").is_hidden()
    buttons = mac.evaluate("() => [...document.querySelectorAll('#topbar button')].map(b => b.title)")
    assert buttons == ["白板库", "白板设置", "导出"] or buttons == ["白板库", "导出", "白板设置"]

    board = mac.evaluate("() => whiteboard.state.id")
    mac.evaluate("([id]) => whiteboard.net.send({ t: 'rename', board: id, name: '线性代数' })", [board])
    mac.evaluate("([id]) => whiteboard.net.send({ t: 'folder', board: id, folder: '数学' })", [board])
    mac.wait_for_function("() => document.querySelector('#topbar .topbar-name').textContent === '线性代数'")
    mac.wait_for_function("() => document.querySelector('#topbar .topbar-folder').textContent === '数学'")
    assert mac.locator("#topbar .topbar-folder").is_visible()

    mac.click('#topbar button[title="白板库"]')
    mac.wait_for_selector(".gallery.library")


def test_the_zoom_control_shows_the_percentage(browser, server):
    """右下角缩放中间是百分比：放大缩小跟着变，点百分比回到 100%。"""
    mac = open_mac(browser, server.port)
    start = mac.evaluate(ZOOM_TEXT)
    assert start.endswith("%")
    mac.click('#zoombar button[title="实际大小"]')
    mac.wait_for_function(f"{ZOOM_TEXT} === '100%'")
    mac.click('#zoombar button[title="放大"]')
    mac.wait_for_function(f"{ZOOM_TEXT} === '125%'")
    mac.click('#zoombar button[title="缩小"]')
    mac.wait_for_function(f"{ZOOM_TEXT} === '100%'")
    mac.keyboard.press("Control+=")
    mac.wait_for_function(f"{ZOOM_TEXT} === '125%'")


def test_the_toolbar_stays_at_the_bottom_and_more_clears_the_board(browser, server):
    """工具栏固定在底部正中，只有工具、颜色、撤销重做和更多；更多里清空要确认。"""
    mac = open_mac(browser, server.port)
    titles = mac.evaluate("() => [...document.querySelectorAll('#toolbar button')].map(b => b.title)")
    assert titles == ["钢笔", "马克笔", "荧光笔", "橡皮擦", "颜色与粗细", "撤销", "重做", "更多"]
    box = mac.locator("#toolbar").bounding_box()
    assert box["width"] > box["height"] and box["y"] + box["height"] > 800 - 40

    draw(mac, [(500, 300), (600, 360)])
    wait_strokes(mac, 1)
    mac.click('#toolbar button[title="更多"]')
    labels = mac.locator(".menu .menu-label").all_inner_texts()
    assert labels == ["回到内容", "清空白板…"]  # 浏览器里没有关于
    menu_pick(mac, "清空白板…")
    assert mac.locator(".dialog.ask .ask-text").inner_text() == "清空这块白板"
    mac.click('.dialog button[title="清空"]')
    mac.wait_for_function("() => whiteboard.state.strokes.length === 0")


def test_the_color_panel_has_system_colors_and_five_widths(browser, server):
    mac = open_mac(browser, server.port)
    mac.click('#toolbar button[title="颜色与粗细"]')
    assert mac.locator(".popover .swatch").count() == 8
    widths = mac.locator(".popover .width-opt").evaluate_all("(nodes) => nodes.map(n => n.title)")
    assert widths == ["极细", "细", "中", "粗", "极粗"]
    mac.click('.popover .swatch[title="#007aff"]')
    mac.wait_for_function("() => whiteboard.tool.color === '#007aff'")
    mac.click('.popover .width-opt[title="粗"]')
    mac.wait_for_function("() => whiteboard.tool.width === 8")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.popover')")


def test_board_settings_open_as_a_panel(browser, server):
    """白板设置从按钮下面弹出：背景四种带名字；Mac 窗口里还有 iPad 可以和存储位置。"""
    mac = native_mac(browser, server.port)
    mac.click('#topright button[title="白板设置"]')
    panel = mac.wait_for_selector(".panel-settings")
    assert mac.locator(".panel-settings .group-title").all_inner_texts() == ["背景", "iPad 可以", "存储位置"]
    assert mac.locator(".panel-settings .bg-label").all_inner_texts() == ["空白", "方格", "横线", "点阵"]
    perms = mac.locator(".panel-settings .perm-title").all_inner_texts()
    assert perms == ["管理白板", "设置白板", "清空白板", "导出白板"]
    assert mac.locator(".panel-settings .addr").inner_text() == "~/Documents/白板"
    button = mac.locator('#topright button[title="白板设置"]').bounding_box()
    box = panel.bounding_box()
    assert box["y"] >= button["y"] + button["height"]  # 贴着按钮往下开

    mac.click('.bg-opt[title="横线"]')
    mac.wait_for_function("() => whiteboard.state.meta.background.pattern === 'lines'")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.panel')")


def test_connect_ipad_shows_a_qr_code_and_the_connected_devices(browser, server):
    """连接 iPad：二维码能载入，地址在下面；iPad 连上之后列在已连接里、图标变蓝、
    弹一条提示，断开时也提示。"""
    mac = native_mac(browser, server.port)
    mac.click("#status")
    mac.wait_for_selector(".panel-connect")
    mac.wait_for_function(
        "() => { const i = document.querySelector('.connect-qr'); return i.complete && i.naturalWidth > 0; }"
    )
    src = mac.get_attribute(".connect-qr", "src")
    assert src.startswith("/api/qr?text=")
    assert mac.locator(".panel-connect .addr").inner_text() == f"127.0.0.1:{server.port}"
    assert mac.locator(".panel-connect .panel-note").last.inner_text() == "没有连接的设备"
    assert mac.get_attribute("#status", "data-peers") == ""

    ipad = remote_ipad(browser, server.port)
    mac.wait_for_function("() => document.querySelector('.panel-connect .device-row')?.textContent === 'iPad'")
    mac.wait_for_function("() => document.getElementById('status').dataset.peers === '1'")
    mac.wait_for_selector('.notice:has-text("iPad 已连接")')

    # 安装外壳：二维码换成安装页
    mac.click('.panel-connect .link:has-text("在 iPad 上安装白板")')
    mac.wait_for_function("() => decodeURIComponent(document.querySelector('.connect-qr').src).endsWith('/ipad')")

    ipad.close()
    mac.wait_for_selector('.notice:has-text("iPad 已断开")')
    mac.wait_for_function("() => document.getElementById('status').dataset.peers === ''")


def test_about_is_in_the_more_menu_of_the_mac_window(browser, server):
    mac = native_mac(browser, server.port)
    mac.click('#toolbar button[title="更多"]')
    menu_pick(mac, "关于白板")
    dialog = mac.wait_for_selector(".dialog.about")
    assert mac.locator(".about-version").inner_text() == "版本 1.3.0"
    assert dialog.query_selector("button:has-text('检查更新')") is not None
    mac.click(".about-close")
    mac.wait_for_function("() => !document.querySelector('.dialog.about')")


def test_the_ipad_corner_shows_the_connection(browser, server):
    """iPad 左上角回白板库；右上角第一个按钮的颜色和名字就是连接状态，点开说连的是哪台 Mac。"""
    ipad = open_touch(browser, server.port)
    assert ipad.evaluate("() => [...document.querySelectorAll('#topleft button')].map(b => b.title)") == ["白板库"]
    status = ipad.locator("#topright #status")
    ipad.wait_for_function("() => document.getElementById('status').dataset.status === 'online'")
    assert status.get_attribute("aria-label").startswith("已连接到")
    status.click()
    ipad.wait_for_selector(".panel-status")
    assert ipad.locator(".panel-status .panel-row-text").inner_text().startswith("已连接到")

    ipad.click('#topleft button[title="白板库"]')
    ipad.wait_for_selector(".gallery.library")


def test_the_status_button_still_opens_diagnostics(browser, server):
    """连点三下连接按钮打开诊断面板（原来是左上角的圆点）。"""
    ipad = open_touch(browser, server.port)
    for _ in range(3):
        ipad.click("#status")
    ipad.wait_for_selector("#perf", state="visible")


def test_strokes_survive_switching_through_the_library(browser, server):
    """从白板库新建再回来，原来那块的笔迹还在。"""
    mac = open_mac(browser, server.port)
    draw(mac, [(400, 300), (500, 340)])
    wait_strokes(mac, 1)
    first = mac.evaluate("() => whiteboard.state.id")
    open_gallery(mac)
    mac.click(".lib-new")
    mac.click('.kind-tile:has-text("四向延伸")')
    mac.wait_for_function("(id) => whiteboard.state.id !== id", arg=first)
    assert stroke_count(mac) == 0
    open_gallery(mac)
    mac.dblclick(f'.board-item[data-board="{first}"] .board-card')
    mac.wait_for_function("(id) => whiteboard.state.id === id", arg=first)
    wait_strokes(mac, 1)


def test_the_connect_panel_uses_the_server_address(browser, server):
    """本地接口里没有地址时（support.as_native 的替身），二维码用服务端给的地址。"""
    mac = open_mac(browser, server.port)
    as_native(mac)
    mac.click("#status")
    mac.wait_for_function("() => { const i = document.querySelector('.connect-qr'); return i && i.naturalWidth > 0; }")


# ----------------------------------------------------------------- 点外面关上
#
# 每个弹出来的东西（菜单、面板）点外面的空白处都要关上，而且这一下点到的东西照常起作用，
# 不用先选一项或者按 Esc。


def test_clicking_outside_closes_the_more_menu(browser, server):
    mac = open_mac(browser, server.port)
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    mac.mouse.click(600, 300)  # 画布上的空白处
    mac.wait_for_selector(".menu", state="detached")

    # 菜单开着时直接点工具栏上的别的按钮：菜单关上，按钮也照常生效
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    mac.click('#toolbar button[title="橡皮擦"]')
    mac.wait_for_selector(".menu", state="detached")
    assert mac.evaluate("() => whiteboard.tool.tool") == "eraser"


def test_clicking_outside_closes_the_panels(browser, server):
    mac = native_mac(browser, server.port)
    mac.click('#toolbar button[title="颜色与粗细"]')
    mac.wait_for_selector(".popover")
    mac.mouse.click(600, 300)
    mac.wait_for_selector(".popover", state="detached")

    for title, panel in (("白板设置", ".panel-settings"), ("连接 iPad", ".panel-connect")):
        mac.click(f'#topbar button[title="{title}"]')
        mac.wait_for_selector(panel)
        mac.mouse.click(600, 300)
        mac.wait_for_selector(panel, state="detached")


def test_clicking_outside_closes_the_library_menus(browser, server):
    mac = open_mac(browser, server.port)
    open_gallery(mac)
    mac.click(".lib-scroll .board-item[data-board]", button="right")
    mac.wait_for_selector(".menu")
    mac.mouse.click(900, 600)  # 白板库的空白处
    mac.wait_for_selector(".menu", state="detached")
    assert mac.query_selector(".gallery") is not None  # 只关菜单，不关白板库

    mac.click(".lib-new")
    mac.wait_for_selector(".lib-new-panel")
    mac.mouse.click(900, 600)
    mac.wait_for_selector(".lib-new-panel", state="detached")


def test_clicking_outside_closes_the_menu_on_the_ipad(browser, server):
    tablet = open_touch(browser, server.port)
    tablet.tap('#toolbar button[title="更多"]')
    tablet.wait_for_selector(".menu")
    tablet.tap("#stage", position={"x": 300, "y": 300})
    tablet.wait_for_selector(".menu", state="detached")

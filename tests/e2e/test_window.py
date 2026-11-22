"""Mac 窗口的标题栏（whiteboard/titlebar.py）：没有系统标题栏，红绿灯摆在网页顶栏的左边。

Mac 应用打开页面时地址里带 ``window=inset``。这里像它一样打开页面，只看页面：红绿灯那块
（离左边 20 到 72、顶栏中线上）空着，点下去落在拖动区域上；顶栏和白板库顶上都是 52 高；
标题这些字不挡拖动；双击顶栏空白处让本地进程放大窗口，双击按钮不会。

红绿灯本身、拖动窗口不跳、双击放大，要在真机上看（发版检查表）。
"""

from __future__ import annotations

from e2e.support import LIBRARY, gone, new_board, new_page, open_gallery, wait_online

# 本地进程的接口：记下页面让它放大了几次窗口
NATIVE = """
  window.__zooms = 0;
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.3.0', packaged: true, hostname: 'MacBook.local',
      port: Number(location.port), urls: [location.origin + '/'], data_dir: '/Users/me/Documents/白板',
      remote_permissions: {}, apps: [] }),
    zoom_window: async () => { window.__zooms += 1; return true; },
  }};
"""

# 红绿灯的中心：离左边 20 起，三个 12 的按钮间隔 8；竖直在 52 高的顶栏中线上
LIGHTS = [(26, 26), (46, 26), (66, 26)]

HIT = """([x, y]) => {
  const node = document.elementFromPoint(x, y);
  return node ? { drag: node.matches('.pywebview-drag-region'), name: node.id || node.className } : null;
}"""


def mac_window(browser, port, query="&window=inset"):
    page = new_page(browser, viewport=(1200, 800))
    page.add_init_script(NATIVE)
    page.goto(f"http://127.0.0.1:{port}/?role=mac{query}")
    wait_online(page)
    page.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000)
    return page


def box(page, selector):
    return page.locator(selector).first.bounding_box()


def test_the_traffic_lights_sit_in_the_title_bar(browser, server):
    mac = mac_window(browser, server.port)
    bar = box(mac, "#topbar")
    assert bar["y"] == 0 and bar["height"] == 52
    # 红绿灯那块什么按钮都没有，点下去是拖动区域
    for point in LIGHTS:
        hit = mac.evaluate(HIT, list(point))
        assert hit and hit["drag"], (point, hit)
    # 顶栏里第一个按钮在绿灯右边，留出和左边一样的空隙
    first = box(mac, '#topbar button[title="白板库"]')
    assert first["x"] >= 72 + 12
    assert abs(first["y"] + first["height"] / 2 - 26) <= 1  # 和红绿灯同一条中线


def test_titles_do_not_get_in_the_way_of_dragging(browser, server):
    """点在白板名字上也是在拖窗口：名字不能接住这一下，否则那一块拖不动。"""
    mac = mac_window(browser, server.port)
    title = box(mac, ".topbar-name")
    hit = mac.evaluate(HIT, [title["x"] + title["width"] / 2, title["y"] + title["height"] / 2])
    assert hit and hit["drag"], hit


def test_the_library_lines_up_with_the_traffic_lights(browser, server):
    mac = mac_window(browser, server.port)
    new_board(mac, "笔记")
    open_gallery(mac)
    head = box(mac, f"{LIBRARY} .lib-head")
    assert head["y"] == 0 and head["height"] == 52
    for point in LIGHTS:
        assert mac.evaluate(HIT, list(point))["drag"], point
    # 边栏的第一行在红绿灯那一条下面
    first_row = box(mac, f"{LIBRARY} .lib-nav")
    assert first_row["y"] >= 52
    title = box(mac, f"{LIBRARY} .lib-title")
    assert mac.evaluate(HIT, [title["x"] + 4, title["y"] + title["height"] / 2])["drag"]


def test_double_clicking_the_title_bar_zooms_the_window(browser, server):
    mac = mac_window(browser, server.port)
    spacer = box(mac, ".topbar-spacer")
    mac.mouse.dblclick(spacer["x"] + spacer["width"] / 2, 26)
    mac.wait_for_function("() => window.__zooms === 1")
    # 双击按钮是点两下按钮，不放大窗口
    mac.dblclick('#topbar button[title="白板设置"]')
    mac.keyboard.press("Escape")
    gone(mac, ".panel")
    assert mac.evaluate("() => window.__zooms") == 1


def test_full_screen_gives_the_space_back(browser, server):
    """全屏时红绿灯由系统收起来（本地进程把 data-window 改成 full），左边不再空着。"""
    mac = mac_window(browser, server.port)
    before = box(mac, '#topbar button[title="白板库"]')["x"]
    mac.evaluate("() => { document.documentElement.dataset.window = 'full'; }")
    mac.wait_for_function(
        "(x) => document.querySelector('#topbar button[title=\"白板库\"]').getBoundingClientRect().x < x - 60",
        arg=before,
    )


def test_a_browser_tab_leaves_no_room_for_traffic_lights(browser, server):
    """电脑上的浏览器打开 Mac 页面：窗口有自己的标题栏，顶栏左边不用留空。"""
    mac = mac_window(browser, server.port, query="")
    assert mac.evaluate("() => document.documentElement.dataset.window") is None
    assert box(mac, '#topbar button[title="白板库"]')["x"] < 40

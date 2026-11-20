"""浏览器用例共用的东西：起服务端、开页面、在页面上书写，以及几组常用操作。

fixture 在 conftest.py 里；这里只放普通函数和常量，用例直接 import。
页面里用的小工具在 probe.js（``window.__t``），由 :func:`prepare` 注入。
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

from whiteboard.config import Config
from whiteboard.runner import ServerThread

IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
# 别的触摸设备（安卓平板）：书写、手势和 iPad 一样，工具栏是普通那条
TABLET_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# iPadOS 的 Safari 默认报桌面版 UA，只能靠有没有触摸点认出来
DESKTOP_SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# 用哪个浏览器内核跑，默认 Chromium。CI 另外用 WebKit（iPad 上的 Safari 就是它）
# 跑一组冒烟用例：WB_BROWSER=webkit。
ENGINE = os.environ.get("WB_BROWSER", "chromium")

PROBE = (Path(__file__).with_name("probe.js")).read_text("utf-8")
ROOT = Path(__file__).resolve().parents[2]

ONLINE = "() => window.whiteboard && whiteboard.net.status === 'online'"


def app_constant(path, name):
    """从应用源码里读一个数值常量（``const NAME = 120;`` 或 ``NAME: 120,``）。

    测试里非得按时间等的地方用它：等多久跟着应用里的取值走，改了常量不用再来改测试。
    """
    text = (ROOT / path).read_text("utf-8")
    match = re.search(rf"\b{name}\s*[:=]\s*(-?\d+(?:\.\d+)?)\b", text)
    if not match:
        raise LookupError(f"{path} 里找不到常量 {name}")
    value = match.group(1)
    return float(value) if "." in value else int(value)


# 笔具盘：松手前多少毫秒内的移动算进速度（pkpicker.js 的 TUNING）
VELOCITY_WINDOW_MS = app_constant("whiteboard/web/static/js/pkpicker.js", "VELOCITY_WINDOW_MS")


# ----------------------------------------------------------------- 环境


def chromium_path():
    """挑本机装着的最新那一版 Chromium。

    挑最新的是为了和 CI 对齐：CI 上没有 /opt/pw-browsers，走 Playwright 自带的
    那一版，而这里如果挑了台机器上碰巧留着的旧版本，本地全绿、CI 却红，
    而且看不出为什么。新版目录叫 chrome-linux64，旧版叫 chrome-linux，两种都认。
    """
    builds = []
    for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome"):
        for candidate in Path("/opt/pw-browsers").glob(pattern):
            match = re.search(r"chromium-(\d+)", str(candidate))
            builds.append((int(match.group(1)) if match else 0, str(candidate)))
    if not builds:
        return None
    return max(builds)[1]


def free_port():
    """让系统给一个空闲端口。以前按当前毫秒数取 8000～9999，两次运行挨得近就会撞。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def make_config(tmp_path, port=None):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = port or free_port()
    return config


def start_server(tmp_path, port=None, prepare=None):
    """在后台线程里起一个白板服务端。``prepare(config)`` 在启动之前调用，用来摆好数据。

    调用方负责 ``stop()``；一般用 conftest 里的 ``server`` / ``make_server``。
    """
    config = make_config(tmp_path, port)
    if prepare is not None:
        prepare(config)
    thread = ServerThread(config, advertise=False)
    thread.start()
    return thread


# ----------------------------------------------------------------- 页面


def prepare(page):
    """给页面装上 probe.js（``window.__t``），之后每次加载都在。"""
    page.add_init_script(PROBE)
    return page


def wait_online(page, timeout=None):
    if timeout is None:
        page.wait_for_function(ONLINE)
    else:
        page.wait_for_function(ONLINE, timeout=timeout)


def page_now(page):
    """页面自己的时钟（performance.now()），应用里的计时窗口都按它算。"""
    return page.evaluate("() => performance.now()")


def wait_page_ms(page, ms, since=None):
    """按页面自己的时钟，等到从 ``since``（默认是现在）起过了 ``ms`` 毫秒。

    只给确实要量时间的地方用：应用里按 performance.now() 计时的窗口（外壳断流、
    松手前停住）。和页面用同一个时钟比，不用再加余量去猜。
    """
    if since is None:
        since = page_now(page)
    page.wait_for_function("([since, ms]) => performance.now() - since > ms", arg=[since, ms])


def poll_page(page, check, arg=None, timeout=5000, interval=50):
    """在页面里反复调用异步的 ``check(arg)``，直到它返回真值，返回那个值；超时返回 None。

    ``wait_for_function`` 不等 Promise（异步函数返回的 Promise 本身就算真值），
    读剪贴板、IndexedDB 这类只有异步接口的条件用这个。
    """
    return page.evaluate(
        "async ([arg, timeout, interval]) => {"
        f" const check = {check};"
        " const end = performance.now() + timeout;"
        " for (;;) {"
        "   const value = await check(arg);"
        "   if (value) return value;"
        "   if (performance.now() > end) return null;"
        "   await new Promise((done) => setTimeout(done, interval));"
        " } }",
        [arg, timeout, interval],
    )


def wait_seq(page, other):
    """等 ``page`` 收齐 ``other`` 已经确认过的全部操作（两边的 lastSeq 对齐）。

    要比较两台设备上的内容时先等这个：对端的广播和发送端的回执不保证谁先到。
    """
    seq = other.evaluate("() => whiteboard.net.lastSeq")
    page.wait_for_function("(seq) => whiteboard.net.lastSeq >= seq", arg=seq)


def reload(page):
    page.reload()
    wait_online(page)


def new_page(browser, *, viewport=(1180, 820), user_agent=None, touch=False):
    options = {"viewport": {"width": viewport[0], "height": viewport[1]}, "has_touch": touch}
    if user_agent:
        options["user_agent"] = user_agent
    return prepare(browser.new_context(**options).new_page())


def open_mac(browser, port):
    """Mac 上的窗口。"""
    page = new_page(browser, viewport=(1200, 800))
    page.goto(f"http://127.0.0.1:{port}/?role=mac")
    wait_online(page)
    return page


def open_touch(browser, port, picker=False):
    """一台触摸设备。笔具盘只有 iPad 上有：``picker=True`` 时是 iPad（工具栏是笔具盘），
    默认是一台安卓平板——书写和手势照 iPad 那一套，工具栏是普通那条。"""
    page = new_page(browser, user_agent=IPAD_UA if picker else TABLET_UA, touch=True)
    page.goto(f"http://127.0.0.1:{port}/?role=ipad")
    wait_online(page)
    return page


def open_pages(browser, port, picker=False):
    """开两个页面：Mac 窗口和一台触摸设备（见 :func:`open_touch`），两边都连上之后返回。

    只用得到其中一边的用例直接用 :func:`open_mac` / :func:`open_touch`，省一个页面。
    """
    return open_mac(browser, port), open_touch(browser, port, picker)


# ----------------------------------------------------------------- 书写


# 在页面里合成指针事件，可以指定 pointerType，用来验证 Pencil 相关行为。
FIRE = """
([type, x, y, pointerType, pointerId, pressure]) => {
  const stage = document.getElementById('stage');
  stage.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, pointerType, pointerId, pressure,
    buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
  }));
}
"""


def fire(page, kind, x, y, pointer_type="pen", pointer_id=1, pressure=0.6):
    """派发一个指针事件。kind 是 down / move / up / cancel。"""
    page.evaluate(FIRE, [f"pointer{kind}", x, y, pointer_type, pointer_id, pressure])


def draw(page, points, pointer_type="pen", pointer_id=1, pressure=0.6):
    fire(page, "down", *points[0], pointer_type, pointer_id, pressure)
    for x, y in points[1:]:
        fire(page, "move", x, y, pointer_type, pointer_id, pressure)
    fire(page, "up", *points[-1], pointer_type, pointer_id, 0)


def stroke_count(page):
    return page.evaluate("() => whiteboard.state.strokes.length")


def wait_strokes(page, count, timeout=4000):
    page.wait_for_function(f"() => whiteboard.state.strokes.length === {count}", timeout=timeout)


def view(page):
    """视口的平移量 [x, y]。"""
    return page.evaluate("() => __t.view()")


def screen_of_first_stroke(page):
    """第一条笔画起点在屏幕上的位置。"""
    return page.evaluate(
        "() => { const s = whiteboard.state.strokes[0];"
        " return whiteboard.viewport.toScreen(s.p[0], s.p[1]); }"
    )


def pick_pixel_eraser(page):
    """经工具栏切到像素橡皮。"""
    page.click('button[title="橡皮擦"]')
    page.click('button[title="颜色与粗细"]')
    page.click(".popover .seg button:nth-child(2)")
    page.keyboard.press("Escape")
    assert page.evaluate("() => whiteboard.tool.eraserMode") == "pixel"


def mask_chains(op):
    """``wait_for_function`` 用：某条笔画的遮罩链数满足 op（如 ``"> 0"``）。参数是 ``[id]``。"""
    return (
        "([id]) => { const s = whiteboard.state.byId.get(id);"
        f" return !!s && (s.m || []).length {op}; }}"
    )


# ----------------------------------------------------------------- 白板选择界面


def open_gallery(page):
    page.click('button[title="白板"]')


def new_board(page, kind="笔记"):
    """白板列表 → 新建 → 选一种（「笔记」或「大白板」），等切过去之后返回新白板的 id。"""
    before = page.evaluate("() => whiteboard.state.id")
    open_gallery(page)
    page.click(".board-card.add")
    page.click(f'.kind-tile[title^="{kind}"]')
    page.wait_for_function("(was) => whiteboard.state.id !== was", arg=before)
    if kind == "笔记":
        page.wait_for_function("() => whiteboard.state.kind === 'note'")
    return page.evaluate("() => whiteboard.state.id")


def new_folder(page, name=None):
    """点「新建文件夹」：名字框直接进入编辑、默认名全选；输入名字（或保留默认名）后回车。

    要已经在白板选择界面里、并且点开了「新建」。
    """
    page.click('.kind-tile[title="新建文件夹"]')
    field = page.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    assert page.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    if name is not None:
        page.keyboard.type(name)
    page.keyboard.press("Enter")
    target = name if name is not None else field.get_attribute("value")
    page.wait_for_function("name => whiteboard.ui.folders.includes(name)", arg=target)
    return target


def gallery_with_folder(page, name=None):
    """打开选择界面、新建一个文件夹，返回文件夹的名字。"""
    open_gallery(page)
    page.click(".board-card.add")
    name = new_folder(page, name)
    page.wait_for_selector(".board-card.folder")
    return name


def file_into(page, folder):
    """用卡片下面的文件夹按钮把（唯一那块）白板归进 folder。"""
    page.click(".board-folder")
    page.click(f'.folder-row:has-text("{folder}")')
    page.wait_for_function("(f) => whiteboard.ui.boards.every(b => b.folder === f)", arg=folder)


def all_in_folder(folder):
    """``wait_for_function`` 用：所有白板都在 folder 里。"""
    return f"() => whiteboard.ui.boards.every(b => b.folder === {folder!r})"


# mouse_drag 的 ready：指着的文件夹（或「移出」按钮）亮起来了
DROP_TARGET_LIT = "() => !!document.querySelector('.drop-target')"
# mouse_drag 的 ready：空位让到了白板卡片的最后（别的卡片已经让开；「新建」不算）
SLOT_IS_LAST = (
    "() => { const items = document.querySelectorAll("
    "'.gallery-grid .board-item[data-board], .gallery-grid .board-item.slot');"
    " return items.length > 0 && items[items.length - 1].classList.contains('slot'); }"
)


def mouse_drag(page, from_sel, to_sel, ready, steps=12, at=(0.5, 0.5)):
    """鼠标拖一张卡片，停在落点上等 ``ready`` 成立再松手。

    ``ready`` 是页面上看得见的结果，比如 :data:`DROP_TARGET_LIT`、:data:`SLOT_IS_LAST`：
    让位要在同一个插入位置停够 REORDER_DELAY（见 dragsort.js），等的是它真的让开了。
    ``at`` 是落点在目标元素上的相对位置。
    """
    a = page.locator(from_sel).bounding_box()
    b = page.locator(to_sel).bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 12, a["y"] + a["height"] / 2, steps=3)
    page.mouse.move(b["x"] + b["width"] * at[0], b["y"] + b["height"] * at[1], steps=steps)
    page.wait_for_function(ready)
    page.mouse.up()


# ----------------------------------------------------------------- 笔具盘


PICKER = "#pk-host .pk-picker"
GRIP = "#pk-host .pk-grip"


def enable_pk_picker(page):
    """等 iPad 上的笔具盘装好（页面要用 ``picker=True`` 打开）。"""
    page.wait_for_selector(PICKER, timeout=20000)
    page.wait_for_selector('#pk-host [data-tool="pen"]')


def center(page, selector):
    return page.evaluate("(sel) => __t.center(sel)", selector)


# 这个元素和它里面的元素上没有正在走的动画（CSS 过渡也算）
STILL = (
    "(sel) => { const el = document.querySelector(sel);"
    " return !!el && el.getAnimations({ subtree: true }).length === 0; }"
)


def wait_still(page, selector=PICKER, timeout=None):
    """等动画走完：贴边、收起、展开、面板淡出都是 CSS 过渡。"""
    if timeout is None:
        page.wait_for_function(STILL, arg=selector)
    else:
        page.wait_for_function(STILL, arg=selector, timeout=timeout)


def settled_center(page, selector=GRIP):
    """贴边、收起都带动画：等工具盘的动画都走完再量，否则抓到的是半路上的坐标。"""
    wait_still(page)
    return center(page, selector)


def picker_top(page):
    return page.evaluate(
        f"() => document.querySelector('{PICKER}').getBoundingClientRect().top"
    )


def pk_form(page):
    return page.evaluate("() => [whiteboard.ui.pk.state, whiteboard.ui.pk.dock]")


def minimize_picker(page, corner="br"):
    page.evaluate("(c) => whiteboard.ui.pk.minimize(c)", corner)
    page.wait_for_function("() => whiteboard.ui.pk.state === 'minimized'")


def drag(page, x0, y0, x1, y1, steps=12):
    """慢放：终点停住一下再松手，速度归零，工具盘就以松手点为停点。"""
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    hold_still(page)
    page.mouse.move(x1, y1)
    page.mouse.up()


def hold_still(page):
    """拖着不动，停过 VELOCITY_WINDOW_MS：松手时速度归零，算慢放。

    「手停住了」本身就是一段时间，只能按时间等（按页面的时钟，见 :func:`wait_page_ms`）。
    """
    wait_page_ms(page, VELOCITY_WINDOW_MS)


def fling(page, x0, y0, x1, y1, steps=5):
    """甩：一路不停直接松手，工具盘按惯性推算停点。

    步数要少。每一次 mouse.move 都是一趟 CDP 往返、大约 17 ms，12 步就铺开到
    200 ms，而速度只取最后 VELOCITY_WINDOW_MS（100 ms）里的采样，算出来是
    「半程距离 ÷ 100 ms」，正好压在 FLING_SPEED 上：实测六次里两次落到 924 和
    1183，被当成慢放，这一条就成了三成概率失败的用例。步数减到 5，每一步跨的
    距离变成三倍，速度离阈值就有足够余量了。
    """
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    page.mouse.up()


def nudge(page, x, y, offsets, pause=0):
    """按下、沿 offsets 一点点挪、松手——「蹭一下」。``pause`` 是每挪一下之后停多久（毫秒）。"""
    page.mouse.move(x, y)
    page.mouse.down()
    for dx, dy in offsets:
        page.mouse.move(x + dx, y + dy)
        if pause:
            # 这是手移动的节奏，不是在等什么：慢慢蹭和一下划过去是两种手势
            page.wait_for_timeout(pause)
    page.mouse.up()


# ----------------------------------------------------------------- 权限和本地进程


def serve_with_perms(page, perms):
    """让这一页重新加载时，按一份改过的权限清单启动。

    服务端是按对端地址发权限的，测试里连不上非本机地址，只能在客户端这边改。

    别用 ``page.route`` 拦下首页再 ``fulfill`` 一份改过的 HTML：那样文档是
    Playwright 伪造的，不是从真实网络端点来的，新版 Chromium 因此不把它归到
    「本地地址空间」，页面再连 ``ws://127.0.0.1`` 就成了跨地址空间请求，被
    Local Network Access 检查拦掉（``ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS``），
    net.status 永远到不了 online。Chromium 1194 还放行，1243 开始拦。

    客户端只在启动时读一次 ``documentElement.dataset.perms``（见 app.js 的
    resolvePerms），而 type=module 的脚本在解析完才执行，所以在 document_start
    把这个属性改掉就够了，首页仍然是服务端原样发的。
    """
    page.add_init_script(
        """(() => {
          const perms = %r;
          const apply = () => {
            if (!document.documentElement) return false;
            document.documentElement.dataset.perms = perms;
            return true;
          };
          if (!apply()) {
            new MutationObserver((_, observer) => {
              if (apply()) observer.disconnect();
            }).observe(document, { childList: true });
          }
        })();""" % perms
    )
    reload(page)
    assert page.evaluate("() => document.documentElement.dataset.perms") == perms


# 「关于」「连接 iPad」「存储目录」这几段只有本地进程里才有意义，界面按
# window.pywebview 在不在来决定给不给，所以要在页面加载之前就把它放好。
NATIVE_STUB = """
  window.__perms = { manage: false, settings: false, clear: false, export: false };
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '0.9.4', packaged: true,
      data_dir: '/tmp/boards',
      remote_permissions: Object.assign({}, window.__perms),
      releases: 'https://github.com/Maimai-l/white-board/releases' }),
    check_update_now: async () => ({ status: 'latest', version: '0.9.4' }),
    set_remote_permission: async (name, on) => {
      window.__perms[name] = !!on;
      return Object.assign({}, window.__perms);
    },
  }};
"""


def as_native(page):
    """把这一页变成「本地进程里的那个窗口」，重新加载后生效。"""
    page.add_init_script(NATIVE_STUB)
    reload(page)
    page.wait_for_function(
        "() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000
    )

"""指针和动效：鼠标指到任何一个能点的东西上，它都要有看得见的变化；菜单、面板、弹窗
开的时候有进场，关的时候淡出，而不是一下子没了。

这里只看页面显示出来的东西：悬停前后的计算样式、元素上正在跑的动画、关掉之后
它是不是还在、还挡不挡点击。设计和取值见 docs/design/motion.zh-CN.md。
"""

from __future__ import annotations

import pytest

from e2e.support import ENGINE, new_board, new_page, open_gallery, open_mac, wait_online

# 本地进程的接口：有它才有顶栏上的连接其他设备按钮和设置里的存储位置
NATIVE = """
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.3.0', packaged: true, hostname: 'MacBook.local',
      port: Number(location.port), urls: [location.origin + '/'], data_dir: '/Users/me/Documents/白板',
      remote_permissions: { manage: true, settings: false, clear: false, export: true }, apps: [] }),
    set_remote_permission: async () => ({}),
  }};
"""

# 在 root 里找出看得见、能点的控件，给每个编一个号。当前选中的那一个（当前工具、
# 分段控件里选中的那段、当前所在的边栏项）不算：它已经是高亮的样子了。
TAG = """([root, start]) => {
  const roots = [...document.querySelectorAll(root)];
  const picks = 'button, select, input[type=checkbox], input[type=radio], .board-item, label.export-radio';
  const skip = '.active, .is-on, .current, .selected, :disabled, [aria-current], [aria-pressed=true], .lib-nav-edit';
  const found = [];
  for (const scope of roots) {
    for (const node of scope.querySelectorAll(picks)) {
      if (node.matches(skip) || node.closest('[inert], .leaving')) continue;
      // 卡片里的按钮跟着卡片一起，单独再指一遍没有意义
      if (node.parentElement && node.parentElement.closest('.board-item') && !node.matches('.board-item')) continue;
      if (node.matches('input[type=radio]') && node.closest('label.export-radio')) continue;
      const box = node.getBoundingClientRect();
      if (box.width < 4 || box.height < 4) continue;
      if (box.right < 0 || box.bottom < 0 || box.left > innerWidth || box.top > innerHeight) continue;
      if (getComputedStyle(node).visibility === 'hidden') continue;
      const id = String(start + found.length);
      node.dataset.hoverProbe = id;
      const name = node.title || node.getAttribute('aria-label') || node.textContent.trim().slice(0, 16)
        || node.className;
      found.push([id, `${node.tagName.toLowerCase()}.${String(node.className).split(' ')[0]} 「${name}」`]);
    }
  }
  return found;
}"""

# 悬停能改变的样子：自己和里面的元素（含 ::before）的颜色、底色、阴影、位移、透明度、下划线
LOOK = """(node) => {
  const props = ['background-color', 'background-image', 'color', 'transform', 'box-shadow', 'opacity',
    'text-decoration-line', 'border-top-color', 'filter'];
  const read = (el, pseudo) => { const s = getComputedStyle(el, pseudo); return props.map((p) => s.getPropertyValue(p)).join('|'); };
  return [node, ...node.querySelectorAll('*')].slice(0, 40).map((n) => read(n, null) + read(n, '::before')).join('\\n');
}"""

SETTLED = "(node) => node.getAnimations({ subtree: true }).every((a) => a.playState !== 'running')"

UNDER = """([node, x, y]) => { const hit = document.elementFromPoint(x, y); return !!hit && (hit === node || node.contains(hit)); }"""


def look_at_rest_and_hover(page, selector):
    """先把指针挪到窗口左下角（画布或边栏的空白处），读一遍样子；再指上去，等过渡走完再读一遍。

    不挪到控件旁边：指在一行 label 上时，浏览器把 label 里的开关也算作悬停。
    """
    node = page.locator(selector)
    node.scroll_into_view_if_needed()
    box = node.bounding_box()
    page.mouse.move(2, page.viewport_size["height"] - 2)
    page.wait_for_function(SETTLED, arg=node.element_handle(), timeout=3000)
    rest = node.evaluate(LOOK)
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=2)
    if not page.evaluate(UNDER, [node.element_handle(), x, y]):
        return rest, None
    page.wait_for_function(SETTLED, arg=node.element_handle(), timeout=3000)
    return rest, node.evaluate(LOOK)


def dead_controls(page, root, start=0):
    """root 里指上去什么都不变（或者被别的东西盖住、指不到）的控件。"""
    dead = []
    for probe, name in page.evaluate(TAG, [root, start]):
        rest, hover = look_at_rest_and_hover(page, f'[data-hover-probe="{probe}"]')
        if hover is None:
            dead.append(f"{name}：指不到（被盖住）")
        elif hover == rest:
            dead.append(name)
    return dead


def native_mac(browser, port, size=(1200, 800)):
    page = new_page(browser, viewport=size)
    page.add_init_script(NATIVE)
    page.goto(f"http://127.0.0.1:{port}/?role=mac")
    wait_online(page)
    page.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000)
    return page


def test_every_control_in_the_mac_window_answers_the_pointer(browser, server):
    """Mac 窗口里每个能点的东西：顶栏、工具栏、缩放、各个面板和菜单、白板库、导出对话框。"""
    mac = native_mac(browser, server.port)
    dead = []

    dead += [f"编辑界面 {d}" for d in dead_controls(mac, "#topbar, #toolbar, #zoombar")]

    mac.click('#toolbar button[title="颜色与粗细"]')
    mac.wait_for_selector(".popover")
    dead += [f"颜色与粗细 {d}" for d in dead_controls(mac, ".popover", 100)]
    mac.keyboard.press("Escape")

    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    dead += [f"更多菜单 {d}" for d in dead_controls(mac, ".menu-layer:not(.leaving) .menu", 200)]
    mac.keyboard.press("Escape")

    mac.click('#topbar button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings .group-title")
    dead += [f"白板设置 {d}" for d in dead_controls(mac, ".panel-settings", 300)]
    mac.keyboard.press("Escape")

    mac.click("#status")
    mac.wait_for_selector(".panel-connect")
    dead += [f"连接其他设备 {d}" for d in dead_controls(mac, ".panel-connect", 400)]
    mac.keyboard.press("Escape")

    mac.click('#topbar button[title="导出"]')
    mac.wait_for_selector(".export-dialog .export-card")
    dead += [f"导出 {d}" for d in dead_controls(mac, ".export-dialog", 500)]
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.export-dialog:not(.leaving)')")

    mac.evaluate("() => whiteboard.ui.confirmClear()")
    mac.wait_for_selector(".dialog.ask")
    dead += [f"确认框 {d}" for d in dead_controls(mac, ".dialog.ask", 600)]
    mac.click('.dialog button[title="取消"]')

    new_board(mac, "笔记")
    open_gallery(mac)
    dead += [f"白板库 {d}" for d in dead_controls(mac, ".gallery.library", 700)]
    mac.click(".lib-new")
    mac.wait_for_selector(".lib-new-panel .kind-tile")
    dead += [f"新建白板 {d}" for d in dead_controls(mac, ".lib-new-panel", 900)]

    assert not dead, "指上去没有反应：\n" + "\n".join(dead)


# 记下 selector 匹配的元素什么时候加进来、什么时候开始退场、什么时候真的拿掉，
# 以及那一刻它身上有几个 CSS 动画、还接不接收点击。在动手之前装上，事后读 window.__motion。
WATCH = """(selector) => {
  window.__motion = [];
  const note = (node, what) => window.__motion.push({
    what,
    t: performance.now(),
    anims: node.getAnimations({ subtree: true }).filter((a) => a instanceof CSSAnimation).length,
    pointer: getComputedStyle(node).pointerEvents,
  });
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'childList') {
        for (const n of r.addedNodes) if (n instanceof Element && n.matches(selector)) note(n, 'added');
        for (const n of r.removedNodes) {
          if (n instanceof Element && n.matches(selector)) window.__motion.push({ what: 'removed', t: performance.now() });
        }
      } else if (r.target.matches(selector) && r.target.classList.contains('leaving')
          && !(r.oldValue || '').split(' ').includes('leaving')) {
        note(r.target, 'leaving');
      }
    }
  }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });
}"""


def watch(page, selector):
    page.evaluate(WATCH, selector)


def events(page):
    return page.evaluate("() => window.__motion")


def assert_enters_and_leaves(page, what):
    """开的时候有进场动画；关的时候先退场（有动画、不挡点击），过一会儿才从页面上拿掉。"""
    # 白板库这类会整个重画的，中途也会有不带退场的拿掉：等的是退场之后那一次
    page.wait_for_function(
        """() => { const log = window.__motion; const i = log.findLastIndex((e) => e.what === 'leaving');
          return i >= 0 && log.slice(i).some((e) => e.what === 'removed'); }""",
        timeout=3000,
    )
    log = events(page)
    added = [e for e in log if e["what"] == "added"]
    assert added and added[0]["anims"] > 0, f"{what}：打开时没有进场动画 {log}"
    leaving = [e for e in log if e["what"] == "leaving"]
    assert leaving, f"{what}：关的时候一下子就没了，没有退场 {log}"
    assert leaving[-1]["anims"] > 0, f"{what}：退场时没有动画 {log}"
    assert leaving[-1]["pointer"] == "none", f"{what}：退场时还挡着点击 {log}"
    removed = [e for e in log if e["what"] == "removed" and e["t"] >= leaving[-1]["t"]]
    assert removed and removed[0]["t"] - leaving[-1]["t"] >= 80, f"{what}：退场太快 {log}"


def test_menus_panels_and_dialogs_fade_out_instead_of_vanishing(browser, server):
    mac = native_mac(browser, server.port)

    watch(mac, ".menu-layer")
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    mac.keyboard.press("Escape")
    assert_enters_and_leaves(mac, "更多菜单")

    watch(mac, ".panel")
    mac.click('#topbar button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings")
    mac.keyboard.press("Escape")
    assert_enters_and_leaves(mac, "白板设置")

    watch(mac, ".popover")
    mac.click('#toolbar button[title="颜色与粗细"]')
    mac.wait_for_selector(".popover")
    mac.mouse.click(300, 300)
    assert_enters_and_leaves(mac, "颜色与粗细")

    watch(mac, ".scrim")
    mac.click('#topbar button[title="导出"]')
    mac.wait_for_selector(".export-dialog")
    mac.keyboard.press("Escape")
    assert_enters_and_leaves(mac, "导出")

    watch(mac, ".scrim")
    mac.evaluate("() => whiteboard.ui.confirmClear()")
    mac.wait_for_selector(".dialog.ask")
    mac.keyboard.press("Escape")
    assert_enters_and_leaves(mac, "确认框")

    # 白板库：点一块白板打开它，白板库淡出
    new_board(mac, "笔记")
    watch(mac, ".gallery.library")
    open_gallery(mac)
    mac.dblclick(".gallery.library .board-item")
    assert_enters_and_leaves(mac, "白板库")


def test_a_closing_menu_does_not_get_in_the_way(browser, server):
    """菜单淡出的那一下，紧接着再点按钮要能马上开一张新的，而且页面上只有这一张能用。"""
    mac = native_mac(browser, server.port)
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    mac.keyboard.press("Escape")
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu-layer:not(.leaving) .menu")
    mac.locator(".menu-layer:not(.leaving) .menu-item", has_text="回到内容").click()
    mac.wait_for_function("() => !document.querySelector('.menu-layer')")


def test_export_and_confirm_close_with_escape(browser, server):
    """导出对话框和确认框按 Esc 等于取消，和 macOS 的表单、提醒框一样。"""
    mac = open_mac(browser, server.port)
    mac.click('#topbar button[title="导出"]')
    mac.wait_for_selector(".export-dialog .export-card")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.export-dialog')", timeout=3000)

    mac.evaluate("() => whiteboard.ui.confirmClear()")
    mac.wait_for_selector(".dialog.ask")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.dialog.ask')", timeout=3000)


@pytest.mark.skipif(ENGINE != "chromium", reason="减弱动态效果的模拟只在 Chromium 上跑一遍")
def test_reduced_motion_opens_and_closes_at_once(browser, server):
    """系统设置了减弱动态效果：没有进场和退场，关上就是直接没了。"""
    context = browser.new_context(viewport={"width": 1200, "height": 800}, reduced_motion="reduce")
    mac = context.new_page()
    mac.goto(f"http://127.0.0.1:{server.port}/?role=mac")
    wait_online(mac)
    watch(mac, ".menu-layer")
    mac.click('#toolbar button[title="更多"]')
    mac.wait_for_selector(".menu")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => window.__motion.some((e) => e.what === 'removed')", timeout=3000)
    log = events(mac)
    assert [e["what"] for e in log] == ["added", "removed"], log
    assert log[0]["anims"] == 0, log

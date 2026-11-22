"""界面的每个状态都摆出来检查一遍，并且截图存档（test-results/ui-states/）。

每个状态检查四件事，都是看页面上实际画出来的样子：

1. 看得见的控件和弹出层都在窗口里，没有被窗口边裁掉；
2. 弹出层（菜单、面板、对话框）不盖住工具栏、顶栏、缩放这些常驻的条，常驻的条之间也不互相压；
3. 每个看得见的按钮正中那一点点下去，点到的就是它自己（没有被别的东西挡住）；
4. 按钮和单行文字没有被截断（文字比框宽，又没有省略号）。

另外每个弹出层都要能点外面关上、按 Esc 关上。窗口尺寸取 Mac 窗口允许的最小尺寸
（800 × 560，见 app.py）和常见的大尺寸，iPad 横竖两种。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.support import (
    IPAD_UA,
    TABLET_UA,
    board_menu,
    draw,
    menu_pick,
    new_page,
    open_gallery,
    wait_online,
    wait_strokes,
)

SHOTS = Path(__file__).resolve().parents[2] / "test-results" / "ui-states"

# 常驻的条：弹出层不能压在它们上面，它们之间也不能互相压
BARS = ["#topbar", "#toolbar", "#zoombar", "#topleft", "#topright.pill"]
# 弹出层
POPUPS = [
    ".menu",
    ".popover",
    ".panel",
    ".dialog",
    ".lib-new-panel",
    ".export-dialog",
    ".notice",
    ".export-bar",
    ".export-adjust-hint",
]

CHECK = """
([bars, popups]) => {
  const problems = [];
  const vw = innerWidth, vh = innerHeight;
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const name = (el) => {
    const label = el.getAttribute('aria-label') || el.getAttribute('title') || (el.textContent || '').trim().slice(0, 16);
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${[...el.classList].join('.')} "${label}"`;
  };
  const overlap = (a, b) => {
    const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return x > 1 && y > 1;
  };
  const barNodes = bars.flatMap((sel) => [...document.querySelectorAll(sel)]).filter(visible);
  const popupNodes = popups.flatMap((sel) => [...document.querySelectorAll(sel)]).filter(visible);

  // 1. 在窗口里
  for (const el of [...barNodes, ...popupNodes]) {
    const r = el.getBoundingClientRect();
    if (r.left < -1 || r.top < -1 || r.right > vw + 1 || r.bottom > vh + 1) {
      problems.push(`出了窗口：${name(el)} [${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.right)},${Math.round(r.bottom)}] / ${vw}×${vh}`);
    }
  }
  // 2. 不互相压。白板库和全屏的暗底盖住常驻的条是对的，这时不查条
  const covered = !!document.querySelector('.gallery, .export-scrim:not([hidden]), .scrim:not(.clear)');
  if (!covered) {
    for (let i = 0; i < barNodes.length; i++) {
      for (let j = i + 1; j < barNodes.length; j++) {
        if (barNodes[i].contains(barNodes[j]) || barNodes[j].contains(barNodes[i])) continue;
        if (overlap(barNodes[i].getBoundingClientRect(), barNodes[j].getBoundingClientRect())) {
          problems.push(`条和条压在一起：${name(barNodes[i])} / ${name(barNodes[j])}`);
        }
      }
    }
    for (const pop of popupNodes) {
      for (const bar of barNodes) {
        if (pop.contains(bar) || bar.contains(pop)) continue;
        if (overlap(pop.getBoundingClientRect(), bar.getBoundingClientRect())) {
          problems.push(`弹出层压住了条：${name(pop)} / ${name(bar)}`);
        }
      }
    }
  }
  // 3. 按钮点得到。最上面那一层弹出层开着时，只查它里面的（底下的本来就被盖住）；
  //    白板库开着时只查白板库里的（编辑界面的按钮被它整个盖住是对的）
  const topPopup = popupNodes.filter((el) => !el.classList.contains('notice')).at(-1);
  const scope = topPopup || document.querySelector('.gallery') || document;
  for (const button of scope.querySelectorAll('button, input, select, a[href]')) {
    if (!visible(button) || button.disabled || button.closest('[aria-hidden="true"]')) continue;
    if (button.type === 'file' || button.type === 'radio' || button.type === 'checkbox') continue;
    const r = button.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > vw || y > vh) continue;  // 在滚动区域里滚出去了，不算
    const hit = document.elementFromPoint(x, y);
    const scroller = button.closest('.lib-scroll, .export-cards, .lib-nav-list, .panel, .export-main');
    if (scroller) {
      const s = scroller.getBoundingClientRect();
      if (y < s.top || y > s.bottom) continue;
    }
    if (!hit || !(button === hit || button.contains(hit) || hit.contains(button) && hit.tagName === 'LABEL')) {
      problems.push(`点不到：${name(button)} 被 ${hit ? name(hit) : '空'} 挡住`);
    }
  }
  // 4. 文字没被截断
  for (const el of document.querySelectorAll('button, .menu-label, .lib-nav-label, .panel-title, .group-title, .export-label, .lib-title, .topbar-name, .board-name, h1, h2, label')) {
    if (!visible(el)) continue;
    const s = getComputedStyle(el);
    if (s.textOverflow === 'ellipsis') continue;
    if (el.scrollWidth > el.clientWidth + 2 && s.overflowX !== 'visible') {
      problems.push(`文字被截断：${name(el)} ${el.scrollWidth} > ${el.clientWidth}`);
    }
    // 按钮和菜单项里的每一段文字都应该是一行：量文字本身占了几行
    if (el.tagName === 'BUTTON' || el.classList.contains('menu-label')) {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const tops = new Set([...range.getClientRects()].map((r) => Math.round(r.top)));
        if (tops.size > 1) problems.push(`文字折行：${name(el)} "${node.textContent.trim()}"`);
      }
    }
  }
  return problems;
}
"""

FAKE_NATIVE = """
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.3.0', packaged: true, hostname: 'MacBook.local',
      port: Number(location.port), urls: [location.origin + '/'], data_dir: '/Users/me/Documents/白板',
      remote_permissions: { manage: true, settings: false, clear: false, export: true }, apps: [] }),
    set_remote_permission: async () => ({}),
  }};
"""

MAC_SIZES = {"mac-min": (800, 560), "mac": (1440, 900)}
TOUCH_SIZES = {"ipad": (1180, 820), "ipad-portrait": (820, 1180)}


def check(page, label):
    """截图存档，然后检查。返回问题清单（空就是没问题）。"""
    SHOTS.mkdir(parents=True, exist_ok=True)
    # 等刚关掉的东西淡出完、刚打开的东西进场完：截图和检查看的是停下来之后的样子
    page.wait_for_function(
        "() => !document.querySelector('.leaving')"
        " && document.getAnimations().every((a) => !(a instanceof CSSAnimation) || a.playState !== 'running')",
        timeout=3000,
    )
    page.evaluate("() => new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)))")
    page.screenshot(path=str(SHOTS / f"{label}.png"))
    problems = page.evaluate(CHECK, [BARS, POPUPS])
    return [f"{label}：{p}" for p in problems]


def closes(page, opener, sel, outside, label):
    """``opener()`` 打开的 ``sel`` 能按 Esc 关上，也能点 ``outside``（窗口坐标）关上。"""
    problems = []
    for how in ("Escape", "outside"):
        opener()
        page.wait_for_selector(sel)
        if how == "Escape":
            page.keyboard.press("Escape")
        else:
            page.mouse.click(*outside)
        try:
            page.wait_for_selector(sel, state="detached", timeout=2000)
        except Exception:  # noqa: BLE001 — 没关上就是这一条的结论，记下来往下查
            problems.append(f"{label}：{sel} {'按 Esc' if how == 'Escape' else '点外面'}关不上")
            page.keyboard.press("Escape")
    return problems


def mac_page(browser, server, size):
    page = new_page(browser, viewport=size)
    page.add_init_script(FAKE_NATIVE)
    page.goto(f"http://127.0.0.1:{server.port}/?role=mac")
    wait_online(page)
    page.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000)
    return page


def seed(page):
    """几块白板、一个文件夹、一块在回收站里，白板上有笔迹。"""
    draw(page, [(300, 300), (420, 360), (520, 300)], pointer_type="mouse")
    wait_strokes(page, 1)
    page.evaluate("() => whiteboard.net.send({ t: 'newfolder', name: '数学' })")
    page.wait_for_function("() => whiteboard.ui.folders.includes('数学')")
    for kind, folder in (("note", "数学"), ("board", ""), ("note", "")):
        page.evaluate("([k, f]) => whiteboard.net.send({ t: 'newboard', kind: k, folder: f })", [kind, folder])
    page.wait_for_function("() => whiteboard.ui.boards.length === 4")
    victim = page.evaluate("() => whiteboard.ui.boards.find(b => b.id !== whiteboard.state.id && !b.folder).id")
    page.evaluate("(id) => whiteboard.net.send({ t: 'delboard', board: id })", victim)
    page.wait_for_function("() => whiteboard.ui.boards.some(b => b.trashed)")
    page.wait_for_function("() => whiteboard.net.status === 'online'")


@pytest.mark.parametrize("size_name", list(MAC_SIZES))
def test_mac_editor_states(browser, server, size_name):
    size = MAC_SIZES[size_name]
    page = mac_page(browser, server, size)
    seed(page)
    problems = []
    w, h = size
    blank = (w // 2, h // 2 - 40)
    problems += check(page, f"{size_name}-editor")

    page.click('#toolbar button[title="颜色与粗细"]')
    problems += check(page, f"{size_name}-color")
    page.keyboard.press("Escape")
    page.click('#toolbar button[title="橡皮擦"]')
    page.click('#toolbar button[title="颜色与粗细"]')
    problems += check(page, f"{size_name}-eraser")
    page.keyboard.press("Escape")
    page.click('#toolbar button[title="钢笔"]')

    page.click('#toolbar button[title="更多"]')
    problems += check(page, f"{size_name}-more")
    page.keyboard.press("Escape")

    page.click('#topbar button[title="白板设置"]')
    problems += check(page, f"{size_name}-settings")
    page.keyboard.press("Escape")
    page.click("#status")
    page.wait_for_function("() => document.querySelector('.connect-qr')?.naturalWidth > 0")
    problems += check(page, f"{size_name}-connect")
    page.keyboard.press("Escape")

    page.evaluate("() => whiteboard.ui.confirmClear()")
    problems += check(page, f"{size_name}-confirm")
    page.click('.dialog button[title="取消"]')

    page.evaluate("() => whiteboard.ui.message('正在导入 一份很长很长的文件名字.pdf…', 'doc', 60000)")
    problems += check(page, f"{size_name}-notice")
    page.evaluate("() => document.querySelectorAll('.notice').forEach(n => n.remove())")

    page.click('#topbar button[title="导出"]')
    page.wait_for_selector(".export-card")
    problems += check(page, f"{size_name}-export")
    page.click('.export-seg button:has-text("整张长图")')
    problems += check(page, f"{size_name}-export-whole")
    page.click('.export-seg button:has-text("WBZ")')
    problems += check(page, f"{size_name}-export-wbz")
    page.click('.export-seg button:has-text("PNG")')
    page.click('.export-seg button:has-text("按页")')
    problems += check(page, f"{size_name}-export-png")
    page.click('.export-paging button:has-text("调整")')
    page.wait_for_selector(".export-frame")
    problems += check(page, f"{size_name}-export-adjust")
    page.click('.export-bar button:has-text("完成")')
    page.click('.export-footer button:has-text("取消")')

    # 拖着文件经过白板
    page.evaluate("""() => { const d = new DataTransfer(); d.items.add(new File(['x'], 'a.pdf', {type: 'application/pdf'}));
        document.getElementById('stage').dispatchEvent(new DragEvent('dragover', {dataTransfer: d, bubbles: true, cancelable: true})); }""")
    problems += check(page, f"{size_name}-drop")
    page.evaluate("() => whiteboard.ui.showDrop(null)")

    # 按 Esc 关掉面板之后，按钮上不留焦点框
    page.click('#topbar button[title="白板设置"]')
    page.keyboard.press("Escape")
    assert page.evaluate("() => !document.activeElement.closest('#topbar')"), "按 Esc 之后焦点还留在顶栏按钮上"

    # 弹出层都能按 Esc、点外面关上
    problems += closes(page, lambda: page.click('#toolbar button[title="更多"]'), ".menu", blank, size_name)
    problems += closes(page, lambda: page.click('#toolbar button[title="颜色与粗细"]'), ".popover", blank, size_name)
    problems += closes(page, lambda: page.click('#topbar button[title="白板设置"]'), ".panel", blank, size_name)
    problems += closes(page, lambda: page.click("#status"), ".panel", blank, size_name)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size_name", list(MAC_SIZES))
def test_mac_library_states(browser, server, size_name):
    size = MAC_SIZES[size_name]
    page = mac_page(browser, server, size)
    seed(page)
    w, h = size
    problems = []
    open_gallery(page)
    problems += check(page, f"{size_name}-library-recent")
    board_menu(page)
    problems += check(page, f"{size_name}-library-menu")
    page.hover('.menu .menu-item:has-text("移到文件夹")')
    problems += check(page, f"{size_name}-library-submenu")
    page.keyboard.press("Escape")
    page.click(".lib-scroll", button="right", position={"x": 20, "y": h - 140})
    problems += check(page, f"{size_name}-library-background-menu")
    page.keyboard.press("Escape")
    page.click('.lib-side .lib-nav[data-folder="数学"]', button="right")
    problems += check(page, f"{size_name}-library-folder-menu")
    page.keyboard.press("Escape")
    page.click(".lib-new")
    problems += check(page, f"{size_name}-library-new")
    page.keyboard.press("Escape")

    page.click('.lib-side .lib-nav:has(.lib-nav-label:text-is("全部白板"))')
    page.click(".lib-sort")
    problems += check(page, f"{size_name}-library-sort")
    page.keyboard.press("Escape")
    page.click('.lib-seg button[title="列表"]')
    problems += check(page, f"{size_name}-library-list")
    page.click('.lib-seg button[title="图标"]')
    page.click('.lib-side .lib-nav[data-folder="数学"]')
    problems += check(page, f"{size_name}-library-folder")
    page.click('.lib-side .lib-nav:has(.lib-nav-label:text-is("回收站"))')
    problems += check(page, f"{size_name}-library-trash")
    page.click(".board-search")
    page.keyboard.type("查无此板")
    problems += check(page, f"{size_name}-library-search-empty")
    page.keyboard.press("Escape")
    page.click('.lib-side button[title="新建文件夹"]')
    problems += check(page, f"{size_name}-library-new-folder")
    page.keyboard.press("Escape")
    page.click('.lib-side .lib-nav:has(.lib-nav-label:text-is("最近"))')
    board_menu(page)
    menu_pick(page, "重命名")
    problems += check(page, f"{size_name}-library-rename")
    page.keyboard.press("Escape")

    blank = (w - 40, h - 40)
    problems += closes(page, lambda: board_menu(page), ".menu", blank, f"{size_name}-library")
    problems += closes(page, lambda: page.click(".lib-new"), ".lib-new-panel", blank, f"{size_name}-library")
    page.click('.lib-side .lib-nav:has(.lib-nav-label:text-is("全部白板"))')
    problems += closes(page, lambda: page.click(".lib-sort"), ".menu", blank, f"{size_name}-library")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size_name", list(TOUCH_SIZES))
def test_ipad_states(browser, server, size_name):
    size = TOUCH_SIZES[size_name]
    page = new_page(browser, viewport=size, user_agent=IPAD_UA, touch=True)
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(page)
    page.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    problems = check(page, f"{size_name}-editor")
    page.tap('#topright button[title="白板设置"]')
    problems += check(page, f"{size_name}-settings")
    page.keyboard.press("Escape")
    page.tap("#status")
    problems += check(page, f"{size_name}-status")
    page.keyboard.press("Escape")
    page.tap('#topright button[title="导出"]')
    page.wait_for_selector(".export-card")
    problems += check(page, f"{size_name}-export")
    page.tap('.export-footer button:has-text("取消")')
    page.tap('#topleft button[title="白板库"]')
    page.wait_for_selector(".gallery.library")
    problems += check(page, f"{size_name}-library")
    page.tap(".lib-new")
    problems += check(page, f"{size_name}-library-new")
    page.keyboard.press("Escape")
    assert not problems, "\n".join(problems)


def test_tablet_without_picker_states(browser, server):
    """别的平板：普通工具栏，更多菜单里有手指书写和形状识别。"""
    page = new_page(browser, viewport=(1024, 768), user_agent=TABLET_UA, touch=True)
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(page)
    problems = check(page, "tablet-editor")
    page.tap('#toolbar button[title="更多"]')
    problems += check(page, "tablet-more")
    page.tap("#stage", position={"x": 500, "y": 300})
    page.wait_for_selector(".menu", state="detached")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size", [(420, 360), (640, 420)])
def test_sketch_page_states(browser, server, size):
    """速写页：插件弹出页（420×360）和单独打开的窗口。"""
    page = new_page(browser, viewport=size)
    page.goto(f"http://127.0.0.1:{server.port}/sketch")
    page.wait_for_function("() => window.sketchPad && sketchPad.status === 'online'")
    problems = check(page, f"sketch-{size[0]}x{size[1]}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size_name", list(MAC_SIZES))
def test_mac_import_and_page_states(browser, server, size_name):
    """导入对话框、页的右键菜单、空白页（docs/backlog/014-import-pages.md）。"""
    from e2e.test_doc_pages import drop_pdf, page_point, wait_doc

    size = MAC_SIZES[size_name]
    page = mac_page(browser, server, size)
    w, h = size
    problems = []
    before = page.evaluate("() => whiteboard.state.id")
    drop_pdf(page, "高等数学 上册.pdf", 12)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.import-dialog .export-thumb-image')].slice(0, 4)"
        ".every((i) => i.complete && i.naturalWidth > 0)",
        timeout=20000,
    )
    problems += check(page, f"{size_name}-import")
    page.fill(".import-dialog .export-range-field", "3-15")
    problems += check(page, f"{size_name}-import-range-error")
    page.fill(".import-dialog .export-range-field", "3-5")
    page.check('.import-dialog .export-radio:has-text("每页后面加一页空白") input')
    problems += check(page, f"{size_name}-import-range")
    page.click(".import-dialog .lib-btn.primary")
    wait_doc(page, before)

    x, y = page_point(page, 1)
    page.mouse.click(x, y, button="right")
    problems += check(page, f"{size_name}-page-menu-blank")
    page.keyboard.press("Escape")
    x, y = page_point(page, 0)
    page.mouse.click(x, y, button="right")
    problems += check(page, f"{size_name}-page-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".menu", state="detached")
    page_point(page, 1, fraction=0.5)
    problems += check(page, f"{size_name}-doc-blank-page")
    problems += closes(
        page, lambda: page.mouse.click(x, y, button="right"), ".menu", (w - 30, h - 30), f"{size_name}-page-menu"
    )
    assert not problems, "\n".join(problems)

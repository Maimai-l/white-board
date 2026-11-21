"""白板库（docs/backlog/011-ui-redesign.md）：边栏、新建、打开、改名、搜索、文件夹、
回收站、复制、显示方式、拖动，Mac 上用鼠标，iPad 上用手指。"""

from __future__ import annotations

import base64
import io

import pytest

from e2e.support import (
    DROP_TARGET_LIT,
    SLOT_IS_LAST,
    all_in_folder,
    board_menu,
    draw,
    file_into,
    folder_row,
    gallery_with_folder,
    gone,
    menu_pick,
    mouse_drag,
    new_board,
    new_folder,
    open_gallery,
    open_mac,
    open_pages,
    open_touch,
    reload,
    stroke_count,
    wait_strokes,
)

BOARD_CARD = ".lib-scroll .board-item[data-board] .board-card"
COUNT_BOARDS = "() => document.querySelectorAll('.lib-scroll .board-item[data-board]').length"
SAME_ORDER = "(ids) => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) === JSON.stringify(ids)"
SHOWN_NAMES = "() => [...document.querySelectorAll('.lib-scroll .board-name')].map(n => n.textContent)"
TITLE = "() => document.querySelector('.lib-title').textContent"

# 手指按住一会儿再拖：按下在卡片上，移动和抬起发给 window（和浏览器的指针捕获一样）。
# 拖到落点之后停住，等 ready（页面上看得见的结果，见 support.mouse_drag）成立再松手
TOUCH_DRAG = """
async ([fromSel, toSel, holdMs, steps, ready]) => {
  const from = document.querySelector(fromSel);
  const a = __t.center(fromSel), b = __t.center(toSel);
  const init = { pointerType: 'touch', pointerId: 11, pressure: 0, buttons: 0 };
  __t.fire('pointerdown', a[0], a[1], init, from);
  await new Promise((done) => setTimeout(done, holdMs));
  for (let i = 1; i <= steps; i++) {
    __t.fire('pointermove', a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, init, window);
    await new Promise((done) => requestAnimationFrame(done));
  }
  const isReady = new Function(`return (${ready})()`);
  const end = performance.now() + 5000;
  while (!isReady() && performance.now() < end) await new Promise((done) => requestAnimationFrame(done));
  __t.fire('pointerup', b[0], b[1], init, window);
}
"""

# 一放下就划走（不按住），返回划的过程中卡片有没有浮起来
SWIPE = """
async ([sel, dx, dy, steps]) => {
  const card = document.querySelector(sel);
  const [x, y] = __t.center(sel);
  const init = { pointerType: 'touch', pointerId: 12, pressure: 0, buttons: 0 };
  __t.fire('pointerdown', x, y, init, card);
  for (let i = 1; i <= steps; i++) {
    __t.fire('pointermove', x + dx * i / steps, y + dy * i / steps, init, window);
    await new Promise((done) => requestAnimationFrame(done));
  }
  await new Promise((done) => setTimeout(done, 60));
  const lifted = !!document.querySelector('.board-item.lifted');
  __t.fire('pointerup', x + dx, y + dy, init, window);
  return lifted;
}
"""

# 长按：按住 holdMs 不动再松手，浏览器补的那一下 click 也发出去
LONG_PRESS = """
async ([sel, holdMs]) => {
  const card = document.querySelector(sel);
  const [x, y] = __t.center(sel);
  const init = { pointerType: 'touch', pointerId: 13, pressure: 0, buttons: 0 };
  __t.fire('pointerdown', x, y, init, card);
  await new Promise((done) => setTimeout(done, holdMs));
  __t.fire('pointerup', x, y, init, window);
  card.dispatchEvent(new MouseEvent('click', { clientX: x, clientY: y, bubbles: true, cancelable: true }));
}
"""

# 把一个文件拖到 target（选择器，空就是窗口）上放下
DROP_FILE = """([name, type, base64, target]) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, { type }));
  const node = target ? document.querySelector(target) : window;
  for (const kind of ["dragover", "drop"]) {
    node.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true }));
  }
}"""


def png_base64():
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (64, 48), (240, 240, 240)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def board_ids(page):
    return page.evaluate("() => whiteboard.ui.boards.map(b => b.id)")


def go(page, label):
    """点边栏里的一行（最近、全部白板、回收站或者某个文件夹）。"""
    page.click(f'.lib-side .lib-nav:has(.lib-nav-label:text-is("{label}"))')
    page.wait_for_function(f"{TITLE} === {label!r}")


def rename_from_menu(page, board_id, name):
    board_menu(page, board_id)
    menu_pick(page, "重命名")
    field = page.wait_for_selector(f'input.board-name[data-focus-key="name:{board_id}"]')
    page.wait_for_function("(id) => document.activeElement.dataset.focusKey === 'name:' + id", arg=board_id)
    assert field.evaluate("(i) => i.selectionStart === 0 && i.selectionEnd === i.value.length")
    page.keyboard.type(name)
    page.keyboard.press("Enter")


# ----------------------------------------------------------------- 打开、新建、切换


def test_board_switch_and_settings_follow(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (400, 350)])
    wait_strokes(mac, 1)
    first_board = ipad.evaluate("() => whiteboard.state.id")

    new_board(mac, "大白板")
    ipad.wait_for_function("(was) => whiteboard.state.id !== was", arg=first_board)
    assert stroke_count(ipad) == 0  # 新白板是空的
    gone(mac, ".gallery")  # 新建之后直接进到新白板

    # 背景改动会同步到 iPad
    mac.click('#topright button[title="白板设置"]')
    mac.click('.bg-opt[title="点阵"]')
    ipad.wait_for_function("() => whiteboard.state.meta.background.pattern === 'dots'")


def test_the_library_lists_recent_boards_with_the_current_one_selected(browser, server):
    """打开白板库：边栏有最近、全部白板（带数量）和回收站；最近按今天分段，
    当前那块白板是选中的。单击只是选中，双击才打开。"""
    mac = open_mac(browser, server.port)
    first = mac.evaluate("() => whiteboard.state.id")
    second = new_board(mac, "笔记")
    open_gallery(mac)

    assert mac.evaluate(TITLE) == "最近"
    assert mac.locator(".lib-section-title").all_inner_texts() == ["今天"]
    rows = mac.locator(".lib-side .lib-nav .lib-nav-label").all_inner_texts()
    assert rows[:2] == ["最近", "全部白板"] and rows[-1] == "回收站"
    assert mac.locator('.lib-side .lib-nav[data-folder=""] .lib-nav-count').inner_text() == "2"
    assert mac.evaluate(SHOWN_NAMES) == ["笔记", "白板"]  # 最近改过的在前
    selected = (
        "() => [...document.querySelectorAll('.board-card.selected')].map(c => c.closest('[data-board]').dataset.board)"
    )
    assert mac.evaluate(selected) == [second]

    # 单击只是选中，界面不关
    mac.click(f'.board-item[data-board="{first}"] .board-card')
    mac.wait_for_function(f"() => ({selected})().join() === {first!r}")
    assert mac.evaluate("() => whiteboard.state.id") == second
    # 双击打开
    mac.dblclick(f'.board-item[data-board="{first}"] .board-card')
    mac.wait_for_function("(id) => whiteboard.state.id === id", arg=first)
    gone(mac, ".gallery")


def test_command_click_selects_several_boards(browser, server):
    """⌘ 单击多选，标题旁边说选了几项；菜单里的移动作用在整个选中上。"""
    mac = open_mac(browser, server.port)
    new_board(mac, "笔记")
    open_gallery(mac)
    folder = new_folder(mac, "数学")
    go(mac, "全部白板")
    cards = mac.locator(BOARD_CARD)
    cards.nth(0).click()
    cards.nth(1).click(modifiers=["Control"])
    mac.wait_for_function("() => document.querySelector('.lib-note').textContent === '已选择 2 项'")
    board_menu(mac, mac.evaluate("() => whiteboard.ui.boards[0].id"))
    menu_pick(mac, "移到文件夹", folder)
    mac.wait_for_function(all_in_folder(folder))


def test_board_cards_show_names_and_dates_and_rename_from_the_menu(browser, server):
    """卡片下面有名字、延伸方式和时间；右键重命名，两端都跟着变。"""
    mac, ipad = open_pages(browser, server.port)
    board_id = new_board(mac, "笔记")
    open_gallery(mac)

    assert mac.evaluate(SHOWN_NAMES) == ["笔记", "白板"]  # 没名字就按延伸方式给默认
    kinds = "() => [...document.querySelectorAll('.board-kind')].map(k => k.title)"
    assert mac.evaluate(kinds) == ["向下延伸", "四向延伸"]
    assert mac.evaluate("() => document.querySelectorAll('.board-date').length") == 2

    rename_from_menu(mac, board_id, "线性代数")
    renamed = "() => whiteboard.ui.boards.some(b => b.name === '线性代数')"
    mac.wait_for_function(renamed, timeout=4000)
    ipad.wait_for_function(renamed, timeout=4000)
    mac.wait_for_function(f"() => ({SHOWN_NAMES})()[0] === '线性代数'")
    assert stroke_count(mac) == 0  # 没把整块白板重发一遍


def test_board_search_filters_by_name(browser, server):
    """搜索框按名字筛，搜不到给个空态；Esc 清空搜索，不关界面。"""
    mac = open_mac(browser, server.port)
    board_id = new_board(mac, "笔记")
    open_gallery(mac)
    rename_from_menu(mac, board_id, "线性代数")
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.name === '线性代数')")
    assert mac.evaluate(COUNT_BOARDS) == 2

    mac.click(".board-search")
    mac.keyboard.type("线性")
    mac.wait_for_function(f"{COUNT_BOARDS} === 1")
    assert mac.evaluate(TITLE) == "搜索结果"
    assert mac.evaluate(SHOWN_NAMES) == ["线性代数"]

    # 默认叫法也能搜到：另一块没起名，显示的是「白板」
    mac.keyboard.press("Control+a")
    mac.keyboard.type("白板")
    mac.wait_for_function(f"() => ({SHOWN_NAMES})().join() === '白板'")

    mac.keyboard.press("Control+a")
    mac.keyboard.type("查无此板")
    mac.wait_for_selector(".gallery-empty")

    mac.keyboard.press("Escape")
    mac.wait_for_function(f"{COUNT_BOARDS} === 2")
    assert mac.query_selector(".gallery") is not None


def test_list_view_and_sorting_are_remembered(browser, server):
    """显示方式换成列表、排序换成名称，重新打开白板库还是这样。"""
    mac = open_mac(browser, server.port)
    board_id = new_board(mac, "笔记")
    open_gallery(mac)
    rename_from_menu(mac, board_id, "阿尔法")
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.name === '阿尔法')")
    go(mac, "全部白板")

    mac.click('.lib-seg button[title="列表"]')
    mac.wait_for_selector(".lib-table")
    headers = mac.locator('.lib-row.head [role="columnheader"]').all_inner_texts()
    assert headers == ["名称", "文件夹", "修改时间"]
    mac.click(".lib-sort")
    menu_pick(mac, "名称")
    mac.wait_for_function(f"() => ({SHOWN_NAMES})().join() === '阿尔法,白板'")

    mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.gallery')")
    open_gallery(mac)
    go(mac, "全部白板")
    mac.wait_for_selector(".lib-table")
    assert mac.locator(".lib-sort").inner_text() == "名称"


# ----------------------------------------------------------------- 文件夹


def test_boards_can_be_moved_into_a_folder(browser, server):
    """文件夹在边栏里：点进去只剩里面的白板，在里面新建的也留在里面；
    再打开白板库，停在当前白板所在的文件夹。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    assert folder == "未命名文件夹"

    file_into(mac, folder)
    ipad.wait_for_function(all_in_folder(folder))
    assert mac.locator(f"{folder_row(folder)} .lib-nav-count").inner_text() == "1"

    go(mac, folder)
    mac.wait_for_function(f"{COUNT_BOARDS} === 1")
    mac.click(".lib-new")
    mac.click('.kind-tile:has-text("向下延伸")')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    mac.wait_for_function("() => whiteboard.ui.boards.length === 2")
    assert mac.evaluate(all_in_folder(folder))

    open_gallery(mac)
    assert mac.evaluate(TITLE) == folder


def test_deleting_a_folder_keeps_the_boards(browser, server):
    """删文件夹只是取消归类，里面的白板一块都不能少。"""
    mac = open_mac(browser, server.port)
    folder = gallery_with_folder(mac)
    file_into(mac, folder)

    mac.click(folder_row(folder), button="right")
    menu_pick(mac, "删除文件夹")
    assert mac.locator(".dialog.ask .ask-text").inner_text() == "删除这个文件夹"
    assert mac.locator(".dialog.ask .ask-note").inner_text() == "里面的白板会留在全部白板中"
    mac.click('.dialog button[title="删除"]')
    mac.wait_for_function("() => whiteboard.ui.folders.length === 0")
    assert mac.evaluate("() => whiteboard.ui.boards.length") == 1
    assert mac.evaluate("() => whiteboard.ui.boards.every(b => !b.folder)")


def test_a_folder_can_be_renamed_from_the_sidebar(browser, server):
    """双击边栏里的文件夹改名，里面每块白板上记的名字一起改掉。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    file_into(mac, folder)

    mac.dblclick(folder_row(folder))
    mac.wait_for_selector('.lib-nav-edit[data-focus-key="folder-edit"]')
    mac.keyboard.press("Control+a")
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('数学')")
    assert mac.evaluate(all_in_folder("数学"))
    ipad.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")


def test_a_new_folder_is_named_right_away_like_finder(browser, server):
    """新建文件夹后名字框直接可编辑、默认名全选：直接打字就是新名字，回车后建好。"""
    mac, ipad = open_pages(browser, server.port)
    draft = '.lib-nav-edit[data-focus-key="folder-edit"]'

    def start_new_folder():
        mac.click('.lib-side button[title="新建文件夹"]')
        mac.wait_for_selector(draft)
        mac.wait_for_function("() => document.activeElement.dataset.focusKey === 'folder-edit'")

    open_gallery(mac)
    start_new_folder()
    start, end, length = mac.evaluate(
        "() => { const i = document.activeElement; return [i.selectionStart, i.selectionEnd, i.value.length]; }"
    )
    assert start == 0 and end == length > 0
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 0  # 回车之前还没建
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    for page in (mac, ipad):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")
    mac.wait_for_selector(folder_row("数学"))

    # Esc：按默认名建，和访达一样不会撤销新建
    start_new_folder()
    mac.keyboard.type("物理")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('未命名文件夹')")
    assert mac.query_selector(".gallery") is not None, "Esc 只结束起名，不关界面"

    # 重名：提示并留在编辑状态
    start_new_folder()
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    assert mac.evaluate("() => document.activeElement.dataset.focusKey") == "folder-edit"
    mac.keyboard.type("化学")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('化学')")
    assert sorted(mac.evaluate("() => whiteboard.ui.folders")) == sorted(["数学", "未命名文件夹", "化学"])


def test_files_dropped_on_the_library_land_in_the_right_folder(browser, server):
    """白板库停在文件夹里时把图片拖进窗口，新白板留在这个文件夹里；拖到边栏的另一个
    文件夹上就进那个文件夹。白板库关着时落在当前白板所在的文件夹。"""
    png = png_base64()
    mac = open_mac(browser, server.port)
    gallery_with_folder(mac, "课件")
    new_folder(mac, "讲义")
    go(mac, "课件")
    before = mac.evaluate("() => whiteboard.ui.boards.length")

    mac.evaluate(DROP_FILE, ["第一课.png", "image/png", png, ""])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 1", arg=before)
    doc = mac.evaluate("() => whiteboard.ui.boards.find(b => b.kind === 'doc')")
    assert doc["folder"] == "课件"

    if not mac.query_selector(".gallery"):
        open_gallery(mac)
    mac.evaluate(DROP_FILE, ["第二课.png", "image/png", png, folder_row("讲义")])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 2", arg=before)
    folders = mac.evaluate("() => whiteboard.ui.boards.filter(b => b.kind === 'doc').map(b => b.folder)")
    assert sorted(folders) == sorted(["课件", "讲义"])

    # 白板库关着：落在当前白板（刚建的文档板）所在的文件夹
    mac.wait_for_function("() => whiteboard.state.meta && whiteboard.state.meta.data && whiteboard.state.meta.data.doc")
    current = mac.evaluate("() => whiteboard.ui.boards.find(b => b.id === whiteboard.state.id).folder")
    if mac.query_selector(".gallery"):
        mac.keyboard.press("Escape")
        mac.keyboard.press("Escape")
    mac.wait_for_function("() => !document.querySelector('.gallery')")
    mac.evaluate(DROP_FILE, ["第三课.png", "image/png", png, ""])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 3", arg=before)
    third = mac.evaluate("() => whiteboard.ui.boards.find(b => b.doc && b.doc.name === '第三课.png').folder")
    assert third == current


# ----------------------------------------------------------------- 回收站、复制


def test_trash_restore_and_delete_forever(browser, server):
    """移到回收站的白板从最近里消失、在回收站里出现；恢复回到原来的文件夹；
    永久删除和清空回收站要先确认。"""
    mac, ipad = open_pages(browser, server.port)
    first = mac.evaluate("() => whiteboard.state.id")
    second = new_board(mac, "笔记")
    open_gallery(mac)
    folder = new_folder(mac, "数学")
    go(mac, "最近")
    board_menu(mac, first)
    menu_pick(mac, "移到文件夹", folder)
    mac.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === {first!r}).folder === {folder!r}")

    board_menu(mac, first)
    menu_pick(mac, "移到回收站")
    for page in (mac, ipad):
        page.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === {first!r}).trashed > 0")
    mac.wait_for_function(f"() => !document.querySelector('.lib-scroll [data-board=\"{first}\"]')")
    assert mac.locator('.lib-side .lib-nav:has-text("回收站") .lib-nav-count').inner_text() == "1"

    go(mac, "回收站")
    assert mac.locator(".lib-hint").inner_text() == "白板会在 30 天后永久删除"
    assert mac.evaluate(f"() => !!document.querySelector('.lib-scroll [data-board=\"{first}\"]')")
    board_menu(mac, first)
    menu_pick(mac, "恢复")
    mac.wait_for_function(f"() => !whiteboard.ui.boards.find(b => b.id === {first!r}).trashed")
    assert mac.evaluate(f"() => whiteboard.ui.boards.find(b => b.id === {first!r}).folder") == folder

    # 当前白板也能移进回收站：所有设备换到别的白板上
    go(mac, "最近")
    board_menu(mac, second)
    menu_pick(mac, "移到回收站")
    for page in (mac, ipad):
        page.wait_for_function("(id) => whiteboard.state.id !== id", arg=second)

    go(mac, "回收站")
    board_menu(mac, second)
    menu_pick(mac, "永久删除")
    assert mac.locator(".dialog.ask .ask-text").inner_text() == "永久删除这块白板"
    mac.click('.dialog button[title="删除"]')
    for page in (mac, ipad):
        page.wait_for_function("(id) => !whiteboard.ui.boards.some(b => b.id === id)", arg=second)
    assert [meta["id"] for meta in server.hub.store.list_metas()] == [first]
    mac.wait_for_selector('.gallery-empty:has-text("回收站是空的")')


def test_empty_trash(browser, server):
    mac = open_mac(browser, server.port)
    first = mac.evaluate("() => whiteboard.state.id")
    second = new_board(mac, "笔记")
    open_gallery(mac)
    board_menu(mac, first)
    menu_pick(mac, "移到回收站")
    mac.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === {first!r}).trashed > 0")
    go(mac, "回收站")
    mac.click('.lib-btn:has-text("清空回收站")')
    assert mac.locator(".dialog.ask .ask-text").inner_text() == "清空回收站"
    mac.click('.dialog button[title="清空"]')
    mac.wait_for_function("(id) => whiteboard.ui.boards.map(b => b.id).join() === id", arg=second)


def test_a_board_can_be_duplicated(browser, server):
    """复制：多出一块同样内容的白板，名字后面加副本，当前白板不变。"""
    mac, ipad = open_pages(browser, server.port)
    draw(mac, [(300, 300), (420, 360)])
    wait_strokes(mac, 1)
    original = mac.evaluate("() => whiteboard.state.id")
    open_gallery(mac)
    board_menu(mac, original)
    menu_pick(mac, "复制")
    for page in (mac, ipad):
        page.wait_for_function("() => whiteboard.ui.boards.length === 2")
    copy = mac.evaluate(f"() => whiteboard.ui.boards.find(b => b.id !== {original!r})")
    assert copy["name"].endswith("副本")
    assert mac.evaluate("() => whiteboard.state.id") == original
    mac.dblclick(f'.board-item[data-board="{copy["id"]}"] .board-card')
    mac.wait_for_function("(id) => whiteboard.state.id === id", arg=copy["id"])
    wait_strokes(mac, 1)


# ----------------------------------------------------------------- 拖动（Mac 上用鼠标）


def test_a_board_can_be_dragged_onto_a_sidebar_folder_and_back_out(browser, server):
    """把卡片拖到边栏的文件夹上就是移进去，拖到全部白板上就是拿出来，拖到回收站上就是删除。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)

    mouse_drag(mac, BOARD_CARD, folder_row(folder), DROP_TARGET_LIT)
    mac.wait_for_function(all_in_folder(folder))
    ipad.wait_for_function(all_in_folder(folder))

    go(mac, folder)
    mouse_drag(mac, BOARD_CARD, folder_row(""), DROP_TARGET_LIT)
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => !b.folder)")
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 1  # 文件夹还在，只是空了

    # 唯一那块白板进了回收站：服务端另建一块空白的当作当前白板
    go(mac, "全部白板")
    only = mac.evaluate("() => whiteboard.ui.boards[0].id")
    mouse_drag(mac, BOARD_CARD, '.lib-side .lib-nav:has-text("回收站")', DROP_TARGET_LIT)
    mac.wait_for_function("(id) => whiteboard.ui.boards.find(b => b.id === id).trashed > 0", arg=only)
    mac.wait_for_function("(id) => whiteboard.state.id !== id", arg=only)


def test_boards_can_be_dragged_into_a_new_order_when_arranged_by_hand(browser, server):
    """排序方式是手动排列时，拖着卡片走别的卡片让位；松手之后这个顺序两端都认，也存得住。"""
    mac, ipad = open_pages(browser, server.port)
    new_board(mac, "笔记")
    new_board(mac, "笔记")
    open_gallery(mac)
    go(mac, "全部白板")
    mac.click(".lib-sort")
    menu_pick(mac, "手动排列")
    mac.wait_for_function(f"{COUNT_BOARDS} === 3")

    order = "() => [...document.querySelectorAll('.gallery-grid .board-item[data-board]')].map(n => n.dataset.board)"
    before = mac.evaluate(order)
    expected = before[1:] + before[:1]

    # 把第一块拖到第三块的右半边，它就排到最后
    mouse_drag(mac, f"{BOARD_CARD} >> nth=0", f"{BOARD_CARD} >> nth=2", SLOT_IS_LAST, at=(0.85, 0.5))
    mac.wait_for_function(SAME_ORDER, arg=expected)
    ipad.wait_for_function(SAME_ORDER, arg=expected)
    mac.wait_for_function("() => !document.querySelector('.board-item.lifted')")
    assert mac.evaluate(order) == expected

    # 刷新之后还是这个顺序：顺序存在服务端的索引里，不是本机记的
    reload(mac)
    open_gallery(mac)
    go(mac, "全部白板")
    mac.wait_for_selector(".board-item")
    assert mac.evaluate(order) == expected


# ----------------------------------------------------------------- 触摸设备上用手指


def test_a_sideways_swipe_starts_the_drag_without_waiting(browser, server):
    """列表竖着滚，所以横着走的手势只可能是想拖：不用按住，立刻起拖。竖着划还是滚列表。"""
    ipad = open_touch(browser, server.port)
    open_gallery(ipad)
    ipad.wait_for_selector(".board-card")
    assert ipad.evaluate(SWIPE, [BOARD_CARD, 120, 0, 6]) is True
    ipad.wait_for_function("() => !document.querySelector('.board-item.lifted')")
    assert ipad.evaluate(SWIPE, [BOARD_CARD, 0, 120, 6]) is False


def test_a_long_press_opens_the_menu_instead_of_the_board(browser, server):
    """按住不挪再松手：卡片浮起来又落回去，弹出菜单，不切过去。"""
    ipad = open_touch(browser, server.port)
    open_gallery(ipad)
    ipad.wait_for_selector(".board-card")
    before = ipad.evaluate("() => whiteboard.state.id")
    ipad.evaluate(LONG_PRESS, [BOARD_CARD, 400])
    ipad.wait_for_selector(".menu")
    labels = ipad.locator(".menu .menu-label").all_inner_texts()
    assert labels[:2] == ["打开", "重命名"] and labels[-1] == "移到回收站"
    assert ipad.evaluate("() => whiteboard.state.id") == before
    assert ipad.query_selector(".gallery") is not None


def test_a_finger_can_drag_a_board_onto_a_sidebar_folder(browser, server):
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    open_gallery(ipad)
    ipad.wait_for_selector(folder_row(folder))
    ipad.evaluate(TOUCH_DRAG, [BOARD_CARD, folder_row(folder), 500, 6, DROP_TARGET_LIT])
    ipad.wait_for_function(all_in_folder(folder))
    mac.wait_for_function(all_in_folder(folder))


def ipad_with_two_boards(browser, server):
    """Mac 上新建一块笔记，于是一共两块白板；iPad 打开白板库。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    first = mac.evaluate("() => whiteboard.state.id")
    second = new_board(mac, "笔记")
    ipad.wait_for_function("(id) => whiteboard.state.id === id", arg=second)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.tap('#topleft button[title="白板库"]')
    ipad.wait_for_selector(f'.board-item[data-board="{first}"]')
    return mac, ipad, first, second


def board_field(board, field):
    """``wait_for_function`` 用：某块白板的某个字段，参数是期望值。"""
    return f"(want) => whiteboard.ui.boards.find(b => b.id === '{board}').{field} === want"


def test_tapping_a_card_opens_that_board(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .board-card')
    for page in (ipad, mac):
        page.wait_for_function("(id) => whiteboard.state.id === id", arg=first)
    gone(ipad, ".gallery")


def test_a_new_folder_can_be_named_on_the_ipad(browser, server):
    """iPad 上点新建文件夹同样直接进入起名，键盘输入就是名字。"""
    mac, ipad = open_pages(browser, server.port)
    open_gallery(ipad)
    if ipad.get_attribute(".gallery.library", "data-side") == "hidden":
        ipad.tap(".lib-show-side")
    ipad.tap('.lib-side button[title="新建文件夹"]')
    ipad.wait_for_selector('.lib-nav-edit[data-focus-key="folder-edit"]')
    assert ipad.evaluate("() => document.activeElement.dataset.focusKey") == "folder-edit"
    ipad.keyboard.type("英语")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '英语'")


def test_a_board_can_be_renamed_by_typing_on_the_ipad(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.evaluate(LONG_PRESS, [f'.board-item[data-board="{first}"] .board-card', 400])
    ipad.tap('.menu .menu-item:has-text("重命名")')
    ipad.wait_for_selector(f'input.board-name[data-focus-key="name:{first}"]')
    style = ipad.evaluate(
        f"() => {{ const s = getComputedStyle(document.querySelector('input.board-name'));"
        " return s.webkitUserSelect || s.userSelect; }"
    )
    assert style != "none", "iPad 上这个输入框会打不了字"
    ipad.keyboard.type("线性代数")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(board_field(first, "name"), arg="线性代数")


def test_a_board_can_be_trashed_from_the_ipad(browser, server):
    """长按卡片，移到回收站：两边都看到它进了回收站，当前白板仍然有效。"""
    mac, ipad, first, second = ipad_with_two_boards(browser, server)
    ipad.evaluate(LONG_PRESS, [f'.board-item[data-board="{first}"] .board-card', 400])
    ipad.tap('.menu .menu-item:has-text("移到回收站")')
    for page in (ipad, mac):
        page.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === '{first}').trashed > 0")
        page.wait_for_function("(id) => whiteboard.state.id === id", arg=second)

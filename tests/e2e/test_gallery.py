"""白板选择界面：新建、切换、改名、搜索、文件夹、拖动排序和归类。"""

from __future__ import annotations

import base64
import io

import pytest

from e2e.support import (
    all_in_folder,
    draw,
    file_into,
    gallery_with_folder,
    mouse_drag,
    new_board,
    open_gallery,
    open_mac,
    open_pages,
    open_touch,
    reload,
    stroke_count,
    wait_strokes,
)

BOARD_CARD = ".board-item[data-board] .board-card"
COUNT_ITEMS = "() => document.querySelectorAll('.board-item').length"
SAME_ORDER = "(ids) => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) === JSON.stringify(ids)"

# 手指按住一会儿再拖：按下在卡片上，移动和抬起发给 window（和浏览器的指针捕获一样）
TOUCH_DRAG = """
async ([fromSel, toSel, holdMs, steps, dwellMs]) => {
  const from = document.querySelector(fromSel);
  const a = __t.center(fromSel), b = __t.center(toSel);
  const init = { pointerType: 'touch', pointerId: 11, pressure: 0, buttons: 0 };
  __t.fire('pointerdown', a[0], a[1], init, from);
  await new Promise((done) => setTimeout(done, holdMs));
  for (let i = 1; i <= steps; i++) {
    __t.fire('pointermove', a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, init, window);
    await new Promise((done) => requestAnimationFrame(done));
  }
  await new Promise((done) => setTimeout(done, dwellMs));
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

DROP_FILE = """([name, type, base64]) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, { type }));
  for (const kind of ["dragover", "drop"]) {
    window.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true }));
  }
}"""


def png_base64():
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (64, 48), (240, 240, 240)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def board_ids(page):
    return page.evaluate("() => whiteboard.ui.boards.map(b => b.id)")


# ----------------------------------------------------------------- 新建、切换、改名、搜索


def test_board_switch_and_settings_follow(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (400, 350)])
    wait_strokes(mac, 1)
    first_board = ipad.evaluate("() => whiteboard.state.id")

    new_board(mac, "大白板")
    ipad.wait_for_function("(was) => whiteboard.state.id !== was", arg=first_board)
    assert stroke_count(ipad) == 0  # 新白板是空的

    # 背景改动会同步到 iPad
    mac.click('button[title="白板设置"]')
    mac.click('.bg-opt[title="dots"]')
    ipad.wait_for_function("() => whiteboard.state.meta.background.pattern === 'dots'")


def test_board_cards_show_names_and_dates(browser, server):
    """卡片下面有名字和日期；没起名的显示默认叫法，改名之后两端都跟着变。"""
    mac, ipad = open_pages(browser, server.port)
    board_id = new_board(mac, "笔记")
    open_gallery(mac)
    mac.wait_for_selector(".board-item")

    names = "() => [...document.querySelectorAll('.board-name')].map(i => [i.value, i.placeholder])"
    assert mac.evaluate(names) == [["", "笔记"], ["", "白板"]]  # 没名字就按延伸方式给默认
    assert mac.evaluate("() => document.querySelectorAll('.board-date').length") == 2

    # 改名：输进去按回车，广播回来之后列表和 iPad 都认得
    field = mac.wait_for_selector(f'.board-name[data-focus-key="name:{board_id}"]')
    field.click()
    field.fill("  线性代数  ")
    mac.keyboard.press("Enter")
    renamed = "() => whiteboard.ui.boards.some(b => b.name === '线性代数')"
    mac.wait_for_function(renamed, timeout=4000)
    ipad.wait_for_function(renamed, timeout=4000)
    assert stroke_count(mac) == 0  # 没把整块白板重发一遍


def test_board_search_filters_by_name(browser, server):
    """搜索框按名字筛，搜不到给个空态；清空之后「新建」那块回来。"""
    mac = open_mac(browser, server.port)
    board_id = new_board(mac, "笔记")
    open_gallery(mac)
    field = mac.wait_for_selector(f'.board-name[data-focus-key="name:{board_id}"]')
    field.click()
    field.fill("线性代数")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.name === '线性代数')")

    assert mac.evaluate(COUNT_ITEMS) == 3  # 两块白板 + 新建

    mac.click(".board-search")
    mac.keyboard.type("线性")
    mac.wait_for_function(f"{COUNT_ITEMS} === 1")  # 搜索结果里不放「新建」
    assert mac.evaluate("() => document.querySelector('.board-name').value") == "线性代数"

    # 默认叫法也能搜到：另一块没起名，显示的是「白板」
    mac.keyboard.press("Control+a")
    mac.keyboard.type("白板")
    mac.wait_for_function(f"{COUNT_ITEMS} === 1")
    assert mac.evaluate("() => document.querySelector('.board-name').placeholder") == "白板"

    mac.keyboard.press("Control+a")
    mac.keyboard.type("查无此板")
    mac.wait_for_selector(".gallery-empty")

    mac.keyboard.press("Escape")  # 清空搜索，不关界面
    mac.wait_for_function(f"{COUNT_ITEMS} === 3")
    assert mac.query_selector(".gallery") is not None


# ----------------------------------------------------------------- 文件夹


def test_boards_can_be_filed_into_a_folder(browser, server):
    """文件夹是格子里的一块卡片：点进去只剩里面的白板，在里面新建的也留在里面。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    assert folder == "未命名文件夹"
    assert mac.evaluate("() => document.querySelector('.board-name.folder').value") == folder

    # 归类：卡片下面那个文件夹按钮，选现成的名字
    file_into(mac, folder)
    ipad.wait_for_function(all_in_folder(folder))

    # 最外面那一层只剩文件夹卡片和「新建」，白板收进去了
    mac.wait_for_function(f"{COUNT_ITEMS} === 2")
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    assert mac.evaluate(COUNT_ITEMS) == 2  # 里面那块白板 + 「新建」

    # 在文件夹里新建的白板跟着落在这个文件夹里
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    mac.wait_for_function("() => whiteboard.ui.boards.length === 2")
    assert mac.evaluate(all_in_folder(folder))

    # 再打开选择界面，直接停在当前白板所在的那一层
    open_gallery(mac)
    mac.wait_for_selector(".folder-bar")


def test_deleting_a_folder_keeps_the_boards(browser, server):
    """删文件夹只是取消归类，里面的白板一块都不能少。"""
    mac = open_mac(browser, server.port)
    folder = gallery_with_folder(mac)
    file_into(mac, folder)

    mac.click(".board-card.folder")
    mac.click('button[title="删除文件夹"]')
    mac.click('.dialog button[title="确定"]')
    mac.wait_for_function("() => whiteboard.ui.folders.length === 0")
    assert mac.evaluate("() => whiteboard.ui.boards.length") == 1
    assert mac.evaluate("() => whiteboard.ui.boards.every(b => !b.folder)")
    mac.wait_for_selector(".board-card:not(.folder)")


def test_a_folder_can_be_renamed_from_its_card(browser, server):
    """文件夹的名字就是它的身份，改名要把里面每块白板上记的名字一起改掉。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    file_into(mac, folder)

    field = mac.wait_for_selector(f'.board-name[data-focus-key="folder:{folder}"]')
    field.click()
    field.fill("  数学  ")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('数学')")
    assert mac.evaluate(all_in_folder("数学"))
    ipad.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")


def test_a_new_folder_is_named_right_away_like_finder(browser, server):
    """新建文件夹后名字框直接可编辑、默认名全选：直接打字就是新名字，回车后建好。"""
    mac, ipad = open_pages(browser, server.port)
    draft = '.board-name[data-focus-key="folder-draft"]'

    def start_new_folder():
        mac.click(".board-card.add")
        mac.click('.kind-tile[title="新建文件夹"]')
        mac.wait_for_selector(draft)

    open_gallery(mac)
    start_new_folder()
    focus, start, end, length = mac.evaluate(
        "() => { const i = document.activeElement;"
        " return [i.dataset.focusKey, i.selectionStart, i.selectionEnd, i.value.length]; }"
    )
    assert focus == "folder-draft" and start == 0 and end == length > 0
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 0  # 回车之前还没建
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    for page in (mac, ipad):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")
    mac.wait_for_selector('.board-name.folder[data-focus-key="folder:数学"]')

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
    assert mac.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    mac.keyboard.type("化学")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('化学')")
    assert sorted(mac.evaluate("() => whiteboard.ui.folders")) == sorted(["数学", "未命名文件夹", "化学"])


def test_a_file_dropped_inside_a_folder_lands_in_that_folder(browser, server):
    """选择界面停在文件夹里时把图片拖进窗口，新文档板留在这个文件夹里。"""
    png = png_base64()
    mac = open_mac(browser, server.port)
    gallery_with_folder(mac, "课件")
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    before = mac.evaluate("() => whiteboard.ui.boards.length")
    mac.evaluate(DROP_FILE, ["讲义.png", "image/png", png])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 1", arg=before)
    doc = mac.evaluate("() => whiteboard.ui.boards.find(b => b.kind === 'doc')")
    assert doc["folder"] == "课件"

    # 选择界面关着：落在当前白板所在的文件夹（现在当前白板就是刚建的文档板）
    mac.wait_for_function("(id) => whiteboard.state.id === id", arg=doc["id"])
    if mac.query_selector(".gallery"):
        mac.click('button[title="关闭"]')
    mac.evaluate(DROP_FILE, ["讲义2.png", "image/png", png])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 2", arg=before)
    folders = mac.evaluate("() => whiteboard.ui.boards.filter(b => b.kind === 'doc').map(b => b.folder)")
    assert folders == ["课件", "课件"]


# ----------------------------------------------------------------- 拖动（Mac 上用鼠标）


def test_a_board_can_be_dragged_into_a_folder_and_back_out(browser, server):
    """Mac 上归类不用开对话框：把卡片拖到文件夹上，拖回返回按钮就是移出来。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)
    board = ".board-card:not(.folder):not(.add)"

    # 拖进去：松手之后这块白板收进文件夹，最外面那层只剩文件夹卡片和「新建」
    mouse_drag(mac, board, ".board-card.folder", dwell=120)
    mac.wait_for_function(all_in_folder(folder))
    ipad.wait_for_function(all_in_folder(folder))
    mac.wait_for_function(f"{COUNT_ITEMS} === 2")

    # 拖出来：进文件夹，把卡片拖到搜索框左边那个返回按钮上
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    mouse_drag(mac, board, 'button[title^="返回"]', dwell=120)
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => !b.folder)")
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 1  # 文件夹还在，只是空了


def test_boards_can_be_dragged_into_a_new_order(browser, server):
    """拖着卡片在格子里走，别的卡片让位；松手之后这个顺序两端都认，也存得住。"""
    mac, ipad = open_pages(browser, server.port)
    new_board(mac, "笔记")
    new_board(mac, "笔记")
    open_gallery(mac)
    mac.wait_for_function(f"{COUNT_ITEMS} === 4")

    # 只数格子里的：正在拖的那一格挪到了界面根节点上，落位动画没走完之前它还在
    order = "() => [...document.querySelectorAll('.gallery-grid .board-item[data-board]')].map(n => n.dataset.board)"
    before = mac.evaluate(order)
    assert len(before) == 3
    expected = before[1:] + before[:1]

    # 把第一块拖到第三块的右半边，它就排到最后（落点在左半边是插到前面）
    items = ".board-item[data-board] .board-card"
    mouse_drag(mac, f"{items} >> nth=0", f"{items} >> nth=2", at=(0.85, 0.5))
    mac.wait_for_function(SAME_ORDER, arg=expected)
    ipad.wait_for_function(SAME_ORDER, arg=expected)
    mac.wait_for_function("() => !document.querySelector('.board-item.lifted')")
    assert mac.evaluate(order) == expected

    # 刷新之后还是这个顺序：顺序存在服务端的索引里，不是本机记的
    reload(mac)
    open_gallery(mac)
    mac.wait_for_selector(".board-item")
    assert mac.evaluate(order) == expected


# ----------------------------------------------------------------- 拖动（触摸设备上用手指）


def test_a_sideways_swipe_starts_the_drag_without_waiting(browser, server):
    """列表竖着滚，所以横着走的手势只可能是想拖：不用按住，立刻起拖。
    竖着划还是滚列表。"""
    ipad = open_touch(browser, server.port)
    open_gallery(ipad)
    ipad.wait_for_selector(".board-card")

    # 横着划 120px，全程不到 100ms，远不够按住时长
    assert ipad.evaluate(SWIPE, [BOARD_CARD, 120, 0, 6]) is True
    ipad.wait_for_function("() => !document.querySelector('.board-item.lifted')")

    # 竖着划同样的距离：这是在滚列表，不能起拖
    assert ipad.evaluate(SWIPE, [BOARD_CARD, 0, 120, 6]) is False


def test_a_slow_tap_does_not_open_the_board(browser, server):
    """按住超过起拖时长再松手，卡片只是浮起来又落回去，不能顺手切过去。"""
    ipad = open_touch(browser, server.port)
    open_gallery(ipad)
    ipad.wait_for_selector(".board-card")
    before = ipad.evaluate("() => whiteboard.state.id")

    ipad.evaluate(
        """async (sel) => {
          const card = document.querySelector(sel);
          const [x, y] = __t.center(sel);
          const init = { pointerType: 'touch', pointerId: 13, pressure: 0, buttons: 0 };
          __t.fire('pointerdown', x, y, init, card);
          await new Promise((done) => setTimeout(done, 400));
          __t.fire('pointerup', x, y, init, window);
          // 浏览器补的那一下 click
          card.dispatchEvent(new MouseEvent('click', { clientX: x, clientY: y, bubbles: true, cancelable: true }));
        }""",
        BOARD_CARD,
    )
    ipad.wait_for_timeout(300)
    assert ipad.evaluate("() => whiteboard.state.id") == before
    assert ipad.query_selector(".gallery") is not None  # 界面也没被关掉


def test_a_finger_can_drag_a_board_on_a_touch_device(browser, server):
    """iPad 上按住一会儿再拖：手指底下跟着一张副本，松手落在文件夹里。"""
    mac, ipad = open_pages(browser, server.port)
    folder = gallery_with_folder(mac)

    open_gallery(ipad)
    ipad.wait_for_selector(".board-card.folder")

    # 手指一放就竖着走：当成滚列表，不拖。必须是竖着走——横着走的手势按规则立刻
    # 起拖（见 test_a_sideways_swipe_starts_the_drag_without_waiting）。这里以前是
    # 从白板卡片直接划向旁边的文件夹，那是横着走，会不会真的落进文件夹全看时机，
    # 用例因此时好时坏。
    assert ipad.evaluate(SWIPE, [BOARD_CARD, 0, 120, 6]) is False
    assert ipad.evaluate("() => whiteboard.ui.boards.every(b => !b.folder)")
    assert ipad.evaluate("() => !document.querySelector('.board-card.ghost')")

    # 按住一会儿再拖：这次算数
    ipad.evaluate(TOUCH_DRAG, [BOARD_CARD, ".board-card.folder", 500, 6, 200])
    ipad.wait_for_function(all_in_folder(folder))
    mac.wait_for_function(all_in_folder(folder))
    # 副本是临时的，落位动画走完就撤掉
    ipad.wait_for_function("() => !document.querySelector('.board-card.ghost')")


def test_a_finger_can_drag_a_board_into_a_new_order(browser, server):
    """同一套长按拖动也能排序：松手之后顺序存到服务端。"""
    mac, ipad = open_pages(browser, server.port)
    new_board(mac, "笔记")
    new_board(mac, "笔记")
    open_gallery(ipad)
    ipad.wait_for_function("() => document.querySelectorAll('.board-item[data-board]').length === 3")

    before = board_ids(ipad)
    ipad.evaluate(
        TOUCH_DRAG, [BOARD_CARD, ".board-item[data-board]:nth-child(3) .board-card", 500, 8, 320]
    )
    ipad.wait_for_function(f"(was) => !({SAME_ORDER})(was)", arg=before)
    after = board_ids(ipad)
    assert sorted(after) == sorted(before) and after != before
    mac.wait_for_function(SAME_ORDER, arg=after)


# ----------------------------------------------------------------- 在 iPad 上照用户的做法操作
#
# 这一组在 iPad 页面上用 tap（真的触摸事件，要经过 input.js 的触摸拦截）和键盘输入，
# 不用鼠标 click，也不用 fill() 直接塞值。


def ipad_with_two_boards(browser, server):
    """Mac 上新建一块笔记，于是一共两块白板；iPad 打开白板选择界面。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    first = mac.evaluate("() => whiteboard.state.id")
    second = new_board(mac, "笔记")
    ipad.wait_for_function("(id) => whiteboard.state.id === id", arg=second)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.tap('button[title="白板"]')
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
    assert ipad.query_selector(".gallery") is None


def test_a_new_folder_can_be_named_on_the_ipad(browser, server):
    """iPad 上点「新建文件夹」同样直接进入起名，键盘输入就是名字。"""
    mac, ipad = open_pages(browser, server.port)
    open_gallery(ipad)
    ipad.tap(".board-card.add")
    ipad.tap('.kind-tile[title="新建文件夹"]')
    ipad.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    assert ipad.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    ipad.keyboard.type("英语")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '英语'")


def test_a_board_can_be_renamed_by_typing_on_the_ipad(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-name[data-focus-key="name:{first}"]')
    ipad.keyboard.type("线性代数")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(board_field(first, "name"), arg="线性代数")


def test_a_board_can_be_filed_and_unfiled_from_the_folder_dialog_on_the_ipad(browser, server):
    """点名字旁边的文件夹按钮，在「新文件夹」里打字新建并归进去；再选「不归类」移出来。"""
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .board-folder')
    ipad.wait_for_selector(".folder-new")
    style = ipad.evaluate(
        "() => { const s = getComputedStyle(document.querySelector('.folder-new'));"
        " return s.webkitUserSelect || s.userSelect; }"
    )
    assert style != "none", "iPad 上这个输入框会打不了字"
    ipad.tap(".folder-new")
    ipad.keyboard.type("数学")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(board_field(first, "folder"), arg="数学")

    # 归进去之后白板在文件夹里：进文件夹，再从同一个对话框里移出来
    ipad.wait_for_selector(".board-card.folder")
    ipad.tap(".board-card.folder")
    ipad.wait_for_selector(f'.board-item[data-board="{first}"] .board-folder')
    ipad.tap(f'.board-item[data-board="{first}"] .board-folder')
    ipad.tap('.folder-row:has-text("不归类")')
    for page in (ipad, mac):
        page.wait_for_function(f"() => !whiteboard.ui.boards.find(b => b.id === '{first}').folder")


def test_a_folder_can_be_renamed_by_typing_on_the_ipad(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .board-folder')
    ipad.tap(".folder-new")
    ipad.keyboard.type("数学")
    ipad.keyboard.press("Enter")
    ipad.wait_for_selector('.board-name.folder[data-focus-key="folder:数学"]')
    ipad.tap('.board-name.folder[data-focus-key="folder:数学"]')
    ipad.keyboard.press("Control+a")
    ipad.keyboard.type("线性代数")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(
            "() => whiteboard.ui.folders.includes('线性代数') && !whiteboard.ui.folders.includes('数学')"
        )
        page.wait_for_function(board_field(first, "folder"), arg="线性代数")


def test_a_board_can_be_deleted_from_the_ipad(browser, server):
    """点卡片上的删除，确认之后两边都没有这块白板了，当前白板仍然有效。"""
    mac, ipad, first, second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .del')
    ipad.wait_for_selector(".dialog.ask")
    ipad.tap('.dialog.ask button[title="确定"]')
    for page in (ipad, mac):
        page.wait_for_function(f"() => !whiteboard.ui.boards.some(b => b.id === '{first}')")
        page.wait_for_function("(id) => whiteboard.state.id === id", arg=second)
    assert [meta["id"] for meta in server.hub.store.list_metas()] == [second]

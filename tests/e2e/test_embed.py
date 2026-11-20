"""嵌入手写板（docs/embed.md）：别的页面（比如刷题）里嵌一块能同步的手写板。

包括示例应用 examples/apps/demo、刷题示例服务端 examples/qb-server，以及
Mac 上只读查看应用的白板、服务端升级之后通知还开着的页面。
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import threading

import pytest

from e2e.support import ROOT, TABLET_UA, free_port, new_page, open_mac, open_pages, poll_page, wait_online
from whiteboard.server import SPACES_KEY

EMBED_STAGE = "#pad .inkpad-stage"

# 在嵌入的手写板上派发指针事件；坐标相对手写板的左上角
EMBED_FIRE = """
([selector, type, x, y, pointerId, pressure]) => {
  const stage = document.querySelector(selector);
  const rect = stage.getBoundingClientRect();
  __t.fire(type, rect.left + x, rect.top + y, { pointerId, pressure }, stage);
}
"""

# 在示例页面上再挂一块手写板：window[id] 是它，window[id + 'Ops'] 记下它发出的操作
ADD_PAD = """async ([id, options]) => {
  const box = document.createElement('div');
  box.id = id;
  box.style.height = '300px';
  document.querySelector('main').append(box);
  const { createInkPad } = await import('/inksync/inkpad.js');
  const pad = createInkPad(box, options);
  const ops = (window[id + 'Ops'] = []);
  pad.on('op', ({ op }) => ops.push(op));
  window[id] = pad;
}"""

PAD_ONLINE = "() => window.pad && pad.status === 'online'"

# 本机缓存（IndexedDB）里存着的这块白板有几条笔画；没存就是 0
CACHED_STROKES = """async (board) => {
  const names = (await indexedDB.databases()).map((d) => d.name).filter((n) => n && n.startsWith('inksync'));
  for (const name of names) {
    const db = await new Promise((ok, fail) => { const r = indexedDB.open(name); r.onsuccess = () => ok(r.result); r.onerror = fail; });
    if (!db.objectStoreNames.contains('kv')) { db.close(); continue; }
    const saved = await new Promise((ok) => { const r = db.transaction('kv').objectStore('kv').get(`board:${board}`); r.onsuccess = () => ok(r.result); });
    db.close();
    if (saved && saved.strokes) return saved.strokes.length;
  }
  return 0;
}"""


def install_demo_app(server):
    target = server.config.data_dir / "apps" / "demo"
    shutil.copytree(ROOT / "examples" / "apps" / "demo", target)


def open_embedded(browser, url):
    page = new_page(browser, viewport=(1000, 900), user_agent=TABLET_UA, touch=True)
    page.goto(url)
    page.wait_for_function(PAD_ONLINE)
    return page


def open_demo(browser, port, path="/apps/demo/"):
    return open_embedded(browser, f"http://127.0.0.1:{port}{path}")


def draw_embedded(page, points, selector=EMBED_STAGE, pointer_id=1):
    """在嵌入的手写板上画一笔；坐标相对手写板的左上角。"""
    page.evaluate(EMBED_FIRE, [selector, "pointerdown", *points[0], pointer_id, 0.6])
    for x, y in points[1:]:
        page.evaluate(EMBED_FIRE, [selector, "pointermove", x, y, pointer_id, 0.6])
    page.evaluate(EMBED_FIRE, [selector, "pointerup", *points[-1], pointer_id, 0])


def space_strokes(server, space, board):
    """服务端存下的某个应用空间里某块白板的笔画 id。"""
    from inksync import FileStorage

    server.save_now()
    _meta, strokes, _problem = FileStorage(server.config.data_dir / "spaces" / space).read_file(board)
    return [s["id"] for s in strokes]


@pytest.fixture
def demo(server):
    """装好示例应用的服务端。"""
    install_demo_app(server)
    return server


# ----------------------------------------------------------------- 示例应用


def test_an_embedded_pad_syncs_between_devices(browser, demo):
    """刷题这类页面嵌入手写板：一台设备上写的内容实时出现在打开同一题的另一台上。"""
    mac = open_mac(browser, demo.port)
    pad = open_demo(browser, demo.port)
    other = open_demo(browser, demo.port)

    meta = pad.evaluate("() => pad.board")
    assert meta["id"] == "q1" and meta["name"] == "示例 第 1 题" and meta["data"] == {"question": 1}
    assert meta["canvas"] == {"mode": "fixed", "width": 800, "height": 1100}
    assert meta["layers"] == [{"src": "/apps/demo/question.svg", "x": 0, "y": 0, "width": 800}]
    assert pad.evaluate("() => pad.caps.write && pad.version === '0.3.0'")

    draw_embedded(pad, [(100, 300), (160, 340), (230, 300), (300, 360)])
    pad.wait_for_function("() => pad.snapshot().strokes.length === 1 && pad.status === 'online'")
    other.wait_for_function("() => pad.snapshot().strokes.length === 1")
    draw_embedded(other, [(100, 400), (160, 440), (230, 400)])
    pad.wait_for_function("() => pad.snapshot().strokes.length === 2")

    # 应用的白板在它自己的空间里：不进用户的白板列表，Mac 没有被切走
    assert mac.evaluate("() => whiteboard.ui.boards.some(b => b.id === 'q1')") is False
    listed = mac.evaluate("async () => (await (await fetch('/api/spaces/demo/boards')).json())")
    assert listed["total"] == 1 and listed["boards"][0]["id"] == "q1"
    assert len(space_strokes(demo, "demo", "q1")) == 2


def test_an_embedded_pad_switches_questions_on_one_connection(browser, demo):
    pad = open_demo(browser, demo.port)
    draw_embedded(pad, [(100, 300), (160, 340), (230, 300)])
    # 不等回执就换题：没送达的那一笔照样落到第 1 题
    pad.click("[data-question='2']")
    pad.wait_for_function("() => pad.board.id === 'q2' && pad.board.data.question === 2")
    assert pad.evaluate("() => pad.snapshot().strokes.length") == 0
    draw_embedded(pad, [(120, 320), (180, 360)])
    pad.wait_for_function("() => pad.snapshot().strokes.length === 1 && pad.status === 'online'")
    assert len(space_strokes(demo, "demo", "q1")) == 1
    assert len(space_strokes(demo, "demo", "q2")) == 1
    # 换回来：第 1 题的内容还在，撤销记录是新的
    pad.click("[data-question='1']")
    pad.wait_for_function("() => pad.board.id === 'q1' && pad.snapshot().strokes.length === 1")
    assert pad.evaluate("() => document.getElementById('undo').disabled") is True


def test_an_embedded_pad_leaves_the_rest_of_the_page_alone(browser, demo):
    """手写板只拦自己区域里的触摸：页面上的输入框照常点得中、打得了字。"""
    pad = open_demo(browser, demo.port)
    pad.tap("#answer")
    pad.keyboard.type("x=2 或 x=3")
    assert pad.evaluate("() => document.getElementById('answer').value") == "x=2 或 x=3"
    pad.tap("button[data-tool='eraser']")
    assert pad.evaluate("() => pad.tool.tool") == "eraser"
    # 手写板对宿主页面只装了外壳用的那一个全局对象，没有改 <html> 的属性
    assert pad.evaluate("() => document.documentElement.dataset.shell") is None
    assert pad.evaluate("() => typeof window.whiteboardShell") == "object"


def test_the_pad_object_exposes_only_its_interface(browser, demo):
    pad = open_demo(browser, demo.port)
    keys = pad.evaluate("() => Object.keys(pad).sort()")
    assert keys == sorted(
        [
            "open",
            "setTool",
            "undo",
            "redo",
            "clear",
            "fit",
            "zoom",
            "setMeta",
            "exportPNG",
            "snapshot",
            "load",
            "on",
            "destroy",
            "board",
            "status",
            "tool",
            "caps",
            "locked",
            "shell",
            "info",
            "version",
        ]
    )
    assert pad.evaluate("() => Object.isFrozen(pad) && pad.state === undefined && pad.net === undefined")
    png = pad.evaluate("async () => (await pad.exportPNG({ layers: true })).slice(0, 22)")
    assert png == "data:image/png;base64,"


def test_two_pads_on_one_page_keep_their_own_boards(browser, demo):
    pad = open_demo(browser, demo.port)
    pad.evaluate(ADD_PAD, ["second", {"space": "demo", "board": "q2", "create": {"name": "第 2 题"}}])
    pad.wait_for_function("() => second.status === 'online'")
    draw_embedded(pad, [(80, 80), (140, 120), (200, 80)], selector="#second .inkpad-stage", pointer_id=2)
    pad.wait_for_function("() => second.snapshot().strokes.length === 1 && second.status === 'online'")
    assert pad.evaluate("() => pad.snapshot().strokes.length") == 0
    assert pad.evaluate("() => second.board.id") == "q2"

    pad.evaluate("() => second.destroy()")
    assert pad.evaluate("() => document.querySelector('#second .inkpad-stage')") is None


def test_the_ipad_opens_the_chosen_home_app(browser, demo):
    demo.config.ipad_home = "demo"
    pad = open_demo(browser, demo.port, path="/?role=ipad")
    assert pad.url.endswith("/apps/demo/")
    # 应用里的「白板」链接带 home=…，回到白板不再跳转
    pad.click("header a")
    wait_online(pad)
    assert "/apps/" not in pad.url
    mac = new_page(browser)
    mac.goto(f"http://127.0.0.1:{demo.port}/?role=mac")
    assert "/apps/" not in mac.url


def test_the_mac_views_app_boards_read_only(browser, demo):
    """应用的白板不在白板列表里：白板库边栏里每个应用一行，点开分页列出，只读查看。"""
    pad = open_demo(browser, demo.port)
    draw_embedded(pad, [(100, 300), (160, 340), (230, 300)])
    pad.wait_for_function("() => pad.status === 'online' && pad.snapshot().strokes.length === 1")

    mac = open_mac(browser, demo.port)
    mac.click('button[title="白板库"]')
    mac.click('.lib-side .lib-nav:has(.lib-nav-label:text-is("demo"))')
    mac.wait_for_selector(".board-card.app-board")
    assert mac.inner_text(".lib-title") == "demo"
    mac.click(".board-card.app-board")
    mac.wait_for_selector(".app-viewer")
    # 浮层里的手写板只读，看得到 iPad 上写的那一笔；Mac 自己的当前白板没有变
    mac.wait_for_function("() => document.querySelector('.app-viewer .inkpad-stage') !== null")
    assert mac.evaluate("() => whiteboard.state.id") == mac.evaluate("() => whiteboard.ui.currentBoardId")
    listed = mac.evaluate("async () => (await (await fetch('/api/spaces/demo/boards')).json()).total")
    assert listed == 1
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => document.querySelector('.app-viewer') === null")


def test_a_pad_is_told_when_the_server_was_upgraded(browser, demo):
    """服务端升级之后，还开着的页面收到 outdated；白板应用据此重新载入页面。"""
    import inksync.server as inksync_server

    pad = open_demo(browser, demo.port)
    _mac, ipad = open_pages(browser, demo.port)
    pad.evaluate("() => { window.outdated = []; pad.on('outdated', (e) => outdated.push(e)); }")
    ipad.evaluate("() => { window.loadedAt = performance.timeOrigin; }")
    original = inksync_server.BUILD
    try:
        inksync_server.BUILD = "next-build"

        async def drop():
            for space in demo.app[SPACES_KEY].hubs().values():
                await space.close_connections()

        demo.run_coroutine(drop()).result(10)
        pad.wait_for_function("() => outdated.length === 1 && outdated[0].server === 'next-build'")
        # 白板应用的页面重新载入了（新的页面没有 loadedAt）
        ipad.wait_for_function("() => window.whiteboard && window.loadedAt === undefined", timeout=15000)
    finally:
        inksync_server.BUILD = original


# ----------------------------------------------------------------- 传输方式


def test_a_local_pad_works_without_a_server(browser, demo):
    """transport: "local"：不连服务器，内容由宿主通过 op 事件和 snapshot() 自己保存。"""
    page = open_demo(browser, demo.port)
    seed = {"id": "seed-1", "tool": "pen", "color": "#1b1b1f", "w": 3, "p": [10, 10, 0.5, 60, 40, 0.5]}
    page.evaluate(ADD_PAD, ["solo", {"board": "local-q1", "transport": "local", "initial": {"strokes": [seed]}}])
    page.wait_for_function("() => solo.status === 'local' && solo.snapshot().strokes.length === 1")
    assert page.evaluate("() => solo.board.id") == "local-q1"

    draw_embedded(page, [(80, 80), (140, 120), (200, 80)], selector="#solo .inkpad-stage", pointer_id=3)
    page.wait_for_function("() => solo.snapshot().strokes.length === 2")
    snapshot = page.evaluate("() => solo.snapshot()")
    assert [s["id"] for s in snapshot["strokes"]][0] == "seed-1" and len(snapshot["strokes"]) == 2
    page.evaluate("() => solo.undo()")
    assert page.evaluate("() => solo.snapshot().strokes.length") == 1

    # 服务器上没有这块白板
    listed = page.evaluate("async () => (await (await fetch('/api/spaces/demo/boards')).json()).boards")
    assert "local-q1" not in [b["id"] for b in listed]

    # 不给 initial 时用本机缓存恢复
    page.evaluate("() => solo.destroy()")
    # destroy() 先把内容写进本机缓存，写是异步的：等撤销之后的那一条真的存进去
    saved = poll_page(page, f"async (board) => (await ({CACHED_STROKES})(board)) === 1", "local-q1")
    assert saved, "销毁之前没有把内容写进本机缓存"
    page.evaluate(ADD_PAD, ["again", {"board": "local-q1", "transport": "local"}])
    page.wait_for_function("() => again.snapshot().strokes.length === 1")
    assert page.evaluate("() => again.snapshot().strokes[0].id") == "seed-1"


def test_local_pad_reports_ops(browser, demo):
    page = open_demo(browser, demo.port)
    page.evaluate(ADD_PAD, ["solo2", {"board": "local-q2", "transport": "local"}])
    page.wait_for_function("() => solo2.status === 'local'")
    draw_embedded(page, [(80, 80), (140, 120), (200, 80)], selector="#solo2 .inkpad-stage", pointer_id=6)
    page.wait_for_function("() => solo2Ops.length === 1")
    assert page.evaluate("() => solo2Ops.map(op => op.op)") == ["add"]
    page.evaluate("() => solo2.undo()")
    assert page.evaluate("() => solo2Ops.at(-1).op") == "remove"


def test_a_pad_can_sync_through_a_given_url_or_a_custom_transport(browser, demo):
    page = open_demo(browser, demo.port)
    url = f"ws://127.0.0.1:{demo.port}/ws"
    page.evaluate(ADD_PAD, ["remote", {"space": "demo", "board": "via-url", "create": {}, "transport": {"url": url}}])
    page.wait_for_function("() => remote.status === 'online' && remote.board && remote.board.id === 'via-url'")
    draw_embedded(page, [(80, 80), (140, 120), (200, 80)], selector="#remote .inkpad-stage", pointer_id=4)
    page.wait_for_function("() => remote.snapshot().strokes.length === 1 && remote.status === 'online'")
    assert len(space_strokes(demo, "demo", "via-url")) == 1

    # 自定义传输：宿主自己实现同一组成员，例如接到刷题项目自己的后端
    page.evaluate(
        """async () => {
          window.sent = [];
          const transport = (handlers) => ({
            outbox: [], boardId: null, lastSeq: 0, epoch: null, status: 'offline',
            connect() {
              handlers.onStatus('online');
              handlers.onMessage({ t: 'init', board: { id: 'custom-1', canvas: { mode: 'infinite' },
                                   background: { pattern: 'blank' }, layers: [], data: {} },
                                   strokes: [], seq: 0, epoch: 'e1' });
            },
            open() {}, send() { return true; }, sendLive() {},
            sendOp(op) { window.sent.push(op); return 'c' + window.sent.length; },
            restoreOutbox() {}, close() {},
          });
          const box = document.createElement('div');
          box.id = 'custom'; box.style.height = '300px';
          document.querySelector('main').append(box);
          const { createInkPad } = await import('/inksync/inkpad.js');
          window.custom = createInkPad(box, { board: 'custom-1', transport });
        }"""
    )
    page.wait_for_function("() => custom.board && custom.board.id === 'custom-1'")
    draw_embedded(page, [(80, 80), (140, 120), (200, 80)], selector="#custom .inkpad-stage", pointer_id=5)
    page.wait_for_function("() => sent.length === 1 && sent[0].op === 'add'")


# ----------------------------------------------------------------- examples/qb-server（A1）


@pytest.fixture
def qb_server(tmp_path):
    """在后台线程里跑刷题示例服务端。"""
    from aiohttp import web

    spec = importlib.util.spec_from_file_location("qb_example", ROOT / "examples" / "qb-server" / "server.py")
    qb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qb)
    port = free_port()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {}

    async def start():
        app = qb.create_app(tmp_path / "qb")
        runner = web.AppRunner(app, shutdown_timeout=1.0)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        state["runner"], state["app"] = runner, app
        ready.set()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
        loop.run_until_complete(state["runner"].cleanup())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    ready.wait(10)
    yield {"port": port, "module": qb, "app": state["app"], "loop": loop, "data": tmp_path / "qb"}
    loop.call_soon_threadsafe(loop.stop)
    thread.join(10)


def open_qb(browser, port, user):
    return open_embedded(browser, f"http://127.0.0.1:{port}/login?user={user}")


def test_the_qb_example_end_to_end(browser, qb_server):
    port = qb_server["port"]
    first = open_qb(browser, port, "42")
    second = open_qb(browser, port, "42")  # 同一个学生的另一台设备
    assert first.evaluate("() => pad.board.id") == "u42-q1"
    assert first.evaluate("() => pad.board.layers[0].src") == "/static/q1.svg"

    draw_embedded(first, [(100, 400), (160, 440), (230, 400)])
    second.wait_for_function("() => pad.snapshot().strokes.length === 1")
    first.wait_for_function("() => pad.status === 'online'")

    # 换题：同一条连接
    first.click("[data-q='2']")
    first.wait_for_function("() => pad.board.id === 'u42-q2' && pad.snapshot().strokes.length === 0")
    draw_embedded(first, [(100, 420), (160, 460)])
    first.wait_for_function("() => pad.snapshot().strokes.length === 1 && pad.status === 'online'")

    # 批改：服务端写分数，页面收到新的元数据
    async def grade():
        hub = qb_server["app"][qb_server["module"].HUB_KEY]
        return await hub.edit_meta("u42-q1", {"data": {"score": 3}})

    asyncio.run_coroutine_threadsafe(grade(), qb_server["loop"]).result(10)
    second.wait_for_function("() => pad.board.data.score === 3")
    second.wait_for_function("() => document.getElementById('score').textContent === '得分 3'")

    # 另一个学生打开的是自己的白板，看不到 42 号的内容
    other = open_qb(browser, port, "7")
    assert other.evaluate("() => pad.board.id") == "u7-q1"
    assert other.evaluate("() => pad.snapshot().strokes.length") == 0

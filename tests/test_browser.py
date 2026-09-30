"""端到端测试：真的开两个浏览器页面（Mac 端 + iPad 端）互相同步。

没装 Playwright 或找不到 Chromium 时自动跳过。
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from whiteboard import inkpdf
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


# 用哪个浏览器内核跑，默认 Chromium。CI 另外用 WebKit（iPad 上的 Safari 就是它）
# 跑一组冒烟用例：WB_BROWSER=webkit。
ENGINE = os.environ.get("WB_BROWSER", "chromium")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            if ENGINE == "chromium":
                path = chromium_path()
                instance = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
            else:
                instance = getattr(pw, ENGINE).launch()
        except Exception as exc:  # pragma: no cover - 环境没有浏览器
            pytest.skip(f"没有可用的 {ENGINE}：{exc}")
        yield instance
        instance.close()


def free_port():
    """让系统给一个空闲端口。以前按当前毫秒数取 8000～9999，两次运行挨得近就会撞。"""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def close_pages(request):
    """每个用例结束时把它开的页面都关掉，用例中途失败也一样，不留给下一个用例。"""
    yield
    if "browser" in request.fixturenames:
        for context in list(request.getfixturevalue("browser").contexts):
            try:
                context.close()
            except Exception:  # noqa: BLE001 - 已经关掉的就算了
                pass


@pytest.fixture
def server(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = free_port()
    thread = ServerThread(config, advertise=False)
    thread.start()
    yield thread
    thread.stop()


def open_pages(browser, port, picker=False):
    """开两个页面：Mac 窗口和一台触摸设备。

    笔具盘只有 iPad 上有。``picker=True`` 时触摸设备是 iPad（工具栏是笔具盘）；
    默认是一台安卓平板，书写和手势照 iPad 那一套，工具栏是普通那条——大部分用例
    测的是普通工具栏，要明说自己要哪一条。
    """
    mac = browser.new_page(viewport={"width": 1200, "height": 800})
    mac.goto(f"http://127.0.0.1:{port}/?role=mac")
    ipad = browser.new_context(
        viewport={"width": 1180, "height": 820},
        user_agent=IPAD_UA if picker else TABLET_UA,
        has_touch=True,
    ).new_page()
    ipad.goto(f"http://127.0.0.1:{port}/?role=ipad")
    for page in (mac, ipad):
        page.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    return mac, ipad


def draw(page, points, pointer_type="pen", pointer_id=1, pressure=0.6):
    page.evaluate(FIRE, ["pointerdown", *points[0], pointer_type, pointer_id, pressure])
    for x, y in points[1:]:
        page.evaluate(FIRE, ["pointermove", x, y, pointer_type, pointer_id, pressure])
    page.evaluate(FIRE, ["pointerup", *points[-1], pointer_type, pointer_id, 0])


RECORD_ERASE = """
([x0, y0, x1, y1, steps, alt]) => {
  const stage = document.getElementById('stage');
  const fire = (type, x, y, pressure) => stage.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, pointerType: 'pen', pointerId: 7, pressure,
    altitudeAngle: alt, azimuthAngle: 0.8,
    buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
  }));
  whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
  whiteboard.recorder.start('用例');
  fire('pointerdown', x0, y0, 0.5);
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, 0.5);
  }
  fire('pointerup', x1, y1, 0);
  return whiteboard.recorder.stop();
}
"""


def stroke_count(page):
    return page.evaluate("() => whiteboard.state.strokes.length")


def wait_strokes(page, count, timeout=4000):
    page.wait_for_function(f"() => whiteboard.state.strokes.length === {count}", timeout=timeout)


def test_drawing_syncs_both_ways(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (360, 340), (430, 300), (500, 380)])
    wait_strokes(mac, 1)
    wait_strokes(ipad, 1)

    draw(mac, [(200, 200), (260, 260), (320, 210)], pointer_type="mouse")
    wait_strokes(ipad, 2)

    # 服务端也收到了，并且带上了层叠序号
    orders = mac.evaluate("() => whiteboard.state.strokes.map(s => s.n)")
    assert orders == sorted(orders)
    mac.close()
    ipad.close()


def test_undo_erase_and_clear(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (400, 320), (500, 300)])
    draw(ipad, [(300, 500), (400, 520), (500, 500)])
    wait_strokes(mac, 2)

    ipad.click('button[title="撤销"]')
    wait_strokes(mac, 1)
    wait_strokes(ipad, 1)

    # Mac 端用橡皮擦擦掉剩下那一笔
    mac.click('button[title="橡皮擦"]')
    box = mac.evaluate(
        "() => { const s = whiteboard.state.strokes[0];"
        "const [x, y] = whiteboard.viewport.toScreen(s.p[0], s.p[1]); return [x, y]; }"
    )
    draw(mac, [(box[0], box[1]), (box[0] + 6, box[1] + 4)], pointer_type="mouse")
    wait_strokes(ipad, 0)

    # 清屏之后再撤销，内容回来
    mac.click('button[title="钢笔"]')
    draw(mac, [(300, 300), (380, 340)], pointer_type="mouse")
    wait_strokes(ipad, 1)
    ipad.click('button[title="清空白板"]')
    ipad.click('.dialog button[title="确定"]')
    wait_strokes(mac, 0)
    ipad.click('button[title="撤销"]')
    wait_strokes(mac, 1)
    mac.close()
    ipad.close()


def test_touch_pans_by_default_and_only_pencil_draws(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (360, 340), (420, 300)], pointer_type="pen", pressure=0.8)
    wait_strokes(ipad, 1)

    # 默认只认 Pencil：手指只平移，不留笔迹（等过了手掌屏蔽的时间窗）
    ipad.wait_for_timeout(700)
    before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    draw(ipad, [(500, 500), (560, 560), (620, 600)], pointer_type="touch", pointer_id=7)
    ipad.wait_for_timeout(300)
    assert stroke_count(ipad) == 1
    after = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    assert after != before, "手指应该把画布拖动了"

    # 打开「手指书写」之后手指才画线，并且这个选择会被记住
    ipad.click('button[title="手指书写"]')
    ipad.wait_for_timeout(100)
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is True
    draw(ipad, [(300, 600), (380, 640), (460, 600)], pointer_type="touch", pointer_id=8)
    wait_strokes(ipad, 2)
    wait_strokes(mac, 2)
    assert ipad.evaluate("() => localStorage.getItem('whiteboard.fingerDraw')") == "1"

    ipad.click('button[title="手指书写"]')
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is False
    mac.close()
    ipad.close()


def test_palm_is_ignored_while_pencil_writes(browser, server):
    """Pencil 写字时手掌落在屏幕上，既不能画线也不能把画布拖走。"""
    mac, ipad = open_pages(browser, server.port)
    before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")

    ipad.evaluate(FIRE, ["pointerdown", 400, 400, "pen", 1, 0.7])
    for x in range(410, 520, 10):
        ipad.evaluate(FIRE, ["pointermove", x, 420, "pen", 1, 0.7])
    # 手掌：落下并滑动
    for step, x in enumerate(range(300, 500, 40)):
        kind = "pointerdown" if step == 0 else "pointermove"
        ipad.evaluate(FIRE, [kind, x, 700, "touch", 9, 0.5])
    ipad.evaluate(FIRE, ["pointerup", 500, 700, "touch", 9, 0])
    ipad.evaluate(FIRE, ["pointerup", 520, 420, "pen", 1, 0])
    wait_strokes(ipad, 1)

    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == before
    assert stroke_count(ipad) == 1

    # 手掌先落屏、笔后落下时，手掌拖出来的位移会被撤回（上面已经断言过位置没变）
    # 抬笔一会儿之后，手指恢复平移
    ipad.wait_for_timeout(700)
    draw(ipad, [(300, 600), (420, 640)], pointer_type="touch", pointer_id=11)
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") != before
    mac.close()
    ipad.close()


def test_palm_landing_before_the_pencil_does_not_shift_the_canvas(browser, server):
    """手掌通常比笔尖先落屏：它拖出来的位移要在笔落下时撤回去。"""
    mac, ipad = open_pages(browser, server.port)
    before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")

    ipad.evaluate(FIRE, ["pointerdown", 300, 700, "touch", 21, 0.5])
    for x in range(320, 440, 20):
        ipad.evaluate(FIRE, ["pointermove", x, 700, "touch", 21, 0.5])
    moved = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    assert moved != before, "手掌确实先把画布拖动了"

    ipad.evaluate(FIRE, ["pointerdown", 500, 400, "pen", 1, 0.7])
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == before
    for x in range(510, 600, 10):
        ipad.evaluate(FIRE, ["pointermove", x, 420, "pen", 1, 0.7])
    ipad.evaluate(FIRE, ["pointerup", 600, 420, "pen", 1, 0])
    wait_strokes(ipad, 1)
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == before
    mac.close()
    ipad.close()


def test_cache_writes_never_land_on_a_stroke(browser, server):
    """写 IndexedDB 会卡主线程，必须等书写停下来之后才写。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(
        """() => {
          window.__writes = [];
          const original = whiteboard.persist.bind(whiteboard);
          whiteboard.persist = () => { window.__writes.push(performance.now()); original(); };
        }"""
    )
    draw(ipad, [(300, 300), (360, 340), (420, 300)])
    ipad.wait_for_timeout(1200)  # 第一笔之后正好是老实现写盘的时刻

    start = ipad.evaluate("() => performance.now()")
    for step, point in enumerate([(300, 500), (340, 540), (380, 500), (430, 540), (480, 500)]):
        kind = "pointerdown" if step == 0 else "pointermove"
        ipad.evaluate(FIRE, [kind, point[0], point[1], "pen", 3, 0.6])
        ipad.wait_for_timeout(120)
    ipad.evaluate(FIRE, ["pointerup", 480, 500, "pen", 3, 0])
    end = ipad.evaluate("() => performance.now()")
    wait_strokes(ipad, 2)

    writes = ipad.evaluate("() => window.__writes")
    during = [w for w in writes if start <= w <= end]
    assert not during, f"第二笔期间写了 {len(during)} 次盘"

    # 停下来之后总得写进去（写盘在停笔之后几秒内发生，等到写了为止）
    ipad.wait_for_function("() => window.__writes.length > 0", timeout=10000)
    cached = ipad.evaluate(
        "async () => { const c = whiteboard.cache; const b = await c.loadBoard(whiteboard.state.id);"
        "return b ? b.strokes.length : 0; }"
    )
    assert cached == 2
    mac.close()
    ipad.close()


def test_stroke_resumes_after_a_system_cancel(browser, server):
    """系统中途取消指针、但笔还按着时，后半截不能丢。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(FIRE, ["pointerdown", 300, 300, "pen", 5, 0.6])
    for x in (320, 340, 360):
        ipad.evaluate(FIRE, ["pointermove", x, 320, "pen", 5, 0.6])
    ipad.evaluate(FIRE, ["pointercancel", 360, 320, "pen", 5, 0.6])
    # 笔仍然按在屏幕上，继续移动
    for x in (380, 400, 420, 440):
        ipad.evaluate(FIRE, ["pointermove", x, 330, "pen", 5, 0.6])
    ipad.evaluate(FIRE, ["pointerup", 440, 330, "pen", 5, 0])

    wait_strokes(ipad, 2)
    tail = ipad.evaluate("() => whiteboard.state.strokes[1].p.length / 3")
    assert tail >= 3, "被取消之后的那一段应该接着画出来"
    assert ipad.evaluate("() => whiteboard.input.stats.cancel") == 1
    wait_strokes(mac, 2)
    mac.close()
    ipad.close()


def test_page_errors_are_reported_to_the_server(browser, server):
    """iPad 上看不到控制台，页面报错必须能在 Mac 的终端里看到。"""
    mac, ipad = open_pages(browser, server.port)
    posted = []
    ipad.on("request", lambda request: posted.append(request.url) if request.url.endswith("/api/debug") else None)
    ipad.evaluate("() => setTimeout(() => { throw new Error('故意炸一个'); }, 0)")
    ipad.wait_for_timeout(500)
    assert posted, "报错应该被送到 /api/debug"

    # 渲染循环不能因为一次异常就停摆
    ipad.evaluate("() => { const t = whiteboard.renderer.tick.bind(whiteboard.renderer); let n = 0;"
                  "whiteboard.renderer.tick = () => { if (n++ === 0) throw new Error('单帧异常'); return t(); }; }")
    ipad.wait_for_timeout(300)
    draw(ipad, [(300, 300), (380, 350), (460, 300)])
    wait_strokes(mac, 1)
    mac.close()
    ipad.close()


def test_strokes_survive_a_reload_without_id_collisions(browser, server):
    """重新打开页面之后再写，笔画不能因为 id 撞车而在抬笔瞬间消失。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    draw(ipad, [(300, 420), (380, 460), (460, 420)])
    wait_strokes(mac, 2)

    ipad.reload()
    ipad.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    wait_strokes(ipad, 2)

    draw(ipad, [(300, 540), (380, 580), (460, 540)])
    draw(ipad, [(300, 640), (380, 680), (460, 640)])
    wait_strokes(ipad, 4)
    wait_strokes(mac, 4)
    ids = ipad.evaluate("() => whiteboard.state.strokes.map(s => s.id)")
    assert len(set(ids)) == 4, f"笔画 id 撞车了：{ids}"
    mac.close()
    ipad.close()


def test_toolbar_still_responds_to_taps(browser, server):
    """挡掉触摸默认行为之后，iOS 仍然要能在按钮上合成 click。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.tap('button[title="橡皮擦"]')
    assert ipad.evaluate("() => whiteboard.tool.tool") == "eraser"
    ipad.tap('button[title="钢笔"]')
    assert ipad.evaluate("() => whiteboard.tool.tool") == "pen"
    ipad.tap('button[title="手指书写"]')
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is True
    mac.close()
    ipad.close()


def test_repeated_pen_interruptions_show_a_hint(browser, server):
    """页面拦不住系统级的随手写，只能在连续被打断时提示用户去关掉它。"""
    mac, ipad = open_pages(browser, server.port)
    for i in range(3):
        pointer = 30 + i
        ipad.evaluate(FIRE, ["pointerdown", 300, 300 + i * 40, "pen", pointer, 0.6])
        ipad.evaluate(FIRE, ["pointermove", 340, 320 + i * 40, "pen", pointer, 0.6])
        ipad.evaluate(FIRE, ["pointermove", 380, 320 + i * 40, "pen", pointer, 0.6])
        ipad.evaluate(FIRE, ["pointercancel", 380, 320 + i * 40, "pen", pointer, 0.6])

    notice = ipad.locator(".notice")
    notice.wait_for(timeout=3000)
    assert "随手写" in notice.inner_text()
    assert ipad.evaluate("() => whiteboard.input.stats.penCancel") == 3
    notice.click()
    assert notice.count() == 0
    mac.close()
    ipad.close()


def test_note_boards_only_extend_downwards(browser, server):
    """笔记：宽度固定成一页，纸外不落笔、也划不出去；大白板不受这些约束。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    ipad.wait_for_function("() => whiteboard.state.kind === 'note'")
    assert ipad.evaluate("() => whiteboard.state.limits.x1") == 1000

    # 纸内照常书写
    ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.scale = 0.5;"
        "v.centerOn(500, 400, whiteboard.renderer.viewW, whiteboard.renderer.viewH);"
        "whiteboard.clampView(); whiteboard.renderer.requestFull(); }"
    )
    inside = ipad.evaluate("() => whiteboard.viewport.toScreen(500, 400)")
    draw(ipad, [(inside[0] - 60, inside[1]), (inside[0], inside[1] + 30), (inside[0] + 60, inside[1])])
    wait_strokes(mac, 1)

    # 纸外不落笔，而是拖动画布
    before = ipad.evaluate("() => [whiteboard.state.strokes.length, whiteboard.viewport.y]")
    draw(ipad, [(40, 560), (50, 440), (60, 320)], pointer_id=9)  # 往上拖 = 向下翻页
    ipad.wait_for_timeout(200)
    after = ipad.evaluate("() => [whiteboard.state.strokes.length, whiteboard.viewport.y]")
    assert after[0] == before[0], "纸外不应该留下笔迹"
    assert after[1] != before[1], "纸外拖动应该滚动页面"

    # 横向划不出纸外：纸比视口窄时始终居中
    centered = ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.panBy(3000, 0); whiteboard.clampView();"
        "const l = whiteboard.state.limits;"
        "return [v.x + l.x0 * v.scale, v.x + l.x1 * v.scale, whiteboard.renderer.viewW]; }"
    )
    assert centered[0] > 0 and centered[1] < centered[2]

    # 页首之上翻不过去
    top = ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.panBy(0, 5000); whiteboard.clampView(); return v.y; }"
    )
    assert top <= ipad.evaluate("() => whiteboard.renderer.viewH * 0.1") + 1
    mac.close()
    ipad.close()


FLICK = """
async ([points, pointerType, delay]) => {
  const stage = document.getElementById('stage');
  const fire = (type, x, y) => stage.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, pointerType, pointerId: 77, pressure: type === 'pointerup' ? 0 : 0.5,
    buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
  }));
  fire('pointerdown', points[0][0], points[0][1]);
  for (const [x, y] of points.slice(1)) {
    await new Promise((resolve) => setTimeout(resolve, delay || 8));
    fire('pointermove', x, y);
  }
  fire('pointerup', points[points.length - 1][0], points[points.length - 1][1]);
}
"""


def make_note(mac, ipad):
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    ipad.wait_for_function("() => whiteboard.state.kind === 'note'")
    ipad.wait_for_timeout(200)


def test_flick_keeps_scrolling_with_momentum(browser, server):
    """笔记上下翻要有惯性：松手之后还会继续滑一段再停。"""
    mac, ipad = open_pages(browser, server.port)
    make_note(mac, ipad)

    points = [[590, 700 - i * 40] for i in range(9)]  # 快速上划
    ipad.evaluate(FLICK, [points, "touch", 8])
    released = ipad.evaluate("() => whiteboard.viewport.y")
    ipad.wait_for_timeout(120)
    gliding = ipad.evaluate("() => whiteboard.viewport.y")
    assert gliding < released - 5, "松手之后应该继续往下滑"

    ipad.wait_for_timeout(1200)
    settled = ipad.evaluate("() => whiteboard.viewport.y")
    ipad.wait_for_timeout(200)
    assert abs(ipad.evaluate("() => whiteboard.viewport.y") - settled) < 0.5, "最后要停下来"

    # 慢慢拖不该有惯性
    slow = [[590, 600 - i * 4] for i in range(6)]
    ipad.evaluate(FLICK, [slow, "touch", 30])
    before = ipad.evaluate("() => whiteboard.viewport.y")
    ipad.wait_for_timeout(200)
    assert abs(ipad.evaluate("() => whiteboard.viewport.y") - before) < 0.5
    mac.close()
    ipad.close()


def test_note_snaps_to_screen_width_when_close(browser, server):
    """缩放到接近一页宽时，松手自动吸附到屏幕两边。"""
    mac, ipad = open_pages(browser, server.port)
    make_note(mac, ipad)
    target = ipad.evaluate("() => whiteboard.renderer.viewW / 1000")

    # 差一点点：松手后吸附
    ipad.evaluate(
        "([s]) => { whiteboard.viewport.scale = s; whiteboard.clampView();"
        "whiteboard.renderer.requestFull(); }",
        [target * 1.045],
    )
    ipad.evaluate(FLICK, [[[590, 500], [590, 495]], "touch", 30])
    ipad.wait_for_function(
        "(t) => whiteboard.viewAnim === 0 && Math.abs(whiteboard.viewport.scale - t) < t * 0.001",
        arg=target,
        timeout=3000,
    )
    edges = ipad.evaluate(
        "() => { const v = whiteboard.viewport; const l = whiteboard.state.limits;"
        "return [v.x + l.x0 * v.scale, v.x + l.x1 * v.scale, whiteboard.renderer.viewW]; }"
    )
    assert abs(edges[0]) < 0.5 and abs(edges[1] - edges[2]) < 0.5, "纸的左右边要贴住屏幕"

    # 差得远：不要自作主张
    ipad.evaluate(
        "([s]) => { whiteboard.viewport.scale = s; whiteboard.clampView();"
        "whiteboard.renderer.requestFull(); }",
        [target * 1.6],
    )
    ipad.evaluate(FLICK, [[[590, 500], [590, 495]], "touch", 30])
    ipad.wait_for_timeout(400)
    assert ipad.evaluate("() => whiteboard.viewport.scale") > target * 1.5
    mac.close()
    ipad.close()


def test_canvas_is_infinite(browser, server):
    """画布没有边界：任意位置都能写，缩放只受上下限约束。"""
    mac, ipad = open_pages(browser, server.port)
    # 移到离原点很远的地方照样能画
    ipad.evaluate(
        "() => { whiteboard.viewport.scale = 1;"
        "whiteboard.viewport.centerOn(50000, -30000, whiteboard.renderer.viewW, whiteboard.renderer.viewH);"
        "whiteboard.renderer.requestFull(); }"
    )
    draw(ipad, [(300, 300), (400, 360), (500, 300)])
    wait_strokes(mac, 1)
    far = ipad.evaluate("() => whiteboard.state.strokes[0].p[0]")
    assert far > 40000, "远处的笔迹应该照常落在世界坐标上"

    # 视口不会被拉回任何边界
    moved = ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.panBy(-4000, 2500);"
        "whiteboard.renderer.requestFull(); return [v.x, v.y]; }"
    )
    ipad.wait_for_timeout(200)
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == moved

    # 缩放仍然有上下限
    limits = ipad.evaluate(
        "async () => { const m = await import('/static/js/viewport.js');"
        "const v = whiteboard.viewport;"
        "v.zoomAt(1e6, 0, 0); const max = v.scale;"
        "v.zoomAt(1e-9, 0, 0); const min = v.scale;"
        "return [min, max, m.MIN_SCALE, m.MAX_SCALE]; }"
    )
    assert limits[0] == limits[2] and limits[1] == limits[3]

    # 「回到内容」把笔迹带回视野
    ipad.evaluate("() => whiteboard.fit()")
    visible = ipad.evaluate(
        "() => { const s = whiteboard.state.strokes[0];"
        "const [x, y] = whiteboard.viewport.toScreen(s.p[0], s.p[1]);"
        "return x > 0 && y > 0 && x < whiteboard.renderer.viewW && y < whiteboard.renderer.viewH; }"
    )
    assert visible
    mac.close()
    ipad.close()


UPDATE_NOTES = """## What's Changed
* fix: 修好了一个东西 by @someone in https://github.com/Maimai-l/white-board/pull/1285
* chore: bump `aiohttp` and **tidy** the build
"""

# 假装自己是 pywebview 注入的本地接口，把每次调用记下来
UPDATE_STUB = """
([notes, staged]) => {
  window.__calls = [];
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.0.0', packaged: true }),
    update_state: async () => ({
      current: '1.0.0', packaged: true, auto: true,
      info: { version: '1.2.0', notes },
      staged, downloading: !staged, progress: staged ? 1 : 0.4, onQuit: false,
    }),
    pending_update: async () => ({ version: '1.2.0', notes }),
    set_auto_update: async (value) => { window.__calls.push(['auto', value]); return value; },
    skip_update: async () => { window.__calls.push(['skip']); return true; },
    download_update: async () => { window.__calls.push(['download']); return true; },
    install_update: async (mode) => { window.__calls.push(['install', mode]); return true; },
    check_update_now: async () => ({ version: '1.2.0', notes }),
  }};
}
"""


def open_update_dialog(page, staged=True):
    page.evaluate(UPDATE_STUB, [UPDATE_NOTES, staged])
    page.evaluate(
        "async () => { whiteboard.offeredUpdate = null;"
        "await whiteboard.offerUpdate({ version: '1.2.0' }); }"
    )
    page.locator(".update-dialog").wait_for(timeout=3000)


def test_update_dialog_offers_install_skip_and_later(browser, server):
    """更新对话框：版本、更新说明、自动下载开关、三个按钮都要工作。"""
    mac, ipad = open_pages(browser, server.port)
    open_update_dialog(mac)

    dialog = mac.locator(".update-dialog")
    assert "1.2.0" in dialog.inner_text()
    assert "已下载完毕" in dialog.inner_text()
    # PR 链接渲染成 #1285
    assert mac.locator(".update-notes a").first.inner_text() == "#1285"
    assert mac.locator(".update-notes code").count() == 1

    mac.locator(".update-auto input").uncheck()
    assert ["auto", False] in mac.evaluate("() => window.__calls")

    mac.click("button:has-text('退出应用时安装')")
    mac.locator(".update-dialog").wait_for(state="detached", timeout=3000)
    assert ["install", "quit"] in mac.evaluate("() => window.__calls")

    open_update_dialog(mac)
    mac.click("button:has-text('跳过这个版本')")
    mac.locator(".update-dialog").wait_for(state="detached", timeout=3000)
    assert ["skip"] in mac.evaluate("() => window.__calls")
    mac.close()
    ipad.close()


def test_update_downloads_before_installing_when_not_staged(browser, server):
    """没下好就点安装：先下载再装，两步分开，进度条才有意义。"""
    mac, ipad = open_pages(browser, server.port)
    open_update_dialog(mac, staged=False)
    mac.click("button:has-text('下载并安装')")
    mac.wait_for_function("() => window.__calls.some(c => c[0] === 'install')", timeout=3000)
    calls = mac.evaluate("() => window.__calls.map(c => c.join(':'))")
    assert calls.index("download") < calls.index("install:now"), "应当先下载再安装"
    mac.close()
    ipad.close()


def test_update_dialog_shows_progress_while_downloading(browser, server):
    mac, ipad = open_pages(browser, server.port)
    open_update_dialog(mac, staged=False)
    dialog = mac.locator(".update-dialog")
    assert "正在下载" in dialog.inner_text()
    width = mac.evaluate("() => document.querySelector('.update-progress i').style.width")
    assert width == "40%"
    assert "40%" == mac.evaluate(
        "() => document.querySelector('.update-progress i').style.width"
    )
    mac.close()
    ipad.close()


def test_release_notes_are_escaped(browser, server):
    """更新说明来自网络，必须当纯文本处理。"""
    mac, _ipad = open_pages(browser, server.port)
    html = mac.evaluate(
        "async () => { const m = await import('/static/js/notes.js');"
        "return m.renderNotes('- <img src=x onerror=alert(1)> **粗** `码`\\n- [看](https://example.com/a)'); }"
    )
    assert "<img" not in html
    assert "&lt;img" in html
    assert "<strong>粗</strong>" in html and "<code>码</code>" in html
    assert '<a href="https://example.com/a">看</a>' in html
    mac.close()


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


ALL_PERMS = 'data-perms="clear export manage settings"'


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
    page.reload()
    page.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    assert page.evaluate("() => document.documentElement.dataset.perms") == perms


def as_native(page):
    """把这一页变成「本地进程里的那个窗口」，重新加载后生效。"""
    page.add_init_script(NATIVE_STUB)
    page.reload()
    page.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    page.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true",
                           timeout=15000)


def test_about_panel_holds_version_and_update_entries(browser, server):
    """更新相关的入口收在「关于」里，白板设置只管背景、存储和权限。"""
    mac, ipad = open_pages(browser, server.port)
    as_native(mac)

    # 设置面板里不该再有检查更新，也不该再有连接 iPad 那张卡
    mac.click('button[title="白板设置"]')
    mac.wait_for_selector(".sheet .group-title")
    assert mac.locator("button:has-text('检查更新')").count() == 0
    assert mac.locator(".sheet .card .addr").count() == 1  # 只剩存储目录那一张
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.sheet .group-title')].map(h => h.textContent)"
    ) == ["背景", "存储目录", "其他设备权限"]
    mac.evaluate("() => whiteboard.ui.closeSheet()")

    mac.click('button[title="关于"]')
    about = mac.locator(".dialog.about")
    about.wait_for(timeout=3000)
    assert "白板" in about.inner_text()
    assert "0.9.4" in about.inner_text()
    assert about.locator("button:has-text('检查更新')").count() == 1
    assert about.locator("button:has-text('更新日志')").count() == 1
    mac.close()
    ipad.close()


def test_permission_switches_are_separate(browser, server):
    """三项权限各一个开关，互不牵连，改的是本地进程里的配置。"""
    mac, ipad = open_pages(browser, server.port)
    as_native(mac)
    mac.click('button[title="白板设置"]')
    mac.wait_for_selector(".sheet .perm-row")

    rows = "() => [...document.querySelectorAll('.sheet .perm-row input')].map(i => i.checked)"
    assert mac.evaluate(rows) == [False, False, False, False]
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.sheet .perm-title')].map(s => s.textContent)"
    ) == ["管理白板", "设置白板", "清空白板", "导出白板"]

    mac.locator(".sheet .perm-row input").nth(3).click()
    mac.wait_for_function("() => whiteboard.ui.info.remote_permissions.export === true")
    assert mac.evaluate(rows) == [False, False, False, True]
    assert mac.evaluate("() => window.__perms") == {
        "manage": False,
        "settings": False,
        "clear": False,
        "export": True,
    }
    mac.close()
    ipad.close()


def test_the_ui_only_draws_the_entries_it_is_allowed(browser, server):
    """右上角那几个入口按权限出，没权限就根本不画。"""
    mac, ipad = open_pages(browser, server.port)
    titles = "() => [...document.querySelectorAll('#topright button')].map(b => b.title)"

    # 本机：权限齐全，但「连接 iPad」「关于」要本地进程才有
    assert mac.evaluate(titles) == ["白板", "白板设置", "导出 PNG"]
    as_native(mac)
    assert mac.evaluate(titles) == ["白板", "白板设置", "导出 PNG", "连接 iPad", "关于"]

    # 装成局域网里的别的设备：把服务端发下来的那份清单改成只有 export
    mac.add_init_script("delete window.pywebview;")  # 别的设备不是本地进程
    serve_with_perms(mac, "export")
    assert mac.evaluate("() => document.documentElement.dataset.perms") == "export"
    assert mac.evaluate(titles) == ["导出 PNG"]

    # 没有任何权限时，那一簇整个不出现
    serve_with_perms(mac, "")
    assert mac.evaluate("() => !document.getElementById('topright')")
    mac.close()
    ipad.close()


def test_ipad_keeps_the_board_entries_when_the_picker_hides_the_toolbar(browser, server):
    """iPad 开了笔具盘之后，管理白板、白板设置、导出仍然要有入口。

    笔具盘一挂上，普通工具栏整条 `.hidden`。清空在笔具盘的「更多」里，连接状态点
    在左上角、不属于工具栏，这两项不受影响；剩下三项只剩右上角这一组。样式照
    iOS 的做法：同一条磨砂底，图标用强调色。
    """
    mac, ipad = open_pages(browser, server.port, picker=True)
    # 笔具盘是动态载入的，挂上之后才会把普通工具栏收起来
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    out = ipad.evaluate("""() => {
      const group = document.getElementById('topright');
      const toolbar = document.getElementById('toolbar');
      const dot = document.getElementById('status');
      return {
        条目: group ? [...group.querySelectorAll('button')].map((b) => b.title) : null,
        类名: group ? group.className : null,
        图标颜色: group ? getComputedStyle(group.querySelector('.icon-btn')).color : null,
        强调色: getComputedStyle(document.documentElement).getPropertyValue('--primary').trim(),
        工具栏隐藏: toolbar.classList.contains('hidden'),
        状态点可见: !!dot && getComputedStyle(dot).display !== 'none',
      };
    }""")
    assert out["工具栏隐藏"], out           # 笔具盘模式，普通工具栏收起来了
    assert out["条目"] == ["白板", "白板设置", "导出 PNG"], out
    assert "tinted" in out["类名"], out
    assert out["状态点可见"], out           # 诊断和录制的入口还在
    # 图标用强调色，不是默认的灰色
    r, g, b = [int(v) for v in out["图标颜色"].replace("rgb(", "").rstrip(")").split(",")]
    assert (r, g, b) == (0, 122, 255), out
    mac.close()
    ipad.close()


def test_clear_asks_before_it_wipes_the_board(browser, server):
    """垃圾桶点开要说清楚问的是什么，别让人对着一个图标猜。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 340)])
    wait_strokes(mac, 1)

    ipad.click('button[title="清空白板"]')
    ask = ipad.wait_for_selector(".dialog.ask")
    assert ipad.locator(".dialog.ask .ask-text").inner_text() == "清空白板？"
    ipad.click('.dialog button[title="取消"]')
    assert stroke_count(ipad) == 1  # 取消就是什么都没发生

    ipad.click('button[title="清空白板"]')
    ipad.click('.dialog button[title="确定"]')
    wait_strokes(mac, 0)

    # 白板列表里那个垃圾桶问的是另一件事
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.click('button[title="白板"]')
    mac.locator(".board-card").first.hover()  # 删除按钮悬停才出来
    mac.locator(".board-card .del").first.click()
    mac.wait_for_selector(".dialog.ask")
    assert mac.locator(".dialog.ask .ask-text").inner_text() == "删除白板？"
    mac.close()
    ipad.close()


def test_dialog_stays_solid_while_the_scrim_darkens(browser, server):
    """弹窗不能跟着那层暗底一起变透明，否则暗底直接透过来，看着像弹窗自己也黑了。

    暗底一旦用 opacity 淡入，它就成了子元素的 backdrop root：弹窗既跟着它一起
    半透明，磨砂又只采样得到这层暗底。所以暗底只淡背景色，弹窗的透明度在开头
    那一小段里就走完。这里把动画拉长，在中途去量。
    """
    mac, ipad = open_pages(browser, server.port)
    values = mac.evaluate(
        """async () => {
          document.documentElement.style.setProperty('--motion', '1200ms linear');
          document.querySelector('button[title="清空白板"]').click();
          await new Promise(r => setTimeout(r, 600));  // 动画正中间
          const scrim = document.querySelector('.scrim');
          const dialog = document.querySelector('.dialog.ask');
          return {
            scrim: getComputedStyle(scrim).opacity,
            dialog: getComputedStyle(dialog).opacity,
            shade: getComputedStyle(scrim).backgroundColor,
          };
        }"""
    )
    assert values["scrim"] == "1"  # 暗底自己不透明，只是背景色在变深
    assert values["dialog"] == "1"  # 弹窗早就到位了
    assert values["shade"] not in ("rgba(0, 0, 0, 0)", "transparent")  # 确实在变深
    mac.close()
    ipad.close()


def test_clear_button_disappears_without_the_permission(browser, server):
    """没给清空权限的设备，工具栏上连这个按钮都没有。"""
    mac, ipad = open_pages(browser, server.port)
    assert ipad.locator('button[title="清空白板"]').count() == 1

    serve_with_perms(ipad, "export manage settings")
    assert ipad.locator('button[title="清空白板"]').count() == 0
    assert ipad.locator('button[title="撤销"]').count() == 1  # 别的按钮照旧
    mac.close()
    ipad.close()


def test_trackpad_pinch_zooms(browser, server):
    """触控板捏合：WebKit 发的是 gesture 事件，Chrome 发的是 ctrl + wheel，两条都要认。"""
    mac, ipad = open_pages(browser, server.port)
    read = "() => whiteboard.viewport.scale"
    mac.evaluate("() => { whiteboard.viewport.scale = 1; whiteboard.renderer.requestFull(); }")

    # WebKit 那套：scale 是从手势开始算起的累计倍数
    mac.evaluate(
        """() => {
          const stage = document.getElementById('stage');
          const fire = (type, scale) => stage.dispatchEvent(Object.assign(
            new Event(type, { bubbles: true, cancelable: true }),
            { scale, rotation: 0, clientX: 600, clientY: 400 }));
          fire('gesturestart', 1);
          fire('gesturechange', 1.5);
          fire('gesturechange', 2);
          fire('gestureend', 2);
        }"""
    )
    assert abs(mac.evaluate(read) - 2) < 0.05

    # Chrome 那套：ctrl + wheel，一格鼠标滚轮（deltaY 100）会被夹住，不会一下缩掉三分之二
    mac.evaluate("() => { whiteboard.viewport.scale = 1; }")
    mac.evaluate(
        """() => document.getElementById('stage').dispatchEvent(new WheelEvent('wheel', {
          deltaY: 100, ctrlKey: true, clientX: 600, clientY: 400,
          bubbles: true, cancelable: true }))"""
    )
    after = mac.evaluate(read)
    assert 0.7 < after < 0.85, after

    mac.evaluate("() => { whiteboard.viewport.scale = 1; }")
    mac.evaluate(
        """() => document.getElementById('stage').dispatchEvent(new WheelEvent('wheel', {
          deltaY: -6, ctrlKey: true, clientX: 600, clientY: 400,
          bubbles: true, cancelable: true }))"""
    )
    assert mac.evaluate(read) > 1  # 捏开就是放大

    # 指针停在工具栏上时不动画布（浏览器的整页缩放仍然被拦下）
    mac.evaluate("() => { whiteboard.viewport.scale = 1; }")
    pinch = """(where) => {
      const node = document.querySelector(where);
      const fire = (type, scale) => node.dispatchEvent(Object.assign(
        new Event(type, { bubbles: true, cancelable: true }),
        { scale, rotation: 0, clientX: 600, clientY: 400 }));
      fire('gesturestart', 1);
      fire('gesturechange', 2);
      fire('gestureend', 2);
    }"""
    mac.evaluate(pinch, "#toolbar")
    assert mac.evaluate(read) == 1

    # iPad 上双指是自己用 pointer 事件做的，WebKit 还会同时发 gesture，别缩两次
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; whiteboard.input.gesture = {}; }")
    ipad.evaluate(pinch, "#stage")
    assert ipad.evaluate(read) == 1
    ipad.evaluate("() => { whiteboard.input.gesture = null; }")
    mac.close()
    ipad.close()


def test_check_update_button_says_what_happened(browser, server):
    """点「检查更新」必须给出人话，而不是一个没头没尾的对勾。"""
    mac, ipad = open_pages(browser, server.port)

    cases = [
        ({"status": "latest", "version": "0.9.3"}, "已是最新"),
        ({"status": "error", "message": "连不上 GitHub"}, "检查失败：连不上 GitHub"),
        ({"status": "source", "version": "1.0.0"}, "git pull"),
        ({"status": "skipped", "version": "1.2.0"}, "已被跳过"),
    ]
    for result, expected in cases:
        mac.evaluate(
            """([result]) => {
              window.pywebview = { api: {
                info: async () => ({ native: true, version: '0.9.3', packaged: true }),
                check_update_now: async () => result,
              }};
            }""",
            [result],
        )
        mac.evaluate("() => { document.querySelectorAll('.scrim').forEach(n => n.remove());"
                     "whiteboard.ui.openAbout(); }")
        mac.click("button:has-text('检查更新')")
        note = mac.locator(".check-note")
        note.wait_for()
        mac.wait_for_function(
            "(text) => { const n = document.querySelector('.check-note');"
            "return n && n.textContent.includes(text); }",
            arg=expected,
            timeout=3000,
        )

    # 查到新版本时直接弹出对话框
    mac.evaluate(
        """([notes]) => {
          window.__calls = [];
          window.pywebview = { api: {
            info: async () => ({ native: true, version: '0.9.3', packaged: true }),
            check_update_now: async () => ({ status: 'update', version: '1.0.0', notes }),
            update_state: async () => ({ current: '0.9.3', auto: true, staged: true,
              downloading: false, progress: 1, info: { version: '1.0.0', notes } }),
            set_auto_update: async (v) => v,
            skip_update: async () => true,
            install_update: async (mode) => { window.__calls.push(mode); return true; },
          }};
        }""",
        [UPDATE_NOTES],
    )
    mac.evaluate("() => { document.querySelectorAll('.scrim').forEach(n => n.remove());"
                 "whiteboard.ui.openAbout(); }")
    mac.click("button:has-text('检查更新')")
    mac.locator(".update-dialog").wait_for(timeout=3000)
    assert "1.0.0" in mac.locator(".update-dialog").inner_text()
    mac.close()
    ipad.close()


def test_debug_overlay_toggles(browser, server):
    mac, ipad = open_pages(browser, server.port)
    assert ipad.evaluate("() => !!document.getElementById('perf')") is False
    for _ in range(3):
        ipad.click("#status")
    assert ipad.evaluate("() => whiteboard.perf.enabled") is True
    ipad.wait_for_function("() => { const n = document.getElementById('perf'); return n && n.textContent.includes('帧'); }")
    for _ in range(3):
        ipad.click("#status")
    assert ipad.evaluate("() => whiteboard.perf.enabled") is False
    mac.close()
    ipad.close()


def test_stage_blocks_ios_selection_gestures(browser, server):
    """iPadOS 的选择 / 查词手势必须被挡掉，否则写快了会丢笔。"""
    mac, ipad = open_pages(browser, server.port)
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
    mac.close()
    ipad.close()


def test_board_switch_and_settings_follow(browser, server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (400, 350)])
    wait_strokes(mac, 1)
    first_board = ipad.evaluate("() => whiteboard.state.id")

    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="大白板"]')
    ipad.wait_for_function(f"() => whiteboard.state.id !== '{first_board}'")
    assert stroke_count(ipad) == 0  # 新白板是空的

    # 背景改动会同步到 iPad
    mac.click('button[title="白板设置"]')
    mac.click('.bg-opt[title="dots"]')
    ipad.wait_for_function("() => whiteboard.state.meta.background === 'dots'")
    mac.close()
    ipad.close()


def test_the_version_survives_switching_boards(browser, server):
    """「关于」里的版本号来自服务端。以前是本地接口给的，被 switch 里那份信息冲掉。"""
    mac, ipad = open_pages(browser, server.port)
    mac.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.version")
    version = mac.evaluate("() => whiteboard.ui.info.version")
    assert version

    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    assert mac.evaluate("() => whiteboard.ui.info.version") == version

    # 「关于」里能看到版本；从源码跑时再多一行「构建」，写的是分支和提交。
    # 那个按钮只在 pywebview 窗口里有（this.native），浏览器里直接把面板叫出来。
    mac.evaluate("() => whiteboard.ui.openAbout()")
    mac.wait_for_selector(".dialog.about")
    rows = mac.evaluate("() => [...document.querySelectorAll('.about-key')].map(n => n.textContent)")
    values = mac.evaluate("() => [...document.querySelectorAll('.about-val')].map(n => n.textContent)")
    assert rows[0] == "版本" and values[0] == version
    assert "构建" in rows
    mac.close()
    ipad.close()


def test_board_cards_show_names_and_dates(browser, server):
    """卡片下面有名字和日期；没起名的显示默认叫法，改名之后两端都跟着变。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    mac.click('button[title="白板"]')
    mac.wait_for_selector(".board-item")

    names = "() => [...document.querySelectorAll('.board-name')].map(i => [i.value, i.placeholder])"
    assert mac.evaluate(names) == [["", "笔记"], ["", "白板"]]  # 没名字就按延伸方式给默认
    assert mac.evaluate("() => document.querySelectorAll('.board-date').length") == 2

    # 改名：输进去按回车，广播回来之后列表和 iPad 都认得
    board_id = mac.evaluate("() => whiteboard.state.id")
    field = mac.wait_for_selector(f'.board-name[data-focus-key="name:{board_id}"]')
    field.click()
    field.fill("  线性代数  ")
    mac.keyboard.press("Enter")
    mac.wait_for_function(
        "() => whiteboard.ui.boards.some(b => b.name === '线性代数')", timeout=4000
    )
    ipad.wait_for_function(
        "() => whiteboard.ui.boards.some(b => b.name === '线性代数')", timeout=4000
    )
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 0  # 没把整块白板重发一遍
    mac.close()
    ipad.close()


def test_the_corner_group_gets_out_of_the_pickers_way(browser, server):
    """笔具盘停到右上角那一组底下时，那一组让开；挪走再回来。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker")
    ipad.wait_for_selector("#topright")
    assert ipad.evaluate("() => document.querySelector('#topright').classList.contains('shy')") is False

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
    ipad.wait_for_function("() => document.querySelector('#topright').classList.contains('shy')")

    ipad.evaluate("() => { document.querySelector('#pk-host .pk-picker').style.top = '600px'; }")
    ipad.wait_for_function("() => !document.querySelector('#topright').classList.contains('shy')")
    mac.close()
    ipad.close()


def test_touch_devices_get_no_hover_styling(browser, server):
    """iPadOS 上点过的元素会一直挂着 :hover，看着像被按住。所有 hover 都关在
    `@media (hover: hover)` 里，触摸设备不给这套反馈。"""
    _mac, ipad = open_pages(browser, server.port)
    ipad.wait_for_selector("#topright")
    rules = ipad.evaluate(
        """() => {
          const bare = [];
          for (const sheet of document.styleSheets) {
            let list;
            try { list = sheet.cssRules; } catch (err) { continue; }
            for (const rule of list) {
              if (rule.selectorText && rule.selectorText.includes(':hover')) bare.push(rule.selectorText);
            }
          }
          return bare;
        }"""
    )
    assert rules == [], f"这些 :hover 没有关在 hover 媒体查询里：{rules}"

    # 触摸设备上删除按钮一直显示：没有「划过」这回事，否则删不掉
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-item")
    ipad.click(".board-card.add")
    ipad.click('.kind-tile[title^="笔记"]')
    ipad.wait_for_function("() => whiteboard.state.kind === 'note'")
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-card .del")
    assert ipad.evaluate(
        "() => getComputedStyle(document.querySelector('.board-card .del')).display"
    ) == "flex"
    ipad.close()


def test_the_picker_does_not_float_over_the_board_chooser(browser, server):
    """笔具盘自带 z-index，白板选择界面必须压在它上面，否则一进来它还浮着。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker")
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".gallery")

    # 选择界面是不透明的，盖住就等于看不见：量的是同一个点上谁在上面
    top = ipad.evaluate(
        """() => {
          const box = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();
          const node = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
          return !!(node && node.closest('.gallery'));
        }"""
    )
    assert top, "笔具盘还浮在白板选择界面上面"
    # 而且整条收起来了：它每帧都在画自己那层，留在上面就是一直闪
    assert ipad.evaluate("() => getComputedStyle(document.querySelector('#pk-host')).display") == "none"

    # 列表被广播刷新时不重放淡入，否则别处一改名这边就闪一下
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    ipad.wait_for_function("() => whiteboard.ui.folders.length === 1")
    assert ipad.evaluate("() => !!document.querySelector('.gallery.opening')") is False

    ipad.click('button[title="关闭"]')
    ipad.wait_for_function(
        "() => getComputedStyle(document.querySelector('#pk-host')).display !== 'none'"
    )
    mac.close()
    ipad.close()


def test_boards_can_be_filed_into_a_folder(browser, server):
    """文件夹是格子里的一块卡片：点进去只剩里面的白板，在里面新建的也留在里面。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    mac.wait_for_selector(".board-card.folder")
    assert mac.evaluate("() => document.querySelector('.board-name.folder').value") == "未命名文件夹"

    # 归类：卡片下面那个文件夹按钮，选现成的名字
    mac.click(".board-folder")
    mac.click('.folder-row:has-text("未命名文件夹")')
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")
    ipad.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")

    # 最外面那一层只剩文件夹卡片和「新建」，白板收进去了
    count = "() => document.querySelectorAll('.board-item').length"
    mac.wait_for_function(f"{count} === 2")
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    assert mac.evaluate(count) == 2  # 里面那块白板 + 「新建」

    # 在文件夹里新建的白板跟着落在这个文件夹里
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    mac.wait_for_function("() => whiteboard.ui.boards.length === 2")
    assert mac.evaluate("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")

    # 再打开选择界面，直接停在当前白板所在的那一层
    mac.click('button[title="白板"]')
    mac.wait_for_selector(".folder-bar")
    mac.close()
    ipad.close()


def test_a_board_can_be_dragged_into_a_folder_and_back_out(browser, server):
    """Mac 上归类不用开对话框：把卡片拖到文件夹上，拖回返回按钮就是移出来。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    mac.wait_for_selector(".board-card.folder")

    # 拖进去：松手之后这块白板收进文件夹，最外面那层只剩文件夹卡片和「新建」
    mouse_drag(mac, ".board-card:not(.folder):not(.add)", ".board-card.folder", dwell=120)
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")
    ipad.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")
    mac.wait_for_function("() => document.querySelectorAll('.board-item').length === 2")

    # 拖出来：进文件夹，把卡片拖到搜索框左边那个返回按钮上
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    mouse_drag(mac, ".board-card:not(.folder):not(.add)", 'button[title^="返回"]', dwell=120)
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => !b.folder)")
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 1  # 文件夹还在，只是空了
    mac.close()
    ipad.close()


def test_boards_can_be_dragged_into_a_new_order(browser, server):
    """拖着卡片在格子里走，别的卡片让位；松手之后这个顺序两端都认，也存得住。"""
    mac, ipad = open_pages(browser, server.port)
    for kind in ("笔记", "笔记"):
        mac.click('button[title="白板"]')
        mac.click(".board-card.add")
        mac.click(f'.kind-tile[title^="{kind}"]')
        mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    mac.click('button[title="白板"]')
    mac.wait_for_function("() => document.querySelectorAll('.board-item').length === 4")

    # 只数格子里的：正在拖的那一格挪到了界面根节点上，落位动画没走完之前它还在
    order = "() => [...document.querySelectorAll('.gallery-grid .board-item[data-board]')].map(n => n.dataset.board)"
    before = mac.evaluate(order)
    assert len(before) == 3

    # 把第一块拖到第三块的右半边，它就排到最后（落点在左半边是插到前面）
    items = ".board-item[data-board] .board-card"
    mouse_drag(mac, f"{items} >> nth=0", f"{items} >> nth=2", at=(0.85, 0.5))
    mac.wait_for_function(
        f"() => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) === "
        f"JSON.stringify({before[1:] + before[:1]})"
    )
    ipad.wait_for_function(
        f"() => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) === "
        f"JSON.stringify({before[1:] + before[:1]})"
    )
    mac.wait_for_function("() => !document.querySelector('.board-item.lifted')")
    assert mac.evaluate(order) == before[1:] + before[:1]

    # 刷新之后还是这个顺序：顺序存在服务端的索引里，不是本机记的
    mac.reload()
    mac.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    mac.click('button[title="白板"]')
    mac.wait_for_selector(".board-item")
    assert mac.evaluate(order) == before[1:] + before[:1]
    mac.close()
    ipad.close()


def mouse_drag(page, from_sel, to_sel, dwell=320, steps=12, at=(0.5, 0.5)):
    """鼠标拖一张卡片。

    ``dwell`` 是停在落点上多久再松手：让位要在同一个插入位置停够 REORDER_DELAY
    （见 dragsort.js）。``at`` 是落点在目标元素上的相对位置。
    """
    a = page.locator(from_sel).bounding_box()
    b = page.locator(to_sel).bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 12, a["y"] + a["height"] / 2, steps=3)
    page.mouse.move(b["x"] + b["width"] * at[0], b["y"] + b["height"] * at[1], steps=steps)
    page.wait_for_timeout(dwell)
    page.mouse.up()


TOUCH_DRAG = """
async ([fromSel, toSel, holdMs, steps, dwellMs]) => {
  const from = document.querySelector(fromSel).getBoundingClientRect();
  const to = document.querySelector(toSel).getBoundingClientRect();
  const at = { x: from.left + from.width / 2, y: from.top + from.height / 2 };
  const end = { x: to.left + to.width / 2, y: to.top + to.height / 2 };
  const fire = (type, x, y, target) => target.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, pointerId: 11, pointerType: 'touch', isPrimary: true,
    bubbles: true, cancelable: true,
  }));
  fire('pointerdown', at.x, at.y, document.querySelector(fromSel));
  await new Promise((done) => setTimeout(done, holdMs));
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', at.x + (end.x - at.x) * i / steps, at.y + (end.y - at.y) * i / steps, window);
    await new Promise((done) => requestAnimationFrame(done));
  }
  await new Promise((done) => setTimeout(done, dwellMs));
  fire('pointerup', end.x, end.y, window);
}
"""


SWIPE = """
async ([sel, dx, dy, steps]) => {
  const card = document.querySelector(sel);
  const box = card.getBoundingClientRect();
  const at = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  const fire = (type, x, y, node) => node.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, pointerId: 12, pointerType: 'touch', isPrimary: true,
    bubbles: true, cancelable: true,
  }));
  fire('pointerdown', at.x, at.y, card);
  for (let i = 1; i <= steps; i++) {
    fire('pointermove', at.x + dx * i / steps, at.y + dy * i / steps, window);
    await new Promise((done) => requestAnimationFrame(done));
  }
  await new Promise((done) => setTimeout(done, 60));
  const lifted = !!document.querySelector('.board-item.lifted');
  fire('pointerup', at.x + dx, at.y + dy, window);
  return lifted;
}
"""


def test_a_sideways_swipe_starts_the_drag_without_waiting(browser, server):
    """列表竖着滚，所以横着走的手势只可能是想拖：不用按住，立刻起拖。
    竖着划还是滚列表。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-card")
    card = ".board-item[data-board] .board-card"

    # 横着划 120px，全程不到 100ms，远不够按住时长
    assert ipad.evaluate(SWIPE, [card, 120, 0, 6]) is True
    ipad.wait_for_function("() => !document.querySelector('.board-item.lifted')")

    # 竖着划同样的距离：这是在滚列表，不能起拖
    assert ipad.evaluate(SWIPE, [card, 0, 120, 6]) is False
    mac.close()
    ipad.close()


def test_a_slow_tap_does_not_open_the_board(browser, server):
    """按住超过起拖时长再松手，卡片只是浮起来又落回去，不能顺手切过去。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-card")
    before = ipad.evaluate("() => whiteboard.state.id")

    ipad.evaluate(
        """async (sel) => {
          const card = document.querySelector(sel);
          const box = card.getBoundingClientRect();
          const x = box.left + box.width / 2;
          const y = box.top + box.height / 2;
          const fire = (type, node) => node.dispatchEvent(new PointerEvent(type, {
            clientX: x, clientY: y, pointerId: 13, pointerType: 'touch', isPrimary: true,
            bubbles: true, cancelable: true,
          }));
          fire('pointerdown', card);
          await new Promise((done) => setTimeout(done, 400));
          fire('pointerup', window);
          // 浏览器补的那一下 click
          card.dispatchEvent(new MouseEvent('click', { clientX: x, clientY: y, bubbles: true, cancelable: true }));
        }""",
        ".board-item[data-board] .board-card",
    )
    ipad.wait_for_timeout(300)
    assert ipad.evaluate("() => whiteboard.state.id") == before
    assert ipad.query_selector(".gallery") is not None  # 界面也没被关掉
    mac.close()
    ipad.close()


def test_a_finger_can_drag_a_board_on_a_touch_device(browser, server):
    """iPad 上按住一会儿再拖：手指底下跟着一张副本，松手落在文件夹里。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    mac.wait_for_selector(".board-card.folder")

    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-card.folder")

    # 手指一放就竖着走：当成滚列表，不拖。必须是竖着走——横着走的手势按规则立刻
    # 起拖（见 test_a_sideways_swipe_starts_the_drag_without_waiting）。这里以前是
    # 从白板卡片直接划向旁边的文件夹，那是横着走，会不会真的落进文件夹全看时机，
    # 用例因此时好时坏。
    lifted = ipad.evaluate(SWIPE, [".board-item[data-board] .board-card", 0, 120, 6])
    assert lifted is False
    assert ipad.evaluate("() => whiteboard.ui.boards.every(b => !b.folder)")
    assert ipad.evaluate("() => !document.querySelector('.board-card.ghost')")

    # 按住一会儿再拖：这次算数
    ipad.evaluate(TOUCH_DRAG, [".board-item[data-board] .board-card", ".board-card.folder", 500, 6, 200])
    ipad.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")
    # 副本是临时的，落位动画走完就撤掉
    ipad.wait_for_function("() => !document.querySelector('.board-card.ghost')")
    mac.close()
    ipad.close()


def test_a_finger_can_drag_a_board_into_a_new_order(browser, server):
    """同一套长按拖动也能排序：松手之后顺序存到服务端。"""
    mac, ipad = open_pages(browser, server.port)
    for _ in range(2):
        mac.click('button[title="白板"]')
        mac.click(".board-card.add")
        mac.click('.kind-tile[title^="笔记"]')
        mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    ipad.click('button[title="白板"]')
    ipad.wait_for_function("() => document.querySelectorAll('.board-item[data-board]').length === 3")

    order = "() => whiteboard.ui.boards.map(b => b.id)"
    before = ipad.evaluate(order)
    cards = ".board-item[data-board] .board-card"
    ipad.evaluate(
        TOUCH_DRAG,
        [f"{cards}", ".board-item[data-board]:nth-child(3) .board-card", 500, 8, 320],
    )
    ipad.wait_for_function(
        "(was) => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) !== JSON.stringify(was)",
        arg=before,
    )
    after = ipad.evaluate(order)
    assert sorted(after) == sorted(before) and after != before
    mac.wait_for_function(
        "(now) => JSON.stringify(whiteboard.ui.boards.map(b => b.id)) === JSON.stringify(now)",
        arg=after,
    )
    mac.close()
    ipad.close()


def test_deleting_a_folder_keeps_the_boards(browser, server):
    """删文件夹只是取消归类，里面的白板一块都不能少。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    mac.wait_for_selector(".board-card.folder")
    mac.click(".board-folder")
    mac.click('.folder-row:has-text("未命名文件夹")')
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")

    mac.click(".board-card.folder")
    mac.click('button[title="删除文件夹"]')
    mac.click('.dialog button[title="确定"]')
    mac.wait_for_function("() => whiteboard.ui.folders.length === 0")
    assert mac.evaluate("() => whiteboard.ui.boards.length") == 1
    assert mac.evaluate("() => whiteboard.ui.boards.every(b => !b.folder)")
    mac.wait_for_selector(".board-card:not(.folder)")
    mac.close()
    ipad.close()


def test_a_folder_can_be_renamed_from_its_card(browser, server):
    """文件夹的名字就是它的身份，改名要把里面每块白板上记的名字一起改掉。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac)
    mac.wait_for_selector(".board-card.folder")
    mac.click(".board-folder")
    mac.click('.folder-row:has-text("未命名文件夹")')
    mac.wait_for_function("() => whiteboard.ui.boards.every(b => b.folder === '未命名文件夹')")

    field = mac.wait_for_selector('.board-name[data-focus-key="folder:未命名文件夹"]')
    field.click()
    field.fill("  数学  ")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('数学')")
    assert mac.evaluate("() => whiteboard.ui.boards.every(b => b.folder === '数学')")
    ipad.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")
    mac.close()
    ipad.close()


def new_folder(page, name=None):
    """点「新建文件夹」：名字框直接进入编辑、默认名全选；输入名字（或保留默认名）后回车。"""
    page.click('.kind-tile[title="新建文件夹"]')
    field = page.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    assert page.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    if name is not None:
        page.keyboard.type(name)
    page.keyboard.press("Enter")
    target = name if name is not None else field.get_attribute("value")
    page.wait_for_function("name => whiteboard.ui.folders.includes(name)", arg=target)
    return target


def test_a_new_folder_is_named_right_away_like_finder(browser, server):
    """新建文件夹后名字框直接可编辑、默认名全选：直接打字就是新名字，回车后建好。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title="新建文件夹"]')
    mac.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    selected = mac.evaluate(
        "() => { const i = document.activeElement; return [i.dataset.focusKey, i.selectionStart, i.selectionEnd, i.value.length]; }"
    )
    assert selected[0] == "folder-draft" and selected[1] == 0 and selected[2] == selected[3] > 0
    assert mac.evaluate("() => whiteboard.ui.folders.length") == 0  # 回车之前还没建
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    for page in (mac, ipad):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '数学'")
    mac.wait_for_selector('.board-name.folder[data-focus-key="folder:数学"]')

    # Esc：按默认名建，和访达一样不会撤销新建
    mac.click(".board-card.add")
    mac.click('.kind-tile[title="新建文件夹"]')
    mac.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    mac.keyboard.type("物理")
    mac.keyboard.press("Escape")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('未命名文件夹')")
    assert mac.query_selector(".gallery") is not None, "Esc 只结束起名，不关界面"

    # 重名：提示并留在编辑状态
    mac.click(".board-card.add")
    mac.click('.kind-tile[title="新建文件夹"]')
    mac.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    mac.keyboard.type("数学")
    mac.keyboard.press("Enter")
    assert mac.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    mac.keyboard.type("化学")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.folders.includes('化学')")
    assert sorted(mac.evaluate("() => whiteboard.ui.folders")) == sorted(["数学", "未命名文件夹", "化学"])
    mac.close()
    ipad.close()


def test_a_new_folder_can_be_named_on_the_ipad(browser, server):
    """iPad 上点「新建文件夹」同样直接进入起名，键盘输入就是名字。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.click('button[title="白板"]')
    ipad.tap(".board-card.add")
    ipad.tap('.kind-tile[title="新建文件夹"]')
    ipad.wait_for_selector('.board-name[data-focus-key="folder-draft"]')
    assert ipad.evaluate("() => document.activeElement.dataset.focusKey") == "folder-draft"
    ipad.keyboard.type("英语")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function("() => whiteboard.ui.folders.join() === '英语'")
    mac.close()
    ipad.close()


DROP_FILE = """([name, type, base64]) => {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const data = new DataTransfer();
  data.items.add(new File([bytes], name, { type }));
  for (const kind of ["dragover", "drop"]) {
    window.dispatchEvent(new DragEvent(kind, { dataTransfer: data, bubbles: true, cancelable: true }));
  }
}"""


def _png_base64():
    import base64, io
    from PIL import Image as _Image

    buffer = io.BytesIO()
    _Image.new("RGB", (64, 48), (240, 240, 240)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_a_file_dropped_inside_a_folder_lands_in_that_folder(browser, server):
    """选择界面停在文件夹里时把图片拖进窗口，新文档板留在这个文件夹里。"""
    pytest.importorskip("PIL")
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    new_folder(mac, "课件")
    mac.click(".board-card.folder")
    mac.wait_for_selector(".folder-bar")
    before = mac.evaluate("() => whiteboard.ui.boards.length")
    mac.evaluate(DROP_FILE, ["讲义.png", "image/png", _png_base64()])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 1", arg=before)
    doc = mac.evaluate("() => whiteboard.ui.boards.find(b => b.kind === 'doc')")
    assert doc["folder"] == "课件"

    # 选择界面关着：落在当前白板所在的文件夹（现在当前白板就是刚建的文档板）
    mac.wait_for_function(f"() => whiteboard.state.id === '{doc['id']}'")
    if mac.query_selector(".gallery"):
        mac.click('button[title="关闭"]')
    mac.evaluate(DROP_FILE, ["讲义2.png", "image/png", _png_base64()])
    mac.wait_for_function("n => whiteboard.ui.boards.length === n + 2", arg=before)
    folders = mac.evaluate("() => whiteboard.ui.boards.filter(b => b.kind === 'doc').map(b => b.folder)")
    assert folders == ["课件", "课件"]
    mac.close()
    ipad.close()


def test_board_search_filters_by_name(browser, server):
    """搜索框按名字筛，搜不到给个空态；清空之后「新建」那块回来。"""
    mac, ipad = open_pages(browser, server.port)
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    board_id = mac.evaluate("() => whiteboard.state.id")
    mac.click('button[title="白板"]')
    field = mac.wait_for_selector(f'.board-name[data-focus-key="name:{board_id}"]')
    field.click()
    field.fill("线性代数")
    mac.keyboard.press("Enter")
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.name === '线性代数')")

    count = "() => document.querySelectorAll('.board-item').length"
    assert mac.evaluate(count) == 3  # 两块白板 + 新建

    mac.click(".board-search")
    mac.keyboard.type("线性")
    mac.wait_for_function(f"{count} === 1")  # 搜索结果里不放「新建」
    assert mac.evaluate("() => document.querySelector('.board-name').value") == "线性代数"

    # 默认叫法也能搜到：另一块没起名，显示的是「白板」
    mac.keyboard.press("Control+a")
    mac.keyboard.type("白板")
    mac.wait_for_function(f"{count} === 1")
    assert mac.evaluate("() => document.querySelector('.board-name').placeholder") == "白板"

    mac.keyboard.press("Control+a")
    mac.keyboard.type("查无此板")
    mac.wait_for_selector(".gallery-empty")

    mac.keyboard.press("Escape")  # 清空搜索，不关界面
    mac.wait_for_function(f"{count} === 3")
    assert mac.query_selector(".gallery") is not None
    mac.close()
    ipad.close()


def test_offline_drawing_is_flushed_after_server_restart(browser, server, tmp_path):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 350)])
    wait_strokes(mac, 1)

    port = server.port
    server.stop()
    ipad.wait_for_function("() => whiteboard.net.status === 'offline'")

    # 断线期间照常书写：本地立刻可见，操作进入待发队列
    draw(ipad, [(400, 400), (480, 460), (520, 400)])
    assert stroke_count(ipad) == 2
    assert ipad.evaluate("() => whiteboard.net.outbox.length") >= 1

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = port
    restarted = ServerThread(config, advertise=False)
    restarted.start()
    try:
        ipad.wait_for_function("() => whiteboard.net.status === 'online'", timeout=15000)
        ipad.wait_for_function("() => whiteboard.net.outbox.length === 0", timeout=15000)
        assert stroke_count(ipad) == 2  # 重启后内容没有变成空白
        wait_strokes(mac, 2)
    finally:
        restarted.stop()
    mac.close()
    ipad.close()


def test_export_png_has_content(browser, server):
    mac, _ipad = open_pages(browser, server.port)
    draw(mac, [(300, 300), (400, 360), (500, 300)], pointer_type="mouse")
    wait_strokes(mac, 1)
    data_url = mac.evaluate(
        "async () => { const m = await import('/static/js/exporter.js');"
        "return m.exportDataURL(whiteboard.state); }"
    )
    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 5000
    mac.close()


def make_doc(tmp_path, pages=((595, 842), (595, 842))):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    writer = pypdf.PdfWriter()
    for width, height in pages:
        writer.add_blank_page(width=width, height=height)
    path = tmp_path / "讲义.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def open_doc_board(mac, path):
    """走完整的界面路径：白板列表 → 新建 → 选文件。"""
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    with mac.expect_file_chooser() as chooser:
        mac.click(".dialog.kinds .kind-tile:nth-child(3)")
    chooser.value.set_files(str(path))
    mac.wait_for_function("() => whiteboard.state.kind === 'doc'", timeout=15000)


def test_doc_board_created_from_file_and_synced(browser, server, tmp_path):
    """拖 / 选一份 PDF：两端都切过去，页面底图取得回来，笔只能写在文档范围内。"""
    path = make_doc(tmp_path)
    mac, ipad = open_pages(browser, server.port)
    open_doc_board(mac, path)
    ipad.wait_for_function("() => whiteboard.state.kind === 'doc'", timeout=15000)

    pages = mac.evaluate("() => whiteboard.state.pages")
    assert len(pages) == 2
    assert pages[1]["y"] == pytest.approx(842 + 24)
    limits = mac.evaluate("() => whiteboard.state.limits")
    assert limits["y1"] == pytest.approx(842 * 2 + 24)

    # 首页底图由服务端渲染后送过来
    mac.wait_for_function("() => whiteboard.renderer.docPages.get(0) !== null", timeout=15000)

    # 从末页里落笔，一路划到文档下方：超出的部分被夹回最后一页里
    start = ipad.evaluate(
        "() => { const l = whiteboard.state.limits;"
        " whiteboard.viewport.scale = 0.4;"
        " whiteboard.viewport.centerOn(l.x1 / 2, l.y1 - 120, 1180, 820);"
        " whiteboard.renderer.requestFull();"
        " return whiteboard.viewport.toScreen(l.x1 / 2, l.y1 - 60); }"
    )
    draw(ipad, [(start[0], start[1]), (start[0] + 40, start[1] + 120), (start[0] + 80, start[1] + 240)])
    wait_strokes(mac, 1)
    ys = mac.evaluate("() => whiteboard.state.strokes[0].p.filter((_, i) => i % 3 === 1)")
    assert max(ys) <= limits["y1"] + 0.01
    mac.close()
    ipad.close()


def test_doc_board_export_merges_ink(browser, server, tmp_path):
    import urllib.request

    path = make_doc(tmp_path)
    mac, ipad = open_pages(browser, server.port)
    open_doc_board(mac, path)
    draw(mac, [(300, 300), (380, 360), (460, 300)], pointer_type="mouse")
    wait_strokes(ipad, 1)

    board_id = mac.evaluate("() => whiteboard.state.id")
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/export/{board_id}") as out:
        body = out.read()
    assert body.startswith(b"%PDF")
    # 笔迹进去了，但体积没涨多少
    assert len(body) > path.stat().st_size
    assert len(body) < path.stat().st_size + 4096
    mac.close()
    ipad.close()


def test_ipad_toolbar_can_move_to_the_top(browser, server):
    """底部那条够不着时可以挪到上边，并且记在本机。"""
    _mac, ipad = open_pages(browser, server.port)

    def dock():
        return ipad.evaluate("() => document.documentElement.dataset.toolbar")

    def toolbar_top():
        return ipad.evaluate("() => document.getElementById('toolbar').getBoundingClientRect().top")

    assert dock() == "bottom"
    bottom_y = toolbar_top()

    ipad.click('button[title="工具栏换个位置"]')
    assert dock() == "top"
    assert toolbar_top() < bottom_y / 2

    # 提示条让开了，不会压在工具栏上
    notice_top = ipad.evaluate(
        "() => { const n = document.createElement('div'); n.className = 'notice';"
        " document.getElementById('ui').append(n);"
        " const t = n.getBoundingClientRect().top; n.remove(); return t; }"
    )
    assert notice_top > toolbar_top()

    # 颜色面板改成往下开，不会跑到屏幕外面
    ipad.click('button[title="颜色与粗细"]')
    box = ipad.evaluate("() => { const p = document.querySelector('.popover');"
                        " const r = p.getBoundingClientRect(); return [r.top, r.bottom]; }")
    assert box[0] > toolbar_top()
    assert box[1] <= ipad.evaluate("() => innerHeight")

    ipad.reload()
    ipad.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    assert dock() == "top"  # 换个位置之后记得住

    ipad.click('button[title="工具栏换个位置"]')
    assert dock() == "bottom"
    _mac.close()
    ipad.close()


def drag(page, x0, y0, x1, y1, steps=12):
    """慢放：终点停住一下再松手，速度归零，工具盘就以松手点为停点。"""
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    page.wait_for_timeout(180)  # 超过 VELOCITY_WINDOW_MS，算作手停住了
    page.mouse.move(x1, y1)
    page.mouse.up()


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


def test_picker_is_only_on_ipad(browser, server):
    """笔具盘只给 iPad；别的设备（Mac 窗口、安卓平板）一律是普通工具栏，两条之间没有开关。"""
    desktop_ipad_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    )
    pages = []

    def open_as(user_agent, touch, role, legacy_off=False):
        ctx = browser.new_context(
            viewport={"width": 1180, "height": 820}, user_agent=user_agent, has_touch=touch
        )
        page = ctx.new_page()
        if legacy_off:
            # 早先版本里关掉过笔具盘的机器，本机还留着这个记录
            page.add_init_script("try { localStorage.setItem('whiteboard.picker', '0'); } catch (e) {}")
        page.goto(f"http://127.0.0.1:{server.port}/?role={role}")
        page.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
        pages.append(ctx)
        return page

    # iPad：默认就是笔具盘，以前关掉过的记录不再起作用
    for ipad in (open_as(IPAD_UA, True, "ipad"), open_as(IPAD_UA, True, "ipad", legacy_off=True)):
        ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
        assert ipad.is_hidden("#toolbar")
        ipad.click("#pk-host button[data-act='more']")
        assert ipad.query_selector("#pk-host .pk-pop [data-wb='leave']") is None

    # iPadOS 的 Safari 默认报桌面版 UA（Macintosh），靠有触摸点认出来
    desktop_mode = browser.new_context(
        viewport={"width": 1180, "height": 820}, user_agent=desktop_ipad_ua, has_touch=True
    )
    pages.append(desktop_mode)
    page = desktop_mode.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 })")
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    page.wait_for_selector("#pk-host .pk-picker", timeout=20000)

    # 安卓平板和 Mac 窗口：普通工具栏，颜色面板里也没有切换的开关
    for page in (open_as(TABLET_UA, True, "ipad"), open_as(desktop_ipad_ua, False, "mac")):
        page.wait_for_selector("#toolbar", state="visible")
        assert page.evaluate("() => whiteboard.ui.picker") is False
        assert page.query_selector("#pk-host") is None
        page.click('button[title="颜色与粗细"]')
        assert page.locator(".popover input[type=checkbox]").count() == 0

    for ctx in pages:
        ctx.close()


def test_pen_altitude_reads_both_tilt_apis(browser, server):
    """倾斜有两套 API，都要认：tiltX / tiltY 优先，altitudeAngle 兜底。

    只认 altitudeAngle 是不行的——规范规定设备报不出倾斜时它返回 π/2（竖直），
    不支持这个属性的浏览器于是看起来「笔一直立着」，倾斜永远读不出来。
    """
    mac, ipad = open_pages(browser, server.port)
    deg = """(event) => Math.round((whiteboard.penAltitude(event) * 180) / Math.PI)"""

    # Level 2 的 tiltX / tiltY，从竖直方向算起
    assert ipad.evaluate(deg, {"tiltX": 0, "tiltY": 0}) == 90
    assert ipad.evaluate(deg, {"tiltX": 45, "tiltY": 0}) == 45
    assert ipad.evaluate(deg, {"tiltX": 60, "tiltY": 0}) == 30
    assert ipad.evaluate(deg, {"tiltX": 0, "tiltY": 55}) == 35
    assert ipad.evaluate(deg, {"tiltX": -60, "tiltY": 0}) == 30  # 往哪边倒都一样

    # 没有 tiltX / tiltY 时才看 altitudeAngle，从屏幕平面算起
    import math as _math

    assert ipad.evaluate(deg, {"altitudeAngle": _math.pi / 2}) == 90
    assert ipad.evaluate(deg, {"altitudeAngle": _math.pi / 4}) == 45
    assert ipad.evaluate(deg, {"altitudeAngle": 0}) == 0  # 平贴屏幕，不是「没数据」
    assert ipad.evaluate(deg, {}) == 90  # 两套都没有，按竖直算

    # 两套都在时以 tiltX / tiltY 为准
    assert ipad.evaluate(deg, {"tiltX": 60, "tiltY": 0, "altitudeAngle": _math.pi / 2}) == 30
    mac.close()
    ipad.close()


# iPad 原生 PencilKit 像素橡皮的实测印记：(笔身与屏幕的夹角°, 直径)。
#
# 采集方式见 docs/eraser.md：在一大片实心墨迹上点一排孤立的橡皮点，每个点固定
# 一个角度，再把 PKStroke.mask 里对应那个洞的面积换算成等效直径。
# 沿拖动路径量的**垂直宽度**：擦痕在屏幕上有多宽，就是这个数。曲线按它来。
NATIVE_DRAG_WIDTH = [
    (67.8, 16.5), (64.8, 16.5),
    (58.8, 15.5), (57.7, 15.5), (54.4, 16.0), (53.4, 14.5), (52.6, 16.0),
    (50.7, 15.5), (45.3, 16.5), (44.9, 18.0), (44.0, 16.5),
    # 会话 a 的五条，把平台段的下端定在 37°
    (43.0, 15.5), (40.9, 15.5), (39.6, 15.5), (38.9, 16.0), (38.4, 16.0),
    # 会话 c，zoom 0.25：322 个 drawing 单位乘回缩放是 80.5 个屏幕单位
    (10.4, 80.5),
]

# 孤立点的**洞面积换算的等效直径**。和垂直宽度只有在落笔是圆的时候才是同一个数，
# 所以不能直接拿来定曲线——原来就是把两者接在一起，才在 37° 处接出一道悬崖。
NATIVE_DAB_AREA = [
    (83.3, 6.9), (82.0, 6.3), (81.1, 7.2), (80.0, 9.0), (79.1, 8.1),
    (50.3, 16.5), (50.1, 17.7), (49.8, 17.6), (49.7, 18.2), (49.7, 15.9),
    (35.0, 35.2), (34.4, 40.7), (33.7, 45.0), (32.5, 50.5), (31.8, 54.1),
    (28.5, 72.2), (27.3, 78.7),
    (20.3, 80.7), (14.8, 82.1), (12.6, 81.2), (12.1, 80.3),
]


def test_eraser_width_follows_the_native_curve(browser, server):
    """像素橡皮的直径要跟着倾角走，而且要对得上原生实测。

    原来是「20° 以上一律笔尖 6、15° 以下一律 45」，全是拍脑袋定的。实测下来
    原生从 80° 就开始变粗、25° 左右饱和在 81；常握笔大约 50°，那里原生已经是 17，
    而原来的实现还停在 6——细得没法用橡皮写字，这就是那个「擦痕像针」的直接原因。

    两种量法分开判。曲线按**垂直宽度**定，因为擦痕在屏幕上有多宽就是这个数；
    孤立点的**等效直径**只在落笔接近圆形的两端（≥44° 和 ≤25°）和它一致，
    中间那一段等效直径系统性地偏大，把两者接在一起就是 37° 处那道悬崖的由来。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.click('button[title="橡皮擦"]')

    # 橡皮的面板里只有模式二选一，没有粗细那一排
    ipad.click('button[title="颜色与粗细"]')
    assert ipad.locator(".popover .seg button").count() == 2
    assert ipad.locator(".popover .width-opt").count() == 0
    ipad.keyboard.press("Escape")

    radius = """([deg, mode]) => {
      whiteboard.tool.eraserMode = mode;
      return whiteboard.input.eraserRadius({
        pointerType: 'pen', altitudeAngle: (deg * Math.PI) / 180 });
    }"""

    # 对象橡皮擦：立着、压着、贴着都是笔尖，它是整笔删除，作用点本来就只是一个点
    assert [ipad.evaluate(radius, [deg, "object"]) for deg in (88, 60, 45, 35, 15)] == [3] * 5

    # 垂直宽度：曲线就是按它定的，要贴得紧
    worst = 0.0
    for deg, native in NATIVE_DRAG_WIDTH:
        ours = ipad.evaluate(radius, [deg, "pixel"]) * 2
        # 同一角度原生自己就有散布（50° 那五个点是 15.9～18.2），所以按相对误差比
        error = abs(ours - native) / native
        worst = max(worst, error)
        assert error < 0.18, (deg, native, ours)
    assert worst < 0.18

    # 等效直径：两端要对上（那里落笔接近圆形，两种量法是同一个数）
    for deg, native in NATIVE_DAB_AREA:
        if not (deg >= 44 or deg <= 25):
            continue
        ours = ipad.evaluate(radius, [deg, "pixel"]) * 2
        assert abs(ours - native) / native < 0.18, (deg, native, ours)

    # 中间那一段等效直径一律比曲线大：这正是「落笔是拉长的椭圆」的样子，
    # 反过来说，曲线在这里绝不能去追等效直径，否则就把悬崖接回来了
    for deg, native in NATIVE_DAB_AREA:
        if 25 < deg < 44:
            ours = ipad.evaluate(radius, [deg, "pixel"]) * 2
            assert ours < native, (deg, native, ours)

    # 单调：笔越平擦得越宽，中间不许有回头
    widths = [ipad.evaluate(radius, [deg, "pixel"]) for deg in range(85, 5, -5)]
    assert widths == sorted(widths), widths
    assert widths[0] * 2 < 8 and widths[-1] * 2 > 78  # 两端分别贴着笔尖和饱和值

    # 37° 以下这一段没有拖动样本，只在两个信得过的锚点之间连直线，所以这里不拿
    # 具体数值当判据；真正保证手感的是「整笔粗细不变」，见下一条用例。

    # 报不出倾斜的笔和鼠标按常握笔那一档给，不然一直是笔尖等于没法用
    middle = ipad.evaluate(
        "() => { whiteboard.tool.eraserMode = 'pixel';"
        " return whiteboard.input.eraserRadius({ pointerType: 'mouse' }); }"
    )
    assert 8 < middle * 2 < 26
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")
    mac.close()
    ipad.close()


def test_pixel_eraser_severs_without_splitting(browser, server):
    """像素橡皮横穿一条笔画：视觉上断成两截，但仍然是同一条笔画，只是多了遮罩。

    断开是遮罩把它截断的结果，不是另一种模式——原生也是这样，断开之后两段各自
    还带着自己的遮罩。撤销把遮罩收回去。
    """
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(200 + i * 20, 400) for i in range(30)])  # 一条横线
    wait_strokes(mac, 1)
    original = ipad.evaluate("() => whiteboard.state.strokes[0].id")

    ipad.click('button[title="橡皮擦"]')
    ipad.click('button[title="颜色与粗细"]')
    ipad.click(".popover .seg button:nth-child(2)")  # 像素橡皮擦
    ipad.keyboard.press("Escape")
    assert ipad.evaluate("() => whiteboard.tool.eraserMode") == "pixel"

    # 从线的正中间竖着划过去
    draw(ipad, [(400, 380), (400, 400), (400, 420)])

    def chains(op):
        return (
            "([id]) => { const s = whiteboard.state.byId.get(id);"
            " return !!s && (s.m || []).length %s; }" % op
        )

    ipad.wait_for_function(chains("> 0"), arg=[original])
    mac.wait_for_function(chains("> 0"), arg=[original])  # 对端也收到了
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1  # 没被拆成两条

    # 墨迹真的断了：沿中心线采样，橡皮走过的那一小段不许有墨
    gone = ipad.evaluate(
        """() => {
          const s = whiteboard.state.strokes[0];
          const bounds = whiteboard.state.contentBounds();
          const canvas = whiteboard.Renderer.renderToCanvas(whiteboard.state,
            { scale: 1, background: false, bounds });
          const ctx = canvas.getContext('2d');
          const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
          const y = Math.round(s.p[1] - bounds.y0);
          const runs = [];
          let start = null;
          for (let x = 0; x < canvas.width; x++) {
            const on = d[(y * canvas.width + x) * 4 + 3] > 127;
            if (!on && start === null) start = x;
            if (on && start !== null) { runs.push(x - start); start = null; }
          }
          return runs.filter((n) => n > 2);
        }"""
    )
    assert len(gone) == 1, gone  # 中间正好一个缺口

    ipad.click('button[title="撤销"]')
    ipad.wait_for_function(chains("=== 0"), arg=[original])
    mac.wait_for_function(chains("=== 0"), arg=[original])
    assert ipad.evaluate("() => whiteboard.state.strokes[0].id") == original
    mac.close()
    ipad.close()


def test_object_eraser_still_deletes_whole_strokes(browser, server):
    """对象橡皮擦是默认的那一种，碰到哪一笔整笔删掉。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(200 + i * 20, 400) for i in range(30)])
    wait_strokes(mac, 1)
    assert ipad.evaluate("() => whiteboard.tool.eraserMode") == "object"

    ipad.click('button[title="橡皮擦"]')
    draw(ipad, [(400, 380), (400, 400), (400, 420)])
    wait_strokes(ipad, 0)
    wait_strokes(mac, 0)
    mac.close()
    ipad.close()


def test_split_stroke_geometry(browser, server):
    """切笔画本身：没碰到返回 null，整条被盖住返回空，只剩一个点的碎屑不留。

    每一段还带 ``cut``，标出哪一头是切出来的（1 = 起点，2 = 终点），
    渲染时那一头画平口。
    """
    mac, ipad = open_pages(browser, server.port)
    split = """([x0, y0, x1, y1, r]) => {
      const flat = [];
      for (let i = 0; i < 20; i++) flat.push(i * 10, 0, 0.6);
      const stroke = { id: 'a', tool: 'pen', color: '#000000', w: 3, p: flat };
      const runs = whiteboard.splitStroke(stroke, x0, y0, x1, y1, r);
      return runs === null ? null : runs.map((run) => [run.p.length / 3, run.cut]);
    }"""
    assert ipad.evaluate(split, [95, 400, 95, 500, 8]) is None  # 离得远，没碰到
    # 从中间切一刀：两头各留 9 个原采样点，再各加一个落在交点上的新点
    assert ipad.evaluate(split, [95, -5, 95, 5, 8]) == [[10, 2], [10, 1]]
    assert ipad.evaluate(split, [-50, 0, 250, 0, 30]) == []  # 整条都被扫掉
    # 起点那头切掉：留下交点 + 后面 17 个点，起点是切口
    assert ipad.evaluate(split, [8, 0, 8, 0, 14]) == [[18, 1]]
    mac.close()
    ipad.close()


def test_eraser_gap_matches_its_diameter(browser, server):
    """缺口宽度等于橡皮直径，跟采样密度无关。

    以前切口只能落在采样点上，缺口因此跟着采样密度走：同样是直径 16 的橡皮，
    密采样缺口 42、疏采样 84。判定里还额外加了笔画自己的半宽，橡皮只要蹭到外沿
    就把整个截面切断。两条都改了，现在缺口恒等于直径。
    """
    mac, ipad = open_pages(browser, server.port)
    gap = """([spacing, r]) => {
      const flat = [];
      for (let x = 0; x <= 400; x += spacing) flat.push(x, 0, 1);
      const stroke = { id: 'a', tool: 'pen', color: '#000000', w: 16, p: flat };
      const runs = whiteboard.splitStroke(stroke, 200, -60, 200, 60, r);
      const left = runs[0].p;
      return runs[1].p[0] - left[left.length - 3];
    }"""
    for spacing in (4, 14, 28, 60):
        assert abs(ipad.evaluate(gap, [spacing, 8]) - 16) < 0.1, spacing
        assert abs(ipad.evaluate(gap, [spacing, 3]) - 6) < 0.1, spacing

    # 只蹭到外沿：中心线没被盖住，这一笔不该动
    graze = """([offset]) => {
      const flat = [];
      for (let x = 0; x <= 400; x += 10) flat.push(x, 0, 1);
      const stroke = { id: 'a', tool: 'pen', color: '#000000', w: 30, p: flat };
      return whiteboard.splitStroke(stroke, 200, offset, 200, offset, 7);
    }"""
    # 笔半宽 15、橡皮半径 7：圆心在 20 时圆已经压进外沿 2，但离中心线还有 20
    # （老代码比的是 radius + half = 22 ≥ 20，所以这一下会把整条笔画切断）
    assert ipad.evaluate(graze, [20]) is None
    assert ipad.evaluate(graze, [6]) is not None  # 盖住中心线了才切
    mac.close()
    ipad.close()


def test_eraser_size_is_fixed_on_screen(browser, server):
    """橡皮的尺寸恒定在屏幕上：放大等于擦得更细，而不是橡皮跟着变大。

    以前 eraserRadius 的返回值被直接当世界坐标用，放到 8 倍时最粗的 45 在屏幕上
    是 360 px，而且放大完全不提高擦除精度。
    """
    mac, ipad = open_pages(browser, server.port)
    radius = """([scale]) => {
      whiteboard.viewport.scale = scale;
      let seen = null;
      const real = whiteboard.input.hooks.onErase;
      whiteboard.input.hooks.onErase = (x, y, r) => { seen = r; return []; };
      whiteboard.input.erase = { pointerId: 1, ids: [], radius: 12, last: null };
      whiteboard.input.moveErase({ pointerId: 1, pointerType: 'pen',
        clientX: 300, clientY: 300, tiltX: 0, tiltY: 0 });
      whiteboard.input.erase = null;
      whiteboard.input.hooks.onErase = real;
      return [seen, whiteboard.renderer.cursor.r];
    }"""
    at1 = ipad.evaluate(radius, [1])
    at4 = ipad.evaluate(radius, [4])
    # 世界半径随缩放反比变化，屏幕上看到的那个圈大小不变
    assert abs(at4[0] - at1[0] / 4) < 1e-6
    assert at4[1] == at4[0]  # 光标圈和判定用的是同一个值
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; }")
    mac.close()
    ipad.close()


def test_erase_keeps_up_on_a_crowded_board(browser, server):
    """擦除的开销只跟扫过的那一片有关，不跟白板上一共有多少笔有关。

    以前每个指针事件都要把整块白板过一遍（还顺带整个数组重排一次），笔一多就卡。
    现在按空间网格取候选，插入和删除也改成只动该动的那几条。
    """
    mac, ipad = open_pages(browser, server.port)
    bench = """([count]) => {
      const strokes = [];
      for (let i = 0; i < count; i++) {
        const p = [];
        const x0 = 100 + Math.floor(i / 40) * 26;
        const y0 = 100 + (i % 40) * 22;
        for (let j = 0; j < 14; j++) p.push(x0 + j * 1.8, y0 + j * 0.4, 0.6);
        strokes.push({ id: 'b' + i, tool: 'pen', color: '#1b1b1f', w: 3, p, n: i });
      }
      whiteboard.state.reset(whiteboard.state.meta, strokes);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';
      const t0 = performance.now();
      let prev = null;
      for (let i = 0; i < 60; i++) {
        const pt = [120 + i * 7, 300];
        whiteboard.input.hooks.onErase(pt[0], pt[1], 25, prev);
        prev = pt;
        whiteboard.flushErase();
      }
      return performance.now() - t0;
    }"""
    small = ipad.evaluate(bench, [400])
    large = ipad.evaluate(bench, [4000])
    # 笔画数翻十倍，耗时不该跟着翻。留足余量，这是防回归不是跑分
    assert large < small * 4 + 20, (small, large)
    mac.close()
    ipad.close()


def test_erase_only_repaints_what_it_touched(browser, server):
    """擦除不整屏重绘，只重画被动过的那一块——iPad 上卡就卡在这里。

    加笔画可以直接往底图上叠，删笔画不行：墨迹已经合成进去了，只能把那一块
    连背景一起重画。整屏重绘是「和白板上一共有多少笔成正比」，几千笔的板上
    每帧都要十几毫秒，而擦除每一帧都要重来一次。
    """
    mac, ipad = open_pages(browser, server.port)
    for row in range(5):
        draw(ipad, [(200 + i * 18, 250 + row * 60) for i in range(35)])
    wait_strokes(mac, 5)

    result = ipad.evaluate("""() => {
      const r = whiteboard.renderer;
      r.requestFull();
      r.tick();
      let fulls = 0;
      const realFull = r.fullRedraw.bind(r);
      r.fullRedraw = () => { fulls += 1; realFull(); };
      whiteboard.tool.eraserMode = 'pixel';
      const p = whiteboard.state.strokes[2].p;
      const at = Math.floor(p.length / 3 / 2) * 3;
      let prev = null;
      for (let i = 0; i < 6; i++) {
        const pt = [p[at] + i * 3, p[at + 1]];
        whiteboard.input.hooks.onErase(pt[0], pt[1], 12, prev);
        prev = pt;
        whiteboard.flushErase();
        r.tick();                       // 每帧一次
      }
      const dirty = r.dirty;
      r.fullRedraw = realFull;
      return { fulls, dirty, strokes: whiteboard.state.strokes.length,
               masked: whiteboard.state.strokes.filter(x => x.m && x.m.length).length };
    }""")
    assert result["fulls"] == 0  # 一次整屏重绘都没有
    assert result["strokes"] == 5  # 像素橡皮不拆笔画，只记遮罩
    assert result["masked"] > 0  # 确实擦到了
    mac.close()
    ipad.close()


def test_erase_sends_one_batch_per_frame(browser, server):
    """一次拖动里的擦除操作攒起来每帧发一次，不是每个指针事件发一条。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(200 + i * 20, 400) for i in range(30)])
    wait_strokes(mac, 1)

    ops = ipad.evaluate("""() => {
      const sent = [];
      const real = whiteboard.net.send.bind(whiteboard.net);
      whiteboard.net.send = (m) => { if (m.t === 'op') sent.push(m.op.op); real(m); };
      whiteboard.tool.eraserMode = 'pixel';
      // onErase 收的是世界坐标，照着那一笔自己的点走
      const p = whiteboard.state.strokes[0].p;
      let prev = null;
      for (let i = 0; i < 12; i++) {           // 一帧之内连发 12 个事件
        const at = (Math.floor(p.length / 3 / 2) + i) * 3;
        const pt = [p[at], p[at + 1]];
        whiteboard.input.hooks.onErase(pt[0], pt[1], 12, prev);
        prev = pt;
      }
      whiteboard.flushErase();                  // 到帧末才冲出去
      whiteboard.net.send = real;
      return sent;
    }""")
    assert ops == ["mask"]  # 十二个事件合成一条遮罩操作
    mac.close()
    ipad.close()


def test_picker_switches_eraser_mode(browser, server):
    """笔具盘里橡皮那个面板的「对象 / 像素」二选一：选了就生效，记在本机。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker")
    ipad.click('#pk-host [data-tool="eraser"]')
    ipad.wait_for_function("() => whiteboard.tool.tool === 'eraser'")
    ipad.wait_for_timeout(500)  # vendor 会吞掉拖动结束后一小段时间内的点击
    ipad.click('#pk-host [data-tool="eraser"]')  # 再点一次弹出面板
    ipad.wait_for_selector('#pk-host [data-emode="pixel"]')
    ipad.click('#pk-host [data-emode="pixel"]')
    ipad.wait_for_function("() => whiteboard.tool.eraserMode === 'pixel'")

    # 重新打开页面，选择还在
    ipad.reload()
    ipad.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    assert ipad.evaluate("() => whiteboard.tool.eraserMode") == "pixel"
    mac.close()
    ipad.close()


def test_classic_toolbar_still_shares_one_colour(browser, server):
    """没开笔具盘时保持老行为：改颜色是所有笔一起改。"""
    _mac, ipad = open_pages(browser, server.port)
    ipad.click('button[title="颜色与粗细"]')
    ipad.click(".popover .swatch:nth-child(4)")
    picked = ipad.evaluate("() => whiteboard.tool.color")
    ipad.click('button[title="马克笔"]')
    assert ipad.evaluate("() => whiteboard.tool.color") == picked
    _mac.close()
    ipad.close()


def enable_pk_picker(page):
    """等 iPad 上的笔具盘装好（页面要用 open_pages(..., picker=True) 打开）。"""
    page.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    page.wait_for_selector('#pk-host [data-tool="pen"]')


def pk_state(page):
    return page.evaluate("() => whiteboard.tool")


def test_pencilkit_picker_drives_the_board(browser, server):
    """笔具盘（beta）：选工具、选颜色、改粗细都落到白板上，画布照常能写。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)

    assert ipad.is_hidden("#toolbar")  # 原来那条收起来了
    assert ipad.query_selector('#pk-host [data-tool="lasso"]') is None  # 套索直尺去掉了
    assert ipad.query_selector('#pk-host [data-tool="ruler"]') is None

    # 画布在工具盘下面，笔迹照常同步
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    wait_strokes(mac, 1)

    # 换成荧光笔（用的是那份实现里的「马克笔」造型）
    ipad.click('#pk-host [data-tool="marker"]')
    assert pk_state(ipad)["tool"] == "highlighter"

    # 选一个预置色
    ipad.click('#pk-host .pk-sw[data-color="#157efa"]')
    assert pk_state(ipad)["color"] == "#157efa"

    # 点已经选中的工具 → 粗细面板；换一档粗细
    ipad.click('#pk-host [data-tool="marker"]')
    ipad.wait_for_selector("#pk-host .pk-pop[data-open] .pk-tips")
    ipad.click("#pk-host .pk-tips button[data-size='4']")
    assert pk_state(ipad)["width"] > 8

    # 每支笔各记各的：换回钢笔，颜色不是刚才那支的
    ipad.click('#pk-host [data-tool="pen"]')
    assert pk_state(ipad)["tool"] == "pen"
    assert pk_state(ipad)["color"] != "#157efa"
    mac.close()
    ipad.close()


def test_pencilkit_picker_undo_and_clear(browser, server):
    """撤销按钮跟着白板的撤销栈亮灭，清屏在更多菜单里。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    undo = "#pk-host button[data-act='undo']"
    assert ipad.is_disabled(undo)

    draw(ipad, [(320, 320), (400, 360), (480, 320)])
    wait_strokes(mac, 1)
    ipad.wait_for_function(f"() => !document.querySelector(\"{undo}\").disabled")
    ipad.click(undo)
    wait_strokes(mac, 0)

    # 更多菜单里的清屏
    draw(ipad, [(320, 420), (400, 460)])
    wait_strokes(mac, 1)
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-wb='clear']")
    ipad.click('.dialog button[title="确定"]')
    wait_strokes(mac, 0)

    # 触摸设备上这条笔具盘就是唯一的工具栏，更多菜单里没有换回普通工具栏那一行
    ipad.click("#pk-host button[data-act='more']")
    assert ipad.query_selector("#pk-host .pk-pop [data-wb='leave']") is None
    mac.close()
    ipad.close()


def test_pencilkit_picker_docks_and_minimizes(browser, server):
    """拖握把换边，丢进角落缩成圆，点圆展开；位置本身由那份实现自己管。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)

    def grip():
        # 贴边是带动画的：一直量到位置不再变，否则抓到的是半路上的坐标
        read = (
            "() => { const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
            " return [g.left + g.width / 2, g.top + g.height / 2]; }"
        )
        last = None
        for _ in range(30):
            now = ipad.evaluate(read)
            if last == now:
                return now
            last = now
            ipad.wait_for_timeout(100)
        return last

    def state():
        return ipad.evaluate("() => document.querySelector('#pk-host .pk-picker').dataset.state")

    def dock():
        return ipad.evaluate("() => document.querySelector('#pk-host .pk-picker').dataset.dock")

    assert state() == "docked" and dock() == "bottom"
    x, y = grip()
    drag(ipad, x, y, 60, 400)
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').dataset.dock === 'left'"
    )

    x, y = grip()
    drag(ipad, x, y, 1130, 770)
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').dataset.state === 'minimized'"
    )
    # 用手指点那个圆（鼠标会先悬停，一靠近就自己展开了，见下一个用例）。
    # 先等：收起来是带动画的，而且它会吞掉拖动结束后 400ms 内的点击。
    ipad.wait_for_timeout(600)
    spot = ipad.evaluate(
        "() => { const r = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2]; }"
    )
    ipad.touchscreen.tap(spot[0], spot[1])
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').dataset.state === 'docked'"
    )
    _mac.close()
    ipad.close()


def test_undo_and_redo_round_trip(browser, server):
    """撤销之后能重做回来，两端都跟着变；新动作会把重做那一支作废。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    draw(ipad, [(300, 420), (380, 460), (460, 420)])
    wait_strokes(mac, 2)

    redo = 'button[title="重做"]'
    assert ipad.is_disabled(redo)
    ipad.click('button[title="撤销"]')
    wait_strokes(mac, 1)
    wait_strokes(ipad, 1)
    assert not ipad.is_disabled(redo)

    ipad.click(redo)
    wait_strokes(mac, 2)  # 笔画回来了，对端也收到了
    wait_strokes(ipad, 2)
    assert ipad.is_disabled(redo)

    # 擦除也能重做
    ipad.click('button[title="橡皮擦"]')
    spot = ipad.evaluate(
        "() => { const s = whiteboard.state.strokes[0];"
        " return whiteboard.viewport.toScreen(s.p[0], s.p[1]); }"
    )
    draw(ipad, [(spot[0], spot[1]), (spot[0] + 6, spot[1] + 4)])
    wait_strokes(mac, 1)
    ipad.click('button[title="撤销"]')
    wait_strokes(mac, 2)
    ipad.click(redo)
    wait_strokes(mac, 1)

    # 再画一笔，重做就没得可重做了
    ipad.click('button[title="钢笔"]')
    draw(ipad, [(600, 300), (660, 340)])
    wait_strokes(mac, 2)
    assert ipad.is_disabled(redo)
    mac.close()
    ipad.close()


def test_keyboard_redo(browser, server):
    mac, _ipad = open_pages(browser, server.port)
    draw(mac, [(300, 300), (380, 340)], pointer_type="mouse")
    wait_strokes(mac, 1)
    mac.keyboard.press("Control+z")
    wait_strokes(mac, 0)
    mac.keyboard.press("Control+Shift+z")
    wait_strokes(mac, 1)
    mac.close()
    _ipad.close()


def test_picker_docks_to_the_top(browser, server):
    """原实现只有下 / 左 / 右，顶部停靠是加的：笔要转过来，面板要往下开。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)

    def settle():
        last = None
        for _ in range(30):
            now = ipad.evaluate(
                "() => { const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
                " return [g.left + g.width / 2, g.top + g.height / 2]; }"
            )
            if last == now:
                return now
            last = now
            ipad.wait_for_timeout(100)
        return last

    x, y = settle()
    drag(ipad, x, y, 590, 24)
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').dataset.dock === 'top'"
    )
    # 贴边有动画，等它停下来再量
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top < 80",
        timeout=4000,
    )
    bar = ipad.evaluate("() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top")

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
    box = ipad.evaluate(
        "() => { const p = document.querySelector('#pk-host .pk-pop');"
        " const r = p.getBoundingClientRect(); return [r.top, p.dataset.side]; }"
    )
    assert box[1] == "bottom" and box[0] > bar
    _mac.close()
    ipad.close()


def pk_form(page):
    return page.evaluate("() => [whiteboard.ui.pk.state, whiteboard.ui.pk.dock]")


def test_picker_expands_while_dragging_to_an_edge(browser, server):
    """在触发区里停够 DOCK_DWELL_MS 就当场展开成那条边的样子，不用等松手；出了触发区立刻变回圆。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_timeout(500)

    grip = ipad.evaluate(
        "() => { const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
        " return [g.left + g.width / 2, g.top + g.height / 2]; }"
    )
    ipad.mouse.move(grip[0], grip[1])
    ipad.mouse.down()
    ipad.mouse.move(560, 410)  # 先拖到中间：不在任何触发区，立刻变成圆
    assert pk_form(ipad)[0] == "moving"

    ipad.mouse.move(560, 120)  # 进顶部触发区，但还没停够
    assert pk_form(ipad)[0] == "moving"
    ipad.wait_for_function("() => whiteboard.ui.pk.state === 'docked'", timeout=2000)
    assert pk_form(ipad) == ["docked", "top"]

    # 但它只是展开，没有自己跑到边上去：整条栏还跟在手底下（形态变化是带动画的，等它铺开）
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().width > 400",
        timeout=2000,
    )
    top = ipad.evaluate(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top"
    )
    assert top > 40  # 还没贴到顶（贴上去是 20）

    ipad.mouse.move(60, 410)  # 换到左边触发区，重新计时
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'left'", timeout=2000)

    ipad.mouse.move(590, 410)  # 回到中间：出了触发区立刻缩成圆，不等
    assert ipad.evaluate("() => whiteboard.ui.pk.state") == "moving"
    ipad.mouse.move(1130, 780)  # 角落：松手收起来
    ipad.wait_for_timeout(180)
    ipad.mouse.up()
    ipad.wait_for_function("() => whiteboard.ui.pk.state === 'minimized'", timeout=4000)
    _mac.close()
    ipad.close()


def test_picker_takes_a_tap_right_after_a_drag(browser, server):
    """拖完立刻点就得生效（vendor 是松手后 400ms 内一律吞掉），但轻轻一蹭不能误选工具。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_timeout(500)

    def grip():
        return ipad.evaluate(
            "() => { const r = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
            " return [r.left + r.width / 2, r.top + r.height / 2]; }"
        )

    # 在底边触发区里原地小幅拖一下，长条贴回原位；松手后马上点马克笔（白板里是荧光笔）
    x, y = grip()
    ipad.mouse.move(x, y)
    ipad.mouse.down()
    for step in range(1, 7):
        ipad.mouse.move(x, y + step * 4)
    ipad.mouse.up()
    box = ipad.evaluate(
        '() => { const r = document.querySelector(`#pk-host [data-tool="marker"]`).getBoundingClientRect();'
        " return [r.left + r.width / 2, r.top + r.height / 2]; }"
    )
    ipad.touchscreen.tap(*box)
    ipad.wait_for_function("() => whiteboard.tool.tool === 'highlighter'", timeout=2000)

    # 但松手当下那一下要挡住：长条以手指为中心跟手，手指正压在橡皮那一格上
    ipad.evaluate("() => whiteboard.ui.pk.applyState({tool: 'pen', color: '#000000', sizeIndex: 1})")
    x, y = grip()
    ipad.mouse.move(x, y)
    ipad.mouse.down()
    for dy in (8, 16):
        ipad.mouse.move(x, y - dy)
        ipad.wait_for_timeout(30)
    ipad.mouse.up()
    ipad.wait_for_timeout(300)
    assert ipad.evaluate("() => whiteboard.tool.tool") == "pen"

    # 圆也一样：蹭一下不算点一下，不该展开
    ipad.evaluate("() => { const pk = whiteboard.ui.pk; pk.minCorner = 'br';"
                  " pk._setState('minimized'); pk._apply(pk._geom()); }")
    ipad.wait_for_timeout(900)
    spot = ipad.evaluate(
        "() => { const r = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2]; }"
    )
    ipad.mouse.move(*spot)
    ipad.mouse.down()
    for dx in (8, 16):
        ipad.mouse.move(spot[0] - dx, spot[1])
        ipad.wait_for_timeout(30)
    ipad.mouse.up()
    ipad.wait_for_timeout(300)
    assert ipad.evaluate("() => whiteboard.ui.pk.state") == "minimized"
    _mac.close()
    ipad.close()


def test_picker_fling_carries_past_the_release_point(browser, server):
    """甩出去有惯性：同一个松手点，慢放落到最近的底边，甩出去要按推算的停点落到顶边。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_timeout(500)

    def grip():
        last = None
        for _ in range(30):
            now = ipad.evaluate(
                "() => { const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
                " return [g.left + g.width / 2, g.top + g.height / 2]; }"
            )
            if last == now:
                return now
            last = now
            ipad.wait_for_timeout(100)
        return last

    # 慢放：松手点略偏下半屏，落到底边
    x, y = grip()
    drag(ipad, x, y, 590, 420)
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'bottom'", timeout=4000)

    # 同一个松手点，一路向上甩：惯性把停点推过顶部触发区
    x, y = grip()
    fling(ipad, x, y, 590, 420)
    ipad.wait_for_function("() => whiteboard.ui.pk.dock === 'top'", timeout=4000)
    _mac.close()
    ipad.close()


def test_picker_only_snaps_to_the_edge_after_release(browser, server):
    """松手才真的贴到边上，拖的过程里它一直跟着手。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.wait_for_timeout(500)
    grip = ipad.evaluate(
        "() => { const g = document.querySelector('#pk-host .pk-grip').getBoundingClientRect();"
        " return [g.left + g.width / 2, g.top + g.height / 2]; }"
    )
    ipad.mouse.move(grip[0], grip[1])
    ipad.mouse.down()
    for spot in [(560, 410), (500, 200), (460, 150)]:
        ipad.mouse.move(*spot)
        ipad.wait_for_timeout(60)
    before = ipad.evaluate(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top"
    )
    ipad.mouse.up()
    ipad.wait_for_function(
        "() => document.querySelector('#pk-host .pk-picker').getBoundingClientRect().top < 40",
        timeout=4000,
    )
    assert before > 60  # 松手之前没贴上去
    _mac.close()
    ipad.close()


def test_picker_expands_when_the_pointer_comes_close(browser, server):
    """收进角落之后，指针靠近就展开，不用非得点中那个圆。"""
    _mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    ipad.evaluate("() => { const pk = whiteboard.ui.pk; pk.minCorner = 'br';"
                  " pk._setState('minimized'); pk._apply(pk._geom()); }")
    ipad.wait_for_function("() => whiteboard.ui.pk.state === 'minimized'")

    ipad.mouse.move(600, 400)  # 离得远，不动
    ipad.wait_for_timeout(120)
    assert ipad.evaluate("() => whiteboard.ui.pk.state") == "minimized"

    # 收进角落是带动画的，等它停稳再量，否则量到的是半路上的坐标
    read = (
        "() => { const r = document.querySelector('#pk-host .pk-picker').getBoundingClientRect();"
        " return [r.left, r.top]; }"
    )
    bubble = last = None
    for _ in range(30):
        bubble = ipad.evaluate(read)
        if bubble == last:
            break
        last = bubble
        ipad.wait_for_timeout(100)

    ipad.mouse.move(bubble[0] - 40, bubble[1] - 30)
    ipad.wait_for_function("() => whiteboard.ui.pk.state === 'docked'", timeout=4000)
    _mac.close()
    ipad.close()


def test_status_dot_uses_traffic_light_colours(browser, server):
    mac, _ipad = open_pages(browser, server.port)
    # 颜色是渐变过去的，等它停下来再读
    mac.wait_for_function(
        "() => getComputedStyle(document.getElementById('status'), '::after')"
        ".backgroundColor === 'rgb(40, 200, 64)'",  # macOS 红绿灯的绿
        timeout=4000,
    )
    mac.close()
    _ipad.close()


def test_the_settled_tail_does_not_leave_a_knob(browser, server):
    """笔停住之后的那几个采样点不能留在笔画里，否则末端会鼓一个球。

    抬笔之前笔通常已经停住了，事件却还在来：坐标在相邻整数之间游走，真机录像里
    一笔末尾常有四五个点挤在一两个世界单位之内，而且相对笔画走向偏出去一点。
    轮廓那边会在最后一个点上扣一个整圆的笔帽，于是那一撮点把笔帽顶到轴线外面。

    判据是末端那个笔帽的圆心离笔画轴线多远：把最后一段真实走向延长出去，看帽心
    偏出去多少。偏出去超过半个笔半宽就是肉眼能看见的球。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate("""() => {
      const stage = document.getElementById('stage');
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 13 };
      const fire = (type, x, y, pressure) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 4, pressure,
        tiltX: 40, tiltY: 0, buttons: type === 'pointerup' ? 0 : 1,
        bubbles: true, cancelable: true, isPrimary: true }));
      // 一条直线，最后减速停住，再在原地抖四下（真机录像里就是这样）
      fire('pointerdown', 100, 300, 0.05);
      let x = 100;
      for (const step of [12, 12, 12, 12, 12, 8, 5, 3, 2]) {
        x += step; fire('pointermove', x, 300, 0.05);
      }
      for (const [dx, dy] of [[1,-1],[0,-1],[1,0],[0,-1]]) {
        x += dx; fire('pointermove', x, 300 + dy, 0.05);
      }
      fire('pointerup', x, 299, 0.05);
      const s = whiteboard.state.strokes[0];
      const p = s.p, n = p.length / 3;
      // 笔画主体是一条直线，用前两个点定出轴线（世界坐标），再看末点离它多远
      const x0 = p[0], y0 = p[1], x1 = p[3], y1 = p[4];
      const dx = x1 - x0, dy = y1 - y0, len = Math.hypot(dx, dy);
      const ex = p[(n-1)*3], ey = p[(n-1)*3+1];
      const off = Math.abs((ex - x0) * dy - (ey - y0) * dx) / len;
      return { 偏离: +off.toFixed(2), 点数: n };
    }""")
    # 笔画主体是直的，末端帽心不该被抖动顶到半个笔半宽（3.25）以外
    assert out["偏离"] <= 3.25, out
    assert out["点数"] >= 6, out
    mac.close()


def test_the_start_of_a_stroke_is_not_swallowed(browser, server):
    """起笔那一小截必须立刻出墨，不能等笔走够一个笔宽。

    perfect-freehand 的 getStroke 把同一个 size 同时当成「起笔处先丢掉多长一段」
    和「笔有多粗」。那一段是用来挡落笔抖动的，按笔宽算就太大了：13 宽的笔要走满
    13 个单位才开始留下点，写小字时每个笔画的头都被吞掉一截，手上的感觉是笔动了
    墨没跟上。所以 getStrokePoints 和 getStrokeOutlinePoints 分开调，前者传一个
    小得多的阈值。

    判据：一条总长 24 个单位的小笔画（写小字时一笔就这么长），墨迹必须从起点附近
    就开始，不能从六个单位之后才开始。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate("""() => {
      const p = [];
      for (let i = 0; i < 13; i++) p.push(i * 2, 0, 0.25);   // 总长 24
      const pts = whiteboard.strokeOutline(
        { id: 'x', tool: 'pen', color: '#000', w: 13, p });
      let x0 = Infinity;
      for (const q of pts) x0 = Math.min(x0, q[0]);
      return { 最左: x0, 轮廓点: pts.length };
    }""")
    # 起点在 x=0，笔半径约 3，圆头会往左伸出一点，所以最左应当是负的
    assert out["最左"] < 0.5, out
    assert out["轮廓点"] > 20, out
    mac.close()


def test_python_outline_matches_perfect_freehand(browser, server):
    """Python 那份 perfect-freehand 移植必须和原版给出一模一样的点。

    屏幕用的是原版（``static/js/vendor/perfect-freehand.js``），导出用的是
    ``whiteboard/freehand.py``——因为导出在服务端跑，碰不到浏览器。两份实现一旦
    漂开，导出的 PDF 和屏幕就不是一个形状，而这种偏差只有把 PDF 打开才看得见。

    所以这里不是「差不多就行」：拿真机录的两条笔画，逐点比对，容差 1e-9。升级
    vendor 里那个文件之后第一个要跑的就是这条。
    """
    mac, _ = open_pages(browser, server.port)
    cases = [
        ("真机-快笔", [list(p) for p in REAL_FAST_STROKE], 13.0, 0),
        ("真机-回钩", [list(p) for p in REAL_HOOK_STROKE], 13.0, 0),
        ("切口两头", [[0.0, 0.0, 1.0], [40.0, 0.0, 1.0], [80.0, 0.0, 1.0]], 16.0, 3),
        ("单点", [[5.0, 5.0, 0.4]], 9.0, 0),
    ]
    for name, pts, width, cut in cases:
        flat = [v for p in pts for v in p]
        js = mac.evaluate(
            """([flat, width, cut]) =>
                whiteboard.strokeOutline(
                    { id: 'x', tool: 'pen', color: '#000', w: width, cut, p: flat })""",
            [flat, width, cut],
        )
        # 压感通道和 stroke.js 的 outlinePressure 一样：半径除以笔宽
        k = inkpdf.INK_SCALE
        raw = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]
        py = [
            (x / k, y / k)
            for x, y in inkpdf.freehand.get_stroke(
                [(x * k, y * k, inkpdf.radius("pen", width, pr) / max(width, 1e-6))
                 for x, y, pr in raw],
                size=max(width, 0.6) * k,
                thinning=1.0,
                smoothing=inkpdf.OUTLINE_SMOOTHING,
                streamline=inkpdf.OUTLINE_STREAMLINE,
                cap_start=not cut & 1,
                cap_end=not cut & 2,
                last=True,
                start_noise=inkpdf.START_NOISE * k,
            )
        ]
        assert len(js) == len(py), (name, len(js), len(py))
        for i, (a, b) in enumerate(zip(js, py)):
            assert a[0] == pytest.approx(b[0], abs=1e-9), (name, i, a, b)
            assert a[1] == pytest.approx(b[1], abs=1e-9), (name, i, a, b)
    mac.close()


def test_screen_and_pdf_outlines_agree(browser, server):
    """屏幕的 buildPath 和导出的 outline_path 必须画出同一个形状。

    两边是两份独立实现（一份 JS 一份 Python），任何一边动了几何都可能悄悄跑偏，
    而跑偏只会在导出的 PDF 里看出来。这里让 Python 算出导出用的路径指令，
    交给浏览器用同样的光栅化画一遍，和 buildPath 的结果逐像素比。
    """
    mac, ipad = open_pages(browser, server.port)

    # 一笔里同时有平滑段、急弯和两个折角，压感也在变
    points = []
    for i in range(60):
        t = i / 59
        points.append((60 + t * 300, 200 + math.sin(t * 4) * 60, 0.4 + 0.5 * t))
    points += [(360, 260, 0.9), (250, 90, 0.9), (330, 250, 0.9)]

    compare = """([cmds, flat, width, cut]) => {
      const W = 460, H = 340, S = 2;
      const draw = (build) => {
        const c = document.createElement('canvas');
        c.width = W * S; c.height = H * S;
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.setTransform(S, 0, 0, S, 0, 0);
        ctx.fillStyle = '#000';
        ctx.fill(build(), 'nonzero');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        const m = new Uint8Array(d.length / 4);
        for (let i = 0; i < m.length; i++) m[i] = d[i * 4 + 3] > 127 ? 1 : 0;
        return m;
      };
      const screen = draw(() => whiteboard.buildPath(
        { id: 'x', tool: 'pen', color: '#000', w: width, cut, p: flat }));
      const pdf = draw(() => {
        const path = new Path2D();
        for (const c of cmds) {
          if (c[0] === 'm') path.moveTo(c[1], c[2]);
          else if (c[0] === 'l') path.lineTo(c[1], c[2]);
          else path.bezierCurveTo(c[1], c[2], c[3], c[4], c[5], c[6]);
        }
        path.closePath();
        return path;
      });
      let diff = 0, area = 0;
      for (let i = 0; i < screen.length; i++) {
        if (screen[i] !== pdf[i]) diff++;
        if (screen[i]) area++;
      }
      return [100 * diff / Math.max(1, area), area];
    }"""

    # 第二组是真机录的一笔，采样点隔得很开，补曲线那一步会真的细分——两边的
    # 细分必须一模一样，差一刀轮廓就对不上
    fast = [(x / 8 + 40, y / 8 + 40, pr) for x, y, pr in REAL_FAST_STROKE]

    for label, pts in (("合成", points), ("真机", fast)):
        for width, cut in ((4.0, 0), (20.0, 0), (20.0, 3)):
            cmds = [list(c) for c in inkpdf.outline_path(pts, "pen", width, cut)]
            flat = [v for point in pts for v in point]
            pct, area = ipad.evaluate(compare, [cmds, flat, width, cut])
            assert area > 5000, (label, width, cut, area)  # 真的画上去了
            # 只剩抗锯齿边缘的差别：两条轮廓之间差一个像素都会让这个数大起来
            assert pct < 1.2, (label, width, cut, pct)

    mac.close()
    ipad.close()


def test_object_eraser_deletes_only_the_piece_you_touch(browser, server):
    """像素橡皮把一笔断成两截之后，对象橡皮点哪一截只删哪一截。

    像素橡皮不拆笔画，只给笔画挂一条遮罩（见 docs/format.md）。所以看上去断成
    两截的笔画其实还是一条，两截共用一个 id——对象橡皮点任意一截都会把整条删掉。

    现在对象橡皮碰到带遮罩的笔画时先按缺口切成独立的几条，再只删碰到的那一条。
    """
    mac, ipad = open_pages(browser, server.port)
    out = ipad.evaluate("""() => {
      const w = window.whiteboard;
      const p = [];
      for (let x = 0; x <= 400; x += 10) p.push(x, 0, 1);
      w.state.reset(w.state.meta, [{ id: 'long', tool: 'pen', color: '#1b1b1f', w: 6, p, n: 0 }]);
      w.net.send = () => {};
      // 像素橡皮在正中间横着擦一刀，把它断成两截
      w.tool.eraserMode = 'pixel';
      w.input.hooks.onErase(200, 0, 24, [200, -40]);
      w.input.hooks.onErase(200, 0, 24, [200, 40]);
      w.flushErase();
      const afterCut = w.state.strokes.map((s) => ({ id: s.id, 有遮罩: !!(s.m && s.m.length) }));
      // 换对象橡皮，点左边那一截
      w.tool.eraserMode = 'object';
      w.input.hooks.onErase(60, 0, 6, [60, 0]);
      w.input.hooks.onEraseEnd();
      const left = w.state.strokes.filter((s) => {
        let x0 = Infinity; for (let i = 0; i < s.p.length; i += 3) x0 = Math.min(x0, s.p[i]);
        return x0 < 100;
      });
      const right = w.state.strokes.filter((s) => {
        let x1 = -Infinity; for (let i = 0; i < s.p.length; i += 3) x1 = Math.max(x1, s.p[i]);
        return x1 > 300;
      });
      return { 断开后: afterCut, 剩下: w.state.strokes.length, 左边还在: left.length, 右边还在: right.length };
    }""")
    # 像素橡皮擦完仍然是一条（挂着遮罩），这是设计如此
    assert len(out["断开后"]) == 1, out
    assert out["断开后"][0]["有遮罩"], out
    # 对象橡皮点左边：左边没了，右边还在
    assert out["左边还在"] == 0, out
    assert out["右边还在"] >= 1, out
    mac.close()
    ipad.close()


def test_undo_after_erasing_one_piece_brings_the_whole_stroke_back(browser, server):
    """点掉一截之后撤销，要把原来那一条整个换回来，不是只回来一截。

    对象橡皮删这一截之前先做了切分，撤销记录记的是「原来那一条换成这几截」加上
    「这一截被删了」两件事，合成一条：换回原来那一条，把切出来的段全部清掉。
    """
    mac, ipad = open_pages(browser, server.port)
    out = ipad.evaluate("""() => {
      const w = window.whiteboard;
      const p = [];
      for (let x = 0; x <= 400; x += 10) p.push(x, 0, 1);
      w.state.reset(w.state.meta, [{ id: 'long', tool: 'pen', color: '#1b1b1f', w: 6, p, n: 0 }]);
      w.net.send = () => {};
      w.tool.eraserMode = 'pixel';
      w.input.hooks.onErase(200, 0, 24, [200, -40]);
      w.input.hooks.onErase(200, 0, 24, [200, 40]);
      w.flushErase();
      w.input.hooks.onEraseEnd();
      w.tool.eraserMode = 'object';
      w.input.hooks.onErase(60, 0, 6, [60, 0]);
      w.input.hooks.onEraseEnd();
      const afterErase = w.state.strokes.length;
      w.undo();
      const back = w.state.strokes;
      let x0 = Infinity, x1 = -Infinity;
      for (const s of back) for (let i = 0; i < s.p.length; i += 3) {
        x0 = Math.min(x0, s.p[i]); x1 = Math.max(x1, s.p[i]);
      }
      return { 删后: afterErase, 撤销后: back.length, 跨度: [x0, x1] };
    }""")
    assert out["撤销后"] == 1, out
    # 换回来的是完整的那一条，从 0 铺到 400
    assert out["跨度"][0] <= 1 and out["跨度"][1] >= 399, out
    mac.close()
    ipad.close()


def test_pixel_eraser_always_masks(browser, server):
    """像素橡皮只有一种处理方式：记遮罩，不拆笔画。

    曾经按「橡皮半径是否不小于笔画半宽」分成切断和啃两种走法。压感沿笔画变化、
    局部半宽跟着变，同一次拖动走到一半判定就会跨过阈值，前半截被切出平口断面、
    后半截变成啃，来回跳。原生也是只记遮罩。
    """
    mac, ipad = open_pages(browser, server.port)
    setup = """([half, radius, offset]) => {
      const p = [];
      for (let x = 0; x <= 400; x += 10) p.push(x, 0, 1);
      const stroke = { id: 'wide', tool: 'pen', color: '#1b1b1f', w: half * 2, p, n: 0 };
      whiteboard.state.reset(whiteboard.state.meta, [stroke]);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';
      whiteboard.input.hooks.onErase(320, offset, radius, [80, offset]);
      whiteboard.flushErase();
      const now = whiteboard.state.strokes;
      return [now.length, now[0] && now[0].id, now[0] && (now[0].m || []).length];
    }"""
    # 笔半宽 15、橡皮半径 5：切不断，只能沿上沿啃一道 —— 笔画还是那一条，多了遮罩
    count, ident, chains = ipad.evaluate(setup, [15, 5, -12])
    assert [count, ident, chains] == [1, "wide", 1]

    # 同一条笔画上再啃一道，接成同一条链而不是两条
    again = ipad.evaluate(
        "() => { whiteboard.input.hooks.onErase(360, -12, 5, [320, -12]);"
        " whiteboard.flushErase();"
        " return whiteboard.state.strokes[0].m.map((c) => (c.length - 1) / 2); }"
    )
    assert again == [3]  # 一条链、三个点，不是两条链

    # 橡皮和笔一样粗也还是遮罩，不会把笔画拆成两条——拆不拆由遮罩的形状决定，
    # 不是另一种模式。按半宽分流会让同一次拖动走到一半换判定，前半截切出平口
    # 断面、后半截变成啃。
    count, ident, chains = ipad.evaluate(setup, [5, 6, 0])
    assert [count, ident] == [1, "wide"] and chains == 1
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")
    mac.close()
    ipad.close()


def test_bite_syncs_and_undoes(browser, server):
    """啃出来的遮罩要同步到对端，撤销要把它收回去。"""
    mac, ipad = open_pages(browser, server.port)
    # 马克笔画一条粗的：笔尖那么大的橡皮切不断它，只能啃
    ipad.evaluate(
        "() => { whiteboard.tool.tool = 'marker'; whiteboard.tool.width = 12; }"
    )
    draw(ipad, [(200 + i * 20, 400) for i in range(20)], pressure=1.0)
    wait_strokes(mac, 1)
    ident = ipad.evaluate("() => whiteboard.state.strokes[0].id")
    half = ipad.evaluate("() => whiteboard.state.strokes[0].w / 2")
    assert half > 8, half  # 确实比橡皮尖（半径 3）粗得多

    ipad.click('button[title="橡皮擦"]')
    ipad.click('button[title="颜色与粗细"]')
    ipad.click(".popover .seg button:nth-child(2)")
    ipad.keyboard.press("Escape")
    # 沿上沿削一道：贴着笔画边缘走，够不到中心线
    edge = 400 - int(half) + 2
    draw(ipad, [(300, edge), (400, edge), (500, edge)])

    def chains(op):
        return (
            "([id]) => { const s = whiteboard.state.byId.get(id);"
            " return !!s && (s.m || []).length %s; }" % op
        )

    ipad.wait_for_function(chains("> 0"), arg=[ident])
    mac.wait_for_function(chains("> 0"), arg=[ident])  # 对端也收到了
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1  # 没被切断

    ipad.click('button[title="撤销"]')
    ipad.wait_for_function(chains("=== 0"), arg=[ident])
    mac.wait_for_function(chains("=== 0"), arg=[ident])
    ipad.click('button[title="重做"]')
    ipad.wait_for_function(chains("> 0"), arg=[ident])
    mac.close()
    ipad.close()


def test_mask_does_not_grow_without_bound(browser, server):
    """遮罩不能无限堆：裁剪开销和胶囊数成正比，来回涂同一块地方是堆积的主要来源。

    新胶囊整个盖住的旧胶囊直接丢掉；攒过上限就看看是不是已经啃断了，断了就
    落实成独立笔画、把用掉的胶囊丢掉。
    """
    mac, ipad = open_pages(browser, server.port)
    scrub = """([passes]) => {
      const p = [];
      for (let x = 0; x <= 600; x += 10) p.push(x, 0, 1);
      whiteboard.state.reset(whiteboard.state.meta,
        [{ id: 'wide', tool: 'pen', color: '#1b1b1f', w: 40, p, n: 0 }]);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';
      for (let i = 0; i < passes; i++) {
        const y = -16;
        whiteboard.input.hooks.onErase(560, y, 6, [40, y]);   // 来回涂同一条
        whiteboard.input.hooks.onErase(40, y, 6, [560, y]);
      }
      whiteboard.flushErase();
      const s = whiteboard.state.byId.get('wide');
      return s ? whiteboard.maskSize(s) : 0;
    }"""
    assert ipad.evaluate(scrub, [1]) <= 4
    # 涂五十个来回，胶囊数不该跟着涨五十倍
    assert ipad.evaluate(scrub, [50]) <= 8

    # 擦够 MASK_LIMIT 次不同的地方，遮罩落实成切分、清空
    baked = """([passes]) => {
      const p = [];
      for (let x = 0; x <= 12000; x += 10) p.push(x, 0, 1);
      whiteboard.state.reset(whiteboard.state.meta,
        [{ id: 'wide', tool: 'pen', color: '#1b1b1f', w: 40, p, n: 0 }]);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';
      for (let i = 0; i < passes; i++) {
        const x = 20 + i * 20;
        whiteboard.input.hooks.onErase(x, 16, 6, [x, -16]);  // 每次擦一个新地方
      }
      whiteboard.flushErase();
      const strokes = whiteboard.state.strokes;
      return [strokes.length, Math.max(...strokes.map((s) => whiteboard.maskSize(s)))];
    }"""
    count, biggest = ipad.evaluate(baked, [60])
    assert count == 1 and biggest == 60  # 没到上限，还是一条笔画挂着遮罩
    count, biggest = ipad.evaluate(baked, [500])
    assert count > 1  # 过了上限，落实成了好几条
    assert biggest <= 400
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")
    mac.close()
    ipad.close()


def test_object_eraser_ignores_bitten_away_ink(browser, server):
    """点在已经被啃掉的地方不算命中：那里看着是空的，点下去却删掉一整条会很突兀。"""
    mac, ipad = open_pages(browser, server.port)
    probe = """([x, y]) => {
      const p = [];
      for (let i = 0; i <= 40; i++) p.push(i * 10, 0, 1);
      const s = { id: 'wide', tool: 'pen', color: '#1b1b1f', w: 40, p,
                  m: [[8, 100, 0, 300, 0]] };
      return whiteboard.strokeHit(s, x, y, 3);
    }"""
    assert probe and ipad.evaluate(probe, [200, 0]) is False  # 正在缺口里
    assert ipad.evaluate(probe, [200, 14]) is True  # 缺口旁边还有墨迹
    assert ipad.evaluate(probe, [20, 0]) is True  # 没被啃过的一段
    mac.close()
    ipad.close()


def test_erased_trace_is_continuous(browser, server):
    """橡皮扫过的地方不许有残留墨迹——沿路径逐点采样，一个点都不许剩。

    这一条抓的是「擦痕断成一排小块」那个 bug：遮罩原来用 even-odd 裁剪，而
    even-odd 算的是对称差不是并集，一次拖动里相邻两段胶囊在共用的圆端点处必然
    重叠，重叠处被算两次、判定成「不擦」，于是每隔一个采样点就留下一块正好等于
    橡皮直径的墨。

    以前所有用例和所有对照图用的都是**单独一段**胶囊，穿孔只在两段以上时出现，
    所以全绿也测不出来。这里必须是连续拖动。
    """
    mac, ipad = open_pages(browser, server.port)
    result = """([radius]) => {
      // 一大片实心墨迹，像用马克笔来回涂出来的那样
      const strokes = [];
      for (let row = 0; row < 12; row++) {
        const p = [];
        for (let i = 0; i <= 24; i++) p.push(120 + i * 22, 160 + row * 16, 1);
        strokes.push({ id: 'f' + row, tool: 'marker', color: '#1b1b1f', w: 34, p, n: row });
      }
      whiteboard.state.reset(whiteboard.state.meta, strokes);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';

      // 橡皮走一条带弯的路径，一路擦过去
      const path = [];
      for (let i = 0; i < 70; i++) {
        const t = i / 69;
        path.push([180 + t * 440, 200 + Math.sin(t * 5) * 70 + t * 40]);
      }
      let prev = null;
      for (const pt of path) {
        whiteboard.input.hooks.onErase(pt[0], pt[1], radius, prev);
        prev = pt;
      }
      whiteboard.flushErase();

      // 把这一块单独渲染出来，逐点看橡皮中心还有没有墨
      const bounds = whiteboard.state.contentBounds();
      const canvas = whiteboard.Renderer.renderToCanvas(whiteboard.state,
        { scale: 1, background: false, bounds });
      const ctx = canvas.getContext('2d');
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let left = 0;
      for (const [px, py] of path.slice(3, -3)) {
        const x = Math.round(px - bounds.x0);
        const y = Math.round(py - bounds.y0);
        if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) continue;
        if (data[(y * canvas.width + x) * 4 + 3] > 127) left++;
      }
      return { 残留: left, 采样: path.length - 6, 带遮罩: whiteboard.state.strokes.filter(s => s.m && s.m.length).length };
    }"""
    for radius in (5, 9, 16):
        got = ipad.evaluate(result, [radius])
        assert got["带遮罩"] > 0, got
        assert got["残留"] == 0, (radius, got)
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")
    mac.close()
    ipad.close()


def test_eraser_keeps_its_screen_size_at_every_zoom(browser, server):
    """橡皮在屏幕上恒定大小，放大缩小都按缩放等比换算到世界坐标。

    放大方向：同一个倾角在 zoom 1 和 zoom 2.02 下，原生印记在 drawing 坐标里差一倍。
    缩小方向：会话 c 里 zoom 0.25、倾角 10.4°，原生擦出来的垂直宽度是 322 个 drawing
    单位，乘回缩放是 80.5 个屏幕单位，倾角曲线在那里给的是 81。

    这里曾经有过一个「缩小不变粗」的下限，依据是 bench2 那次 zoom 0.25 下一个遮罩
    都没留下。会话 c 在同样的缩放下擦得很彻底，那次是整条橡皮没被记录下来。
    """
    mac, ipad = open_pages(browser, server.port)
    world = """([scale]) => {
      whiteboard.viewport.scale = scale;
      return whiteboard.input.worldRadius(12);
    }"""
    at1 = ipad.evaluate(world, [1])
    assert ipad.evaluate(world, [2]) == pytest.approx(at1 / 2)
    assert ipad.evaluate(world, [4]) == pytest.approx(at1 / 4)
    assert ipad.evaluate(world, [0.5]) == pytest.approx(at1 * 2)
    assert ipad.evaluate(world, [0.25]) == pytest.approx(at1 * 4)
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; }")
    mac.close()
    ipad.close()


def test_recorder_replays_an_erase_exactly(browser, server):
    """录制回放要逐字复现：同一份输入喂回去，板上剩下的笔画必须一模一样。

    真笔才触发得了的问题（压感沿笔画变化、倾角一直在动、同一个位置投两遍）
    在开发机上敲不出来。录一次带回来回放，才谈得上在这里复现和验证。
    """
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300 + i * 4) for i in range(40)])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")

    data = ipad.evaluate(RECORD_ERASE, [200, 380, 420, 380, 24, 0.9])
    assert data["events"], data
    # 录像要自报跑的是哪一份代码，不然回放对不上时分不清是版本问题还是回放问题
    assert data["build"] == ipad.evaluate("() => document.documentElement.dataset.build")
    assert data["build"]
    assert data["before"] and len(data["before"]) == 1
    # 录的是原始指针事件本身，不是擦出来的结果
    assert {e["type"] for e in data["events"]} == {
        "pointerdown", "pointermove", "pointerup"
    }
    assert data["events"][0]["tool"]["eraserMode"] == "pixel"
    assert data["events"][0]["alt"] == pytest.approx(0.9, abs=1e-4)

    after = data["after"]
    assert any(s.get("m") for s in after), "这一下应该留下遮罩"

    # 回放：先把板恢复成录制开始时的样子，再把事件按时序喂回去
    replayed = ipad.evaluate(
        "async ([data]) => whiteboard.recorder.replay(data, { wait: false })", [data]
    )
    assert replayed == after
    mac.close()
    ipad.close()


def test_recorder_panel_rides_the_diagnostics_toggle(browser, server):
    """录制跟着诊断面板一起开关：连点左上角状态圆点三下。

    iPad 是靠配置描述文件装的 Web Clip 打开的，没有地址栏，改不了查询参数，
    所以入口只能是屏幕上点得到的东西；而这个开关用户本来就知道。
    """
    mac, ipad = open_pages(browser, server.port)
    assert ipad.locator("#rec").is_hidden()
    for _ in range(3):
        ipad.click("#status")
    ipad.wait_for_selector("#rec button")
    assert ipad.evaluate("() => whiteboard.perf.enabled") is True
    assert ipad.locator("#rec button").inner_text() == "录制输入"

    ipad.click("#rec button")
    assert ipad.evaluate("() => whiteboard.recorder.recording") is True
    assert ipad.locator("#rec button").inner_text() == "停止录制"

    # 录着的时候把诊断关掉：先把录像停下来存好，不然那一段就白录了
    for _ in range(3):
        ipad.click("#status")
    ipad.wait_for_function("() => whiteboard.recorder.recording === false")
    assert ipad.locator("#rec").is_hidden()
    mac.close()
    ipad.close()


def test_eraser_reaches_where_the_pen_left_the_screen(browser, server):
    """抬笔那一下的位置也要擦掉。

    最后一个 pointermove 停在上一帧，笔离开屏幕之前还走了一段，这一段只有
    pointerup 里有。不补的话擦痕停在上一帧的位置，末端留下一道正好是橡皮直径宽
    的硬边，而笔真正压过的地方还留着墨。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 8 }; }")
    draw(ipad, [(300, 200 + i * 6) for i in range(90)], pressure=0.9)
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    out = ipad.evaluate("""() => {
      const stage = document.getElementById('stage');
      const fire = (type, x, y) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 9, pressure: 0.5,
        altitudeAngle: 0.9, azimuthAngle: 0.8,
        buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
      }));
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      // 橡皮停在笔画左边，抬笔那一下才压到笔画上
      fire('pointerdown', 180, 500);
      fire('pointermove', 240, 500);
      fire('pointermove', 280, 500);
      const mid = whiteboard.state.strokes.map((s) => whiteboard.maskSize(s));
      fire('pointerup', 300, 500);
      return { mid, end: whiteboard.state.strokes.map((s) => whiteboard.maskSize(s)) };
    }""")
    assert out["mid"] == [0], "抬笔之前还没碰到笔画"
    assert out["end"][0] > 0, "抬笔那一下压在笔画上，应该擦掉"

    # 取消不算抬笔：那一下不是用户抬的笔，位置不代表他想擦到哪
    cancelled = ipad.evaluate("""() => {
      const stage = document.getElementById('stage');
      const fire = (type, x, y) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 11, pressure: 0.5,
        altitudeAngle: 0.9, azimuthAngle: 0.8,
        buttons: type === 'pointercancel' ? 0 : 1,
        bubbles: true, cancelable: true, isPrimary: true,
      }));
      const before = whiteboard.state.strokes.map((s) => whiteboard.maskSize(s));
      fire('pointerdown', 180, 260);
      fire('pointermove', 240, 260);
      fire('pointercancel', 300, 260);
      return { before, after: whiteboard.state.strokes.map((s) => whiteboard.maskSize(s)) };
    }""")
    assert cancelled["after"] == cancelled["before"]
    mac.close()
    ipad.close()


def test_eraser_uses_every_coalesced_sample(browser, server):
    """一帧里的合并采样点橡皮要全吃掉，不能只取最后一个。

    iPad 上笔是 120Hz 而 pointermove 一帧才来一次，中间那些点都在
    getCoalescedEvents 里。只取最后一个等于把一帧里的一段曲线压成一条直线，
    擦得越快压得越狠，擦痕边上就出现一节一节的直棱。画线那边一直取全部的。

    判据是「一帧里六个点」和「六帧各一个点」擦出来的遮罩完全一样。
    """
    mac, ipad = open_pages(browser, server.port)
    # getCoalescedEvents 造不出来，只能直接喂给 moveErase——回放走的也是这条路
    probe = """([coalesce]) => {
      const input = whiteboard.input;
      const rect = document.getElementById('stage').getBoundingClientRect();
      const pt = (x, y) => ({
        clientX: rect.left + x, clientY: rect.top + y, pointerType: 'pen',
        pointerId: 3, pressure: 0.5, altitudeAngle: 0.9, azimuthAngle: 0.8,
        preventDefault() {},
      });
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 96 };
      const p = [];
      for (let i = 0; i < 80; i++) p.push(-260 + i * 6, -10, 1);
      whiteboard.state.add([{ id: 'band', tool: 'pen', color: '#000', w: 96, p, n: 1 }]);
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      input._rect = null;
      input.erase = { pointerId: 3, ids: [], radius: 8, last: null };
      // 一帧里笔走了一段圆弧，六个采样点
      const arc = [];
      for (let i = 0; i < 6; i++) {
        arc.push(pt(300 + i * 9, 400 - Math.sin((i / 5) * Math.PI) * 8));
      }
      if (coalesce) {
        input.moveErase({ ...arc[arc.length - 1], getCoalescedEvents: () => arc });
      } else {
        for (const sample of arc) input.moveErase({ ...sample, getCoalescedEvents: () => [sample] });
      }
      input.endErase(null);
      return (whiteboard.state.byId.get('band') || {}).m || null;
    }"""
    one_frame = ipad.evaluate(probe, [True])
    per_frame = ipad.evaluate(probe, [False])
    assert one_frame, "应该留下遮罩"
    # 六个采样点都要落进遮罩里，不能只剩首尾
    assert sum((len(c) - 1) // 2 for c in one_frame) >= 6, one_frame
    assert one_frame == per_frame
    mac.close()
    ipad.close()


def test_mask_is_thinned_before_it_is_baked_into_cuts(browser, server):
    """遮罩攒到上限先抽稀，抽不动了才落实成切分。

    落实成切分是看得见的变化：啃出来的形状换成平口断面，贴边的细条还会被一起
    清掉。以前一条笔画上擦够 400 个采样点就触发，iPad 上 120Hz 只要三秒多，
    在一大块墨上来回擦几下就撞上了，手感上就是「擦着擦着忽然多出一道平口」。

    裁剪的开销随段数是平方涨的（400 段整屏重画 2.9 ms、1000 段 15 ms、
    2000 段 57 ms），所以不能靠放宽上限解决，只能让段数真的变少。容差取 r/6，
    和 inkpdf.py 导出时用的是同一个，屏幕和导出才对得上。
    """
    mac, ipad = open_pages(browser, server.port)
    scrub = """([count]) => {
      const input = whiteboard.input;
      const rect = document.getElementById('stage').getBoundingClientRect();
      const pt = (x, y, alt) => ({
        clientX: rect.left + x, clientY: rect.top + y, pointerType: 'pen',
        pointerId: 3, pressure: 0.5, altitudeAngle: alt, azimuthAngle: 0.8,
        preventDefault() {},
      });
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      const p = [];
      for (let i = 0; i < 200; i++) p.push(-400 + i * 4, -10, 1);
      whiteboard.state.add([{ id: 'band', tool: 'pen', color: '#000', w: 96, p, n: 1 }]);
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      input._rect = null;
      input.erase = { pointerId: 3, ids: [], radius: 8, last: null };
      for (let k = 0; k < count; k++) {
        const s = pt(300 + 140 * Math.sin(k / 18), 400 + 10 * Math.sin(k / 5),
                     0.9 + 0.02 * Math.sin(k / 7));
        input.moveErase({ ...s, getCoalescedEvents: () => [s] });
      }
      input.endErase(null);
      return {
        strokes: whiteboard.state.strokes.length,
        segs: whiteboard.state.strokes.map((s) => whiteboard.maskSize(s)),
        cut: whiteboard.state.strokes.reduce((a, s) => a + (s.cut ? 1 : 0), 0),
      };
    }"""
    # 一千下——iPad 上 120Hz 差不多八秒连续擦一条笔画，还得是遮罩不是切分
    long_scrub = ipad.evaluate(scrub, [1000])
    assert long_scrub["cut"] == 0, long_scrub
    assert long_scrub["strokes"] == 1, long_scrub
    assert max(long_scrub["segs"]) <= 400, long_scrub

    # 抽稀本身不改形状：没到上限的时候遮罩一个点都不能少
    untouched = ipad.evaluate("""() => {
      const s = { m: [[8, 0, 0, 10, 0, 20, 3, 30, 0]] };
      const before = JSON.stringify(s.m);
      whiteboard.simplifyMask(s);
      return [before, JSON.stringify(s.m)];
    }""")
    # 30,0 和 0,0 之间那个 20,3 离直线 3 个单位，容差是 8/6，留着
    assert untouched[0] == untouched[1], untouched
    mac.close()
    ipad.close()


def test_a_long_erase_survives_the_round_trip(browser, server):
    """擦掉的东西不能在同步这一圈里丢掉。

    像素橡皮每擦一下就往遮罩里加一段胶囊，一次长擦除能攒出上百条链。服务端按
    MAX_MASK_CHAINS 截断，截完的结果既广播给对端，也顺着回执盖回发送端自己——
    于是这边刚擦掉的墨，过一会儿自己又回来了一部分，对端和存档里也少擦。真机
    录像里一次擦除攒到 113 条链，截到 64，丢掉 43%。

    判据是擦过的地方在两端都真的擦掉了，而不是链数对得上：链怎么攒是实现细节，
    擦过的地方该没墨才是用户看到的东西。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 8 }; }")
    draw(ipad, [(200 + i * 8, 400) for i in range(100)], pressure=0.9)
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    mac.wait_for_function("() => whiteboard.state.strokes.length === 1")

    # 沿着这条笔画一下一下地点着擦：每一下都是独立的一笔，各自成一条链
    spots = ipad.evaluate("""() => {
      const stage = document.getElementById('stage');
      const fire = (type, x, y) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 5, pressure: 0.5,
        altitudeAngle: 1.1, azimuthAngle: 0.8,
        buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
      }));
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      const out = [];
      for (let i = 0; i < 80; i++) {
        const x = 220 + i * 9;
        fire('pointerdown', x, 400);
        fire('pointermove', x + 3, 400);
        fire('pointerup', x + 3, 400);
        out.push(whiteboard.input.toWorld({ clientX: x + 1, clientY: 400 }));
      }
      return out;
    }""")
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0")
    mac.wait_for_function("() => (whiteboard.state.strokes[0] || {}).m")
    ipad.wait_for_timeout(400)

    left = """([spots]) => {
      const s = whiteboard.state.strokes[0];
      return spots.filter(([x, y]) => whiteboard.strokeHit(s, x, y, 0)).length;
    }"""
    here = ipad.evaluate(left, [spots])
    there = mac.evaluate(left, [spots])
    assert here == 0, f"本机上还有 {here} 处擦过的地方留着墨"
    assert there == 0, f"对端上还有 {there} 处擦过的地方留着墨"
    mac.close()
    ipad.close()


def test_one_drag_stays_one_mask_chain(browser, server):
    """一次连续拖动只攒一条胶囊链，不是一个采样点一条。

    橡皮的粗细每个采样点都重新平滑一次，收敛是指数的，永远差那么一点点。
    判「还是同一次扫掠」原来用的是半径严格相等，于是几乎判不出来：一份真机
    录像里一次擦除攒出 113 条链、82 个互不相同的半径，而它们在两位小数上全是
    同一个值。链一多，裁剪开销（随段数平方涨）和同步的体量都跟着涨。
    """
    mac, ipad = open_pages(browser, server.port)
    chains = ipad.evaluate("""() => {
      const input = whiteboard.input;
      const rect = document.getElementById('stage').getBoundingClientRect();
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      const p = [];
      for (let i = 0; i < 200; i++) p.push(-400 + i * 4, -10, 1);
      whiteboard.state.add([{ id: 'band', tool: 'pen', color: '#000', w: 96, p, n: 1 }]);
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      input._rect = null;
      // 倾角一路缓慢变化：平滑出来的半径每一下都不一样，正是真机的样子
      input.erase = { pointerId: 3, ids: [], radius: 8, last: null };
      for (let k = 0; k < 120; k++) {
        const s = {
          clientX: rect.left + 260 + k * 2, clientY: rect.top + 400,
          pointerType: 'pen', pointerId: 3, pressure: 0.5,
          altitudeAngle: 0.9 + k * 0.0008, azimuthAngle: 0.8, preventDefault() {},
        };
        input.moveErase({ ...s, getCoalescedEvents: () => [s] });
      }
      input.endErase(null);
      const m = (whiteboard.state.byId.get('band') || {}).m || [];
      return { chains: m.length, segs: whiteboard.maskSize({ m }),
               radii: new Set(m.map((c) => c[0])).size };
    }""")
    assert chains["segs"] >= 100, chains
    # 一次拖动、一条链。允许因为半径真的走远了而断开几次，但不能一下一条
    assert chains["chains"] <= 3, chains
    mac.close()
    ipad.close()


def test_a_very_long_stroke_still_reaches_the_other_device(browser, server):
    """画得特别久的一笔不能只留在本机。

    服务端对超长点列是整条丢掉（sanitize_stroke），不是截断。不切分的话这一笔
    本机看得见、对端和存档里没有，而且只有重新载入才看得出来——和遮罩被截断
    是同一类问题：显示和存下来的东西不一致。

    切开的几段接缝共用同一个采样点，两端都是默认圆头，叠在一起看不出接缝；
    撤销仍然是一步，因为用户画的就是一笔。
    """
    mac, ipad = open_pages(browser, server.port)
    # 直接提交一笔超长的：靠真的发两万多个指针事件太慢，切分这一段和事件无关
    n = ipad.evaluate("""() => {
      const n = 25000;
      const p = [];
      for (let i = 0; i < n; i++) p.push(-500 + i * 0.05, -10 + Math.sin(i / 60) * 30, 0.6);
      whiteboard.commitStroke({ id: whiteboard.input.newStrokeId(), tool: 'pen',
                                color: '#1b1b1f', w: 3, p });
      return n;
    }""")
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0")
    mac.wait_for_timeout(300)

    read = """() => {
      const all = whiteboard.state.strokes;
      return { pieces: all.length, points: all.reduce((a, s) => a + s.p.length / 3, 0),
               p: all.map((s) => s.p) };
    }"""
    here = ipad.evaluate(read)
    there = mac.evaluate(read)
    assert there["points"] == here["points"], (
        f"对端只收到 {there['points']} 个点，本机有 {here['points']} 个")
    assert there["p"] == here["p"], "对端和本机的点列必须一样"
    # 接缝共用一个点，所以总点数比原来多「段数 - 1」个
    assert here["points"] == n + here["pieces"] - 1
    joins = [here["p"][i][-3:] == here["p"][i + 1][:3] for i in range(here["pieces"] - 1)]
    assert all(joins), "每个接缝都要共用同一个采样点"

    # 用户画的是一笔，撤销就该一次全没
    ipad.evaluate("() => whiteboard.undo()")
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 0")
    mac.wait_for_function("() => whiteboard.state.strokes.length === 0")
    mac.close()
    ipad.close()


def test_eraser_width_is_decided_when_the_pen_lands(browser, server):
    """橡皮的粗细在落笔那一刻定下来，整笔不再跟着倾斜走。

    原生就是这样：92 条原生橡皮笔画里，65% 整笔只报一个倾角读数，九成笔内极差在
    2.36° 以内，中位数是 0。

    我们原来每个采样点都重新平滑一次。iPad 报的 tiltX / tiltY 是整度的，写字时
    笔身本来就在晃，而曲线在 37° 以下很陡——一份真机录像里，写字的笔身角度在
    33°～47° 之间来回晃，同一笔里直径差到 2.7 倍，擦痕一节粗一节细，像一串香肠。

    悬停时的光标圈照旧跟着倾斜走，那是预览，本来就该跟手。
    """
    mac, ipad = open_pages(browser, server.port)
    out = ipad.evaluate("""() => {
      const input = whiteboard.input;
      const rect = document.getElementById('stage').getBoundingClientRect();
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      const p = [];
      for (let i = 0; i < 200; i++) p.push(-400 + i * 4, -10, 1);
      whiteboard.state.add([{ id: 'band', tool: 'pen', color: '#000', w: 96, p, n: 1 }]);
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      input._rect = null;
      const at = (k) => ({
        clientX: rect.left + 260 + k * 3, clientY: rect.top + 400,
        pointerType: 'pen', pointerId: 4, pressure: 0.5,
        // 笔身在 33°～47° 之间晃，正是真机录像里写字的样子
        altitudeAngle: ((40 + 7 * Math.sin(k / 3)) * Math.PI) / 180,
        azimuthAngle: 0.8, preventDefault() {}, getCoalescedEvents() { return [this]; },
      });
      input.onDown({ ...at(0), buttons: 1, isPrimary: true, button: 0 });
      for (let k = 1; k < 60; k++) input.onMove(at(k));
      input.onUp({ ...at(60), buttons: 0, isPrimary: true, button: 0 });
      const m = (whiteboard.state.byId.get('band') || {}).m || [];
      const radii = [...new Set(m.map((c) => c[0]))];
      // 悬停光标不受影响：它该跟着倾斜走。取曲线上确实有坡度的两个角度
      const hover = (deg) => ({ ...at(0), altitudeAngle: (deg * Math.PI) / 180 });
      whiteboard.input.updateCursor(hover(80));
      const a = whiteboard.renderer.cursor.r;
      whiteboard.input.updateCursor(hover(30));
      const b = whiteboard.renderer.cursor.r;
      return { chains: m.length, radii, cursorMoves: a !== b };
    }""")
    assert out["chains"] >= 1
    # 整笔一个粗细，所以整笔也就一条链
    assert len(out["radii"]) == 1, out["radii"]
    assert out["chains"] == 1, out["chains"]
    assert out["cursorMoves"], "悬停光标还是要跟着倾斜走"
    mac.close()
    ipad.close()


def test_the_panel_separates_pen_events_from_new_positions(browser, server):
    """诊断面板要分开显示「笔每秒来多少个事件」和「其中多少个是新位置」。

    iPad 上同一个位置会被投递两遍：录像里每一笔的事件速率约 120/s，其中一半
    和前一条坐标完全相同，真正能用的位置只有约 60/s。笔迹的上限由后一个数决定
    ——采不到的那一段，再好的平滑也补不回来。真机上接不了开发者工具，这个数只能
    显示在屏幕上，改完系统设置当场就能看出有没有用。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate("""() => {
      const stage = document.getElementById('stage');
      whiteboard.perf.toggle(true);
      const fire = (x, y) => stage.dispatchEvent(new PointerEvent('pointermove', {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 9, pressure: 0.03,
        buttons: 0, bubbles: true, cancelable: true, isPrimary: true }));
      // 六个事件，坐标只有三个是新的——和真机上「一个位置投两遍」一样
      for (const [x, y] of [[10,10],[10,10],[20,10],[20,10],[30,10],[30,10]]) fire(x, y);
      whiteboard.perf.paint();
      return {
        text: document.getElementById('perf').textContent,
        hz: whiteboard.input.stats.penHz,
        moved: whiteboard.input.stats.penMoveHz,
      };
    }""")
    assert out["hz"] == 6, out
    assert out["moved"] == 3, out
    assert "笔事件 6/s" in out["text"], out["text"]
    assert "新位置 3/s" in out["text"], out["text"]
    mac.close()


def test_the_diagnostics_panel_says_which_build_it_is(browser, server):
    """诊断面板上要写清楚跑的是哪一份代码。

    对着一张截图讨论问题，先得确定两边说的是同一份代码。源码运行时显示
    「分支@短commit」，打包之后不是 git 仓库，退回版本号。
    """
    mac, ipad = open_pages(browser, server.port)
    build = ipad.evaluate("() => document.documentElement.dataset.build")
    assert build, "页面上要带着版本"
    for _ in range(3):
        ipad.click("#status")
    ipad.wait_for_selector("#perf")
    # 打开面板就该立刻有内容，不用等下一帧
    text = ipad.inner_text("#perf")
    assert build in text, (build, text)
    mac.close()
    ipad.close()


def test_the_stylus_erases_as_evenly_as_the_mouse(browser, server):
    """同一条路径，笔擦出来的痕迹要和鼠标一样匀，不能一节粗一节细。

    鼠标那条路橡皮半径是写死的常数（没有倾斜可依据），所以从来不会香肠；
    只有笔会跟着倾角走。iPad 的 Safari 报的又是整度的 tiltX / tiltY，写字时笔身
    本来就在晃，曲线在 37° 以下还很陡——三样凑在一起，一笔之内直径能差 2.7 倍。

    粗细该由落笔时的倾角决定（笔压得平就该擦得宽，这是对的），但决定之后整笔
    就不该再变。判据就是：两种输入都只攒出一条链、一个半径。
    """
    mac, ipad = open_pages(browser, server.port)
    run = """([pen]) => {
      const input = whiteboard.input;
      const rect = document.getElementById('stage').getBoundingClientRect();
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      const p = [];
      for (let i = 0; i < 200; i++) p.push(-400 + i * 4, -10, 1);
      whiteboard.state.add([{ id: 'band', tool: 'pen', color: '#000', w: 96, p, n: 1 }]);
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      input._rect = null;
      const at = (k) => {
        const ev = { clientX: rect.left + 260 + k * 3, clientY: rect.top + 400,
          pointerId: 6, pointerType: pen ? 'pen' : 'mouse', pressure: 0.5,
          buttons: 1, button: 0, isPrimary: true,
          preventDefault() {}, getCoalescedEvents() { return [this]; } };
        if (pen) {
          // 笔身在 40° 上下晃 ±6°，换算成整度的 tiltX / tiltY——真机就是这样报的
          const alt = 40 + 6 * Math.sin(k / 5);
          ev.tiltX = Math.round(Math.atan(1 / Math.tan((alt * Math.PI) / 180)) * 180 / Math.PI);
          ev.tiltY = 0;
        }
        return ev;
      };
      input.onDown(at(0));
      for (let k = 1; k < 60; k++) input.onMove(at(k));
      input.onUp(at(60));
      const m = (whiteboard.state.byId.get('band') || {}).m || [];
      return { chains: m.length, radii: [...new Set(m.map((c) => c[0]))] };
    }"""
    with_pen = ipad.evaluate(run, [True])
    with_mouse = ipad.evaluate(run, [False])
    for label, got in (("笔", with_pen), ("鼠标", with_mouse)):
        assert got["chains"] == 1, (label, got)
        assert len(got["radii"]) == 1, (label, got)
    # 粗细本来就该由倾角决定，所以两者的半径不必相等，只要各自匀
    assert with_pen["radii"][0] > 0 and with_mouse["radii"][0] > 0
    mac.close()
    ipad.close()


def test_our_own_mask_echo_does_not_rewind_the_erase(browser, server):
    """自己发出去的遮罩从服务器回来时，不能盖掉本地已经擦到的新位置。

    遮罩发的是全量。回执回到手里时本地往往已经又擦了几下，照盖就是拿旧快照
    覆盖新状态：擦掉的点白丢，下一段胶囊也接不回去，链断开、断口处细成一道
    脖子——擦痕于是一节一节，像一串香肠。

    这一条只有走完整条网络路径才看得见：`recorder.replay` 是直接改本地状态的，
    服务器根本不认识那些笔画，mask 操作被丢弃、回执从不返回，所以回放永远是
    干净的。真机上回执是回来的。

    判据：一次连续拖动只攒一条链、一个点都不少，而且对端拿到的是同一份。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 13 }; }")
    draw(ipad, [(150 + i * 6, 400) for i in range(120)], pressure=0.9)
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    mac.wait_for_function("() => whiteboard.state.strokes.length === 1")
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0")

    steps = 120
    # 回执慢一点回来，把真机上的局域网往返放大出来
    got = ipad.evaluate("""async ([steps]) => {
      const net = whiteboard.net;
      const real = net._ack.bind(net);
      net._ack = (msg) => setTimeout(() => real(msg), 60);
      const stage = document.getElementById('stage');
      const fire = (type, x, y) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 9, pressure: 0.5,
        tiltX: 47, tiltY: 20, buttons: type === 'pointerup' ? 0 : 1,
        bubbles: true, cancelable: true, isPrimary: true }));
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      fire('pointerdown', 160, 400);
      for (let k = 1; k <= steps; k++) {
        fire('pointermove', 160 + k * 5, 400);
        // 让出一帧：擦一下、发一次、回执插进来，正是真机的节奏
        await new Promise((r) => requestAnimationFrame(r));
      }
      fire('pointerup', 160 + steps * 5, 400);
      await new Promise((r) => setTimeout(r, 600));
      net._ack = real;
      const m = whiteboard.state.strokes[0].m || [];
      return { chains: m.length, pts: m.reduce((a, c) => a + (c.length - 1) / 2, 0) };
    }""", [steps])
    assert got["chains"] == 1, got
    assert got["pts"] == steps + 1, got

    # 对端还是要收得到：不回放自己的回执，不等于不发给别人
    mac.wait_for_function("() => (whiteboard.state.strokes[0] || {}).m")
    same = mac.evaluate("""([m]) => JSON.stringify(whiteboard.state.strokes[0].m) === m""",
                        [ipad.evaluate("() => JSON.stringify(whiteboard.state.strokes[0].m)")])
    assert same, "对端的遮罩要和本机一致"
    mac.close()
    ipad.close()


# 真机录像里一整段手写的压感读数（20260926-204816 / 231400 / 20260927-105648 三份
# 录像，2124 个非零采样）分位数。iPad Safari 根本不把 0～1 用满：
#   p1 0.0092  p25 0.0229  p50 0.0282  p75 0.0365  p95 0.0800  p99 0.1072  max 0.1253
RECORDED_PRESSURE = {
    "p1": 0.0092,
    "p25": 0.0229,
    "p50": 0.0282,
    "p75": 0.0365,
    "p95": 0.0800,
    "max": 0.1253,
}


def test_pressure_actually_changes_how_thick_the_pen_is(browser, server):
    """Apple Pencil 的压感读数挤在 0～0.13，粗细曲线得照这个量程来。

    这条曲线以前是 `0.42 + 0.58 * p^0.8`，假设设备把 0～1 用满。真机上常用的
    0.023～0.080 一段算出来是 0.448～0.497——差 11%，看不出来有压感。
    直接照搬 atrament 的 `#getWeightWithPressure` 更糟：它以 0.5 为轴，0.5 以下
    是 `weight * 2p`，0.028 算出来是设定线宽的 5.6%，一条头发丝。

    现在先按膝点把读数展开再算粗细。用例钉三件事：常用一段的粗细要拉得开、
    「放松写字」那一档的绝对粗细不许变（不然已经写好的字整体变粗变细），
    用满力还是正好设定的线宽。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate(
        """(q) => {
      const f = (p) => whiteboard.strokeRadius('pen', 2, p);   // 设定线宽 2，半径即倍数
      return {
        p1: f(q.p1), p25: f(q.p25), p50: f(q.p50), p75: f(q.p75),
        p95: f(q.p95), max: f(q.max), full: f(1),
      };
    }""",
        RECORDED_PRESSURE,
    )
    # 常用的 p25～p95 一段至少要差一半以上，才叫看得出压感
    assert out["p95"] / out["p25"] > 1.5, out
    # 整个量程（p1～max）要差两倍以上
    assert out["max"] / out["p1"] > 2.0, out
    # 单调
    keys = ["p1", "p25", "p50", "p75", "p95", "max"]
    assert all(out[a] < out[b] for a, b in zip(keys, keys[1:])), out
    # 「放松写字」的 p50 还是设定线宽的 0.45 倍上下：改的是动态范围，不是整体粗细
    assert 0.42 < out["p50"] < 0.48, out
    # 用满力正好是设定的线宽——线宽滑块的含义没变
    assert out["full"] == pytest.approx(1.0), out
    mac.close()


def test_a_pointer_without_pressure_keeps_the_width_it_had(browser, server):
    """鼠标、手指和不带压感的笔画多粗，这次不许跟着压感曲线一起变。

    它们的粗细是按速度算的（走得快就细），可是文件里每个点只有一个压感字段，
    所以粗细得折回压感值再存。曲线一换，折算也得跟着换，不然 Mac 上用鼠标
    画出来的线会莫名变粗变细。判据是和旧管线（`clamp(1 - speed / 3.2, 0.38, 1)`
    配旧曲线 `0.42 + 0.58 * p^0.8`）算出来的粗细逐点比对，全程差不到 2%。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate("""() => {
      const speeds = [0, 0.25, 0.5, 0.75, 1, 1.5, 1.984, 3, 6];
      const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
      return speeds.map((speed) => {
        const oldP = clamp(1 - speed / 3.2, 0.38, 1);
        const before = 0.42 + 0.58 * Math.pow(oldP, 0.8);
        const stored = whiteboard.pressureForFactor(clamp(1 - speed * 0.157, 0.69, 1));
        return { speed, before, after: whiteboard.strokeRadius('pen', 2, stored) };
      });
    }""")
    for row in out:
        assert abs(row["after"] / row["before"] - 1) < 0.02, row
    mac.close()


def test_the_tail_of_a_stroke_thins_out_instead_of_swelling(browser, server):
    """笔快离开屏幕时压感掉到 0，那是真读数，末尾该收细而不是鼓一个包。

    `pressure === 0` 有两种含义：设备根本报不了压感，和笔快抬起来了。原来一律
    当成前者顶成 0.5。真机录像里抬笔前那两下压感是 0.005、0.005、0，顶成 0.5
    之后末端反而粗了两成半——每一笔的收尾都带个包。

    只要这一笔里报过一次正压感，后面的 0 就沿用上一次的值；一次都没报过才算
    这支笔没有压感（鼠标和不带压感的笔照旧走默认值）。
    """
    mac, ipad = open_pages(browser, server.port)
    out = ipad.evaluate("""() => {
      const stage = document.getElementById('stage');
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 13 };
      const fire = (type, x, y, pressure) => stage.dispatchEvent(new PointerEvent(type, {
        clientX: x, clientY: y, pointerType: 'pen', pointerId: 2, pressure,
        tiltX: 40, tiltY: 0, buttons: type === 'pointerup' ? 0 : 1,
        bubbles: true, cancelable: true, isPrimary: true }));
      // 真机录到的收尾就是这样：一路降到 0.005，最后一下报 0
      const tail = [0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.012, 0.005, 0.005, 0];
      fire('pointerdown', 200, 300, tail[0]);
      for (let i = 1; i < tail.length; i++) fire('pointermove', 200 + i * 9, 300, tail[i]);
      fire('pointerup', 200 + tail.length * 9, 300, 0);
      const s = whiteboard.state.strokes[0];
      const p = s.p.filter((_, i) => i % 3 === 2);
      return { p, radii: p.map((v) => whiteboard.strokeRadius('pen', s.w, v)) };
    }""")
    r = out["radii"]
    assert len(r) >= 6, out
    # 末尾不许比中段粗
    assert r[-1] <= r[len(r) // 2] + 1e-9, r
    # 而且要确实在收细
    assert r[-1] < r[0], r
    mac.close()
    ipad.close()


# 真机录的一笔：笔走得快，采样点之间隔开几十个世界单位。折线和该走的曲线最远
# 差 7.3 个单位，转角大的地方就是肉眼可见的直线拼接。
REAL_FAST_STROKE = [
    (141.875, 0, 1), (144.625, 13.75, 0.9961), (148.5, 57.75, 0.8863), (150.0, 116.625, 0.7961),
    (150.625, 189.75, 0.7098), (139.875, 271.125, 0.6275), (119.0, 358.75, 0.5686), (91.5, 443.375, 0.5216),
    (64.0, 521.125, 0.5098), (39.125, 588, 0.5216), (21.0, 645, 0.5451), (8.25, 684.375, 0.6078),
    (0.375, 702.875, 0.6863), (0.0, 710.25, 0.7647), (32.875, 682.875, 0.7059), (90.0, 633.5, 0.6235),
    (162.375, 578, 0.5647), (238.125, 522.75, 0.5176), (337.125, 459.375, 0.4824), (462.0, 392.875, 0.4588),
    (619.25, 322.125, 0.4392), (800.375, 252.625, 0.4235), (1002.125, 186.375, 0.4118), (1198.25, 129.625, 0.4039),
    (1384.0, 93.125, 0.4), (1551.875, 64.75, 0.3922), (1707.0, 45.125, 0.3922), (1837.875, 29.125, 0.3882),
    (1945.125, 22.625, 0.3843), (2032.125, 20.125, 0.4157), (2097.125, 19, 0.4745), (2145.125, 21.375, 0.5451),
    (2180.875, 25.125, 0.6118), (2211.625, 32, 0.6588), (2235.0, 37.625, 0.7098), (2255.25, 42.625, 0.749),
    (2279.875, 52.75, 0.7647), (2303.5, 65.125, 0.7765), (2332.25, 83.875, 0.7647), (2360.25, 99.625, 0.7725),
    (2393.375, 127.875, 0.7333), (2437.0, 169.375, 0.6902), (2484.625, 219.125, 0.6392), (2531.125, 280.125, 0.5882),
    (2574.5, 337.625, 0.5725), (2608.375, 393.625, 0.5765), (2633.0, 438, 0.6118), (2651.0, 466.75, 0.6706),
    (2658.25, 489.25, 0.7216), (2663.875, 506.5, 0.7647), (2666.125, 521.625, 0.8), (2667.0, 538.75, 0.8196),
    (2664.625, 551.125, 0.8392), (2658.25, 567, 0.8549), (2644.625, 581.625, 0.851), (2617.25, 595.75, 0.8196),
    (2587.0, 612.375, 0.7608), (2566.75, 623.5, 0.7608),
]


def test_the_outline_is_drawn_as_curves_not_chords(browser, server):
    """轮廓点之间要画二次贝塞尔，不能直接连直线。

    perfect-freehand 的 getStroke 返回的是一串轮廓点，配套的渲染是「二次贝塞尔穿过
    相邻两点的中点、顶点当控制点」。直接 lineTo 连那串点也能出形状，但笔走得快、
    轮廓点隔得开的时候边上就是一段段直线——正是当初那个「直线拼出来的曲线」。

    判据：同一串轮廓点，一份按 buildPath 画，一份 lineTo 连起来，比两者的面积。
    弦永远落在曲线内侧，所以曲线那份必须明显更大。用真机录的那一笔量，它的采样点
    隔开几十个单位，差别最明显。
    """
    mac, _ = open_pages(browser, server.port)
    out = mac.evaluate("""([flat, width]) => {
      const stroke = { id:'x', tool:'pen', color:'#000', w:width, p:flat };
      const pts = whiteboard.strokeOutline(stroke);
      const S = 2, PAD = 8;
      let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
      for (const q of pts) { x0=Math.min(x0,q[0]); x1=Math.max(x1,q[0]);
        y0=Math.min(y0,q[1]); y1=Math.max(y1,q[1]); }
      x0-=PAD; y0-=PAD; x1+=PAD; y1+=PAD;
      const fill = (path) => {
        const c = document.createElement('canvas');
        c.width = Math.ceil((x1-x0)*S); c.height = Math.ceil((y1-y0)*S);
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.setTransform(S,0,0,S,-x0*S,-y0*S);
        ctx.fillStyle = '#000'; ctx.fill(path);
        const d = ctx.getImageData(0,0,c.width,c.height).data;
        const m = new Uint8Array(d.length/4);
        for (let i = 0; i < m.length; i++) m[i] = d[i*4+3] > 128 ? 1 : 0;
        return m;
      };
      const count = (m) => { let n = 0; for (const v of m) n += v; return n/(S*S); };
      const xor = (a, b) => {
        const A = fill(a), B = fill(b);
        let n = 0; for (let i = 0; i < A.length; i++) if (A[i] !== B[i]) n++;
        return n/(S*S);
      };
      const area = (path) => {
        const c = document.createElement('canvas');
        c.width = Math.ceil((x1-x0)*S); c.height = Math.ceil((y1-y0)*S);
        const ctx = c.getContext('2d', { willReadFrequently: true });
        ctx.setTransform(S,0,0,S,-x0*S,-y0*S);
        ctx.fillStyle = '#000'; ctx.fill(path);
        const d = ctx.getImageData(0,0,c.width,c.height).data;
        let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 128) n++;
        return n/(S*S);
      };
      const chords = new Path2D();
      chords.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) chords.lineTo(pts[i][0], pts[i][1]);
      chords.closePath();
      const curve = whiteboard.buildPath(stroke);
      return { curve: area(curve), chord: area(chords),
               差: xor(curve, chords), n: pts.length };
    }""", [[v for pt in REAL_FAST_STROKE for v in pt], 13.0])
    assert out["n"] > 50, out
    # 两份形状必须真的不一样：换成 lineTo 的话这个数会是 0
    assert out["差"] > out["curve"] * 0.02, out
    mac.close()
# 真机录的一笔：往右上画上去再原路收回来。白洞就出在收回来那一段。
REAL_HOOK_STROKE = [
    (0.0, 56.598, 0.1034), (0.056, 56.485, 0.1034), (0.385, 54.983, 0.1031), (0.549, 54.191, 0.1029),
    (1.371, 52.461, 0.1023), (1.823, 51.51, 0.1018), (3.365, 49.018, 0.0984), (4.213, 47.648, 0.0958),
    (6.48, 44.25, 0.0924), (7.726, 42.382, 0.0899), (11.0, 37.585, 0.0892), (12.8, 34.947, 0.0887),
    (18.065, 28.096, 0.0873), (20.961, 24.328, 0.0863), (25.366, 19.331, 0.0858), (27.789, 16.582, 0.0854),
    (32.721, 12.089, 0.0871), (35.434, 9.618, 0.0884), (40.358, 6.121, 0.0948), (43.065, 4.198, 0.0996),
    (46.355, 2.465, 0.1035), (48.164, 1.512, 0.1064), (50.059, 0.763, 0.1102), (51.101, 0.351, 0.113),
    (52.518, 0.125, 0.1098), (53.297, 0.0, 0.1074), (54.12, 0.156, 0.0991), (54.572, 0.242, 0.0929),
    (54.765, 0.515, 0.0855), (54.871, 0.665, 0.08), (54.029, 1.478, 0.0836), (53.566, 1.926, 0.0863),
    (51.68, 3.466, 0.0909), (50.643, 4.313, 0.0944), (47.766, 6.578, 0.1014), (46.184, 7.825, 0.1066),
    (42.782, 10.816, 0.1111), (40.912, 12.462, 0.1145), (37.464, 15.617, 0.1099), (35.568, 17.352, 0.1064),
    (32.275, 20.5, 0.1023), (30.464, 22.231, 0.0992), (27.442, 25.209, 0.1029), (25.781, 26.846, 0.1056),
    (23.629, 29.097, 0.1118), (22.446, 30.335, 0.1164), (21.177, 31.691, 0.1237), (20.478, 32.436, 0.1291),
    (19.926, 33.071, 0.1288), (19.622, 33.421, 0.1286), (19.398, 33.613, 0.127), (19.275, 33.719, 0.1258),
    (19.208, 33.777, 0.1264), (19.114, 33.865, 0.1236), (18.978, 33.94, 0.1192), (19.048, 34.028, 0.0726),
    (19.102, 34.133, 0.0578), (19.118, 34.252, 0.0527), (19.067, 34.319, 0.0523), (19.011, 34.568, 0.0403),
    (19.006, 34.694, 0.0332), (19.003, 34.876, 0.0273), (19.002, 34.975, 0.0228), (19.0, 35.348, 0.0228),
]


def test_a_stroke_never_has_holes_in_it(browser, server):
    """笔画里不许出现被墨迹围住的白色缺口。

    这一笔是真机录的：往右上画上去，再原路收回来（「往一个方向画收回会出现诡异的
    白色」说的就是它）。白洞当初出在收回来那一段——那时两侧各算一条斜接偏移线接成
    闭合回路，内侧偏移点折回去自交，自交出来的小环绕向和主体相反，nonzero 下算 0。

    判据不假设任何画法：把笔画填出来，从图像边界灌水，灌不到的白色像素就是被墨迹
    围住的洞。换渲染实现也不会假红。
    """
    mac, ipad = open_pages(browser, server.port)
    holes = ipad.evaluate("""([flat, width]) => {
      const S = 4, PAD = 8;
      let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
      for (let i = 0; i < flat.length; i += 3) {
        x0=Math.min(x0,flat[i]); x1=Math.max(x1,flat[i]);
        y0=Math.min(y0,flat[i+1]); y1=Math.max(y1,flat[i+1]);
      }
      x0-=PAD; y0-=PAD; x1+=PAD; y1+=PAD;
      const c = document.createElement('canvas');
      c.width = Math.ceil((x1-x0)*S); c.height = Math.ceil((y1-y0)*S);
      const ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.setTransform(S,0,0,S,-x0*S,-y0*S);
      ctx.fillStyle = '#000';
      ctx.fill(whiteboard.buildPath({ id:'x', tool:'pen', color:'#000', w:width, p:flat }));
      const W = c.width, H = c.height;
      const d = ctx.getImageData(0,0,W,H).data;
      const white = new Uint8Array(W*H);
      let ink = 0;
      for (let i = 0; i < W*H; i++) {
        const solid = d[i*4+3] > 128;
        white[i] = solid ? 0 : 1;
        if (solid) ink++;
      }
      const seen = new Uint8Array(W*H);
      const stack = [];
      const push = (x,y) => { const k=y*W+x; if(white[k] && !seen[k]){seen[k]=1; stack.push(k);} };
      for (let x = 0; x < W; x++) { push(x,0); push(x,H-1); }
      for (let y = 0; y < H; y++) { push(0,y); push(W-1,y); }
      while (stack.length) {
        const k = stack.pop(), x = k % W, y = (k - x) / W;
        if (x > 0) push(x-1,y);
        if (x < W-1) push(x+1,y);
        if (y > 0) push(x,y-1);
        if (y < H-1) push(x,y+1);
      }
      let trapped = 0;
      for (let k = 0; k < W*H; k++) if (white[k] && !seen[k]) trapped++;
      return { trapped: Math.round(trapped/(S*S)), ink: Math.round(ink/(S*S)) };
    }""", [[v for pt in REAL_HOOK_STROKE for v in pt], 13.0])
    assert holes["ink"] > 500, holes
    assert holes["trapped"] == 0, holes
    mac.close()
    ipad.close()


# ---------------------------------------------- 读不全的白板以只读方式打开


def test_a_board_that_cannot_be_read_opens_read_only_until_unlocked(browser, tmp_path):
    """文件损坏的白板：两边都提示并停止书写；Mac 上选「仍然编辑」之后，
    先备份原文件，两边一起恢复书写。"""
    from whiteboard.store import BoardStore

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = free_port()
    store = BoardStore(config.data_dir)
    board_file = store.boards_dir / f"{store.current_id}.wbz"
    board_file.write_bytes(b"not a board")
    thread = ServerThread(config, advertise=False)
    thread.start()
    try:
        mac, ipad = open_pages(browser, thread.port)
        for page in (mac, ipad):
            page.wait_for_selector(".dialog.locked")
            assert page.evaluate("() => whiteboard.locked.reason") == "corrupt"
        # 只有 Mac（有管理权限）才有「仍然编辑」；测试里两边都是本机，所以都有
        ipad.locator(".dialog.locked button.danger").wait_for()

        # 只读时落笔不生效，也不会发给服务端（合成事件直接发到画布上，不经过提示框）
        draw(ipad, [(300, 300), (360, 340), (430, 300)])
        assert stroke_count(ipad) == 0
        assert thread.hub.board().strokes == {}
        assert board_file.read_bytes() == b"not a board"

        mac.locator(".dialog.locked button.danger").click()
        for page in (mac, ipad):
            page.wait_for_function("() => whiteboard.locked === null")
        # 在 Mac 上选了之后，iPad 上还开着的那个提示也自己收起来
        for page in (mac, ipad):
            assert page.locator(".dialog.locked").count() == 0
        backups = list((config.data_dir / "backups" / "locked").glob("*.wbz"))
        assert len(backups) == 1 and backups[0].read_bytes() == b"not a board"

        draw(ipad, [(300, 300), (360, 340), (430, 300)])
        wait_strokes(mac, 1)
        mac.close()
        ipad.close()
    finally:
        thread.stop()


# ------------------------------------------- iPad 上的输入框要能打字


TEXT_FIELDS_SELECTABLE = """
() => [...document.querySelectorAll('input:not([type=checkbox]):not([type=file]), textarea')]
  .map((el) => {
    const style = getComputedStyle(el);
    return { key: el.className || el.type, select: style.webkitUserSelect || style.userSelect };
  })
"""


def test_text_fields_can_take_typing_on_the_ipad(browser, server):
    """iPadOS 的 Safari 里，-webkit-user-select: none 的输入框弹得出键盘，却打不进字。

    页面为了挡住选择 / 放大镜手势，给所有元素一刀切设了 user-select: none，
    输入框也跟着被关掉了——白板选择界面的搜索框、改名框在 iPad 上都打不了字。
    Chromium 和桌面 WebKit 不按这条规则办事，所以只能检查样式本身。
    """
    _mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.click('button[title="白板"]')
    ipad.wait_for_selector(".board-search")
    fields = ipad.evaluate(TEXT_FIELDS_SELECTABLE)
    assert fields, "白板选择界面里应该有输入框"
    blocked = [field for field in fields if field["select"] == "none"]
    assert not blocked, f"这些输入框在 iPad 上打不了字：{blocked}"

    # 照用户的做法点一下再打字，而不是 fill() 直接塞值
    ipad.click(".board-search")
    ipad.keyboard.type("查无此板")
    ipad.wait_for_selector(".gallery-empty")
    assert ipad.evaluate("() => document.querySelector('.board-search').value") == "查无此板"


# -------------------------------------------- 在输入框里打字不该碰到白板


def open_chooser_with_a_named_board(page):
    page.click('button[title="白板"]')
    return page.wait_for_selector(".board-name")


def test_shortcuts_do_not_reach_the_board_while_typing(browser, server):
    """在改名框里按 ⌘Z 撤销的是打的字，不能把白板上的上一笔也撤掉；⌘+ ⌘- 也不能缩放白板。"""
    mac, _ipad = open_pages(browser, server.port)
    draw(mac, [(200, 200), (260, 260), (320, 210)], pointer_type="mouse")
    wait_strokes(mac, 1)
    scale = mac.evaluate("() => whiteboard.viewport.scale")

    field = open_chooser_with_a_named_board(mac)
    field.click()
    mac.keyboard.type("第三章")
    # 一个一个按、一个一个查：连着按的话 ⌘Z 和 ⌘Y 会互相抵消，看不出问题
    for combo in ("Control+z", "Meta+z", "Control+y", "Control+Shift+z", "Control+=", "Control+-", "Control+0"):
        mac.keyboard.press(combo)
        assert stroke_count(mac) == 1, combo
        assert mac.evaluate("() => whiteboard.undoStack.length") == 1, combo
        assert mac.evaluate("() => whiteboard.viewport.scale") == scale, combo

    # 输入框外面照旧生效
    mac.keyboard.press("Escape")
    mac.click('.gallery-head button[title="关闭"]')
    mac.keyboard.press("Control+z")
    wait_strokes(mac, 0)


def test_space_typed_into_a_field_does_not_turn_the_mouse_into_a_pan(browser, server):
    """在输入框里按空格不能让画布进入「按住空格拖动」的状态。"""
    mac, _ipad = open_pages(browser, server.port)
    field = open_chooser_with_a_named_board(mac)
    field.click()
    mac.keyboard.down(" ")
    assert mac.evaluate("() => whiteboard.input.spaceHeld") is False
    mac.keyboard.up(" ")


COMPOSING_ENTER = """
(selector) => {
  const field = document.querySelector(selector);
  field.focus();
  field.value = 'xian';
  field.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter', keyCode: 229, isComposing: true, bubbles: true, cancelable: true,
  }));
  return document.activeElement === field;
}
"""


def test_enter_that_picks_an_ime_candidate_does_not_commit_the_name(browser, server):
    """用拼音输入法时，回车是选候选词，不能把打了一半的名字当成最终结果提交。"""
    mac, _ipad = open_pages(browser, server.port)
    open_chooser_with_a_named_board(mac)
    assert mac.evaluate(COMPOSING_ENTER, ".board-name") is True
    mac.click(".board-folder")
    mac.wait_for_selector(".folder-new")
    assert mac.evaluate(COMPOSING_ENTER, ".folder-new") is True
    assert mac.query_selector(".dialog.folders") is not None
    assert mac.evaluate("() => whiteboard.ui.folders") == []


# ---------------------------------------- 白板选择界面：在 iPad 上照用户的做法操作
#
# 这一组在 iPad 页面上用 tap（真的触摸事件，要经过 input.js 的触摸拦截）和键盘输入，
# 不用鼠标 click，也不用 fill() 直接塞值。


def ipad_with_two_boards(browser, server):
    """Mac 上新建一块笔记，于是一共两块白板；iPad 打开白板选择界面。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    first = mac.evaluate("() => whiteboard.state.id")
    mac.click('button[title="白板"]')
    mac.click(".board-card.add")
    mac.click('.kind-tile[title^="笔记"]')
    mac.wait_for_function("() => whiteboard.state.kind === 'note'")
    second = mac.evaluate("() => whiteboard.state.id")
    ipad.wait_for_function(f"() => whiteboard.state.id === '{second}'")
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.tap('button[title="白板"]')
    ipad.wait_for_selector(f'.board-item[data-board="{first}"]')
    return mac, ipad, first, second


def test_tapping_a_card_opens_that_board(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .board-card')
    for page in (ipad, mac):
        page.wait_for_function(f"() => whiteboard.state.id === '{first}'")
    assert ipad.query_selector(".gallery") is None


def test_a_board_can_be_renamed_by_typing_on_the_ipad(browser, server):
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    field = f'.board-name[data-focus-key="name:{first}"]'
    ipad.tap(field)
    ipad.keyboard.type("线性代数")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === '{first}').name === '线性代数'")


def test_a_board_can_be_filed_and_unfiled_from_the_folder_dialog_on_the_ipad(browser, server):
    """点名字旁边的文件夹按钮，在「新文件夹」里打字新建并归进去；再选「不归类」移出来。"""
    mac, ipad, first, _second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .board-folder')
    ipad.wait_for_selector(".folder-new")
    style = ipad.evaluate("() => { const s = getComputedStyle(document.querySelector('.folder-new')); return s.webkitUserSelect || s.userSelect; }")
    assert style != "none", "iPad 上这个输入框会打不了字"
    ipad.tap(".folder-new")
    ipad.keyboard.type("数学")
    ipad.keyboard.press("Enter")
    for page in (ipad, mac):
        page.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === '{first}').folder === '数学'")

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
        page.wait_for_function("() => whiteboard.ui.folders.includes('线性代数') && !whiteboard.ui.folders.includes('数学')")
        page.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === '{first}').folder === '线性代数'")


def test_a_board_can_be_deleted_from_the_ipad(browser, server):
    """点卡片上的删除，确认之后两边都没有这块白板了，当前白板仍然有效。"""
    mac, ipad, first, second = ipad_with_two_boards(browser, server)
    ipad.tap(f'.board-item[data-board="{first}"] .del')
    ipad.wait_for_selector(".dialog.ask")
    ipad.tap('.dialog.ask button[title="确定"]')
    for page in (ipad, mac):
        page.wait_for_function(f"() => !whiteboard.ui.boards.some(b => b.id === '{first}')")
        page.wait_for_function(f"() => whiteboard.state.id === '{second}'")
    assert thread_boards(server) == [second]


def thread_boards(server):
    return [meta["id"] for meta in server.hub.store.list_metas()]


# ------------------------------------------------ 工具栏、设置、导出、缩放、平移


def test_classic_toolbar_width_changes_the_pen(browser, server):
    _mac, ipad = open_pages(browser, server.port)
    before = ipad.evaluate("() => whiteboard.tool.width")
    ipad.click('button[title="颜色与粗细"]')
    options = ipad.query_selector_all(".popover .width-opt")
    assert len(options) >= 2
    target = next(o for o in options if "active" not in (o.get_attribute("class") or ""))
    target.click()
    after = ipad.evaluate("() => whiteboard.tool.width")
    assert after != before
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    wait_strokes(ipad, 1)
    assert ipad.evaluate("() => whiteboard.state.strokes[0].w") > 0


def test_background_can_be_changed_from_the_ipad(browser, server):
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    ipad.tap('button[title="白板设置"]')
    ipad.tap('.bg-opt[title="dots"]')
    for page in (ipad, mac):
        page.wait_for_function("() => whiteboard.state.meta.background === 'dots'")


def test_the_export_button_downloads_a_png(browser, server):
    mac, _ipad = open_pages(browser, server.port)
    draw(mac, [(200, 200), (300, 260)], pointer_type="mouse")
    wait_strokes(mac, 1)
    with mac.expect_download() as info:
        mac.click('button[title="导出 PNG"]')
    download = info.value
    assert download.suggested_filename.endswith(".png")
    with open(download.path(), "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_zoom_buttons_and_shortcuts(browser, server):
    mac, _ipad = open_pages(browser, server.port)
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


def test_mac_pans_with_the_middle_button_space_and_the_wheel(browser, server):
    mac, _ipad = open_pages(browser, server.port)
    where = "() => [whiteboard.viewport.x, whiteboard.viewport.y]"

    def drag(button="left"):
        mac.mouse.move(400, 400)
        mac.mouse.down(button=button)
        for i in range(1, 6):
            mac.mouse.move(400 + i * 20, 400 + i * 10)
        mac.mouse.up(button=button)

    before = mac.evaluate(where)
    drag("middle")
    after_middle = mac.evaluate(where)
    assert after_middle != before

    mac.keyboard.down(" ")
    drag("left")
    mac.keyboard.up(" ")
    after_space = mac.evaluate(where)
    assert after_space != after_middle

    mac.mouse.move(500, 500)
    mac.mouse.wheel(0, 120)
    mac.wait_for_function(f"(was) => JSON.stringify(({where})()) !== JSON.stringify(was)", arg=after_space)
    assert stroke_count(mac) == 0  # 上面这些都只是平移，一笔都没画


def test_picker_redo_and_the_more_menu_switches(browser, server):
    """笔具盘：撤销之后重做；「更多」里的用手指绘图和自动最小化都真的起作用。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    undo = "#pk-host button[data-act='undo']"
    redo = "#pk-host button[data-act='redo']"
    draw(ipad, [(320, 320), (400, 360), (480, 320)])
    wait_strokes(mac, 1)
    ipad.wait_for_function(f"() => !document.querySelector(\"{undo}\").disabled")
    ipad.click(undo)
    wait_strokes(mac, 0)
    ipad.wait_for_function(f"() => !document.querySelector(\"{redo}\").disabled")
    ipad.click(redo)
    wait_strokes(mac, 1)

    # 用手指绘图：打开之后手指落笔就是画线
    assert ipad.evaluate("() => whiteboard.input.fingerDraw") is False
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-toggle='fingerDraws']")
    ipad.wait_for_function("() => whiteboard.input.fingerDraw === true")
    # 抬笔后 0.5 秒内手指一律当手掌不理（手掌屏蔽），等这个窗口过去再用手指写
    ipad.wait_for_function("() => !whiteboard.input.penActive()")
    draw(ipad, [(300, 520), (380, 560), (460, 520)], pointer_type="touch", pointer_id=5)
    wait_strokes(mac, 2)

    # 自动最小化：打开之后一落笔工具盘就收起来
    ipad.keyboard.press("Escape")
    ipad.wait_for_timeout(500)  # vendor 会吞掉紧接着的点击
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-toggle='autoMin']")
    ipad.keyboard.press("Escape")
    draw(ipad, [(300, 620), (380, 660)])
    ipad.wait_for_function("() => whiteboard.ui.pk.state === 'minimized'")


# ------------------------------------------------------ 只读白板的几种情况


def serve_locked(tmp_path, content):
    """起一个服务端，当前那块白板的文件换成 content 的内容。"""
    from whiteboard.store import BoardStore

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = free_port()
    store = BoardStore(config.data_dir)
    path = store.boards_dir / f"{store.current_id}.wbz"
    path.write_bytes(content(path))
    thread = ServerThread(config, advertise=False)
    thread.start()
    return thread, path


def newer_version(path):
    import json
    import zlib

    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["v"] = 99
    return zlib.compress(json.dumps(payload).encode("utf-8"))


def test_keep_read_only_really_keeps_it_read_only(browser, tmp_path):
    thread, path = serve_locked(tmp_path, lambda _p: b"damaged")
    try:
        mac, _ipad = open_pages(browser, thread.port)
        mac.wait_for_selector(".dialog.locked")
        mac.click('.dialog.locked button[title="保持只读"]')
        assert mac.query_selector(".dialog.locked") is None
        draw(mac, [(300, 300), (380, 340)], pointer_type="mouse")
        assert stroke_count(mac) == 0
        assert thread.hub.board().locked
        assert path.read_bytes() == b"damaged"
    finally:
        thread.stop()


def test_a_board_from_a_newer_version_says_so(browser, tmp_path):
    thread, path = serve_locked(tmp_path, newer_version)
    try:
        mac, _ipad = open_pages(browser, thread.port)
        mac.wait_for_selector(".dialog.locked")
        text = mac.inner_text(".dialog.locked")
        assert "更新版本" in text
        assert mac.evaluate("() => whiteboard.locked.reason") == "newer"
    finally:
        thread.stop()


def test_a_device_without_manage_cannot_unlock(browser, tmp_path):
    """没有「管理白板」权限的设备只看得到说明，没有「仍然编辑」。"""
    thread, _path = serve_locked(tmp_path, lambda _p: b"damaged")
    try:
        _mac, ipad = open_pages(browser, thread.port)
        serve_with_perms(ipad, "clear export settings")
        ipad.wait_for_selector(".dialog.locked")
        assert ipad.query_selector(".dialog.locked button.danger") is None
        assert "需要在 Mac 上决定" in ipad.inner_text(".dialog.locked")
    finally:
        thread.stop()


def test_two_fingers_pinch_and_pan_on_a_touch_device(browser, server):
    """两根手指分开是放大、一起挪是平移，都不留笔迹。"""
    _mac, ipad = open_pages(browser, server.port)
    scale = "() => whiteboard.viewport.scale"
    where = "() => [whiteboard.viewport.x, whiteboard.viewport.y]"
    start = ipad.evaluate(scale)

    def fingers(steps):
        (a0, b0) = steps[0]
        ipad.evaluate(FIRE, ["pointerdown", *a0, "touch", 21, 0.5])
        ipad.evaluate(FIRE, ["pointerdown", *b0, "touch", 22, 0.5])
        for a, b in steps[1:]:
            ipad.evaluate(FIRE, ["pointermove", *a, "touch", 21, 0.5])
            ipad.evaluate(FIRE, ["pointermove", *b, "touch", 22, 0.5])
        a, b = steps[-1]
        ipad.evaluate(FIRE, ["pointerup", *a, "touch", 21, 0])
        ipad.evaluate(FIRE, ["pointerup", *b, "touch", 22, 0])

    # 分开：放大
    fingers([((500 - d, 400), (600 + d, 400)) for d in range(0, 121, 20)])
    zoomed = ipad.evaluate(scale)
    assert zoomed > start * 1.3

    # 一起往右下挪：平移，比例不变
    before = ipad.evaluate(where)
    fingers([((400 + d, 400 + d), (500 + d, 400 + d)) for d in range(0, 101, 20)])
    assert ipad.evaluate(where) != before
    assert abs(ipad.evaluate(scale) - zoomed) < zoomed * 0.05
    assert stroke_count(ipad) == 0


# ---------------------------------------------------------------- 嵌入手写板（docs/embed.md）

EMBED_FIRE = """
([selector, type, x, y, pointerType, pointerId, pressure]) => {
  const stage = document.querySelector(selector);
  const rect = stage.getBoundingClientRect();
  stage.dispatchEvent(new PointerEvent(type, {
    clientX: rect.left + x, clientY: rect.top + y, pointerType, pointerId, pressure,
    buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
  }));
}
"""


def install_demo_app(server):
    import shutil

    target = server.config.data_dir / "apps" / "demo"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples" / "apps" / "demo", target)


def open_demo(browser, port, path="/apps/demo/"):
    page = browser.new_context(
        viewport={"width": 1000, "height": 900}, user_agent=TABLET_UA, has_touch=True
    ).new_page()
    page.goto(f"http://127.0.0.1:{port}{path}")
    page.wait_for_function("() => window.pad && pad.net.status === 'online'")
    return page


def draw_embedded(page, points, selector="#pad .inkpad-stage", pointer_id=1):
    """在嵌入的手写板上画一笔；坐标相对手写板的左上角。"""
    page.evaluate(EMBED_FIRE, [selector, "pointerdown", *points[0], "pen", pointer_id, 0.6])
    for x, y in points[1:]:
        page.evaluate(EMBED_FIRE, [selector, "pointermove", x, y, "pen", pointer_id, 0.6])
    page.evaluate(EMBED_FIRE, [selector, "pointerup", *points[-1], "pen", pointer_id, 0])


def test_an_embedded_pad_syncs_with_the_mac(browser, server):
    """刷题这类页面嵌入手写板：iPad 上写的内容进 Mac 上的同一块白板，反过来也一样。"""
    install_demo_app(server)
    mac, _ = open_pages(browser, server.port)
    pad = open_demo(browser, server.port)

    meta = pad.evaluate("() => pad.state.meta")
    assert meta["id"] == "demo-q1" and meta["app"] == "demo" and meta["folder"] == "示例"
    assert meta["underlay"] == {"src": "/apps/demo/question.svg", "width": 800}
    # 底图按宽度铺满手写板
    assert pad.evaluate("() => Math.abs(pad.viewport.scale * 800 + 32 - pad.renderer.viewW) < 1")

    draw_embedded(pad, [(100, 300), (160, 340), (230, 300), (300, 360)])
    pad.wait_for_function("() => pad.state.strokes.length === 1 && pad.net.outbox.length === 0")

    # Mac 没有被切走；在白板列表里能看到这块白板，打开它就看到刚写的内容
    assert mac.evaluate("() => whiteboard.state.id") != "demo-q1"
    mac.wait_for_function("() => whiteboard.ui.boards.some(b => b.id === 'demo-q1')")
    mac.evaluate("() => whiteboard.net.send({ t: 'sel', board: 'demo-q1' })")
    mac.wait_for_function("() => whiteboard.state.id === 'demo-q1'")
    wait_strokes(mac, 1)

    draw(mac, [(300, 500), (360, 540), (420, 500)], pointer_type="mouse")
    pad.wait_for_function("() => pad.state.strokes.length === 2")


def test_an_embedded_pad_leaves_the_rest_of_the_page_alone(browser, server):
    """手写板只拦自己区域里的触摸：页面上的输入框照常点得中、打得了字。"""
    install_demo_app(server)
    pad = open_demo(browser, server.port)
    pad.tap("#answer")
    pad.keyboard.type("x=2 或 x=3")
    assert pad.evaluate("() => document.getElementById('answer').value") == "x=2 或 x=3"
    pad.tap("button[data-tool='eraser']")
    assert pad.evaluate("() => pad.tool.tool") == "eraser"


def test_two_pads_on_one_page_keep_their_own_boards(browser, server):
    install_demo_app(server)
    pad = open_demo(browser, server.port)
    pad.evaluate(
        """async () => {
          const box = document.createElement('div');
          box.id = 'second';
          box.style.height = '300px';
          document.querySelector('main').append(box);
          const { createInkPad } = await import('/sdk/inkpad.js');
          window.pad2 = createInkPad(box, { app: 'demo', board: 'demo-q2', name: '第 2 题' });
        }"""
    )
    pad.wait_for_function("() => pad2.net.status === 'online'")
    assert pad.evaluate("() => pad.clientId !== pad2.clientId")
    draw_embedded(pad, [(80, 80), (140, 120), (200, 80)], selector="#second .inkpad-stage", pointer_id=2)
    pad.wait_for_function("() => pad2.state.strokes.length === 1 && pad2.net.outbox.length === 0")
    assert pad.evaluate("() => pad.state.strokes.length") == 0
    assert pad.evaluate("() => pad2.state.id") == "demo-q2"

    pad.evaluate("() => pad2.destroy()")
    assert pad.evaluate("() => document.querySelector('#second .inkpad-stage')") is None


def test_the_ipad_opens_the_chosen_home_app(browser, server):
    install_demo_app(server)
    server.config.ipad_home = "demo"
    pad = open_demo(browser, server.port, path="/?role=ipad")
    assert pad.url.endswith("/apps/demo/")
    # 应用里的「白板」链接带 home=…，回到白板不再跳转
    pad.click("header a")
    pad.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    assert "/apps/" not in pad.url
    mac = browser.new_page()
    mac.goto(f"http://127.0.0.1:{server.port}/?role=mac")
    assert "/apps/" not in mac.url

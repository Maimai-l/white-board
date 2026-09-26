"""端到端测试：真的开两个浏览器页面（Mac 端 + iPad 端）互相同步。

没装 Playwright 或找不到 Chromium 时自动跳过。
"""

from __future__ import annotations

import math
import re
import time
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


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        path = chromium_path()
        try:
            instance = pw.chromium.launch(executable_path=path) if path else pw.chromium.launch()
        except Exception as exc:  # pragma: no cover - 环境没有浏览器
            pytest.skip(f"没有可用的 Chromium：{exc}")
        yield instance
        instance.close()


@pytest.fixture
def server(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = 8000 + (int(time.time() * 1000) % 2000)
    thread = ServerThread(config, advertise=False)
    thread.start()
    yield thread
    thread.stop()


def open_pages(browser, port, picker=False):
    """开两个页面。iPad 上笔具盘是默认的，这里默认关掉，让用例明说自己要哪一条。"""
    mac = browser.new_page(viewport={"width": 1200, "height": 800})
    mac.goto(f"http://127.0.0.1:{port}/?role=mac")
    ipad = browser.new_context(
        viewport={"width": 1180, "height": 820}, user_agent=IPAD_UA, has_touch=True
    ).new_page()
    ipad.add_init_script(
        "try { localStorage.setItem('whiteboard.picker', '%s'); } catch (e) {}" % ("1" if picker else "0")
    )
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

    # 停下来之后总得写进去
    ipad.wait_for_timeout(6000)
    assert ipad.evaluate("() => window.__writes.length") > 0
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


def test_picker_is_the_default_on_touch_devices(browser, server):
    """笔具盘现在是触摸设备上的默认工具栏；关掉之后记在本机，Mac 一直是普通那条。"""
    ctx = browser.new_context(
        viewport={"width": 1180, "height": 820}, user_agent=IPAD_UA, has_touch=True
    )
    ipad = ctx.new_page()
    ipad.goto(f"http://127.0.0.1:{server.port}/?role=ipad")  # 本机什么都没存过
    ipad.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    assert ipad.is_hidden("#toolbar")

    # 在「更多」里换回普通工具栏，这个选择要记住
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-wb='leave']")
    ipad.wait_for_selector("#toolbar", state="visible")
    ipad.reload()
    ipad.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    ipad.wait_for_selector("#toolbar", state="visible")
    assert ipad.query_selector("#pk-host") is None

    # Mac 窗口没有触摸，一直是普通工具栏，连那个开关都不给
    mac = browser.new_page(viewport={"width": 1200, "height": 800})
    mac.goto(f"http://127.0.0.1:{server.port}/?role=mac")
    mac.wait_for_function("() => window.whiteboard && whiteboard.net.status === 'online'")
    assert mac.evaluate("() => whiteboard.ui.picker") is False
    mac.click('button[title="颜色与粗细"]')
    assert mac.locator(".popover .beta-row").count() == 0
    mac.close()
    ipad.close()
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
    """笔具盘里橡皮那个面板的「对象 / 像素」二选一，两边是同一个设置。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    ipad.wait_for_selector("#pk-host .pk-picker")
    ipad.click('#pk-host [data-tool="eraser"]')
    ipad.wait_for_function("() => whiteboard.tool.tool === 'eraser'")
    ipad.wait_for_timeout(500)  # vendor 会吞掉拖动结束后一小段时间内的点击
    ipad.click('#pk-host [data-tool="eraser"]')  # 再点一次弹出面板
    ipad.wait_for_selector('#pk-host [data-emode="pixel"]')
    ipad.click('#pk-host [data-emode="pixel"]')
    ipad.wait_for_function("() => whiteboard.tool.eraserMode === 'pixel'")

    # 换回普通工具栏，那边的二选一要跟着
    ipad.evaluate("() => whiteboard.ui.setPicker(false)")
    ipad.wait_for_selector("#toolbar", state="visible")
    ipad.click('button[title="颜色与粗细"]')
    assert ipad.evaluate(
        "() => document.querySelector('.popover .seg button.is-on').textContent"
    ) == "像素橡皮擦"
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
    """从颜色面板里打开笔具盘（beta），等 PencilKit 那条工具盘装好。"""
    page.click('button[title="颜色与粗细"]')
    page.click(".beta-row input")
    page.wait_for_selector("#pk-host .pk-picker", timeout=20000)
    page.wait_for_selector('#pk-host [data-tool="pen"]')


def pk_state(page):
    return page.evaluate("() => whiteboard.tool")


def test_pencilkit_picker_drives_the_board(browser, server):
    """笔具盘（beta）：选工具、选颜色、改粗细都落到白板上，画布照常能写。"""
    mac, ipad = open_pages(browser, server.port)
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


def test_pencilkit_picker_undo_clear_and_exit(browser, server):
    """撤销按钮跟着白板的撤销栈亮灭，清屏和「换回普通工具栏」在更多菜单里。"""
    mac, ipad = open_pages(browser, server.port)
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

    # 换回普通工具栏
    ipad.click("#pk-host button[data-act='more']")
    ipad.click("#pk-host .pk-pop [data-wb='leave']")
    ipad.wait_for_selector('button[title="颜色与粗细"]', state="visible")
    assert ipad.query_selector("#pk-host") is None
    mac.close()
    ipad.close()


def test_pencilkit_picker_docks_and_minimizes(browser, server):
    """拖握把换边，丢进角落缩成圆，点圆展开；位置本身由那份实现自己管。"""
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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
    _mac, ipad = open_pages(browser, server.port)
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

    for width, cut in ((4.0, 0), (20.0, 0), (20.0, 3)):
        cmds = [list(c) for c in inkpdf.outline_path(points, "pen", width, cut)]
        flat = [v for point in points for v in point]
        pct, area = ipad.evaluate(compare, [cmds, flat, width, cut])
        assert area > 8000, (width, cut, area)  # 真的画上去了
        # 只剩抗锯齿边缘的差别：两条轮廓之间差一个像素都会让这个数大起来
        assert pct < 1.2, (width, cut, pct)

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

    真笔才触发得了的问题（压感沿笔画变化、倾角一直在动、一帧二十几个合并采样点）
    在开发机上敲不出来。录一次带回来回放，才谈得上在这里复现和验证。
    """
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300 + i * 4) for i in range(40)])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")

    data = ipad.evaluate(RECORD_ERASE, [200, 380, 420, 380, 24, 0.9])
    assert data["events"], data
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
    text = ipad.inner_text("#perf")
    assert build in text, (build, text)
    mac.close()
    ipad.close()

"""端到端测试：真的开两个浏览器页面（Mac 端 + iPad 端）互相同步。

没装 Playwright 或找不到 Chromium 时自动跳过。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

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
    for candidate in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
        return str(candidate)
    return None


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


def open_pages(browser, port):
    mac = browser.new_page(viewport={"width": 1200, "height": 800})
    mac.goto(f"http://127.0.0.1:{port}/?role=mac")
    ipad = browser.new_context(
        viewport={"width": 1180, "height": 820}, user_agent=IPAD_UA, has_touch=True
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
    ipad.click('button[title="清屏"]')
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
    mac.click(".board-tile.add")
    ipad.wait_for_function(f"() => whiteboard.state.id !== '{first_board}'")
    assert stroke_count(ipad) == 0  # 新白板是空的

    # 背景与尺寸改动会同步到 iPad
    mac.click('button[title="白板设置"]')
    mac.click('.bg-opt[title="dots"]')
    ipad.wait_for_function("() => whiteboard.state.meta.background === 'dots'")
    mac.click(".size-grid .size-cell:nth-child(12)")  # 第二行第五列 → 5 × 2
    ipad.wait_for_function("() => whiteboard.state.meta.cols === 5 && whiteboard.state.meta.rows === 2")
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

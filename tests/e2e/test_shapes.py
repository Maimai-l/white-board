"""形状识别（docs/backlog/002-shape-recognition-lines.md）：一笔写完停住半秒，变成直线。

笔是在页面里合成的指针事件。停顿用事件自己的时间戳模拟（``timeStamp`` 改成指定值），
用例不用真的等半秒；只有「笔还按着就变直」那一条要等真实时间，用的是条件等待。
"""

from __future__ import annotations

from e2e.support import (
    PICKER,
    TABLET_UA,
    enable_pk_picker,
    more_checked,
    more_pick,
    new_page,
    open_pages,
    reload,
    wait_online,
    wait_strokes,
)

UNDO = "#pk-host button[data-act='undo']"
REDO = "#pk-host button[data-act='redo']"
MORE = "#pk-host button[data-act='more']"
SWITCH = "#pk-host .pk-pop[data-open] [data-toggle='shapeLines']"

# 一笔：[[x, y, 相对落笔的毫秒数], ...]，第一个落笔、最后一个抬笔，全在一次 evaluate
# 里派发完——中间没有渲染帧，按真实时间补判停顿的那条路不会插进来。
PEN = """
(samples) => {
  const stage = document.getElementById('stage');
  const t0 = performance.now();
  samples.forEach(([x, y, dt], i) => {
    const type = i === 0 ? 'pointerdown' : i === samples.length - 1 ? 'pointerup' : 'pointermove';
    const up = type === 'pointerup';
    const event = new PointerEvent(type, {
      clientX: x, clientY: y, pointerType: 'pen', pointerId: 1, pressure: up ? 0 : 0.6,
      buttons: up ? 0 : 1, bubbles: true, cancelable: true, isPrimary: true,
    });
    // 设备给的事件时间：构造函数不收 timeStamp，只能盖在这个事件对象上
    Object.defineProperty(event, 'timeStamp', { value: t0 + dt });
    stage.dispatchEvent(event);
  });
}
"""

# 最后一笔：点数、各点到起止连线的最远距离（世界单位）、起点、终点
SHAPE = """
() => {
  const s = whiteboard.state.strokes[whiteboard.state.strokes.length - 1];
  const p = s.p, n = p.length / 3;
  const x0 = p[0], y0 = p[1], x1 = p[(n - 1) * 3], y1 = p[(n - 1) * 3 + 1];
  const len = Math.hypot(x1 - x0, y1 - y0);
  let dev = 0;
  for (let i = 0; i < n; i++) {
    dev = Math.max(dev, Math.abs((p[i * 3] - x0) * (y1 - y0) - (p[i * 3 + 1] - y0) * (x1 - x0)) / len);
  }
  return { id: s.id, n, dev, len, start: [x0, y0], end: [x1, y1] };
}
"""

# 屏幕坐标在这一页上对应的世界坐标
WORLD = """
([x, y]) => {
  const rect = document.getElementById('stage').getBoundingClientRect();
  return whiteboard.viewport.toWorld(x - rect.left, y - rect.top);
}
"""

START = (300, 260)
END = (620, 330)


def curved_line(hold_ms):
    """一条略弯的线（中间鼓出约 4%），画完在终点附近停 ``hold_ms`` 毫秒再抬笔。"""
    samples = []
    count = 32
    for i in range(count):
        t = i / (count - 1)
        bulge = 4 * 14 * t * (1 - t)  # 中点偏出 14 像素，线长约 330
        samples.append([START[0] + (END[0] - START[0]) * t, START[1] + (END[1] - START[1]) * t + bulge, i * 10])
    last = samples[-1][2]
    # 停住时笔尖在一两个像素里晃，事件照样在来
    for k, (dx, dy) in enumerate([(1, 0), (0, 1), (-1, 0), (0, 0)]):
        samples.append([END[0] + dx, END[1] + dy, last + hold_ms * (k + 1) / 5])
    samples.append([END[0], END[1], last + hold_ms])
    return samples


def pen(page, samples):
    page.evaluate(PEN, samples)


def open_menu(ipad):
    if not ipad.query_selector("#pk-host .pk-pop[data-open]"):
        ipad.click(MORE)
    ipad.wait_for_selector(SWITCH)


def close_menu(ipad):
    ipad.click(MORE)
    ipad.wait_for_function("() => !document.querySelector('#pk-host .pk-pop[data-open]')")


def set_shapes(ipad, on):
    """在笔具盘「更多」里把形状识别拨到 ``on``。"""
    open_menu(ipad)
    if ipad.get_attribute(f"{SWITCH} .pk-switch", "aria-checked") != str(on).lower():
        ipad.click(SWITCH)
    ipad.wait_for_function(
        f"() => document.querySelector(\"{SWITCH} .pk-switch\").getAttribute('aria-checked') === '{str(on).lower()}'"
    )
    close_menu(ipad)


def assert_straight(page, ipad):
    """``page`` 上那一笔是直线，两端是在 iPad 上落笔、抬笔的位置。"""
    shape = page.evaluate(SHAPE)
    assert shape["dev"] < 0.01, f"没有变直：离连线最远 {shape['dev']:.2f}"
    for got, want in ((shape["start"], ipad.evaluate(WORLD, START)), (shape["end"], ipad.evaluate(WORLD, END))):
        assert abs(got[0] - want[0]) < 0.01 and abs(got[1] - want[1]) < 0.01, (got, want)
    return shape


def assert_handwritten(page):
    shape = page.evaluate(SHAPE)
    # 手写的那一笔中间鼓出 14 个屏幕像素，世界单位里也是好几个单位
    assert shape["dev"] > 5, f"手写的线不该变直：离连线最远 {shape['dev']:.2f}"
    return shape


def test_a_paused_stroke_becomes_a_straight_line_on_both_devices(browser, server):
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    set_shapes(ipad, True)

    pen(ipad, curved_line(600))
    wait_strokes(ipad, 1)
    wait_strokes(mac, 1)
    line = assert_straight(ipad, ipad)
    assert_straight(mac, ipad)
    # 两边是同一笔，粗细变化（压感）也还在
    assert mac.evaluate(SHAPE)["id"] == line["id"]


def test_a_short_pause_keeps_the_handwriting(browser, server):
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    set_shapes(ipad, True)

    pen(ipad, curved_line(300))
    wait_strokes(ipad, 1)
    wait_strokes(mac, 1)
    assert_handwritten(ipad)
    assert_handwritten(mac)


def test_the_switch_turns_it_off(browser, server):
    """默认关着；打开之后记在本机，刷新还在；关上之后停住也不变直。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    pen(ipad, curved_line(600))
    wait_strokes(mac, 1)
    assert_handwritten(mac)

    set_shapes(ipad, True)
    reload(ipad)
    enable_pk_picker(ipad)
    open_menu(ipad)
    assert ipad.get_attribute(f"{SWITCH} .pk-switch", "aria-checked") == "true"
    close_menu(ipad)

    set_shapes(ipad, False)
    pen(ipad, curved_line(600))
    wait_strokes(ipad, 2)
    wait_strokes(mac, 2)
    assert_handwritten(ipad)
    assert_handwritten(mac)


def test_undo_brings_back_the_handwriting_on_both_devices(browser, server):
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    set_shapes(ipad, True)
    pen(ipad, curved_line(600))
    wait_strokes(mac, 1)
    assert_straight(mac, ipad)

    # 撤销一次：回到手写的那一笔，另一端跟着换回去
    ipad.wait_for_function(f'() => !document.querySelector("{UNDO}").disabled')
    ipad.click(UNDO)
    mac.wait_for_function(f"() => whiteboard.state.strokes.length === 1 && ({SHAPE})().dev > 5")
    hand = assert_handwritten(ipad)
    assert_handwritten(mac)
    assert mac.evaluate(SHAPE)["id"] == hand["id"]

    # 再撤销一次：这一笔没了
    ipad.click(UNDO)
    wait_strokes(ipad, 0)
    wait_strokes(mac, 0)

    # 重做两次：先回来手写的，再变成直线
    ipad.wait_for_function(f'() => !document.querySelector("{REDO}").disabled')
    ipad.click(REDO)
    mac.wait_for_function(f"() => whiteboard.state.strokes.length === 1 && ({SHAPE})().dev > 5")
    ipad.click(REDO)
    mac.wait_for_function(f"() => whiteboard.state.strokes.length === 1 && ({SHAPE})().dev < 0.01")
    assert_straight(mac, ipad)


def test_holding_still_straightens_before_the_pen_lifts(browser, server):
    """和 iPad 自带的一样：笔还按着、停住半秒就变直，另一端看到的实时笔迹也跟着变直。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    set_shapes(ipad, True)

    samples = curved_line(0)[:32]
    # 真实时间：落笔、画完，然后笔停在终点不动，什么事件也不发
    ipad.evaluate(
        """(samples) => {
          const stage = document.getElementById('stage');
          samples.forEach(([x, y], i) => stage.dispatchEvent(new PointerEvent(i ? 'pointermove' : 'pointerdown', {
            clientX: x, clientY: y, pointerType: 'pen', pointerId: 1, pressure: 0.6, buttons: 1,
            bubbles: true, cancelable: true, isPrimary: true })));
        }""",
        samples,
    )
    # 对端正在显示的实时笔迹变成了直线（条件等待，大约半秒）
    mac.wait_for_function(
        """() => {
          const s = [...whiteboard.remoteLive.values()][0];
          if (!s || s.p.length < 6) return false;
          const p = s.p, n = p.length / 3, x0 = p[0], y0 = p[1], x1 = p[n * 3 - 3], y1 = p[n * 3 - 2];
          const len = Math.hypot(x1 - x0, y1 - y0);
          for (let i = 0; i < n; i++) {
            if (Math.abs((p[i * 3] - x0) * (y1 - y0) - (p[i * 3 + 1] - y0) * (x1 - x0)) / len > 0.01) return false;
          }
          return true;
        }""",
        timeout=4000,
    )
    assert stroke_count_both(mac, ipad) == (0, 0)  # 还没抬笔，什么都没提交

    ipad.evaluate(
        """([x, y]) => document.getElementById('stage').dispatchEvent(new PointerEvent('pointerup', {
          clientX: x, clientY: y, pointerType: 'pen', pointerId: 1, pressure: 0, buttons: 0,
          bubbles: true, cancelable: true, isPrimary: true }))""",
        list(END),
    )
    wait_strokes(mac, 1)
    assert_straight(mac, ipad)


def stroke_count_both(mac, ipad):
    return tuple(page.evaluate("() => whiteboard.state.strokes.length") for page in (mac, ipad))


def test_the_plain_toolbar_on_other_tablets_has_the_switch(browser, server):
    """别的平板用的是普通工具栏：开关和手指书写一起放在更多菜单里，打着勾就是开着。"""
    tablet = new_page(browser, user_agent=TABLET_UA, touch=True)
    tablet.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(tablet)
    assert tablet.query_selector(PICKER) is None
    assert more_checked(tablet, "形状识别") is False
    more_pick(tablet, "形状识别")
    assert more_checked(tablet, "形状识别") is True

    pen(tablet, curved_line(600))
    wait_strokes(tablet, 1)
    assert_straight(tablet, tablet)

    # 记在本机：刷新之后还开着
    reload(tablet)
    assert more_checked(tablet, "形状识别") is True

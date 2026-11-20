"""两台设备之间的同步：书写、撤销重做、刷新、断线重连、超长笔画。"""

from __future__ import annotations

from e2e.support import (
    draw,
    open_mac,
    open_pages,
    open_touch,
    reload,
    screen_of_first_stroke,
    stroke_count,
    wait_seq,
    wait_strokes,
)

# 慢慢写一笔，一直写到「该写盘了」（防抖的计时到点，persistWhenIdle 被叫到）才抬笔。
# 返回 [落笔时刻, 抬笔时刻, 写到一半时写盘是不是到点了]。每 50 ms 挪一下是笔的节奏
WRITE_UNTIL_SAVE_IS_DUE = """
async () => {
  const init = { pointerId: 3, pressure: 0.6 };
  const start = performance.now();
  const due = () => window.__due.some((t) => t > start);
  let x = 300;
  __t.fire('pointerdown', x, 500, init);
  while (!due() && performance.now() - start < 15000) {
    await new Promise((done) => setTimeout(done, 50));
    x += 2;
    __t.fire('pointermove', x, 500 + (x % 40), init);
  }
  __t.fire('pointerup', x, 500, { ...init, pressure: 0 });
  return [start, performance.now(), due()];
}
"""


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
    x, y = screen_of_first_stroke(mac)
    draw(mac, [(x, y), (x + 6, y + 4)], pointer_type="mouse")
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
    x, y = screen_of_first_stroke(ipad)
    draw(ipad, [(x, y), (x + 6, y + 4)])
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


def test_keyboard_redo(browser, server):
    mac = open_mac(browser, server.port)
    draw(mac, [(300, 300), (380, 340)], pointer_type="mouse")
    wait_strokes(mac, 1)
    mac.keyboard.press("Control+z")
    wait_strokes(mac, 0)
    mac.keyboard.press("Control+Shift+z")
    wait_strokes(mac, 1)


def test_strokes_survive_a_reload_without_id_collisions(browser, server):
    """重新打开页面之后再写，笔画不能因为 id 撞车而在抬笔瞬间消失。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 340), (460, 300)])
    draw(ipad, [(300, 420), (380, 460), (460, 420)])
    wait_strokes(mac, 2)

    reload(ipad)
    wait_strokes(ipad, 2)

    draw(ipad, [(300, 540), (380, 580), (460, 540)])
    draw(ipad, [(300, 640), (380, 680), (460, 640)])
    wait_strokes(ipad, 4)
    wait_strokes(mac, 4)
    ids = ipad.evaluate("() => whiteboard.state.strokes.map(s => s.id)")
    assert len(set(ids)) == 4, f"笔画 id 撞车了：{ids}"


def test_cache_writes_never_land_on_a_stroke(browser, server):
    """写 IndexedDB 会卡主线程，必须等书写停下来之后才写。"""
    ipad = open_touch(browser, server.port)
    ipad.evaluate(
        """() => {
          window.__writes = [];
          window.__due = [];
          const original = whiteboard.persist.bind(whiteboard);
          whiteboard.persist = () => { window.__writes.push(performance.now()); original(); };
          const idle = whiteboard.persistWhenIdle.bind(whiteboard);
          whiteboard.persistWhenIdle = () => { window.__due.push(performance.now()); idle(); };
        }"""
    )
    draw(ipad, [(300, 300), (360, 340), (420, 300)])

    # 第一笔之后过一阵就该写盘了：第二笔一直写到这个时刻之后，写盘必须让开
    start, end, due = ipad.evaluate(WRITE_UNTIL_SAVE_IS_DUE)
    assert due, "第二笔写了很久，写盘的时刻一直没到"
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


def test_offline_drawing_is_flushed_after_server_restart(browser, server, make_server):
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, [(300, 300), (380, 350)])
    wait_strokes(mac, 1)

    server.stop()
    ipad.wait_for_function("() => whiteboard.net.status === 'offline'")

    # 断线期间照常书写：本地立刻可见，操作进入待发队列
    draw(ipad, [(400, 400), (480, 460), (520, 400)])
    assert stroke_count(ipad) == 2
    assert ipad.evaluate("() => whiteboard.net.outbox.length") >= 1

    make_server(port=server.port)  # 同一个数据目录、同一个端口
    ipad.wait_for_function("() => whiteboard.net.status === 'online'", timeout=15000)
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0", timeout=15000)
    assert stroke_count(ipad) == 2  # 重启后内容没有变成空白
    wait_strokes(mac, 2)


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
    wait_seq(mac, ipad)

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

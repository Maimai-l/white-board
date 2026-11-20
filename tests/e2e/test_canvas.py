"""画布的形状：大白板无限延伸；笔记宽度固定一页、只往下长，上下翻有惯性。"""

from __future__ import annotations

from e2e.support import draw, new_board, open_pages, wait_strokes

# 带时间间隔的一串触摸移动：快速划过有惯性，慢慢拖没有
FLICK = """
async ([points, pointerType, delay]) => {
  const init = { pointerType, pointerId: 77 };
  __t.fire('pointerdown', points[0][0], points[0][1], init);
  for (const [x, y] of points.slice(1)) {
    await new Promise((resolve) => setTimeout(resolve, delay || 8));
    __t.fire('pointermove', x, y, init);
  }
  const [x, y] = points[points.length - 1];
  __t.fire('pointerup', x, y, init);
}
"""

PAPER_EDGES = (
    "() => { const v = whiteboard.viewport; const l = whiteboard.state.limits;"
    "return [v.x + l.x0 * v.scale, v.x + l.x1 * v.scale, whiteboard.renderer.viewW]; }"
)

# 等页面画完 n 帧：惯性滑动每帧走一步，不动了就是滑完了
FRAMES = """
(n) => new Promise((done) => {
  const next = () => (n-- > 0 ? requestAnimationFrame(next) : done());
  next();
})
"""

# 一帧一帧地看，等视口的 y 连着 n 帧不变（惯性滑动已经停了：滑行时每帧至少走零点几个
# 像素），返回是否等到了。最多等 timeout 毫秒
SETTLE_Y = """
async ([n, timeout]) => {
  const end = performance.now() + timeout;
  let last = whiteboard.viewport.y;
  let still = 0;
  while (still < n) {
    if (performance.now() > end) return false;
    await new Promise((done) => requestAnimationFrame(done));
    const y = whiteboard.viewport.y;
    still = y === last ? still + 1 : 0;
    last = y;
  }
  return true;
}
"""

SET_SCALE = (
    "([s]) => { whiteboard.viewport.scale = s; whiteboard.clampView();"
    "whiteboard.renderer.requestFull(); }"
)


def note_on_both(mac, ipad):
    """Mac 上新建一块笔记，等 iPad 也切过去、视图停稳。"""
    board = new_board(mac, "笔记")
    ipad.wait_for_function(
        "(id) => whiteboard.state.id === id && whiteboard.state.kind === 'note' && whiteboard.viewAnim === 0",
        arg=board,
    )


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
    ipad.evaluate(FRAMES, 10)  # 画过几帧，要拉回边界的话这时已经拉回去了
    assert ipad.evaluate("() => __t.view()") == moved

    # 缩放仍然有上下限
    limits = ipad.evaluate(
        "async () => { const m = await import('/inksync/viewport.js');"
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


def test_note_boards_only_extend_downwards(browser, server):
    """笔记：宽度固定成一页，纸外不落笔、也划不出去；大白板不受这些约束。"""
    mac, ipad = open_pages(browser, server.port)
    new_board(mac, "笔记")
    ipad.wait_for_function("() => whiteboard.state.kind === 'note'")
    assert ipad.evaluate("() => whiteboard.state.limits.x1") == 1000

    # 纸内照常书写
    ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.scale = 0.5;"
        "v.centerOn(500, 400, whiteboard.renderer.viewW, whiteboard.renderer.viewH);"
        "whiteboard.clampView(); whiteboard.renderer.requestFull(); }"
    )
    x, y = ipad.evaluate("() => whiteboard.viewport.toScreen(500, 400)")
    draw(ipad, [(x - 60, y), (x, y + 30), (x + 60, y)])
    wait_strokes(mac, 1)

    # 纸外不落笔，而是拖动画布
    read = "() => [whiteboard.state.strokes.length, whiteboard.viewport.y]"
    before = ipad.evaluate(read)
    draw(ipad, [(40, 560), (50, 440), (60, 320)], pointer_id=9)  # 往上拖 = 向下翻页
    after = ipad.evaluate(read)
    assert after[0] == before[0], "纸外不应该留下笔迹"
    assert after[1] != before[1], "纸外拖动应该滚动页面"

    # 横向划不出纸外：纸比视口窄时始终居中
    ipad.evaluate("() => { whiteboard.viewport.panBy(3000, 0); whiteboard.clampView(); }")
    left, right, width = ipad.evaluate(PAPER_EDGES)
    assert left > 0 and right < width

    # 页首之上翻不过去
    top = ipad.evaluate(
        "() => { const v = whiteboard.viewport; v.panBy(0, 5000); whiteboard.clampView(); return v.y; }"
    )
    assert top <= ipad.evaluate("() => whiteboard.renderer.viewH * 0.1") + 1


def test_flick_keeps_scrolling_with_momentum(browser, server):
    """笔记上下翻要有惯性：松手之后还会继续滑一段再停。"""
    mac, ipad = open_pages(browser, server.port)
    note_on_both(mac, ipad)
    y = "() => whiteboard.viewport.y"

    points = [[590, 700 - i * 40] for i in range(9)]  # 快速上划
    # 松手那一刻的位置和松手之后的几帧放在同一次调用里量：分开量的话，
    # 机器一忙，量到「松手时」的位置时惯性可能已经滑完了
    released, glided = ipad.evaluate(
        f"async (args) => {{ await ({FLICK})(args); const at = whiteboard.viewport.y;"
        f" await ({FRAMES})(10); return [at, whiteboard.viewport.y]; }}",
        [points, "touch", 8],
    )
    assert glided < released - 5, "松手之后应该继续往下滑"

    # 惯性按帧衰减，机器忙时帧少、滑得久：等它连着几帧不动，而不是等一段固定的时间
    assert ipad.evaluate(SETTLE_Y, [5, 10000]), "最后要停下来"
    settled = ipad.evaluate(y)
    ipad.evaluate(FRAMES, 10)
    assert abs(ipad.evaluate(y) - settled) < 0.5, "最后要停下来"

    # 慢慢拖不该有惯性：松手之后的十帧里视图一动不动
    slow = [[590, 600 - i * 4] for i in range(6)]
    before, after = ipad.evaluate(
        f"async (args) => {{ await ({FLICK})(args); const at = whiteboard.viewport.y;"
        f" await ({FRAMES})(10); return [at, whiteboard.viewport.y]; }}",
        [slow, "touch", 30],
    )
    assert abs(after - before) < 0.5


def test_note_snaps_to_screen_width_when_close(browser, server):
    """缩放到接近一页宽时，松手自动吸附到屏幕两边。"""
    mac, ipad = open_pages(browser, server.port)
    note_on_both(mac, ipad)
    target = ipad.evaluate("() => whiteboard.renderer.viewW / 1000")
    tap = [[[590, 500], [590, 495]], "touch", 30]

    # 差一点点：松手后吸附
    ipad.evaluate(SET_SCALE, [target * 1.045])
    ipad.evaluate(FLICK, tap)
    ipad.wait_for_function(
        "(t) => whiteboard.viewAnim === 0 && Math.abs(whiteboard.viewport.scale - t) < t * 0.001",
        arg=target,
        timeout=3000,
    )
    left, right, width = ipad.evaluate(PAPER_EDGES)
    assert abs(left) < 0.5 and abs(right - width) < 0.5, "纸的左右边要贴住屏幕"

    # 差得远：不要自作主张
    ipad.evaluate(SET_SCALE, [target * 1.6])
    ipad.evaluate(FLICK, tap)
    # 要吸附的话松手当下就开始了（见上面那一下）：等十帧，看缩放有没有被动过
    ipad.evaluate(FRAMES, 10)
    assert ipad.evaluate("() => whiteboard.viewport.scale") > target * 1.5

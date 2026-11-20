"""橡皮：对象橡皮整笔删、像素橡皮记遮罩，以及擦除的同步、撤销、性能和手感。

不用开浏览器的部分（半径曲线、切分几何、命中判定）在 tests/js/eraser.test.mjs。
"""

from __future__ import annotations

import pytest

from e2e.support import (
    draw,
    mask_chains,
    open_pages,
    open_touch,
    pick_pixel_eraser,
    wait_seq,
    wait_strokes,
)

LINE = [(200 + i * 20, 400) for i in range(30)]  # 一条横线


def test_eraser_panel_offers_only_the_two_modes(browser, server):
    """橡皮的面板里只有模式二选一，没有粗细那一排：粗细由笔身倾角决定。"""
    ipad = open_touch(browser, server.port)
    ipad.click('button[title="橡皮擦"]')
    ipad.click('button[title="颜色与粗细"]')
    assert ipad.locator(".popover .seg button").count() == 2
    assert ipad.locator(".popover .width-opt").count() == 0
    ipad.keyboard.press("Escape")

    # 曲线本身在 tests/js/eraser.test.mjs 里对照实测；这里只看它确实接到了输入上
    radius = """([deg, mode]) => {
      whiteboard.tool.eraserMode = mode;
      return whiteboard.input.eraserRadius({
        pointerType: 'pen', altitudeAngle: (deg * Math.PI) / 180 });
    }"""
    assert ipad.evaluate(radius, [15, "object"]) == 3
    assert ipad.evaluate(radius, [15, "pixel"]) * 2 > 78
    assert ipad.evaluate(radius, [88, "pixel"]) * 2 < 8
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")


def test_pixel_eraser_severs_without_splitting(browser, server):
    """像素橡皮横穿一条笔画：视觉上断成两截，但仍然是同一条笔画，只是多了遮罩。

    断开是遮罩把它截断的结果，不是另一种模式——原生也是这样，断开之后两段各自
    还带着自己的遮罩。撤销把遮罩收回去。
    """
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, LINE)
    wait_strokes(mac, 1)
    original = ipad.evaluate("() => whiteboard.state.strokes[0].id")

    pick_pixel_eraser(ipad)
    draw(ipad, [(400, 380), (400, 400), (400, 420)])  # 从线的正中间竖着划过去

    ipad.wait_for_function(mask_chains("> 0"), arg=[original])
    mac.wait_for_function(mask_chains("> 0"), arg=[original])  # 对端也收到了
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1  # 没被拆成两条

    # 墨迹真的断了：沿中心线采样，橡皮走过的那一小段不许有墨
    gaps = ipad.evaluate(
        """() => {
          const s = whiteboard.state.strokes[0];
          const { canvas, bounds, data } = __t.render();
          const y = Math.round(s.p[1] - bounds.y0);
          const runs = [];
          let start = null;
          for (let x = 0; x < canvas.width; x++) {
            const on = data[(y * canvas.width + x) * 4 + 3] > 127;
            if (!on && start === null) start = x;
            if (on && start !== null) { runs.push(x - start); start = null; }
          }
          return runs.filter((n) => n > 2);
        }"""
    )
    assert len(gaps) == 1, gaps  # 中间正好一个缺口

    ipad.click('button[title="撤销"]')
    ipad.wait_for_function(mask_chains("=== 0"), arg=[original])
    mac.wait_for_function(mask_chains("=== 0"), arg=[original])
    assert ipad.evaluate("() => whiteboard.state.strokes[0].id") == original


def test_object_eraser_still_deletes_whole_strokes(browser, server):
    """对象橡皮擦是默认的那一种，碰到哪一笔整笔删掉。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, LINE)
    wait_strokes(mac, 1)
    assert ipad.evaluate("() => whiteboard.tool.eraserMode") == "object"

    ipad.click('button[title="橡皮擦"]')
    draw(ipad, [(400, 380), (400, 400), (400, 420)])
    wait_strokes(ipad, 0)
    wait_strokes(mac, 0)


def test_bite_syncs_and_undoes(browser, server):
    """啃出来的遮罩要同步到对端，撤销要把它收回去。"""
    mac, ipad = open_pages(browser, server.port)
    # 马克笔画一条粗的：笔尖那么大的橡皮切不断它，只能啃
    ipad.evaluate("() => { whiteboard.tool.tool = 'marker'; whiteboard.tool.width = 12; }")
    draw(ipad, [(200 + i * 20, 400) for i in range(20)], pressure=1.0)
    wait_strokes(mac, 1)
    ident = ipad.evaluate("() => whiteboard.state.strokes[0].id")
    half = ipad.evaluate("() => whiteboard.state.strokes[0].w / 2")
    assert half > 8, half  # 确实比橡皮尖（半径 3）粗得多

    pick_pixel_eraser(ipad)
    # 沿上沿削一道：贴着笔画边缘走，够不到中心线
    edge = 400 - int(half) + 2
    draw(ipad, [(300, edge), (400, edge), (500, edge)])

    ipad.wait_for_function(mask_chains("> 0"), arg=[ident])
    mac.wait_for_function(mask_chains("> 0"), arg=[ident])  # 对端也收到了
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1  # 没被切断

    ipad.click('button[title="撤销"]')
    ipad.wait_for_function(mask_chains("=== 0"), arg=[ident])
    mac.wait_for_function(mask_chains("=== 0"), arg=[ident])
    ipad.click('button[title="重做"]')
    ipad.wait_for_function(mask_chains("> 0"), arg=[ident])


# ----------------------------------------------------------------- 尺寸


def test_eraser_size_is_fixed_on_screen(browser, server):
    """橡皮的尺寸恒定在屏幕上：放大等于擦得更细，而不是橡皮跟着变大。

    以前 eraserRadius 的返回值被直接当世界坐标用，放到 8 倍时最粗的 45 在屏幕上
    是 360 px，而且放大完全不提高擦除精度。
    """
    ipad = open_touch(browser, server.port)
    radius = """([scale]) => {
      whiteboard.viewport.scale = scale;
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'object' };
      const input = whiteboard.input;
      let seen = null;
      const real = input.hooks.onErase;
      input.hooks.onErase = (x, y, r) => { seen = r; return []; };
      const at = (x) => __t.sample(x, 300, { pointerId: 1, tiltX: 0, tiltY: 0 });
      input.onDown(at(300));
      input.onMove(at(310));
      const cursor = whiteboard.renderer.cursor.r;
      input.hooks.onErase = real;
      input.onUp({ ...at(310), buttons: 0 });
      return [seen, cursor];
    }"""
    at1 = ipad.evaluate(radius, [1])
    at4 = ipad.evaluate(radius, [4])
    # 世界半径随缩放反比变化，屏幕上看到的那个圈大小不变
    assert abs(at4[0] - at1[0] / 4) < 1e-6
    assert at4[1] == at4[0]  # 光标圈和判定用的是同一个值
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; }")


def test_eraser_keeps_its_screen_size_at_every_zoom(browser, server):
    """橡皮在屏幕上恒定大小，放大缩小都按缩放等比换算到世界坐标。

    放大方向：同一个倾角在 zoom 1 和 zoom 2.02 下，原生印记在 drawing 坐标里差一倍。
    缩小方向：会话 c 里 zoom 0.25、倾角 10.4°，原生擦出来的垂直宽度是 322 个 drawing
    单位，乘回缩放是 80.5 个屏幕单位，倾角曲线在那里给的是 81。

    这里曾经有过一个「缩小不变粗」的下限，依据是 bench2 那次 zoom 0.25 下一个遮罩
    都没留下。会话 c 在同样的缩放下擦得很彻底，那次是整条橡皮没被记录下来。
    """
    ipad = open_touch(browser, server.port)
    world = """([scale]) => {
      whiteboard.viewport.scale = scale;
      return whiteboard.input.worldRadius(12);
    }"""
    at1 = ipad.evaluate(world, [1])
    for scale in (2, 4, 0.5, 0.25):
        assert ipad.evaluate(world, [scale]) == pytest.approx(at1 / scale)
    ipad.evaluate("() => { whiteboard.viewport.scale = 1; }")


def test_eraser_width_follows_the_tilt_within_a_stroke(browser, server):
    """一笔之内改变笔身角度，擦除宽度跟着变，不用抬笔。

    宽度每个采样点按 ERASER_SMOOTH 向当前倾角对应的宽度靠拢一部分：从立着（60°）
    慢慢压到 20°，擦痕从笔尖那么细逐渐变到最粗，中间没有一下跳到位的台阶。
    悬停时的光标圈同样跟着倾斜走。
    """
    ipad = open_touch(browser, server.port)
    out = ipad.evaluate("""() => {
      const input = whiteboard.input;
      __t.band();
      // 前 20 个采样立着（60°），之后 40 个采样里匀速压到 20°，最后 20 个停在 20°
      const deg = (k) => (k < 20 ? 60 : k < 60 ? 60 - (k - 20) : 20);
      const at = (k) => __t.sample(260 + k * 3, 400, {
        pointerId: 4, altitudeAngle: (deg(k) * Math.PI) / 180, azimuthAngle: 0.8 });
      const samples = [];
      for (let k = 0; k <= 80; k++) samples.push(at(k));
      __t.drive(samples);
      const hover = (d) => ({ ...at(0), altitudeAngle: (d * Math.PI) / 180 });
      const target = (d) => input.worldRadius(input.eraserRadius(hover(d)));
      input.updateCursor(hover(80));
      const a = whiteboard.renderer.cursor.r;
      input.updateCursor(hover(30));
      const b = whiteboard.renderer.cursor.r;
      return { radii: __t.mask().map((c) => c[0]), upright: target(60), flat: target(20),
               cursorMoves: a !== b };
    }""")
    radii = out["radii"]
    assert len(radii) > 1, radii
    # 笔一路往下压，宽度只增不减
    assert radii == sorted(radii), radii
    # 开头是立着的宽度，结尾到了压平的宽度
    assert abs(radii[0] - out["upright"]) / out["upright"] < 0.1, (radii[0], out["upright"])
    assert abs(radii[-1] - out["flat"]) / out["flat"] < 0.1, (radii[-1], out["flat"])
    assert radii[-1] > radii[0] * 3, radii
    # 逐步变化：曲线在 37°～35° 之间宽度翻倍，平滑之后相邻两段的比值也不超过 1.5
    steps = [b / a for a, b in zip(radii, radii[1:])]
    assert max(steps) < 1.5, steps
    assert out["cursorMoves"], "悬停光标要跟着倾斜走"


def test_the_stylus_erases_as_evenly_as_the_mouse(browser, server):
    """同一条路径、笔身角度不变时，笔擦出来的痕迹要和鼠标一样匀。

    鼠标那条路橡皮半径是写死的常数（没有倾斜可依据）。笔的粗细在一笔之内跟着
    倾角走（见 test_eraser_width_follows_the_tilt_within_a_stroke），但角度不变时
    宽度也不能变：读数换算成整度的 tiltX / tiltY 之后，平滑不能让半径来回漂。
    判据：两种输入都只攒出一条链、一个半径。
    """
    ipad = open_touch(browser, server.port)
    run = """([pen]) => {
      __t.band();
      // 笔身保持 50°，换算成整度的 tiltX / tiltY——真机就是这样报的
      const tiltX = Math.round(Math.atan(1 / Math.tan((50 * Math.PI) / 180)) * 180 / Math.PI);
      const extra = pen ? { pointerId: 6, tiltX, tiltY: 0 } : { pointerId: 6, pointerType: 'mouse' };
      const samples = [];
      for (let k = 0; k <= 60; k++) samples.push(__t.sample(260 + k * 3, 400, extra));
      __t.drive(samples);
      const m = __t.mask();
      return { chains: m.length, radii: [...new Set(m.map((c) => c[0]))] };
    }"""
    with_pen = ipad.evaluate(run, [True])
    with_mouse = ipad.evaluate(run, [False])
    for label, got in (("笔", with_pen), ("鼠标", with_mouse)):
        assert got["chains"] == 1, (label, got)
        assert len(got["radii"]) == 1, (label, got)
    # 粗细本来就该由倾角决定，所以两者的半径不必相等，只要各自匀
    assert with_pen["radii"][0] > 0 and with_mouse["radii"][0] > 0


# ----------------------------------------------------------------- 一次拖动


def test_eraser_reaches_where_the_pen_left_the_screen(browser, server):
    """抬笔那一下的位置也要擦掉。

    最后一个 pointermove 停在上一帧，笔离开屏幕之前还走了一段，这一段只有
    pointerup 里有。不补的话擦痕停在上一帧的位置，末端留下一道正好是橡皮直径宽
    的硬边，而笔真正压过的地方还留着墨。
    """
    ipad = open_touch(browser, server.port)
    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 8 }; }")
    draw(ipad, [(300, 200 + i * 6) for i in range(90)], pressure=0.9)
    wait_strokes(ipad, 1)
    sizes = "() => whiteboard.state.strokes.map((s) => whiteboard.maskSize(s))"
    pen = "{ altitudeAngle: 0.9, azimuthAngle: 0.8, pointerId: %d }"

    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' }; }")
    # 橡皮停在笔画左边，抬笔那一下才压到笔画上
    ipad.evaluate(f"""() => {{
      const init = {pen % 9};
      __t.fire('pointerdown', 180, 500, init);
      __t.fire('pointermove', 240, 500, init);
      __t.fire('pointermove', 280, 500, init);
    }}""")
    assert ipad.evaluate(sizes) == [0], "抬笔之前还没碰到笔画"
    ipad.evaluate(f"() => __t.fire('pointerup', 300, 500, {pen % 9})")
    assert ipad.evaluate(sizes)[0] > 0, "抬笔那一下压在笔画上，应该擦掉"

    # 取消不算抬笔：那一下不是用户抬的笔，位置不代表他想擦到哪
    before = ipad.evaluate(sizes)
    ipad.evaluate(f"""() => {{
      const init = {pen % 11};
      __t.fire('pointerdown', 180, 260, init);
      __t.fire('pointermove', 240, 260, init);
      __t.fire('pointercancel', 300, 260, {{ ...init, buttons: 0 }});
    }}""")
    assert ipad.evaluate(sizes) == before


def test_eraser_uses_every_coalesced_sample(browser, server):
    """一帧里的合并采样点橡皮要全吃掉，不能只取最后一个。

    iPad 上笔是 120Hz 而 pointermove 一帧才来一次，中间那些点都在
    getCoalescedEvents 里。只取最后一个等于把一帧里的一段曲线压成一条直线，
    擦得越快压得越狠，擦痕边上就出现一节一节的直棱。画线那边一直取全部的。

    判据是「一帧里五个点」和「五帧各一个点」擦出来的遮罩完全一样。
    """
    ipad = open_touch(browser, server.port)
    # getCoalescedEvents 造不出来，只能直接喂给 input——回放走的也是这条路
    probe = """([coalesce]) => {
      const input = whiteboard.input;
      __t.band({ count: 80, from: -260, step: 6 });
      // 一笔走了一段圆弧，六个采样点：落笔一个，之后五个在同一帧里
      const arc = [];
      for (let i = 0; i < 6; i++) {
        arc.push(__t.sample(300 + i * 9, 400 - Math.sin((i / 5) * Math.PI) * 8,
                            { altitudeAngle: 0.9, azimuthAngle: 0.8 }));
      }
      input.onDown(arc[0]);
      if (coalesce) {
        input.onMove({ ...arc[5], getCoalescedEvents: () => arc.slice(1) });
      } else {
        for (const sample of arc.slice(1)) input.onMove(sample);
      }
      input.onUp({ ...arc[5], buttons: 0 });
      return __t.mask();
    }"""
    one_frame = ipad.evaluate(probe, [True])
    per_frame = ipad.evaluate(probe, [False])
    assert one_frame, "应该留下遮罩"
    # 六个采样点都要落进遮罩里，不能只剩首尾
    assert sum((len(c) - 1) // 2 for c in one_frame) >= 6, one_frame
    assert one_frame == per_frame


def test_one_drag_stays_one_mask_chain(browser, server):
    """一次连续拖动只攒一条胶囊链，不是一个采样点一条。

    橡皮的粗细每个采样点都重新平滑一次，收敛是指数的，永远差那么一点点。
    判「还是同一次扫掠」原来用的是半径严格相等，于是几乎判不出来：一份真机
    录像里一次擦除攒出 113 条链、82 个互不相同的半径，而它们在两位小数上全是
    同一个值。链一多，裁剪开销（随段数平方涨）和同步的体量都跟着涨。
    """
    ipad = open_touch(browser, server.port)
    chains = ipad.evaluate("""() => {
      __t.band();
      // 倾角一路缓慢变化：平滑出来的半径每一下都不一样，正是真机的样子
      const samples = [];
      for (let k = 0; k < 120; k++) {
        samples.push(__t.sample(260 + k * 2, 400,
                                { altitudeAngle: 0.9 + k * 0.0008, azimuthAngle: 0.8 }));
      }
      __t.drive(samples);
      const m = __t.mask();
      return { chains: m.length, segs: whiteboard.maskSize({ m }) };
    }""")
    assert chains["segs"] >= 100, chains
    # 一次拖动、一条链。允许因为半径真的走远了而断开几次，但不能一下一条
    assert chains["chains"] <= 3, chains


def test_mask_is_thinned_before_it_is_baked_into_cuts(browser, server):
    """遮罩攒到上限先抽稀，抽不动了才落实成切分。

    落实成切分是看得见的变化：啃出来的形状换成平口断面，贴边的细条还会被一起
    清掉。以前一条笔画上擦够 400 个采样点就触发，iPad 上 120Hz 只要三秒多，
    在一大块墨上来回擦几下就撞上了，手感上就是「擦着擦着忽然多出一道平口」。

    裁剪的开销随段数是平方涨的（400 段整屏重画 2.9 ms、1000 段 15 ms、
    2000 段 57 ms），所以不能靠放宽上限解决，只能让段数真的变少。抽稀不改形状的
    那一半在 tests/js/eraser.test.mjs。
    """
    ipad = open_touch(browser, server.port)
    # 一千下——iPad 上 120Hz 差不多八秒连续擦一条笔画，还得是遮罩不是切分
    out = ipad.evaluate("""() => {
      __t.band();
      const samples = [];
      for (let k = 0; k < 1000; k++) {
        samples.push(__t.sample(300 + 140 * Math.sin(k / 18), 400 + 10 * Math.sin(k / 5),
          { altitudeAngle: 0.9 + 0.02 * Math.sin(k / 7), azimuthAngle: 0.8 }));
      }
      __t.drive(samples);
      return {
        strokes: whiteboard.state.strokes.length,
        segs: whiteboard.state.strokes.map((s) => whiteboard.maskSize(s)),
        cut: whiteboard.state.strokes.reduce((a, s) => a + (s.cut ? 1 : 0), 0),
      };
    }""")
    assert out["cut"] == 0, out
    assert out["strokes"] == 1, out
    assert max(out["segs"]) <= 400, out


def test_erased_trace_is_continuous(browser, server):
    """橡皮扫过的地方不许有残留墨迹——沿路径逐点采样，一个点都不许剩。

    这一条抓的是「擦痕断成一排小块」那个 bug：遮罩原来用 even-odd 裁剪，而
    even-odd 算的是对称差不是并集，一次拖动里相邻两段胶囊在共用的圆端点处必然
    重叠，重叠处被算两次、判定成「不擦」，于是每隔一个采样点就留下一块正好等于
    橡皮直径的墨。

    以前所有用例和所有对照图用的都是**单独一段**胶囊，穿孔只在两段以上时出现，
    所以全绿也测不出来。这里必须是连续拖动。
    """
    ipad = open_touch(browser, server.port)
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
      const { inkAt } = __t.render();
      const left = path.slice(3, -3).filter(([x, y]) => inkAt(x, y) === true).length;
      return { 残留: left, 带遮罩: whiteboard.state.strokes.filter(s => s.m && s.m.length).length };
    }"""
    for radius in (5, 9, 16):
        got = ipad.evaluate(result, [radius])
        assert got["带遮罩"] > 0, got
        assert got["残留"] == 0, (radius, got)
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")


# ----------------------------------------------------------------- 遮罩与切分


def test_pixel_eraser_always_masks(browser, server):
    """像素橡皮只有一种处理方式：记遮罩，不拆笔画。

    曾经按「橡皮半径是否不小于笔画半宽」分成切断和啃两种走法。压感沿笔画变化、
    局部半宽跟着变，同一次拖动走到一半判定就会跨过阈值，前半截被切出平口断面、
    后半截变成啃，来回跳。原生也是只记遮罩。
    """
    ipad = open_touch(browser, server.port)
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
    assert ipad.evaluate(setup, [15, 5, -12]) == [1, "wide", 1]

    # 同一条笔画上再啃一道，接成同一条链而不是两条
    again = ipad.evaluate(
        "() => { whiteboard.input.hooks.onErase(360, -12, 5, [320, -12]);"
        " whiteboard.flushErase();"
        " return whiteboard.state.strokes[0].m.map((c) => (c.length - 1) / 2); }"
    )
    assert again == [3]  # 一条链、三个点，不是两条链

    # 橡皮和笔一样粗也还是遮罩，不会把笔画拆成两条——拆不拆由遮罩的形状决定，
    # 不是另一种模式。
    assert ipad.evaluate(setup, [5, 6, 0]) == [1, "wide", 1]
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")


def test_mask_does_not_grow_without_bound(browser, server):
    """遮罩不能无限堆：裁剪开销和胶囊数成正比，来回涂同一块地方是堆积的主要来源。

    新胶囊整个盖住的旧胶囊直接丢掉；攒过上限就看看是不是已经啃断了，断了就
    落实成独立笔画、把用掉的胶囊丢掉。
    """
    ipad = open_touch(browser, server.port)
    wide = """(length) => {
      const p = [];
      for (let x = 0; x <= length; x += 10) p.push(x, 0, 1);
      whiteboard.state.reset(whiteboard.state.meta,
        [{ id: 'wide', tool: 'pen', color: '#1b1b1f', w: 40, p, n: 0 }]);
      whiteboard.net.send = () => {};
      whiteboard.tool.eraserMode = 'pixel';
    }"""
    scrub = """([passes]) => {
      for (let i = 0; i < passes; i++) {
        const y = -16;
        whiteboard.input.hooks.onErase(560, y, 6, [40, y]);   // 来回涂同一条
        whiteboard.input.hooks.onErase(40, y, 6, [560, y]);
      }
      whiteboard.flushErase();
      const s = whiteboard.state.byId.get('wide');
      return s ? whiteboard.maskSize(s) : 0;
    }"""
    ipad.evaluate(wide, 600)
    assert ipad.evaluate(scrub, [1]) <= 4
    # 涂五十个来回，胶囊数不该跟着涨五十倍
    ipad.evaluate(wide, 600)
    assert ipad.evaluate(scrub, [50]) <= 8

    # 擦够 MASK_LIMIT 次不同的地方，遮罩落实成切分、清空
    baked = """([passes]) => {
      for (let i = 0; i < passes; i++) {
        const x = 20 + i * 20;
        whiteboard.input.hooks.onErase(x, 16, 6, [x, -16]);  // 每次擦一个新地方
      }
      whiteboard.flushErase();
      const strokes = whiteboard.state.strokes;
      return [strokes.length, Math.max(...strokes.map((s) => whiteboard.maskSize(s)))];
    }"""
    ipad.evaluate(wide, 12000)
    count, biggest = ipad.evaluate(baked, [60])
    assert count == 1 and biggest == 60  # 没到上限，还是一条笔画挂着遮罩
    ipad.evaluate(wide, 12000)
    count, biggest = ipad.evaluate(baked, [500])
    assert count > 1  # 过了上限，落实成了好几条
    assert biggest <= 400
    ipad.evaluate("() => { whiteboard.tool.eraserMode = 'object'; }")


CUT_THEN_TOUCH_LEFT = """() => {
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
  w.input.hooks.onEraseEnd();
  const afterCut = w.state.strokes.map((s) => ({ id: s.id, masked: !!(s.m && s.m.length) }));
  // 换对象橡皮，点左边那一截
  w.tool.eraserMode = 'object';
  w.input.hooks.onErase(60, 0, 6, [60, 0]);
  w.input.hooks.onEraseEnd();
  const span = (s) => {
    let x0 = Infinity, x1 = -Infinity;
    for (let i = 0; i < s.p.length; i += 3) { x0 = Math.min(x0, s.p[i]); x1 = Math.max(x1, s.p[i]); }
    return [x0, x1];
  };
  return { afterCut, spans: w.state.strokes.map(span) };
}"""


def test_object_eraser_deletes_only_the_piece_you_touch(browser, server):
    """像素橡皮把一笔断成两截之后，对象橡皮点哪一截只删哪一截。

    像素橡皮不拆笔画，只给笔画挂一条遮罩（见 docs/format.md）。所以看上去断成
    两截的笔画其实还是一条，两截共用一个 id——对象橡皮点任意一截都会把整条删掉。

    现在对象橡皮碰到带遮罩的笔画时先按缺口切成独立的几条，再只删碰到的那一条。
    """
    ipad = open_touch(browser, server.port)
    out = ipad.evaluate(CUT_THEN_TOUCH_LEFT)
    # 像素橡皮擦完仍然是一条（挂着遮罩），这是设计如此
    assert out["afterCut"] == [{"id": "long", "masked": True}], out
    # 对象橡皮点左边：左边没了，右边还在
    assert not [s for s in out["spans"] if s[0] < 100], out
    assert [s for s in out["spans"] if s[1] > 300], out


def test_undo_after_erasing_one_piece_brings_the_whole_stroke_back(browser, server):
    """点掉一截之后撤销，要把原来那一条整个换回来，不是只回来一截。

    对象橡皮删这一截之前先做了切分，撤销记录记的是「原来那一条换成这几截」加上
    「这一截被删了」两件事，合成一条：换回原来那一条，把切出来的段全部清掉。
    """
    ipad = open_touch(browser, server.port)
    ipad.evaluate(CUT_THEN_TOUCH_LEFT)
    out = ipad.evaluate("""() => {
      whiteboard.undo();
      const back = whiteboard.state.strokes;
      let x0 = Infinity, x1 = -Infinity;
      for (const s of back) for (let i = 0; i < s.p.length; i += 3) {
        x0 = Math.min(x0, s.p[i]); x1 = Math.max(x1, s.p[i]);
      }
      return { count: back.length, span: [x0, x1] };
    }""")
    assert out["count"] == 1, out
    # 换回来的是完整的那一条，从 0 铺到 400
    assert out["span"][0] <= 1 and out["span"][1] >= 399, out


# ----------------------------------------------------------------- 性能


def test_erase_keeps_up_on_a_crowded_board(browser, server):
    """擦除的开销只跟扫过的那一片有关，不跟白板上一共有多少笔有关。

    以前每个指针事件都要把整块白板过一遍（还顺带整个数组重排一次），笔一多就卡。
    现在按空间网格取候选，插入和删除也改成只动该动的那几条。
    """
    ipad = open_touch(browser, server.port)
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
      r.fullRedraw = realFull;
      return { fulls, strokes: whiteboard.state.strokes.length,
               masked: whiteboard.state.strokes.filter(x => x.m && x.m.length).length };
    }""")
    assert result["fulls"] == 0  # 一次整屏重绘都没有
    assert result["strokes"] == 5  # 像素橡皮不拆笔画，只记遮罩
    assert result["masked"] > 0  # 确实擦到了


# ----------------------------------------------------------------- 同步


def test_erase_sends_one_batch_per_frame(browser, server):
    """一次拖动里的擦除操作攒起来每帧发一次，不是每个指针事件发一条。"""
    mac, ipad = open_pages(browser, server.port)
    draw(ipad, LINE)
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
    wait_strokes(ipad, 1)
    wait_strokes(mac, 1)

    # 沿着这条笔画一下一下地点着擦：每一下都是独立的一笔，各自成一条链
    spots = ipad.evaluate("""() => {
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      const init = { pointerId: 5, altitudeAngle: 1.1, azimuthAngle: 0.8 };
      const out = [];
      for (let i = 0; i < 80; i++) {
        const x = 220 + i * 9;
        __t.fire('pointerdown', x, 400, init);
        __t.fire('pointermove', x + 3, 400, init);
        __t.fire('pointerup', x + 3, 400, init);
        out.push(whiteboard.input.toWorld({ clientX: x + 1, clientY: 400 }));
      }
      return out;
    }""")
    # 回执都回来了（本机被盖回的话这时已经盖完），Mac 也收齐了全部广播
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0")
    wait_seq(mac, ipad)

    left = """([spots]) => {
      const s = whiteboard.state.strokes[0];
      return spots.filter(([x, y]) => whiteboard.strokeHit(s, x, y, 0)).length;
    }"""
    here = ipad.evaluate(left, [spots])
    there = mac.evaluate(left, [spots])
    assert here == 0, f"本机上还有 {here} 处擦过的地方留着墨"
    assert there == 0, f"对端上还有 {there} 处擦过的地方留着墨"


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
    wait_strokes(ipad, 1)
    wait_strokes(mac, 1)
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0")

    steps = 120
    # 回执慢一点回来，把真机上的局域网往返放大出来。这是故意注入的延迟：
    # 换的是 net 收回执的那一个入口，别的照旧走真的网络。
    got = ipad.evaluate("""async ([steps]) => {
      const net = whiteboard.net;
      const real = net._ack.bind(net);
      net._ack = (msg) => setTimeout(() => real(msg), 60);
      const init = { pointerId: 9, tiltX: 47, tiltY: 20 };
      whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
      __t.fire('pointerdown', 160, 400, init);
      for (let k = 1; k <= steps; k++) {
        __t.fire('pointermove', 160 + k * 5, 400, init);
        // 让出一帧：擦一下、发一次、回执插进来，正是真机的节奏
        await new Promise((r) => requestAnimationFrame(r));
      }
      __t.fire('pointerup', 160 + steps * 5, 400, init);
      // 等推迟的回执都回来（待发队列清空），再换回原来的入口
      const end = performance.now() + 5000;
      while (net.outbox.length && performance.now() < end) {
        await new Promise((r) => requestAnimationFrame(r));
      }
      net._ack = real;
      const m = whiteboard.state.strokes[0].m || [];
      return { chains: m.length, pts: m.reduce((a, c) => a + (c.length - 1) / 2, 0) };
    }""", [steps])
    assert got["chains"] == 1, got
    assert got["pts"] == steps + 1, got

    # 对端还是要收得到：不回放自己的回执，不等于不发给别人
    mac.wait_for_function("() => (whiteboard.state.strokes[0] || {}).m")
    mine = ipad.evaluate("() => JSON.stringify(whiteboard.state.strokes[0].m)")
    assert mac.evaluate("(m) => JSON.stringify(whiteboard.state.strokes[0].m) === m", mine), (
        "对端的遮罩要和本机一致"
    )

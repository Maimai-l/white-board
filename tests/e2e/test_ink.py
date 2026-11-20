"""笔迹在屏幕上的样子：起笔收笔、轮廓画成曲线、没有白洞、屏幕和导出的 PDF 一致。

只算数、不画的部分（压感曲线、起笔、Python 和 JS 两份轮廓逐点一致）不用开浏览器：
见 tests/js/pressure.test.mjs 和 tests/test_outline_parity.py。
"""

from __future__ import annotations

import math

from e2e.support import open_mac, open_touch
from tests.fixtures.recorded_strokes import REAL_FAST_STROKE, REAL_HOOK_STROKE
from whiteboard import inkpdf


def flat(points):
    return [v for point in points for v in point]


def test_the_settled_tail_does_not_leave_a_knob(browser, server):
    """笔停住之后的那几个采样点不能留在笔画里，否则末端会鼓一个球。

    抬笔之前笔通常已经停住了，事件却还在来：坐标在相邻整数之间游走，真机录像里
    一笔末尾常有四五个点挤在一两个世界单位之内，而且相对笔画走向偏出去一点。
    轮廓那边会在最后一个点上扣一个整圆的笔帽，于是那一撮点把笔帽顶到轴线外面。

    判据是末端那个笔帽的圆心离笔画轴线多远：把最后一段真实走向延长出去，看帽心
    偏出去多少。偏出去超过半个笔半宽就是肉眼能看见的球。
    """
    mac = open_mac(browser, server.port)
    out = mac.evaluate("""() => {
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 13 };
      const init = { pointerId: 4, pressure: 0.05, tiltX: 40, tiltY: 0 };
      // 一条直线，最后减速停住，再在原地抖四下（真机录像里就是这样）
      __t.fire('pointerdown', 100, 300, init);
      let x = 100;
      for (const step of [12, 12, 12, 12, 12, 8, 5, 3, 2]) {
        x += step; __t.fire('pointermove', x, 300, init);
      }
      for (const [dx, dy] of [[1,-1],[0,-1],[1,0],[0,-1]]) {
        x += dx; __t.fire('pointermove', x, 300 + dy, init);
      }
      __t.fire('pointerup', x, 299, init);
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


def test_the_tail_of_a_stroke_thins_out_instead_of_swelling(browser, server):
    """笔快离开屏幕时压感掉到 0，那是真读数，末尾该收细而不是鼓一个包。

    `pressure === 0` 有两种含义：设备根本报不了压感，和笔快抬起来了。原来一律
    当成前者顶成 0.5。真机录像里抬笔前那两下压感是 0.005、0.005、0，顶成 0.5
    之后末端反而粗了两成半——每一笔的收尾都带个包。

    只要这一笔里报过一次正压感，后面的 0 就沿用上一次的值；一次都没报过才算
    这支笔没有压感（鼠标和不带压感的笔照旧走默认值）。
    """
    ipad = open_touch(browser, server.port)
    out = ipad.evaluate("""() => {
      whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
      whiteboard.tool = { ...whiteboard.tool, tool: 'pen', w: 13 };
      const init = { pointerId: 2, tiltX: 40, tiltY: 0 };
      // 真机录到的收尾就是这样：一路降到 0.005，最后一下报 0
      const tail = [0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.012, 0.005, 0.005, 0];
      __t.fire('pointerdown', 200, 300, { ...init, pressure: tail[0] });
      for (let i = 1; i < tail.length; i++) {
        __t.fire('pointermove', 200 + i * 9, 300, { ...init, pressure: tail[i] });
      }
      __t.fire('pointerup', 200 + tail.length * 9, 300, init);
      const s = whiteboard.state.strokes[0];
      return s.p.filter((_, i) => i % 3 === 2).map((v) => whiteboard.strokeRadius('pen', s.w, v));
    }""")
    assert len(out) >= 6, out
    # 末尾不许比中段粗
    assert out[-1] <= out[len(out) // 2] + 1e-9, out
    # 而且要确实在收细
    assert out[-1] < out[0], out


def test_the_outline_is_drawn_as_curves_not_chords(browser, server):
    """轮廓点之间要画二次贝塞尔，不能直接连直线。

    perfect-freehand 的 getStroke 返回的是一串轮廓点，配套的渲染是「二次贝塞尔穿过
    相邻两点的中点、顶点当控制点」。直接 lineTo 连那串点也能出形状，但笔走得快、
    轮廓点隔得开的时候边上就是一段段直线——正是当初那个「直线拼出来的曲线」。

    判据：同一串轮廓点，一份按 buildPath 画，一份 lineTo 连起来，两份填出来的
    像素要明显不一样。用真机录的那一笔量，它的采样点隔开几十个单位，差别最明显。
    """
    mac = open_mac(browser, server.port)
    out = mac.evaluate(
        """([flat, width]) => {
      const stroke = { id: 'x', tool: 'pen', color: '#000', w: width, p: flat };
      const pts = whiteboard.strokeOutline(stroke);
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of pts) {
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
      const box = [x0 - 8, y0 - 8, x1 + 8, y1 + 8];
      const chords = new Path2D();
      chords.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) chords.lineTo(pts[i][0], pts[i][1]);
      chords.closePath();
      const curve = __t.raster(whiteboard.buildPath(stroke), box).ink;
      const lines = __t.raster(chords, box).ink;
      let area = 0, diff = 0;
      for (let i = 0; i < curve.length; i++) { area += curve[i]; diff += curve[i] !== lines[i]; }
      return { area, diff, n: pts.length };
    }""",
        [flat(REAL_FAST_STROKE), 13.0],
    )
    assert out["n"] > 50, out
    # 两份形状必须真的不一样：换成 lineTo 的话这个数会是 0
    assert out["diff"] > out["area"] * 0.02, out


def test_a_stroke_never_has_holes_in_it(browser, server):
    """笔画里不许出现被墨迹围住的白色缺口。

    这一笔是真机录的：往右上画上去，再原路收回来（「往一个方向画收回会出现诡异的
    白色」说的就是它）。白洞当初出在收回来那一段——那时两侧各算一条斜接偏移线接成
    闭合回路，内侧偏移点折回去自交，自交出来的小环绕向和主体相反，nonzero 下算 0。

    判据不假设任何画法：把笔画填出来，从图像边界灌水，灌不到的白色像素就是被墨迹
    围住的洞。换渲染实现也不会假红。
    """
    ipad = open_touch(browser, server.port)
    holes = ipad.evaluate(
        """([flat, width]) => {
      const S = 4;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < flat.length; i += 3) {
        x0 = Math.min(x0, flat[i]); x1 = Math.max(x1, flat[i]);
        y0 = Math.min(y0, flat[i + 1]); y1 = Math.max(y1, flat[i + 1]);
      }
      const path = whiteboard.buildPath({ id: 'x', tool: 'pen', color: '#000', w: width, p: flat });
      const { ink, width: W, height: H } = __t.raster(path, [x0 - 8, y0 - 8, x1 + 8, y1 + 8], S);
      // 从边界往里灌：白色而且灌得到的就是外面
      const seen = new Uint8Array(W * H);
      const stack = [];
      const push = (x, y) => { const k = y * W + x; if (!ink[k] && !seen[k]) { seen[k] = 1; stack.push(k); } };
      for (let x = 0; x < W; x++) { push(x, 0); push(x, H - 1); }
      for (let y = 0; y < H; y++) { push(0, y); push(W - 1, y); }
      while (stack.length) {
        const k = stack.pop(), x = k % W, y = (k - x) / W;
        if (x > 0) push(x - 1, y);
        if (x < W - 1) push(x + 1, y);
        if (y > 0) push(x, y - 1);
        if (y < H - 1) push(x, y + 1);
      }
      let trapped = 0, solid = 0;
      for (let k = 0; k < W * H; k++) { solid += ink[k]; if (!ink[k] && !seen[k]) trapped++; }
      return { trapped: Math.round(trapped / (S * S)), ink: Math.round(solid / (S * S)) };
    }""",
        [flat(REAL_HOOK_STROKE), 13.0],
    )
    assert holes["ink"] > 500, holes
    assert holes["trapped"] == 0, holes


def test_screen_and_pdf_outlines_agree(browser, server):
    """屏幕的 buildPath 和导出的 outline_path 必须画出同一个形状。

    两边是两份独立实现（一份 JS 一份 Python），任何一边动了几何都可能悄悄跑偏，
    而跑偏只会在导出的 PDF 里看出来。这里让 Python 算出导出用的路径指令，
    交给浏览器用同样的光栅化画一遍，和 buildPath 的结果逐像素比。
    """
    ipad = open_touch(browser, server.port)

    # 一笔里同时有平滑段、急弯和两个折角，压感也在变
    points = []
    for i in range(60):
        t = i / 59
        points.append((60 + t * 300, 200 + math.sin(t * 4) * 60, 0.4 + 0.5 * t))
    points += [(360, 260, 0.9), (250, 90, 0.9), (330, 250, 0.9)]

    compare = """([cmds, flat, width, cut]) => {
      const box = [0, 0, 460, 340];
      const screen = __t.raster(whiteboard.buildPath(
        { id: 'x', tool: 'pen', color: '#000', w: width, cut, p: flat }), box).ink;
      const path = new Path2D();
      for (const c of cmds) {
        if (c[0] === 'm') path.moveTo(c[1], c[2]);
        else if (c[0] === 'l') path.lineTo(c[1], c[2]);
        else path.bezierCurveTo(c[1], c[2], c[3], c[4], c[5], c[6]);
      }
      path.closePath();
      const pdf = __t.raster(path, box).ink;
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
            pct, area = ipad.evaluate(compare, [cmds, flat(pts), width, cut])
            assert area > 5000, (label, width, cut, area)  # 真的画上去了
            # 只剩抗锯齿边缘的差别：两条轮廓之间差一个像素都会让这个数大起来
            assert pct < 1.2, (label, width, cut, pct)

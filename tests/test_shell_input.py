"""iPad 外壳的输入来源（docs/ipad-shell.md 第 5～7 节）。

真的外壳是 iPad 上的原生应用，这里用页面里的 ``window.whiteboardShell`` 直接
模拟它：握手、按 5.1 节的格式送采样，看网页是不是把它们当成 Pencil 的输入。
"""

from __future__ import annotations

import pytest

from tests.test_browser import FIRE, IPAD_UA, browser, open_pages, server  # noqa: F401

HELLO = "(info) => whiteboardShell.hello(info)"
ACTIVE = {"shellVersion": "1.3.0", "bridge": 1, "active": True}

# 按 5.1 节的格式造一笔：坐标带小数，时间是秒，一批里有若干个真实采样和一段预测。
# 每个采样都等着力度更新（est / ui），更新在后面几批里才到。
SHELL_STROKE = """
([id, x0, y0, dx, dy, count, perBatch]) => {
  const fmax = 4.1666666667;
  const batches = [];
  let ui = id * 1000;
  let t = 5321.0412;
  const pending = [];
  for (let i = 0; i < count; i += perBatch) {
    const samples = [];
    for (let k = i; k < Math.min(count, i + perBatch); k++) {
      const ph = k === 0 ? 'down' : k === count - 1 ? 'up' : 'move';
      samples.push({
        id, ph, k: 'real', t, x: x0 + dx * k + 0.37, y: y0 + dy * k + 0.61,
        f: ph === 'up' ? 0 : 1.2 + 0.01 * k, fmax, alt: 0.95, az: 2.21,
        est: ph === 'up' ? [] : ['force'], ui: ph === 'up' ? null : ui,
      });
      if (ph !== 'up') pending.push(ui);
      ui += 1;
      t += 1 / 240;
    }
    const last = samples[samples.length - 1];
    // 更新比采样晚两批才到：最后两批的更新会落在抬笔之后
    const updates = pending.splice(0, Math.max(0, pending.length - 2 * perBatch))
      .map((u) => ({ ui: u, f: 1.9, alt: 0.95, az: 2.21 }));
    const pred = last.ph === 'up' ? null : { id, samples: [1, 2, 3].map((j) => ({
      t: last.t + j / 240, x: last.x + dx * j, y: last.y + dy * j, f: 1.2, fmax, alt: 0.95, az: 2.21,
    })) };
    batches.push({ bridge: 1, samples, pred, updates });
  }
  batches.push({ bridge: 1, samples: [], pred: null,
                 updates: pending.map((u) => ({ ui: u, f: 1.9, alt: 0.95, az: 2.21 })) });
  return batches;
}
"""


def shell_batches(page, stroke_id=1, x0=300, y0=300, dx=3, dy=2, count=40, per_batch=4):
    return page.evaluate(SHELL_STROKE, [stroke_id, x0, y0, dx, dy, count, per_batch])


def send(page, batches):
    page.evaluate("(batches) => { for (const b of batches) whiteboardShell.receive(b); }", batches)


def test_web_page_says_hello_to_the_shell(browser, server):
    """在外壳里（有 messageHandlers.whiteboard）时，页面加载后发出握手。"""
    context = browser.new_context(viewport={"width": 1180, "height": 820}, user_agent=IPAD_UA, has_touch=True)
    page = context.new_page()
    page.add_init_script(
        """
        window.__sent = [];
        window.webkit = { messageHandlers: { whiteboard: { postMessage: (m) => window.__sent.push(m) } } };
        """
    )
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    page.wait_for_function("() => window.whiteboard && window.__sent.length > 0")
    assert page.evaluate("() => window.__sent") == [{"type": "hello", "bridge": [1, 1]}]
    assert page.evaluate("() => whiteboard.input.stats.source") == "browser"

    page.evaluate(HELLO, ACTIVE)
    assert page.evaluate("() => whiteboard.input.stats.source") == "shell"
    assert page.evaluate("() => whiteboard.input.stats.shellVersion") == "1.3.0"
    context.close()


def test_incompatible_bridge_keeps_the_browser_source(browser, server):
    """外壳的接口版本不在网页认得的范围里：不切过去，Pencil 照旧走指针事件。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, {"shellVersion": "9.0.0", "bridge": 99, "active": True})
    assert ipad.evaluate("() => whiteboard.input.stats.source") == "browser"
    ipad.evaluate(FIRE, ["pointerdown", 300, 300, "pen", 1, 0.5])
    ipad.evaluate(FIRE, ["pointermove", 340, 330, "pen", 1, 0.5])
    ipad.evaluate(FIRE, ["pointerup", 360, 340, "pen", 1, 0])
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1
    mac.close()
    ipad.close()


def test_shell_samples_draw_and_safari_pen_events_are_ignored(browser, server):
    """外壳处于 active 时，笔画来自外壳的采样；Safari 自己的 pen 事件不参与书写。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad)
    # 同一笔 Safari 也会发 pen 事件（整数坐标）：只拿来比对，不能再画出第二笔。
    # 直接交给处理函数（和回放一样），时间戳才能照真机那样与采样对齐：
    # 第 3 个采样在落笔之后 12.5 ms
    safari = """([type, x, y, t]) => {
      const e = { clientX: x, clientY: y, pointerType: 'pen', pointerId: 7, pressure: 0.3,
                  tiltX: 0, tiltY: 0, buttons: type === 'up' ? 0 : 1, button: 0, isPrimary: true,
                  timeStamp: 1000 + t, preventDefault() {}, getCoalescedEvents: () => [] };
      const input = whiteboard.input;
      if (type === 'down') input.onDown(e); else if (type === 'move') input.onMove(e); else input.onUp(e);
    }"""
    ipad.evaluate(safari, ["down", 300, 301, 0])
    ipad.evaluate(safari, ["move", 309, 307, 12.5])
    send(ipad, batches)
    ipad.evaluate(safari, ["up", 309, 307, 12.5])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    mac.wait_for_function("() => whiteboard.state.strokes.length === 1")

    stroke = ipad.evaluate("() => whiteboard.state.strokes[0]")
    xs = stroke["p"][0::3]
    # 坐标带小数，原样进了笔画
    expected = ipad.evaluate("() => whiteboard.input.toWorld({ clientX: 300.37, clientY: 300.61 })")
    assert [xs[0], stroke["p"][1]] == pytest.approx(expected)
    assert any(abs(x - round(x)) > 1e-3 for x in xs)
    stats = ipad.evaluate("() => whiteboard.input.stats")
    assert stats["shellHz"] >= 30 and stats["shellMoveHz"] >= 30
    # 坐标偏差：Safari 报的是同一批采样取整之后的样子。
    # (300.37, 300.61) 对 (300, 301)，(309.37, 306.61) 对 (309, 307)
    assert stats["shellDev"] == pytest.approx(0.39)
    assert stats["shellDevN"] == 3
    mac.close()
    ipad.close()


def test_estimated_force_updates_fix_the_open_stroke_only(browser, server):
    """力度更新：还没提交的那一笔就地修正，抬笔之后才到的丢掉并计数。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad, count=24, per_batch=4)
    # 不带更新的同一笔，作对照
    plain = [{**b, "updates": []} for b in batches]
    send(ipad, plain)
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    send(ipad, [{**b, "samples": [{**s, "id": 2, "ui": s["ui"] and s["ui"] + 50000} for s in b["samples"]],
                 "pred": None,
                 "updates": [{**u, "ui": u["ui"] + 50000} for u in b["updates"]]} for b in batches])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 2")
    first, second = ipad.evaluate("() => whiteboard.state.strokes.map((s) => s.p)")
    assert len(first) == len(second)
    # 前面的点力度被改大了，后面那几个的更新在抬笔之后才到，保持原样
    assert second[2] > first[2]
    assert second[-1] == first[-1]
    stats = ipad.evaluate("() => whiteboard.input.stats")
    assert stats["shellUpd"] > 0
    assert stats["shellUpdLate"] > 0
    mac.close()
    ipad.close()


def test_prediction_is_drawn_live_but_never_stored(browser, server):
    """预测采样只画在实时层上：实时层那一笔比真实的长，提交的笔画里没有预测点。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad, count=20, per_batch=4)
    send(ipad, batches[:3])
    live, real = ipad.evaluate(
        "() => [whiteboard.renderer.liveStrokes.get('local').p.length, whiteboard.input.draw.stroke.p.length]"
    )
    assert live == real + 9, "三个预测点接在后面"
    send(ipad, batches[3:])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    xs = ipad.evaluate("() => whiteboard.state.strokes[0].p.filter((_, i) => i % 3 === 0)")
    last = ipad.evaluate("() => whiteboard.input.toWorld({ clientX: 300.37 + 3 * 19, clientY: 0 })")[0]
    assert max(xs) <= last + 1e-6
    mac.close()
    ipad.close()


def test_shell_pencil_on_the_toolbar_does_not_draw(browser, server):
    """外壳的采样落在界面控件上：整次落笔都不管，由控件自己的点击处理。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    box = ipad.locator('button[title="橡皮擦"]').bounding_box()
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    send(ipad, shell_batches(ipad, x0=x, y0=y, dx=0.2, dy=0.1, count=6, per_batch=2))
    ipad.wait_for_timeout(100)
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 0
    assert ipad.evaluate("() => whiteboard.input.draw") is None
    mac.close()
    ipad.close()


def test_palm_is_ignored_while_the_shell_pencil_writes(browser, server):
    """手掌屏蔽照样生效：外壳的笔正在写，手指落下不画、不拖动画布。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad, count=20, per_batch=4)
    send(ipad, batches[:2])
    before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    ipad.evaluate(FIRE, ["pointerdown", 600, 500, "touch", 30, 0])
    ipad.evaluate(FIRE, ["pointermove", 660, 560, "touch", 30, 0])
    ipad.evaluate(FIRE, ["pointerup", 660, 560, "touch", 30, 0])
    send(ipad, batches[2:])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == before
    mac.close()
    ipad.close()


def test_shell_recording_replays_exactly(browser, server):
    """验收标准 A4：外壳来源的录像回放之后与 ``after`` 完全一致。

    做法和 test_recorder_replays_an_erase_exactly 相同：录一段，恢复成 before，
    按时序喂回去，板上剩下的笔画要一模一样。这里一笔写、一笔像素橡皮擦，
    还混着 Safari 自己的 pen 事件和抬笔之后才到的力度更新。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    ipad.evaluate("() => whiteboard.recorder.start('外壳')")
    ipad.evaluate(FIRE, ["pointerdown", 300, 301, "pen", 7, 0.3])
    send(ipad, shell_batches(ipad, stroke_id=1, count=40))
    ipad.evaluate(FIRE, ["pointerup", 417, 379, "pen", 7, 0])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1")
    ipad.evaluate("() => { whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' }; }")
    send(ipad, shell_batches(ipad, stroke_id=2, x0=350, y0=250, dx=0.5, dy=6, count=24))
    data = ipad.evaluate("() => whiteboard.recorder.stop()")

    assert data["source"] == "shell"
    assert data["shell"] == {"version": "1.3.0", "bridge": 1}
    kinds = {e["type"] for e in data["events"]}
    assert "shell" in kinds and "pointerdown" in kinds
    # 录的是原始采样：5.1 节的字段原样保留，坐标带小数（验收标准 A2）
    first = next(e for e in data["events"] if e["type"] == "shell")
    sample = first["batch"]["samples"][0]
    assert set(sample) >= {"id", "ph", "k", "t", "x", "y", "f", "fmax", "alt", "az", "est", "ui"}
    assert sample["x"] != int(sample["x"])
    after = data["after"]
    assert len(after) >= 1 and any(s.get("m") for s in after), "橡皮那一笔应该留下遮罩"

    replayed = ipad.evaluate(
        "async ([data]) => whiteboard.recorder.replay(data, { wait: false })", [data]
    )
    assert replayed == after
    # 回放完恢复原来的输入来源
    assert ipad.evaluate("() => whiteboard.input.stats.source") == "shell"
    mac.close()
    ipad.close()


def test_stroke_is_closed_when_the_shell_never_sends_up(browser, server):
    """外壳的采样中途断了、一直没有 up：Safari 报了抬笔之后，网页替它收尾。

    0.9.43 的录像 20260927-211825 就是这样：每一笔只收到落笔后约 30 ms 的采样。
    不收尾的话这一笔一直开着，手掌屏蔽一直生效，手指的平移和缩放全部失灵。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad, count=40, per_batch=4)
    ipad.evaluate(FIRE, ["pointerdown", 300, 301, "pen", 7, 0.3])
    send(ipad, batches[:3])  # 之后外壳再没送过采样
    ipad.evaluate(FIRE, ["pointermove", 360, 340, "pen", 7, 0.3])
    # 抬笔和检查放在同一次调用里：网页 150 ms 后就会替外壳收尾，分两次调用的话
    # 机器一忙，检查时这一笔可能已经收完了
    still_open = ipad.evaluate(
        f"(args) => {{ ({FIRE})(args); return whiteboard.input.draw !== null; }}",
        ["pointerup", 417, 379, "pen", 7, 0],
    )
    assert still_open
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1", timeout=2000)
    assert ipad.evaluate("() => whiteboard.input.draw") is None
    assert ipad.evaluate("() => whiteboard.input.stats.shellOrphan") == 1

    # 外壳后来又送来这一笔的采样：不能再开出一笔
    send(ipad, batches[3:])
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1

    # 手指照常平移（等过手掌屏蔽的 500 ms）
    ipad.wait_for_timeout(600)
    before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    ipad.evaluate(FIRE, ["pointerdown", 600, 500, "touch", 30, 0])
    ipad.evaluate(FIRE, ["pointermove", 660, 560, "touch", 30, 0])
    ipad.evaluate(FIRE, ["pointerup", 660, 560, "touch", 30, 0])
    after = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    assert after != before
    mac.close()
    ipad.close()


def test_closing_an_orphan_stroke_is_recorded_and_replays(browser, server):
    """替外壳收尾的那一批也进录像，回放结果与 after 一致。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    ipad.evaluate("() => whiteboard.recorder.start('断笔')")
    ipad.evaluate(FIRE, ["pointerdown", 300, 301, "pen", 7, 0.3])
    send(ipad, shell_batches(ipad, count=40, per_batch=4)[:3])
    ipad.evaluate(FIRE, ["pointerup", 417, 379, "pen", 7, 0])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1", timeout=2000)
    data = ipad.evaluate("() => whiteboard.recorder.stop()")
    assert any(e["type"] == "shell" and e["batch"].get("orphan") for e in data["events"])
    replayed = ipad.evaluate(
        "async ([data]) => whiteboard.recorder.replay(data, { wait: false })", [data]
    )
    assert replayed == data["after"]
    mac.close()
    ipad.close()


def test_safari_pen_events_finish_a_stalled_shell_stroke(browser, server):
    """外壳断流（0.9.43 的外壳就是这样）：用 Safari 自己的 pen 事件把这一笔画完。"""
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    batches = shell_batches(ipad, count=40, per_batch=4)
    ipad.evaluate(FIRE, ["pointerdown", 300, 301, "pen", 7, 0.3])
    send(ipad, batches[:2])
    ipad.wait_for_timeout(120)  # 外壳之后再没送过采样
    for i in range(1, 11):
        ipad.evaluate(FIRE, ["pointermove", 324 + 10 * i, 316 + 5 * i, "pen", 7, 0.3])
    ipad.evaluate(FIRE, ["pointerup", 440, 370, "pen", 7, 0])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1", timeout=1000)
    stroke = ipad.evaluate("() => whiteboard.state.strokes[0].p")
    end = ipad.evaluate("() => whiteboard.input.toWorld({ clientX: 440, clientY: 370 })")
    assert stroke[-3:-1] == pytest.approx(end), "笔画一直画到 Safari 报的抬笔位置"
    stats = ipad.evaluate("() => whiteboard.input.stats")
    assert stats["shellFallback"] == 11
    assert stats["shellOrphan"] == 1
    send(ipad, batches[2:])
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1
    assert ipad.evaluate("() => whiteboard.input.draw") is None
    mac.close()
    ipad.close()


def test_safari_pen_events_draw_when_the_shell_sends_nothing(browser, server):
    """外壳整笔一个采样都没送：由 Safari 的 pen 事件起笔、画完；点一下也留下一点。

    外壳的采样到得晚了，也不能再开出第二笔。
    """
    mac, ipad = open_pages(browser, server.port)
    ipad.evaluate(HELLO, ACTIVE)
    ipad.evaluate(FIRE, ["pointerdown", 300, 300, "pen", 7, 0.3])
    ipad.wait_for_timeout(80)
    for i in range(1, 11):
        ipad.evaluate(FIRE, ["pointermove", 300 + 12 * i, 300 + 6 * i, "pen", 7, 0.3])
    ipad.evaluate(FIRE, ["pointerup", 420, 360, "pen", 7, 0])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 1", timeout=1000)
    assert ipad.evaluate("() => whiteboard.input.draw") is None
    # 外壳这一笔的采样后来才到
    send(ipad, shell_batches(ipad, stroke_id=5, x0=299.63, y0=299.39, count=12, per_batch=4))
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 1

    # 点一下就抬起
    ipad.evaluate(FIRE, ["pointerdown", 600, 500, "pen", 8, 0.3])
    ipad.evaluate(FIRE, ["pointerup", 600, 500, "pen", 8, 0])
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 2", timeout=1000)
    mac.close()
    ipad.close()

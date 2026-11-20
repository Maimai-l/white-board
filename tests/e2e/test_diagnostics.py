"""诊断：报错送回 Mac、诊断面板、录制回放、连接状态点。

iPad 上接不了开发者工具，这些是在真机上排查问题的唯一入口。
"""

from __future__ import annotations

import pytest

from e2e.support import draw, open_gallery, open_mac, open_pages, open_touch, wait_strokes

# 录一次像素擦除：切到像素橡皮、开始录制、拖一下、停止录制，返回录像
RECORD_ERASE = """
([x0, y0, x1, y1, steps, alt]) => {
  const init = { pointerId: 7, altitudeAngle: alt, azimuthAngle: 0.8 };
  whiteboard.tool = { ...whiteboard.tool, tool: 'eraser', eraserMode: 'pixel' };
  whiteboard.recorder.start('用例');
  __t.fire('pointerdown', x0, y0, init);
  for (let i = 1; i <= steps; i++) {
    __t.fire('pointermove', x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, init);
  }
  __t.fire('pointerup', x1, y1, init);
  return whiteboard.recorder.stop();
}
"""


def toggle_diagnostics(page):
    """连点左上角状态圆点三下。"""
    for _ in range(3):
        page.click("#status")


def test_page_errors_are_reported_to_the_server(browser, server):
    """iPad 上看不到控制台，页面报错必须能在 Mac 的终端里看到。"""
    mac, ipad = open_pages(browser, server.port)
    # 报错应该被送到 /api/debug：等这个请求发出来，超时就是没送
    with ipad.expect_request(lambda request: request.url.endswith("/api/debug"), timeout=5000):
        ipad.evaluate("() => setTimeout(() => { throw new Error('故意炸一个'); }, 0)")

    # 渲染循环不能因为一次异常就停摆：出错的那一帧之后还在接着画
    ipad.evaluate("() => { const t = whiteboard.renderer.tick.bind(whiteboard.renderer); window.__ticks = 0;"
                  "whiteboard.renderer.tick = () => { if (window.__ticks++ === 0) throw new Error('单帧异常'); return t(); }; }")
    ipad.wait_for_function("() => window.__ticks >= 3")
    draw(ipad, [(300, 300), (380, 350), (460, 300)])
    wait_strokes(mac, 1)


def test_a_failed_thumbnail_upload_is_reported_to_the_server(browser, server):
    """缩略图传不上去时界面上看不出来（白板列表里只是旧图），以前也不留任何记录。

    现在要上报到 /api/debug，在 Mac 的日志里能查到。
    """
    mac = open_mac(browser, server.port)

    def refuse_thumbs(route):
        if route.request.method == "POST":
            route.fulfill(status=500, body="磁盘满了")
        else:
            route.continue_()

    mac.route("**/api/thumb/*", refuse_thumbs)
    reported = lambda request: request.url.endswith("/api/debug") and "缩略图" in (request.post_data or "")
    with mac.expect_request(reported, timeout=5000) as info:
        open_gallery(mac)  # 打开白板列表前先传一次当前白板的缩略图
    assert "500" in info.value.post_data, info.value.post_data


def test_debug_overlay_toggles(browser, server):
    ipad = open_touch(browser, server.port)
    assert ipad.evaluate("() => !!document.getElementById('perf')") is False
    toggle_diagnostics(ipad)
    assert ipad.evaluate("() => whiteboard.perf.enabled") is True
    ipad.wait_for_function("() => { const n = document.getElementById('perf'); return n && n.textContent.includes('帧'); }")
    toggle_diagnostics(ipad)
    assert ipad.evaluate("() => whiteboard.perf.enabled") is False


def test_the_diagnostics_panel_says_which_build_it_is(browser, server):
    """诊断面板上要写清楚跑的是哪一份代码。

    对着一张截图讨论问题，先得确定两边说的是同一份代码。源码运行时显示
    「分支@短commit」，打包之后不是 git 仓库，退回版本号。
    """
    ipad = open_touch(browser, server.port)
    build = ipad.evaluate("() => document.documentElement.dataset.build")
    assert build, "页面上要带着版本"
    toggle_diagnostics(ipad)
    ipad.wait_for_selector("#perf")
    # 打开面板就该立刻有内容，不用等下一帧
    text = ipad.inner_text("#perf")
    assert build in text, (build, text)


def test_the_panel_separates_pen_events_from_new_positions(browser, server):
    """诊断面板要分开显示「笔每秒来多少个事件」和「其中多少个是新位置」。

    iPad 上同一个位置会被投递两遍：录像里每一笔的事件速率约 120/s，其中一半
    和前一条坐标完全相同，真正能用的位置只有约 60/s。笔迹的上限由后一个数决定
    ——采不到的那一段，再好的平滑也补不回来。真机上接不了开发者工具，这个数只能
    显示在屏幕上，改完系统设置当场就能看出有没有用。
    """
    mac = open_mac(browser, server.port)
    out = mac.evaluate("""() => {
      whiteboard.perf.toggle(true);
      const init = { pointerId: 9, pressure: 0.03, buttons: 0 };
      // 六个事件，坐标只有三个是新的——和真机上「一个位置投两遍」一样
      for (const [x, y] of [[10,10],[10,10],[20,10],[20,10],[30,10],[30,10]]) {
        __t.fire('pointermove', x, y, init);
      }
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


def test_status_dot_uses_traffic_light_colours(browser, server):
    mac = open_mac(browser, server.port)
    # 颜色是渐变过去的，等它停下来再读
    mac.wait_for_function(
        "() => getComputedStyle(document.getElementById('status'), '::after')"
        ".backgroundColor === 'rgb(40, 200, 64)'",  # macOS 红绿灯的绿
        timeout=4000,
    )


def test_recorder_replays_an_erase_exactly(browser, server):
    """录制回放要逐字复现：同一份输入喂回去，板上剩下的笔画必须一模一样。

    真笔才触发得了的问题（压感沿笔画变化、倾角一直在动、同一个位置投两遍）
    在开发机上敲不出来。录一次带回来回放，才谈得上在这里复现和验证。
    """
    ipad = open_touch(browser, server.port)
    draw(ipad, [(300, 300 + i * 4) for i in range(40)])
    wait_strokes(ipad, 1)

    data = ipad.evaluate(RECORD_ERASE, [200, 380, 420, 380, 24, 0.9])
    assert data["events"], data
    # 录像要自报跑的是哪一份代码，不然回放对不上时分不清是版本问题还是回放问题
    assert data["build"] == ipad.evaluate("() => document.documentElement.dataset.build")
    assert data["build"]
    assert data["before"] and len(data["before"]) == 1
    # 录的是原始指针事件本身，不是擦出来的结果
    assert {e["type"] for e in data["events"]} == {"pointerdown", "pointermove", "pointerup"}
    assert data["events"][0]["tool"]["eraserMode"] == "pixel"
    assert data["events"][0]["alt"] == pytest.approx(0.9, abs=1e-4)

    after = data["after"]
    assert any(s.get("m") for s in after), "这一下应该留下遮罩"

    # 回放：先把板恢复成录制开始时的样子，再把事件按时序喂回去
    replayed = ipad.evaluate(
        "async ([data]) => whiteboard.recorder.replay(data, { wait: false })", [data]
    )
    assert replayed == after


def test_recorder_panel_rides_the_diagnostics_toggle(browser, server):
    """录制跟着诊断面板一起开关：连点左上角状态圆点三下。

    iPad 是靠配置描述文件装的 Web Clip 打开的，没有地址栏，改不了查询参数，
    所以入口只能是屏幕上点得到的东西；而这个开关用户本来就知道。
    """
    ipad = open_touch(browser, server.port)
    assert ipad.locator("#rec").is_hidden()
    toggle_diagnostics(ipad)
    ipad.wait_for_selector("#rec button")
    assert ipad.evaluate("() => whiteboard.perf.enabled") is True
    assert ipad.locator("#rec button").inner_text() == "录制输入"

    ipad.click("#rec button")
    assert ipad.evaluate("() => whiteboard.recorder.recording") is True
    assert ipad.locator("#rec button").inner_text() == "停止录制"

    # 录着的时候把诊断关掉：先把录像停下来存好，不然那一段就白录了
    toggle_diagnostics(ipad)
    ipad.wait_for_function("() => whiteboard.recorder.recording === false")
    assert ipad.locator("#rec").is_hidden()

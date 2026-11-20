"""速写页面（/sketch，Edge 插件的弹出页）：iPad 跟过来书写，速写页面复制成透明 PNG。

协议和并发在 tests/test_sketch.py，插件本身在 test_extension.py。
"""

from __future__ import annotations

import pytest

from e2e.support import ENGINE, draw, new_page, open_pages, open_touch, poll_page, prepare

SKETCH_ONLINE = "() => window.sketchPad && sketchPad.status === 'online'"

READ_CLIPBOARD_PNG = """
async () => {
  const items = await navigator.clipboard.read();
  const item = items.find((i) => i.types.includes('image/png'));
  if (!item) return null;
  const blob = await item.getType('image/png');
  const bitmap = await createImageBitmap(blob);
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bitmap, 0, 0);
  const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  let dark = 0;
  for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 200 && data[i] < 80) dark += 1;
  return { width: bitmap.width, height: bitmap.height, dark, corner: Array.from(data.slice(0, 4)), size: blob.size };
}
"""

CACHED_KEYS = """
async () => {
  const names = (await indexedDB.databases()).map((d) => d.name).filter((n) => n && n.startsWith('inksync'));
  const keys = [];
  for (const name of names) {
    const db = await new Promise((ok, fail) => { const r = indexedDB.open(name); r.onsuccess = () => ok(r.result); r.onerror = fail; });
    if (!db.objectStoreNames.contains('kv')) { db.close(); continue; }
    const all = await new Promise((ok) => { const r = db.transaction('kv').objectStore('kv').getAllKeys(); r.onsuccess = () => ok(r.result); });
    const last = await new Promise((ok) => { const r = db.transaction('kv').objectStore('kv').get('last'); r.onsuccess = () => ok(r.result); });
    keys.push(...all.map((k) => `${name}/${k}`), `${name}/last=${last}`);
    db.close();
  }
  return keys;
}
"""

# 速写页面上手写板画布里，左右两半各有多少深色像素
INK_HALVES = """() => {
  const canvas = document.querySelector('#pad .inkpad-stage canvas');
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const half = (x0, x1) => {
    const data = ctx.getImageData(x0, 0, x1 - x0, height).data;
    let dark = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 200 && data[i] < 200) dark += 1;
    return dark;
  };
  return [half(0, Math.floor(width / 2)), half(Math.floor(width / 2), width)];
}"""

# 速写页面上手写板画布中的深色像素：看得到 iPad 写的笔迹就不为零
INK_PIXELS = """() => {
  const canvas = document.querySelector('#pad .inkpad-stage canvas');
  const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  let dark = 0;
  for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 200 && data[i] < 100) dark += 1;
  return dark;
}"""

chromium_only = pytest.mark.skipif(ENGINE != "chromium", reason="剪贴板读写和断网只在 Chromium 上测")


def open_sketch(browser, port, viewport=(380, 760)):
    """速写页面：默认是窄长的页面（各种窗口大小另有用例）。可以读写剪贴板。"""
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    if ENGINE == "chromium":
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{port}")
    page = prepare(context.new_page())
    page.goto(f"http://127.0.0.1:{port}/sketch")
    page.wait_for_function(SKETCH_ONLINE)
    return page


def sketch_strokes(page, count, timeout=None):
    script = f"() => sketchPad.snapshot().strokes.length === {count}"
    if timeout is None:
        page.wait_for_function(script)
    else:
        page.wait_for_function(script, timeout=timeout)


def wait_on_sketch(*pages):
    for page in pages:
        page.wait_for_function("() => whiteboard.state.id === 'sketch'")


def copy_and_read(sketch):
    sketch.click("#copy")
    sketch.wait_for_function("() => document.getElementById('notice').textContent.includes('已复制')")
    return sketch.evaluate(READ_CLIPBOARD_PNG)


def test_ipad_follows_a_sketch_and_returns(browser, server):
    """打开速写页面：iPad 和 Mac 窗口切到速写板；iPad 上写的内容实时出现在速写页面；
    关闭速写页面后都回到原来的白板，iPad 的本地缓存里没有速写板。"""
    server.hub.sketch_grace = 0.3
    mac, ipad = open_pages(browser, server.port)
    original = ipad.evaluate("() => whiteboard.state.id")
    sketch = open_sketch(browser, server.port)
    wait_on_sketch(mac, ipad)

    draw(ipad, [(300, 300), (360, 340), (430, 310), (500, 380)])
    draw(ipad, [(320, 420), (480, 420)], pointer_id=2)
    sketch_strokes(sketch, 2)
    mac.wait_for_function("() => whiteboard.state.strokes.length === 2")
    sketch.wait_for_function("() => !document.getElementById('copy').disabled")

    sketch.close()
    for page in (mac, ipad):
        page.wait_for_function("(id) => whiteboard.state.id === id", arg=original, timeout=10000)
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 0
    # 等本地缓存记下「最后打开的是原来那块」：切回去之后该写的都写过了，再看里面有没有速写板
    keys = poll_page(
        ipad,
        f"async (id) => {{ const keys = await ({CACHED_KEYS})();"
        " return keys.some((k) => k.endsWith(`/last=${id}`)) && keys; }",
        original,
    )
    assert keys, "本地缓存一直没记下切回去的那块白板"
    assert not [k for k in keys if "sketch" in k], keys
    assert not server.hub.sketch_active


@chromium_only
def test_sketch_copies_a_png_of_the_ink(browser, server):
    ipad = open_touch(browser, server.port)
    sketch = open_sketch(browser, server.port)
    wait_on_sketch(ipad)
    assert sketch.evaluate("() => document.getElementById('copy').disabled"), "空白时不能复制"

    draw(ipad, [(300, 300), (420, 360), (540, 300)])
    sketch_strokes(sketch, 1)
    image = copy_and_read(sketch)
    assert image is not None and image["dark"] > 50, image
    # 只导出笔迹所在范围（加边距），按 2 倍分辨率：240×60 的笔迹，加上四周各 64 的边距
    assert 600 <= image["width"] <= 800 and 300 <= image["height"] <= 420, image
    assert image["corner"][3] == 0, "透明背景"

    # ⌘C（这里是 Ctrl+C）同样复制
    sketch.evaluate("() => navigator.clipboard.writeText('')")
    sketch.keyboard.press("Control+c")
    image = poll_page(sketch, READ_CLIPBOARD_PNG)  # 等剪贴板里出现图片
    assert image is not None and image["dark"] > 50, image

    sketch.click("#clear")
    sketch_strokes(sketch, 0)
    ipad.wait_for_function("() => whiteboard.state.strokes.length === 0")
    assert sketch.evaluate("() => document.getElementById('copy').disabled")


@chromium_only
def test_a_sprawling_sketch_is_exported_within_size_limits(browser, server):
    """笔画相隔很远（无限画布）：导出的图片包含全部笔迹，但边长不超过 4096 像素。"""
    ipad = open_touch(browser, server.port)
    sketch = open_sketch(browser, server.port)
    wait_on_sketch(ipad)
    for i, (x, y) in enumerate([(-6000, -4000), (6000, 4000), (0, 0)]):
        stroke = {"id": f"far-{i}", "tool": "pen", "color": "#000000", "w": 4, "p": [x, y, 0.6, x + 300, y + 200, 0.6]}
        ipad.evaluate("(s) => whiteboard.net.sendOp({ op: 'add', strokes: [s] })", stroke)
    sketch_strokes(sketch, 3, timeout=10000)
    image = copy_and_read(sketch)
    assert image["width"] <= 4096 and image["height"] <= 4096, image
    assert image["width"] > 1000 and image["dark"] > 30, image


def test_sketch_page_starts_over_when_the_sketch_is_ended_elsewhere(browser, server):
    """速写在别处结束（或服务端重启）：速写页面自动开一块新的空白速写板，iPad 再次跟过来。"""
    mac, ipad = open_pages(browser, server.port)
    original = ipad.evaluate("() => whiteboard.state.id")
    sketch = open_sketch(browser, server.port)
    wait_on_sketch(ipad)
    draw(ipad, [(300, 300), (400, 380)])
    sketch_strokes(sketch, 1)

    mac.evaluate("() => fetch('/api/sketch', { method: 'DELETE' })")
    sketch.wait_for_function(
        "() => sketchPad.snapshot().strokes.length === 0 && sketchPad.status === 'online'", timeout=10000
    )
    ipad.wait_for_function(
        "() => whiteboard.state.id === 'sketch' && whiteboard.state.strokes.length === 0", timeout=10000
    )
    assert server.hub.current_id == original


def test_sketch_page_explains_missing_permission(browser, server, monkeypatch):
    """没有「管理白板」权限的设备打开速写页面：说明原因，不连接。"""
    from whiteboard import server as server_module

    monkeypatch.setattr(server_module, "permissions", lambda request: frozenset())
    page = new_page(browser, viewport=(380, 700))
    page.goto(f"http://127.0.0.1:{server.port}/sketch")
    page.wait_for_function("() => document.getElementById('notice').textContent.includes('管理白板')")
    assert page.evaluate("() => window.sketchPad === undefined")
    assert not server.hub.sketch_active


@chromium_only
def test_ipad_offline_ink_from_a_finished_sketch_does_not_leak(browser, server):
    """iPad 在速写中断网并继续写；断网期间速写结束；iPad 恢复网络后回到原来的白板，
    断网时写的笔画不会出现在原来的白板上。"""
    server.hub.sketch_grace = 0.2
    mac, ipad = open_pages(browser, server.port)
    original = ipad.evaluate("() => whiteboard.state.id")
    sketch = open_sketch(browser, server.port)
    wait_on_sketch(ipad)
    draw(ipad, [(300, 300), (360, 340)])
    sketch_strokes(sketch, 1)

    # 断网：Playwright 的离线模拟不会断开已经建立的 WebSocket，这里同时关掉它；离线期间重连失败
    ipad.context.set_offline(True)
    ipad.evaluate("() => whiteboard.net.ws && whiteboard.net.ws.close()")
    ipad.wait_for_function("() => whiteboard.net.status === 'offline'", timeout=15000)
    draw(ipad, [(400, 400), (480, 450)], pointer_id=2)
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 2
    sketch.close()
    mac.wait_for_function("(id) => whiteboard.state.id === id", arg=original, timeout=10000)

    ipad.context.set_offline(False)
    ipad.wait_for_function(
        "(id) => whiteboard.net.status === 'online' && whiteboard.state.id === id", arg=original, timeout=20000
    )
    ipad.wait_for_function("() => whiteboard.net.outbox.length === 0", timeout=10000)
    # 待发的操作都有了回执：服务端已经处理完。先看服务端——Mac 上的笔画只能来自
    # 服务端的广播，服务端那块白板是空的，Mac 就不会收到什么
    assert server.hub.core.board(original).stroke_list() == []
    assert ipad.evaluate("() => whiteboard.state.strokes.length") == 0
    assert mac.evaluate("() => whiteboard.state.strokes.length") == 0


@pytest.mark.parametrize("viewport", [(360, 270), (380, 760), (900, 680)])
def test_sketch_view_follows_ink_written_anywhere_on_the_ipad(browser, server, viewport):
    """iPad 屏幕比速写窗口大得多：写在 iPad 右下角、左上角的笔迹都要出现在速写窗口里，
    不论速写窗口是小浮窗、窄长的窗口还是展开的大窗口。"""
    ipad = open_touch(browser, server.port)
    sketch = open_sketch(browser, server.port, viewport=viewport)
    wait_on_sketch(ipad)
    draw(ipad, [(1000, 700), (1080, 760), (1150, 720)])
    sketch_strokes(sketch, 1)
    sketch.wait_for_function(f"({INK_PIXELS})() > 20", timeout=5000)
    draw(ipad, [(40, 40), (120, 90)], pointer_id=2)
    sketch_strokes(sketch, 2)
    # 两笔都在视野里：一笔在右下角，一笔在左上角，视图缩到能同时看见两笔（缩小后笔画变细变淡）。
    # 视图是停笔一会儿之后才调整的（FIT_DELAY），等画面上左右两半都有墨
    sketch.wait_for_function(f"() => ({INK_HALVES})().every((dark) => dark > 0)", timeout=5000)
    left, right = sketch.evaluate(INK_HALVES)
    assert left > 0 and right > 0, (left, right)


def test_sketch_page_fits_the_toolbar_popup(browser, server):
    """插件的工具栏弹出页（420×360）：只有画布、状态点、「清空」和「复制」，按钮全部可见，
    画布占大部分高度，复制前后都不出现多余的控件。"""
    sketch = open_sketch(browser, server.port, viewport=(420, 360))
    layout = sketch.evaluate("""() => {
      const box = (id) => document.getElementById(id).getBoundingClientRect();
      return { copy: [box('copy').right, box('copy').bottom], pad: box('pad').height,
               buttons: Array.from(document.querySelectorAll('button')).map((b) => b.id),
               inputs: document.querySelectorAll('input, select, img').length,
               state: document.getElementById('status').dataset.state };
    }""")
    assert layout["copy"][0] <= 420 and layout["copy"][1] <= 360, layout
    assert layout["pad"] >= 280, layout
    assert layout["buttons"] == ["clear", "copy"] and layout["inputs"] == 0, layout
    assert layout["state"] == "online"

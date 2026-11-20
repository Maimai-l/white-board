"""iPad 上的尺子：笔具盘里开关，两指拖动旋转，贴着尺边画直线；尺子不同步，线照常同步。

验收标准见 docs/backlog/003-ruler.md。尺子的位置都从页面上画出来的尺身量，不读内部状态；
存下来的笔画从白板状态里读（和其余用例一样）。
"""

from __future__ import annotations

import math

from e2e.support import (
    draw,
    enable_pk_picker,
    fire,
    open_pages,
    open_touch,
    wait_strokes,
)

RULER_BUTTON = '#pk-host button[title="尺子"]'
RULER = "#stage .ruler"
BAR = "#stage .ruler-bar"
LABEL = "#stage .ruler-angle"


def show_ruler(ipad):
    """在笔具盘里点尺子按钮，等尺子出现。"""
    enable_pk_picker(ipad)
    ipad.click(RULER_BUTTON)
    ipad.wait_for_selector(RULER, state="visible")


def ruler_on_screen(page):
    """画出来的尺身：中心、厚度和角度（度）。从尺身元素的位置和变换矩阵量出来。"""
    return page.evaluate(
        """() => {
          const bar = document.querySelector('#stage .ruler-bar');
          const r = bar.getBoundingClientRect();
          const m = new DOMMatrix(getComputedStyle(bar).transform);
          return {
            cx: (r.left + r.right) / 2,
            cy: (r.top + r.bottom) / 2,
            width: bar.offsetHeight,
            angle: Math.atan2(m.b, m.a) * 180 / Math.PI,
          };
        }"""
    )


def angle_label(page):
    return page.inner_text(LABEL)


def two_fingers(page, start, end, ids=(31, 32), steps=10):
    """两根手指从 start 的两个点同时挪到 end 的两个点，每一步都按两指同时动。"""
    for i in range(steps + 1):
        kind = "down" if i == 0 else "move"
        for finger, pointer_id in enumerate(ids):
            x = start[finger][0] + (end[finger][0] - start[finger][0]) * i / steps
            y = start[finger][1] + (end[finger][1] - start[finger][1]) * i / steps
            fire(page, kind, x, y, "touch", pointer_id, 0.5)
    for finger, pointer_id in enumerate(ids):
        fire(page, "up", *end[finger], "touch", pointer_id, 0)


def rotate_on_ruler(page, ruler, degrees, radius=120, ids=(31, 32), steps=10):
    """两根手指沿尺子按在尺身上、以尺子中心为中点再转 degrees 度（屏幕上顺时针为正）。"""
    cx, cy = ruler["cx"], ruler["cy"]
    for i in range(steps + 1):
        a = math.radians(ruler["angle"] + degrees * i / steps)
        dx, dy = radius * math.cos(a), radius * math.sin(a)
        kind = "down" if i == 0 else "move"
        fire(page, kind, cx - dx, cy - dy, "touch", ids[0], 0.5)
        fire(page, kind, cx + dx, cy + dy, "touch", ids[1], 0.5)
    a = math.radians(ruler["angle"] + degrees)
    fire(page, "up", cx - radius * math.cos(a), cy - radius * math.sin(a), "touch", ids[0], 0)
    fire(page, "up", cx + radius * math.cos(a), cy + radius * math.sin(a), "touch", ids[1], 0)


def upper_edge(ruler):
    """尺子「上边」那条长边（角度 0 时在上面）：线上一点和单位方向，屏幕坐标。"""
    a = math.radians(ruler["angle"])
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux  # 指向尺子「下边」的法向
    half = ruler["width"] / 2
    return (ruler["cx"] - nx * half, ruler["cy"] - ny * half), (ux, uy), (nx, ny)


def to_board(page, x, y):
    """屏幕（视口）坐标 → 白板坐标。"""
    return page.evaluate(
        """([x, y]) => {
          const r = document.getElementById('stage').getBoundingClientRect();
          return whiteboard.viewport.toWorld(x - r.left, y - r.top);
        }""",
        [x, y],
    )


def stroke_points(page, index=0):
    return page.evaluate(
        "(i) => { const p = whiteboard.state.strokes[i].p; const out = [];"
        " for (let k = 0; k < p.length; k += 3) out.push([p[k], p[k + 1]]); return out; }",
        index,
    )


def distances_to_line(points, origin, direction):
    ux, uy = direction
    return [abs((x - origin[0]) * uy - (y - origin[1]) * ux) for x, y in points]


# ----------------------------------------------------------------- 验收


def test_two_fingers_rotate_the_ruler_and_show_the_angle(browser, server):
    """打开尺子后，两指旋转到 30°，显示的角度为 30°。"""
    ipad = open_touch(browser, server.port, picker=True)
    show_ruler(ipad)
    ruler = ruler_on_screen(ipad)
    assert angle_label(ipad) == "0°"
    view_before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y, whiteboard.viewport.scale]")

    rotate_on_ruler(ipad, ruler, 30)

    assert angle_label(ipad) == "30°"
    turned = ruler_on_screen(ipad)
    assert abs(turned["angle"] - 30) < 0.01
    # 两指绕中点转：尺子中心不动，画布也不动
    assert abs(turned["cx"] - ruler["cx"]) < 0.5 and abs(turned["cy"] - ruler["cy"]) < 0.5
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y, whiteboard.viewport.scale]") == view_before

    # 往回转过 0° 到另一边，显示的是和水平线的夹角（0–90），和 iPad 一样
    rotate_on_ruler(ipad, turned, -70)
    assert angle_label(ipad) == "40°"
    assert abs(ruler_on_screen(ipad)["angle"] + 40) < 0.01


def label_opacity(page):
    return float(page.evaluate("(s) => getComputedStyle(document.querySelector(s)).opacity", LABEL))


def test_the_angle_shows_only_while_the_ruler_turns(browser, server):
    """角度和 iPad 一样只在转动时浮现：刚打开时看不到，转的时候出现，停下一会儿后淡出。"""
    ipad = open_touch(browser, server.port, picker=True)
    show_ruler(ipad)
    assert label_opacity(ipad) == 0
    rotate_on_ruler(ipad, ruler_on_screen(ipad), 30)
    ipad.wait_for_function(
        "(s) => Number(getComputedStyle(document.querySelector(s)).opacity) > 0.8", arg=LABEL
    )
    ipad.wait_for_function(
        "(s) => Number(getComputedStyle(document.querySelector(s)).opacity) === 0", arg=LABEL, timeout=3000
    )
    assert angle_label(ipad) == "30°"


def test_the_ruler_button_is_drawn_like_the_pens(browser, server):
    """笔具盘里的尺子按钮和笔、橡皮一样是那份 PencilKit 复刻里画好的工具图，打开时升起来。"""
    ipad = open_touch(browser, server.port, picker=True)
    enable_pk_picker(ipad)
    art = ipad.evaluate(
        """() => [...document.querySelectorAll('#pk-host [data-tool="eraser"], #pk-host button[title="尺子"]')]
            .map((b) => {
              const img = b.querySelector('img.pk-art-base');
              return img && img.complete && img.naturalWidth > 0 ? img.getBoundingClientRect().bottom : null;
            })"""
    )
    assert None not in art and len(art) == 2
    # 两个工具图的底边对齐（站在同一排里）
    assert abs(art[0] - art[1]) < 1
    ipad.click(RULER_BUTTON)
    ipad.wait_for_selector(RULER, state="visible")
    assert ipad.get_attribute(RULER_BUTTON, "aria-pressed") == "true"
    ipad.click(RULER_BUTTON)
    ipad.wait_for_selector(RULER, state="hidden")
    assert ipad.get_attribute(RULER_BUTTON, "aria-pressed") == "false"


def test_a_stroke_along_the_ruler_is_straight_on_both_devices(browser, server):
    """笔沿尺边画一笔：存下来的笔画上所有点到尺边的距离不超过 1 个白板单位；Mac 端看到同一条线。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    show_ruler(ipad)
    start = ruler_on_screen(ipad)
    rotate_on_ruler(ipad, start, 30)
    ruler = ruler_on_screen(ipad)
    (ex, ey), (ux, uy), (nx, ny) = upper_edge(ruler)

    # 手不稳：从尺边外 8 像素起笔，沿着尺边走 300 像素，离边忽远忽近（两边都有）
    path = []
    for k in range(21):
        t = -150 + 15 * k
        off = 8 + 9 * math.sin(k * 1.3)  # 正数在尺子外面，负数压到尺身上
        path.append((ex + ux * t - nx * off, ey + uy * t - ny * off))
    draw(ipad, path, pointer_type="pen", pressure=0.6)
    wait_strokes(ipad, 1)
    wait_strokes(mac, 1)

    origin = to_board(ipad, ex, ey)
    points = stroke_points(ipad)
    assert len(points) >= 10
    assert max(distances_to_line(points, origin, (ux, uy))) <= 1
    span = math.dist(points[0], points[-1])
    assert span > 250, f"笔画应该沿尺边走完整段，只有 {span:.0f}"

    # Mac 上是同一条线
    mac.wait_for_function(
        "(n) => whiteboard.state.strokes[0] && whiteboard.state.strokes[0].p.length === n",
        arg=len(points) * 3,
    )
    on_mac = stroke_points(mac)
    assert max(math.dist(a, b) for a, b in zip(on_mac, points)) < 0.05
    assert max(distances_to_line(on_mac, origin, (ux, uy))) <= 1


def test_the_mac_page_does_not_show_the_ruler(browser, server):
    """Mac 页面上看不到尺子：iPad 上开着尺子、沿尺边画的线同步过来，Mac 上既没有尺子也没有按钮。"""
    mac, ipad = open_pages(browser, server.port, picker=True)
    show_ruler(ipad)
    ruler = ruler_on_screen(ipad)
    (ex, ey), (ux, uy), (nx, ny) = upper_edge(ruler)
    draw(ipad, [(ex - 100 - nx * 6, ey - ny * 6), (ex, ey - 4), (ex + 100, ey - 10)], pointer_type="pen")
    wait_strokes(mac, 1)

    assert mac.locator(RULER).count() == 0
    assert mac.locator(BAR).count() == 0
    assert mac.locator('button[title="尺子"]').count() == 0


# ----------------------------------------------------------------- 手指归谁


def test_fingers_off_the_ruler_still_pan_and_zoom_the_board(browser, server):
    """尺子显示着：两指落在尺子外面照样缩放画布；一指在尺子上、一指在外面也是缩放画布，
    尺子留在原地。"""
    ipad = open_touch(browser, server.port, picker=True)
    show_ruler(ipad)
    ruler = ruler_on_screen(ipad)
    cx, cy = ruler["cx"], ruler["cy"]
    scale = lambda: ipad.evaluate("() => whiteboard.viewport.scale")  # noqa: E731

    before = scale()
    two_fingers(ipad, [(cx - 100, cy + 200), (cx + 100, cy + 200)], [(cx - 180, cy + 200), (cx + 180, cy + 200)])
    zoomed = scale()
    assert zoomed > before * 1.5
    assert ruler_on_screen(ipad) == ruler
    assert angle_label(ipad) == "0°"

    # 第一根手指在尺身上，第二根在外面：是捏合画布，不是转尺子
    two_fingers(
        ipad,
        [(cx - 60, cy), (cx + 60, cy + 200)],
        [(cx - 30, cy), (cx + 30, cy + 100)],
        ids=(41, 42),
    )
    assert scale() < zoomed * 0.8
    assert angle_label(ipad) == "0°"
    after = ruler_on_screen(ipad)
    assert abs(after["angle"]) < 0.01


def test_one_finger_moves_the_ruler_and_the_pen_only_snaps_near_it(browser, server):
    """一根手指在尺身上拖动尺子（画布不动）；离尺子远的笔画照手写的样子留下；
    尺子收起之后，原来尺边的位置也不再吸笔。"""
    ipad = open_touch(browser, server.port, picker=True)
    show_ruler(ipad)
    ruler = ruler_on_screen(ipad)
    view_before = ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]")
    draw(ipad, [(ruler["cx"], ruler["cy"]), (ruler["cx"] + 40, ruler["cy"] - 120)], pointer_type="touch", pointer_id=51)
    moved = ruler_on_screen(ipad)
    assert abs(moved["cx"] - ruler["cx"] - 40) < 0.5 and abs(moved["cy"] - ruler["cy"] + 120) < 0.5
    assert ipad.evaluate("() => [whiteboard.viewport.x, whiteboard.viewport.y]") == view_before

    def wobble(y0):
        return [(400 + 20 * k, y0 + (12 if k % 2 else -12)) for k in range(12)]

    def deviation(points):
        (x0, y0), (x1, y1) = points[0], points[-1]
        length = math.dist(points[0], points[-1])
        return max(abs((x - x0) * (y1 - y0) - (y - y0) * (x1 - x0)) / length for x, y in points)

    # 离尺边 100 多像素：不吸
    (ex, ey), _, _ = upper_edge(moved)
    draw(ipad, wobble(ey - 140), pointer_type="pen")
    wait_strokes(ipad, 1)
    assert deviation(stroke_points(ipad, 0)) > 5

    # 贴着尺边：吸成直线
    draw(ipad, wobble(ey - 6), pointer_type="pen")
    wait_strokes(ipad, 2)
    assert deviation(stroke_points(ipad, 1)) < 0.5

    # 收起尺子：同一个位置不再吸
    ipad.click(RULER_BUTTON)
    ipad.wait_for_selector(RULER, state="hidden")
    draw(ipad, wobble(ey - 6), pointer_type="pen")
    wait_strokes(ipad, 3)
    assert deviation(stroke_points(ipad, 2)) > 5

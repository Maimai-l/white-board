"""Mac 窗口的标题栏（whiteboard/titlebar.py）和主窗口的参数（app.window_options）。

真的红绿灯只能在 Mac 上看（发版检查表）。这里用一个假的 AppKit 检查摆放的几何：
三个按钮在 52 高的顶栏里垂直居中、从左边 20 开始排；标题栏那层只盖到绿灯右边，
不挡网页顶栏里的按钮；全屏时不动；双击按系统设置放大或最小化。
"""

from __future__ import annotations

import sys
import types

from whiteboard import app, titlebar


class Point:
    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = x, y


class Size:
    def __init__(self, width=0.0, height=0.0):
        self.width, self.height = width, height


class Rect:
    def __init__(self, x, y, w, h):
        self.origin, self.size = Point(x, y), Size(w, h)


class View:
    def __init__(self, frame, parent=None):
        self._frame, self._parent = frame, parent
        self.hidden = True

    def frame(self):
        return self._frame

    def setFrame_(self, frame):
        self._frame = frame

    def setFrameOrigin_(self, point):
        self._frame.origin = point

    def superview(self):
        return self._parent

    def setHidden_(self, hidden):
        self.hidden = hidden


class Window:
    def __init__(self, fullscreen=False):
        self.container = View(Rect(0, 832, 1280, 28))
        inner = View(Rect(0, 0, 1280, 28), self.container)
        self.buttons = [View(Rect(7 + 20 * i, 6, 14, 16), inner) for i in range(3)]
        self.mask = titlebar.FULLSCREEN_MASK if fullscreen else 0
        self.calls = []

    def styleMask(self):
        return self.mask

    def standardWindowButton_(self, kind):
        return self.buttons[kind]

    def frame(self):
        return Rect(100, 100, 1280, 860)

    def performZoom_(self, _sender):
        self.calls.append("zoom")

    def performMiniaturize_(self, _sender):
        self.calls.append("minimize")


def fake_appkit(monkeypatch, double_click=None):
    defaults = types.SimpleNamespace(stringForKey_=lambda key: double_click)
    module = types.SimpleNamespace(
        NSWindowCloseButton=0,
        NSWindowMiniaturizeButton=1,
        NSWindowZoomButton=2,
        NSMakePoint=Point,
        NSUserDefaults=types.SimpleNamespace(standardUserDefaults=lambda: defaults),
    )
    monkeypatch.setitem(sys.modules, "AppKit", module)


def test_the_lights_are_centred_in_the_title_bar(monkeypatch):
    fake_appkit(monkeypatch)
    window = Window()
    assert titlebar.place_traffic_lights(window)
    assert all(not button.hidden for button in window.buttons)  # frameless 藏起来的，放出来
    origins = [(b.frame().origin.x, b.frame().origin.y) for b in window.buttons]
    assert origins == [(20, 18), (42, 18), (64, 18)]  # 14 宽、间隔 8；(52 - 16) / 2 = 18
    frame = window.container.frame()
    assert frame.size.height == titlebar.BAR_HEIGHT
    assert frame.origin.y == 860 - titlebar.BAR_HEIGHT  # 贴着窗口顶边
    assert frame.size.width == 64 + 14 + titlebar.CONTAINER_PAD  # 只盖到绿灯右边


def test_full_screen_is_left_to_the_system(monkeypatch):
    fake_appkit(monkeypatch)
    window = Window(fullscreen=True)
    assert not titlebar.place_traffic_lights(window)
    assert window.buttons[0].frame().origin.x == 7


def test_double_click_follows_the_system_setting(monkeypatch):
    for setting, expected in (("Maximize", ["zoom"]), (None, ["zoom"]), ("Minimize", ["minimize"]), ("None", [])):
        fake_appkit(monkeypatch, setting)
        window = Window()
        titlebar.zoom_on_double_click(window)
        assert window.calls == expected, setting


def test_the_page_knows_whether_to_leave_room_for_the_lights():
    assert titlebar.window_mode(False) == "inset"
    assert titlebar.window_mode(True) == "full"
    # 和 app.css 的 --lights 一致：三个 12 的按钮从 20 排到 72
    assert titlebar.light_origins([(12, 12)] * 3)[-1][0] + 12 == 72


def test_the_mac_window_has_no_system_title_bar():
    """带着系统标题栏时，点一下顶栏窗口会往下跳一个标题栏的高度（titlebar.py 开头）。"""
    mac = app.window_options(8848, "darwin")
    assert mac["frameless"] is True and mac["easy_drag"] is False
    assert mac["url"] == "http://127.0.0.1:8848/?role=mac&window=inset"
    other = app.window_options(8848, "linux")
    assert other["frameless"] is False
    assert "window=" not in other["url"]

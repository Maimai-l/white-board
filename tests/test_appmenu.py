"""Mac 菜单栏（whiteboard/appmenu.py）：中文、应用菜单里的检查更新、关于打开应用自己的窗口。

菜单本身由 pywebview 在 Mac 上建，只能在真机上看（发版检查表）。这里测的是不依赖 AppKit 的部分。
"""

from __future__ import annotations

from whiteboard import app, appmenu, resources
from whiteboard.config import Config

# pywebview 6.2 的 webview/localization.py 里 macOS 菜单用到的键：每一个都要有中文
COCOA_KEYS = {
    "cocoa.menu.about",
    "cocoa.menu.services",
    "cocoa.menu.view",
    "cocoa.menu.edit",
    "cocoa.menu.hide",
    "cocoa.menu.hideOthers",
    "cocoa.menu.showAll",
    "cocoa.menu.quit",
    "cocoa.menu.fullscreen",
    "cocoa.menu.cut",
    "cocoa.menu.copy",
    "cocoa.menu.paste",
    "cocoa.menu.selectAll",
}


def test_every_menu_string_is_chinese():
    assert COCOA_KEYS <= set(appmenu.LOCALIZATION)
    for key, text in appmenu.LOCALIZATION.items():
        assert text and not text.isascii(), key


def test_the_app_name_follows_without_a_space():
    """pywebview 拼出来的是「关于 白板」；中文应用名前面不留空格，拉丁字母的应用名照旧。"""
    assert appmenu.tidy_title("关于 白板") == "关于白板"
    assert appmenu.tidy_title("隐藏 白板") == "隐藏白板"
    assert appmenu.tidy_title("退出 白板") == "退出白板"
    assert appmenu.tidy_title("隐藏其他") == "隐藏其他"
    assert appmenu.tidy_title("关于 Python", "Python") == "关于 Python"


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


def test_check_for_updates_in_the_app_menu(tmp_path, monkeypatch):
    """应用菜单里的检查更新：交给 Sparkle 时窗口里不说话（Sparkle 有自己的窗口）；
    从源码运行或者没有 Sparkle 时，窗口里提示一句。"""
    api = app.NativeApi(Config(tmp_path / "config.json"), server=None)
    api.window = FakeWindow()

    api._menu_check_update()
    assert api.window.scripts == ['whiteboard.ui.message("源码运行时用 git pull 更新")']

    monkeypatch.setattr(resources, "is_frozen", lambda: True)
    api.window.scripts.clear()
    api._menu_check_update()
    assert api.window.scripts == ['whiteboard.ui.message("这个版本不能自动更新")']

    monkeypatch.setattr(api.sparkle, "check_now", lambda: True)
    api.window.scripts.clear()
    api._menu_check_update()
    assert api.window.scripts == []


def test_about_in_the_app_menu_opens_the_apps_own_about(tmp_path):
    api = app.NativeApi(Config(tmp_path / "config.json"), server=None)
    api.window = FakeWindow()
    api._menu_about()
    assert api.window.scripts == ["whiteboard.ui.openAbout()"]


def test_menu_methods_are_not_exposed_to_the_page():
    """pywebview 把 js_api 上不以下划线开头的方法暴露给网页；菜单用的两个不该在里面。"""
    exposed = {name for name in dir(app.NativeApi) if not name.startswith("_")}
    assert not {"_menu_about", "_menu_check_update"} & exposed

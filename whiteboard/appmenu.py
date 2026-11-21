"""Mac 菜单栏：中文、应用菜单里的「检查更新…」、「关于白板」打开应用自己的关于窗口。

菜单由 pywebview 建（webview.start 的 ``menu`` 和 ``localization``）：

- 文字全部换成中文，和界面一致（pywebview 默认是英文）。
- 应用菜单里「关于」下面加一项「检查更新…」，这是 Sparkle 应用的惯例位置。
  pywebview 把标题为 ``__app__`` 的菜单里的项放进应用菜单，位置在「关于」和「服务」之间。
- pywebview 在「关于」「隐藏」「退出」后面接应用名时会加一个空格（「关于 白板」），
  中文应用名不该有这个空格；「关于」还会打开系统的关于面板，和应用里的关于窗口是
  两个东西。这两点在菜单建好之后，由 :func:`tidy_app_menu` 在主线程上改过来。

只在 macOS 的 pywebview 窗口里有意义；别处调用这里的函数什么也不做。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

APP_NAME = "白板"

# pywebview 的默认文字（webview/localization.py）换成中文。用词照 macOS 自带应用。
LOCALIZATION: Dict[str, str] = {
    "global.quitConfirmation": "要退出白板吗",
    "global.ok": "好",
    "global.quit": "退出",
    "global.cancel": "取消",
    "global.saveFile": "存储文件",
    "cocoa.menu.about": "关于",
    "cocoa.menu.services": "服务",
    "cocoa.menu.view": "显示",
    "cocoa.menu.edit": "编辑",
    "cocoa.menu.hide": "隐藏",
    "cocoa.menu.hideOthers": "隐藏其他",
    "cocoa.menu.showAll": "全部显示",
    "cocoa.menu.quit": "退出",
    "cocoa.menu.fullscreen": "进入全屏幕",
    "cocoa.menu.cut": "剪切",
    "cocoa.menu.copy": "拷贝",
    "cocoa.menu.paste": "粘贴",
    "cocoa.menu.selectAll": "全选",
}

CHECK_UPDATE = "检查更新…"
ABOUT_ACTION = "whiteboard.about"


def tidy_title(title: str, app_name: str = APP_NAME) -> str:
    """「关于 白板」→「关于白板」：中文应用名前面不留空格（拉丁字母的应用名照旧）。"""
    spaced = f" {app_name}"
    if app_name and not app_name.isascii() and title.endswith(spaced):
        return title[: -len(spaced)] + app_name
    return title


def build_menu(on_check_update: Callable[[], Any]) -> List[Any]:
    """给 webview.start(menu=...) 的菜单：应用菜单里加「检查更新…」。"""
    from webview.menu import Menu, MenuAction

    return [Menu("__app__", [MenuAction(CHECK_UPDATE, on_check_update)])]


def tidy_app_menu(on_about: Callable[[], Any]) -> bool:
    """在主线程调用：去掉应用名前的空格，「关于白板」改成打开应用自己的关于窗口。

    出错只记日志：菜单里多一个空格、关于打开系统面板，都不影响用。
    """
    if sys.platform != "darwin":
        return False
    try:
        import AppKit
        from webview.platforms import cocoa

        menu = AppKit.NSApp.mainMenu()
        app_item = menu.itemAtIndex_(0) if menu is not None and menu.numberOfItems() else None
        app_menu = app_item.submenu() if app_item is not None else None
        if app_menu is None:
            log.warning("appmenu.tidy_app_menu：找不到应用菜单")
            return False
        for index in range(app_menu.numberOfItems()):
            item = app_menu.itemAtIndex_(index)
            title = str(item.title() or "")
            tidy = tidy_title(title)
            if tidy != title:
                item.setTitle_(tidy)
            if item.action() == "orderFrontStandardAboutPanel:":
                # 借 pywebview 自己的菜单分发：和 MenuAction 一样挂到 menu_handler 上
                cocoa.menu_handler.register_action(ABOUT_ACTION, on_about)
                item.setTarget_(cocoa.menu_handler)
                item.setAction_("handleMenuAction:")
                item.setRepresentedObject_(ABOUT_ACTION)
        return True
    except Exception:  # noqa: BLE001 - 菜单改不了就保持 pywebview 的样子
        log.exception("appmenu.tidy_app_menu：改应用菜单失败")
        return False

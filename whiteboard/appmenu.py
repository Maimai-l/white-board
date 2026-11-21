"""Mac 菜单栏：中文、应用菜单里的「检查更新…」、「关于白板」用系统的关于窗口。

菜单由 pywebview 建（webview.start 的 ``menu`` 和 ``localization``）：

- 文字全部换成中文，和界面一致（pywebview 默认是英文）。
- 应用菜单里「关于」下面加一项「检查更新…」，这是 Sparkle 应用的惯例位置。
  pywebview 把标题为 ``__app__`` 的菜单里的项放进应用菜单，位置在「关于」和「服务」之间。
- pywebview 在「关于」「隐藏」「退出」后面接应用名时会加一个空格（「关于 白板」），
  中文应用名不该有这个空格。菜单建好之后由 :func:`tidy_app_menu` 在主线程上改掉。
- 「关于白板」是系统的关于窗口（真窗口、真红绿灯、Apple 的排版），pywebview 默认
  打开的是不带参数的那一种；这里换成带参数的：版本号写全（含 alpha 和构建时间），
  下面一行是「更新日志」「日志文件」两个链接。检查更新在应用菜单里，关于窗口不放按钮，
  和 macOS 自带应用一样。窗口里「版本」这些字按应用的语言显示，Info.plist 声明了中文。

只在 macOS 的 pywebview 窗口里有意义；别处调用这里的函数什么也不做。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
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


def about_options(version: str, build: str, releases_url: str, log_path: str) -> Dict[str, Any]:
    """系统关于窗口的内容：名字、版本、括号里的版本细节、下面一行链接。

    版本号 ``1.3.0-alpha.20261121.150435`` 显示成「版本 1.3.0 (alpha.20261121.150435)」，
    和 macOS 显示构建号的写法一样；正式版没有后缀，括号里是 ``build``（源码运行时是分支和提交）。
    """
    base, _, suffix = version.partition("-")
    detail = suffix or (build if build and build != version else "")
    return {
        "ApplicationName": APP_NAME,
        "ApplicationVersion": base,
        "Version": detail,
        "links": [("更新日志", releases_url), ("日志文件", f"file://{log_path}")],
    }


def show_about(options: Dict[str, Any]) -> None:
    """在主线程调用：打开系统的关于窗口。带参数的那种打不开就退回不带参数的。"""
    import AppKit
    from Foundation import NSURL

    app = AppKit.NSApp
    for _text, url in options["links"]:
        if url.startswith("file://"):
            # 从源码运行时日志只打在终端上，没有这个文件；点链接至少打开一个空的，而不是没反应
            try:
                Path(url[len("file://") :]).touch(exist_ok=True)
            except OSError as exc:
                log.warning("appmenu.show_about：建不了日志文件 %s：%s", url, exc)
    try:
        center = AppKit.NSMutableParagraphStyle.alloc().init()
        center.setAlignment_(AppKit.NSTextAlignmentCenter)
        font = AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize())
        plain = {AppKit.NSFontAttributeName: font, AppKit.NSParagraphStyleAttributeName: center}
        credits = AppKit.NSMutableAttributedString.alloc().init()
        for index, (text, url) in enumerate(options["links"]):
            if index:
                credits.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_("      ", plain)
                )
            link = (
                NSURL.fileURLWithPath_(url[len("file://") :])
                if url.startswith("file://")
                else NSURL.URLWithString_(url)
            )
            attrs = dict(plain)
            attrs[AppKit.NSLinkAttributeName] = link
            credits.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs))
        panel = {
            "ApplicationName": options["ApplicationName"],
            "ApplicationVersion": options["ApplicationVersion"],
            "Credits": credits,
        }
        if options.get("Version"):
            panel["Version"] = options["Version"]
        app.orderFrontStandardAboutPanelWithOptions_(panel)
    except Exception:  # noqa: BLE001 - 关于窗口打不开带参数的，就打开系统默认的那个
        log.exception("appmenu.show_about：带参数的关于窗口打不开，改用默认的")
        app.orderFrontStandardAboutPanel_(None)
    app.activateIgnoringOtherApps_(True)


def tidy_app_menu(about: Dict[str, Any]) -> bool:
    """在主线程调用：去掉应用名前的空格，「关于白板」打开带参数的系统关于窗口（:func:`about_options`）。

    出错只记日志：菜单里多一个空格、关于窗口少了链接，都不影响用。
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
                # 借 pywebview 自己的菜单分发：和 MenuAction 一样挂到 menu_handler 上。
                # 它在别的线程里调，关于窗口要回到主线程开
                from PyObjCTools import AppHelper

                cocoa.menu_handler.register_action(ABOUT_ACTION, lambda: AppHelper.callAfter(show_about, about))
                item.setTarget_(cocoa.menu_handler)
                item.setAction_("handleMenuAction:")
                item.setRepresentedObject_(ABOUT_ACTION)
        return True
    except Exception:  # noqa: BLE001 - 菜单改不了就保持 pywebview 的样子
        log.exception("appmenu.tidy_app_menu：改应用菜单失败")
        return False

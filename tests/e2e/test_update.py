"""检查更新和关于在 Mac 菜单栏的应用菜单里（whiteboard/appmenu.py），网页里不再放。

「关于白板」是 macOS 自己的关于窗口，「检查更新…」交给 Sparkle 的窗口；两者都只能在真机上看
（发版检查表）。这里看的是网页这一边：设置面板只管背景、权限和存储位置，页面上没有自己画的关于窗口。
"""

from __future__ import annotations

from e2e.support import as_native, new_board, open_mac


def test_settings_hold_no_update_or_about_entries(browser, server):
    mac = open_mac(browser, server.port)
    as_native(mac)

    mac.click('#topright button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings .group-title")
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.panel-settings .group-title')].map(h => h.textContent)"
    ) == [
        "背景",
        "其他设备可以",
        "存储位置",
    ]
    for text in ("检查更新", "关于", "更新日志", "日志文件"):
        assert mac.locator(f".panel-settings button:has-text('{text}')").count() == 0, text
    mac.keyboard.press("Escape")

    # 网页里没有自己画的关于窗口：菜单栏的关于白板打开的是系统的那个
    assert mac.evaluate("() => typeof whiteboard.ui.openAbout") == "undefined"
    assert mac.locator(".dialog.about, .about-lights").count() == 0


def test_the_version_survives_switching_boards(browser, server):
    """版本号来自服务端，诊断信息里要用。以前是本地接口给的，被 switch 里那份信息冲掉。"""
    mac = open_mac(browser, server.port)
    mac.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.version")
    version = mac.evaluate("() => whiteboard.ui.info.version")
    assert version

    new_board(mac, "笔记")
    assert mac.evaluate("() => whiteboard.ui.info.version") == version

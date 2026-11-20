"""「关于」和检查更新：版本号、检查更新按钮的提示。

更新本身由打包应用里的 Sparkle 负责，它有自己的窗口，这里只看按钮交接之后说了什么。
本地进程的接口（window.pywebview）用页面里的假对象代替。
"""

from __future__ import annotations

from e2e.support import as_native, new_board, open_mac

CHECK_STUB = """([result]) => {
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '0.9.3', packaged: true }),
    check_update_now: async () => result,
  }};
}"""


def reopen_about(page):
    """关掉所有弹窗，重新打开「关于」。"""
    page.evaluate("() => { document.querySelectorAll('.scrim').forEach(n => n.remove()); whiteboard.ui.openAbout(); }")


def test_check_update_button_says_what_happened(browser, server):
    """点「检查更新」必须给出人话，而不是一个没头没尾的对勾。"""
    mac = open_mac(browser, server.port)

    cases = [
        # 打包应用带着 Sparkle 时由它自己的窗口接手，按钮下面说清楚去哪儿看
        ({"status": "sparkle", "version": "1.3.0-alpha.20261119.153012"}, "更新窗口"),
        # 从源码运行没有 Sparkle
        ({"status": "source", "version": "1.0.0"}, "git pull"),
        # 打包了却没带上能用的 Sparkle（本地构建）：只说不能自动更新，原因 app.py 写进日志
        ({"status": "unavailable", "version": "1.3.0", "message": "framework 加载失败"}, "这个版本不能自动更新"),
        ({"status": "unavailable", "version": "1.3.0", "message": ""}, "这个版本不能自动更新"),
    ]
    for result, expected in cases:
        mac.evaluate(CHECK_STUB, [result])
        reopen_about(mac)
        mac.click("button:has-text('检查更新')")
        mac.locator(".check-note").wait_for()
        mac.wait_for_function(
            "(text) => { const n = document.querySelector('.check-note');return n && n.textContent.includes(text); }",
            arg=expected,
            timeout=3000,
        )


def test_about_holds_version_and_update_entries(browser, server):
    """更新相关的入口收在关于里（更多菜单），白板设置只管背景、权限和存储位置。"""
    mac = open_mac(browser, server.port)
    as_native(mac)

    # 设置面板里不该有检查更新，也不该有连接 iPad 的地址
    mac.click('#topright button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings .group-title")
    assert mac.locator("button:has-text('检查更新')").count() == 0
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.panel-settings .group-title')].map(h => h.textContent)"
    ) == [
        "背景",
        "iPad 可以",
        "存储位置",
    ]
    mac.keyboard.press("Escape")

    mac.click('#toolbar button[title="更多"]')
    mac.click('.menu .menu-item:has-text("关于白板")')
    about = mac.locator(".dialog.about")
    about.wait_for(timeout=3000)
    assert "白板" in about.inner_text()
    assert "0.9.4" in about.inner_text()
    assert about.locator("button:has-text('检查更新')").count() == 1
    assert about.locator("button:has-text('更新日志')").count() == 1


def test_the_version_survives_switching_boards(browser, server):
    """「关于」里的版本号来自服务端。以前是本地接口给的，被 switch 里那份信息冲掉。"""
    mac = open_mac(browser, server.port)
    mac.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.version")
    version = mac.evaluate("() => whiteboard.ui.info.version")
    assert version

    new_board(mac, "笔记")
    assert mac.evaluate("() => whiteboard.ui.info.version") == version

    # 「关于」里能看到版本；从源码跑时再多一行「构建」，写的是分支和提交。
    # 那个按钮只在 pywebview 窗口里有（this.native），浏览器里直接把面板叫出来。
    mac.evaluate("() => whiteboard.ui.openAbout()")
    mac.wait_for_selector(".dialog.about")
    assert mac.locator(".about-version").inner_text() == f"版本 {version}"
    # 从源码跑：构建那一行写的是分支和提交，和版本号不是一回事
    assert "@" in mac.locator(".about-build").inner_text()

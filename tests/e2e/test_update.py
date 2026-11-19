"""「关于」和更新：版本号、检查更新的提示、更新对话框的三个按钮和下载进度。

本地进程的接口（window.pywebview）用页面里的假对象代替，记下每次调用。
"""

from __future__ import annotations

from e2e.support import as_native, new_board, open_mac

UPDATE_NOTES = """## What's Changed
* fix: 修好了一个东西 by @someone in https://github.com/Maimai-l/white-board/pull/1285
* chore: bump `aiohttp` and **tidy** the build
"""

# 假装自己是 pywebview 注入的本地接口，把每次调用记下来
UPDATE_STUB = """
([notes, staged]) => {
  window.__calls = [];
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.0.0', packaged: true }),
    update_state: async () => ({
      current: '1.0.0', packaged: true, auto: true,
      info: { version: '1.2.0', notes },
      staged, downloading: !staged, progress: staged ? 1 : 0.4, onQuit: false,
    }),
    pending_update: async () => ({ version: '1.2.0', notes }),
    set_auto_update: async (value) => { window.__calls.push(['auto', value]); return value; },
    skip_update: async () => { window.__calls.push(['skip']); return true; },
    download_update: async () => { window.__calls.push(['download']); return true; },
    install_update: async (mode) => { window.__calls.push(['install', mode]); return true; },
    check_update_now: async () => ({ version: '1.2.0', notes }),
  }};
}
"""

CHECK_STUB = """([result]) => {
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '0.9.3', packaged: true }),
    check_update_now: async () => result,
  }};
}"""

FOUND_STUB = """([notes]) => {
  window.__calls = [];
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '0.9.3', packaged: true }),
    check_update_now: async () => ({ status: 'update', version: '1.0.0', notes }),
    update_state: async () => ({ current: '0.9.3', auto: true, staged: true,
      downloading: false, progress: 1, info: { version: '1.0.0', notes } }),
    set_auto_update: async (v) => v,
    skip_update: async () => true,
    install_update: async (mode) => { window.__calls.push(mode); return true; },
  }};
}"""


def open_update_dialog(page, staged=True):
    page.evaluate(UPDATE_STUB, [UPDATE_NOTES, staged])
    page.evaluate(
        "async () => { whiteboard.offeredUpdate = null;"
        "await whiteboard.offerUpdate({ version: '1.2.0' }); }"
    )
    page.locator(".update-dialog").wait_for(timeout=3000)


def calls(page):
    return page.evaluate("() => window.__calls")


def reopen_about(page):
    """关掉所有弹窗，重新打开「关于」。"""
    page.evaluate(
        "() => { document.querySelectorAll('.scrim').forEach(n => n.remove());"
        " whiteboard.ui.openAbout(); }"
    )


def test_update_dialog_offers_install_skip_and_later(browser, server):
    """更新对话框：版本、更新说明、自动下载开关、三个按钮都要工作。"""
    mac = open_mac(browser, server.port)
    open_update_dialog(mac)

    dialog = mac.locator(".update-dialog")
    assert "1.2.0" in dialog.inner_text()
    assert "已下载完毕" in dialog.inner_text()
    # PR 链接渲染成 #1285
    assert mac.locator(".update-notes a").first.inner_text() == "#1285"
    assert mac.locator(".update-notes code").count() == 1

    mac.locator(".update-auto input").uncheck()
    assert ["auto", False] in calls(mac)

    mac.click("button:has-text('退出应用时安装')")
    mac.locator(".update-dialog").wait_for(state="detached", timeout=3000)
    assert ["install", "quit"] in calls(mac)

    open_update_dialog(mac)
    mac.click("button:has-text('跳过这个版本')")
    mac.locator(".update-dialog").wait_for(state="detached", timeout=3000)
    assert ["skip"] in calls(mac)


def test_update_downloads_before_installing_when_not_staged(browser, server):
    """没下好就点安装：先下载再装，两步分开，进度条才有意义。"""
    mac = open_mac(browser, server.port)
    open_update_dialog(mac, staged=False)
    mac.click("button:has-text('下载并安装')")
    mac.wait_for_function("() => window.__calls.some(c => c[0] === 'install')", timeout=3000)
    order = mac.evaluate("() => window.__calls.map(c => c.join(':'))")
    assert order.index("download") < order.index("install:now"), "应当先下载再安装"


def test_update_dialog_shows_progress_while_downloading(browser, server):
    mac = open_mac(browser, server.port)
    open_update_dialog(mac, staged=False)
    assert "正在下载" in mac.locator(".update-dialog").inner_text()
    width = mac.evaluate("() => document.querySelector('.update-progress i').style.width")
    assert width == "40%"


def test_check_update_button_says_what_happened(browser, server):
    """点「检查更新」必须给出人话，而不是一个没头没尾的对勾。"""
    mac = open_mac(browser, server.port)

    cases = [
        ({"status": "latest", "version": "0.9.3"}, "已是最新"),
        ({"status": "error", "message": "连不上 GitHub"}, "检查失败：连不上 GitHub"),
        ({"status": "source", "version": "1.0.0"}, "git pull"),
        ({"status": "skipped", "version": "1.2.0"}, "已被跳过"),
    ]
    for result, expected in cases:
        mac.evaluate(CHECK_STUB, [result])
        reopen_about(mac)
        mac.click("button:has-text('检查更新')")
        mac.locator(".check-note").wait_for()
        mac.wait_for_function(
            "(text) => { const n = document.querySelector('.check-note');"
            "return n && n.textContent.includes(text); }",
            arg=expected,
            timeout=3000,
        )

    # 查到新版本时直接弹出对话框
    mac.evaluate(FOUND_STUB, [UPDATE_NOTES])
    reopen_about(mac)
    mac.click("button:has-text('检查更新')")
    mac.locator(".update-dialog").wait_for(timeout=3000)
    assert "1.0.0" in mac.locator(".update-dialog").inner_text()


def test_about_panel_holds_version_and_update_entries(browser, server):
    """更新相关的入口收在「关于」里，白板设置只管背景、存储和权限。"""
    mac = open_mac(browser, server.port)
    as_native(mac)

    # 设置面板里不该再有检查更新，也不该再有连接 iPad 那张卡
    mac.click('button[title="白板设置"]')
    mac.wait_for_selector(".sheet .group-title")
    assert mac.locator("button:has-text('检查更新')").count() == 0
    assert mac.locator(".sheet .card .addr").count() == 1  # 只剩存储目录那一张
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.sheet .group-title')].map(h => h.textContent)"
    ) == ["背景", "存储目录", "其他设备权限"]
    mac.evaluate("() => whiteboard.ui.closeSheet()")

    mac.click('button[title="关于"]')
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
    rows = mac.evaluate("() => [...document.querySelectorAll('.about-key')].map(n => n.textContent)")
    values = mac.evaluate("() => [...document.querySelectorAll('.about-val')].map(n => n.textContent)")
    assert rows[0] == "版本" and values[0] == version
    assert "构建" in rows

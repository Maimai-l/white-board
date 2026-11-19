"""其他设备的权限在界面上的样子：Mac 上的开关，以及没权限的设备看不到对应入口。

服务端怎么按地址发权限在 tests/test_permissions.py。
"""

from __future__ import annotations

from e2e.support import TABLET_UA, as_native, open_mac, open_touch, prepare, serve_with_perms, wait_online

TOPRIGHT = "() => [...document.querySelectorAll('#topright button')].map(b => b.title)"


def test_permission_switches_are_separate(browser, server):
    """四项权限各一个开关，互不牵连，改的是本地进程里的配置。"""
    mac = open_mac(browser, server.port)
    as_native(mac)
    mac.click('button[title="白板设置"]')
    mac.wait_for_selector(".sheet .perm-row")

    rows = "() => [...document.querySelectorAll('.sheet .perm-row input')].map(i => i.checked)"
    assert mac.evaluate(rows) == [False, False, False, False]
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.sheet .perm-title')].map(s => s.textContent)"
    ) == ["管理白板", "设置白板", "清空白板", "导出白板"]

    mac.locator(".sheet .perm-row input").nth(3).click()
    mac.wait_for_function("() => whiteboard.ui.info.remote_permissions.export === true")
    assert mac.evaluate(rows) == [False, False, False, True]
    assert mac.evaluate("() => window.__perms") == {
        "manage": False,
        "settings": False,
        "clear": False,
        "export": True,
    }


def test_the_ui_only_draws_the_entries_it_is_allowed(browser, server):
    """右上角那几个入口按权限出，没权限就根本不画。"""
    mac = open_mac(browser, server.port)

    # 本机：权限齐全，但「连接 iPad」「关于」要本地进程才有
    assert mac.evaluate(TOPRIGHT) == ["白板", "白板设置", "导出"]
    as_native(mac)
    assert mac.evaluate(TOPRIGHT) == ["白板", "白板设置", "导出", "连接 iPad", "关于"]

    # 装成局域网里的别的设备：把服务端发下来的那份清单改成只有 export
    mac.add_init_script("delete window.pywebview;")  # 别的设备不是本地进程
    serve_with_perms(mac, "export")
    assert mac.evaluate(TOPRIGHT) == ["导出"]

    # 没有任何权限时，那一簇整个不出现
    serve_with_perms(mac, "")
    assert mac.evaluate("() => !document.getElementById('topright')")


def test_clear_button_disappears_without_the_permission(browser, server):
    """没给清空权限的设备，工具栏上看不到这个按钮（按钮建好后藏起来，权限放开时直接显示）。"""
    ipad = open_touch(browser, server.port)
    assert ipad.locator('button[title="清空白板"]:visible').count() == 1

    serve_with_perms(ipad, "export manage settings")
    assert ipad.locator('button[title="清空白板"]:visible').count() == 0
    assert ipad.locator('button[title="撤销"]').count() == 1  # 别的按钮照旧


def test_permission_changes_update_a_connected_page(browser, server, monkeypatch):
    """Mac 上给其他设备放开「管理白板」：已经打开的页面不用刷新就出现「白板」入口，收回后消失。"""
    import whiteboard.netinfo as netinfo

    monkeypatch.setattr(netinfo, "is_local_request", lambda request: False)  # 模拟局域网上的其他设备
    page = prepare(browser.new_context(user_agent=TABLET_UA, has_touch=True).new_page())
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(page)
    clear_hidden = "() => document.querySelector('button[title=\"清空白板\"]').hidden"
    assert page.query_selector('#topright button[title="白板"]') is None
    assert page.evaluate(clear_hidden) is True

    server.config.set_remote_permission("manage", True)
    server.config.set_remote_permission("clear", True)
    server.refresh_permissions()
    page.wait_for_selector('#topright button[title="白板"]')
    assert page.evaluate(clear_hidden) is False

    server.config.set_remote_permission("manage", False)
    server.refresh_permissions()
    page.wait_for_function("() => !document.querySelector('#topright button[title=\"白板\"]')")

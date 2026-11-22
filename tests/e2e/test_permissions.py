"""其他设备的权限在界面上的样子：Mac 上的开关，以及没权限的设备看不到对应入口。

服务端怎么按地址发权限在 tests/test_permissions.py。
"""

from __future__ import annotations

from e2e.support import TABLET_UA, as_native, open_mac, open_touch, prepare, serve_with_perms, wait_online

# 顶栏（Mac）或左右上角（触摸设备）里的入口；触摸设备上第一个是连接状态，不算
ENTRIES = (
    "() => [...document.querySelectorAll('#topbar button, #topleft button, #topright button')]"
    ".filter(b => b.id !== 'status' || document.documentElement.dataset.chrome === 'bar').map(b => b.title)"
)
MORE_LABELS = "() => [...document.querySelectorAll('.menu .menu-label')].map(n => n.textContent)"


def more_menu(page):
    """点工具栏上的更多，返回菜单里的各项，再关上。"""
    page.click('#toolbar button[title="更多"]')
    page.wait_for_selector(".menu")
    labels = page.evaluate(MORE_LABELS)
    page.keyboard.press("Escape")
    page.wait_for_selector(".menu", state="detached")
    return labels


def test_permission_switches_are_separate(browser, server):
    """四项权限各一个开关，互不牵连，改的是本地进程里的配置。"""
    mac = open_mac(browser, server.port)
    as_native(mac)
    mac.click('#topright button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings .perm-row")

    rows = "() => [...document.querySelectorAll('.panel-settings .perm-row input')].map(i => i.checked)"
    assert mac.evaluate(rows) == [False, False, False, False]
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.panel-settings .perm-title')].map(s => s.textContent)"
    ) == [
        "管理白板",
        "设置白板",
        "清空白板",
        "导出白板",
    ]
    # 每一行只有名字和开关，不加说明（docs/ui-text.md）
    assert mac.evaluate(
        "() => [...document.querySelectorAll('.panel-settings .perm-row')].map(r => r.innerText.trim())"
    ) == [
        "管理白板",
        "设置白板",
        "清空白板",
        "导出白板",
    ]

    mac.locator(".panel-settings .perm-row input").nth(3).click()
    mac.wait_for_function("() => whiteboard.ui.info.remote_permissions.export === true")
    assert mac.evaluate(rows) == [False, False, False, True]
    assert mac.evaluate("() => window.__perms") == {
        "manage": False,
        "settings": False,
        "clear": False,
        "export": True,
    }


def test_the_ui_only_draws_the_entries_it_is_allowed(browser, server):
    """顶栏里的入口按权限出，没权限就根本不画。"""
    mac = open_mac(browser, server.port)

    # 本机：权限齐全，但连接其他设备要本地进程才有
    assert mac.evaluate(ENTRIES) == ["白板库", "导出", "白板设置"]
    as_native(mac)
    assert mac.evaluate(ENTRIES) == ["白板库", "连接其他设备", "导出", "白板设置"]
    assert "关于白板" not in more_menu(mac)  # 在菜单栏的应用菜单里

    # 装成局域网里的别的设备：把服务端发下来的那份清单改成只有 export
    mac.add_init_script("delete window.pywebview;")  # 别的设备不是本地进程
    serve_with_perms(mac, "export")
    assert mac.evaluate(ENTRIES) == ["导出"]
    assert "关于白板" not in more_menu(mac)

    # 没有任何权限时，一个入口都不画
    serve_with_perms(mac, "")
    assert mac.evaluate(ENTRIES) == []


def test_clear_is_missing_from_the_more_menu_without_the_permission(browser, server):
    """没给清空权限的设备，更多菜单里没有清空白板（权限放开时直接出现）。"""
    ipad = open_touch(browser, server.port)
    assert "清空白板…" in more_menu(ipad)

    serve_with_perms(ipad, "export manage settings")
    assert "清空白板…" not in more_menu(ipad)
    assert ipad.locator('#toolbar button[title="撤销"]').count() == 1  # 别的按钮照旧


def test_permission_changes_update_a_connected_page(browser, server, monkeypatch):
    """Mac 上给其他设备放开「管理白板」：已经打开的页面不用刷新就出现「白板」入口，收回后消失。"""
    import whiteboard.netinfo as netinfo

    monkeypatch.setattr(netinfo, "is_local_request", lambda request: False)  # 模拟局域网上的其他设备
    page = prepare(browser.new_context(user_agent=TABLET_UA, has_touch=True).new_page())
    page.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(page)
    library = 'button[title="白板库"]'
    assert page.query_selector(library) is None
    assert "清空白板…" not in more_menu(page)

    server.config.set_remote_permission("manage", True)
    server.config.set_remote_permission("clear", True)
    server.refresh_permissions()
    page.wait_for_selector(library)
    assert "清空白板…" in more_menu(page)

    server.config.set_remote_permission("manage", False)
    server.refresh_permissions()
    page.wait_for_function("() => !document.querySelector('button[title=\"白板库\"]')")

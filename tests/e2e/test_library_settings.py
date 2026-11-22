"""白板库左下角的设置（docs/backlog/013-app-settings.md）。

用户反馈：在白板库里检查连接、改设置还得先打开一块白板，再点白板设置，很奇怪。现在边栏
最下面有设置，右边是完整的设置；编辑界面的白板设置面板照旧能随手改，底部的所有设置…进到这里。
局域网里任何设备的浏览器都能打开白板，界面上管所有设备的地方叫其他设备，不叫 iPad。
"""

from __future__ import annotations

from e2e.support import LIBRARY, IPAD_UA, gone, new_page, open_gallery, open_touch, wait_online

NATIVE = """
  window.__perms = { manage: false, settings: false, clear: false, export: false };
  window.pywebview = { api: {
    info: async () => ({ native: true, version: '1.3.0', packaged: true, hostname: 'MacBook.local',
      port: Number(location.port), urls: [location.origin + '/'], data_dir: '/Users/me/Documents/白板',
      remote_permissions: Object.assign({}, window.__perms), apps: [] }),
    set_remote_permission: async (name, on) => { window.__perms[name] = !!on; return Object.assign({}, window.__perms); },
  }};
"""

SECTION_TITLES = "() => [...document.querySelectorAll('.lib-settings .settings-title')].map((h) => h.textContent)"


def native_mac(browser, port):
    page = new_page(browser, viewport=(1200, 800))
    page.add_init_script(NATIVE)
    page.goto(f"http://127.0.0.1:{port}/?role=mac")
    wait_online(page)
    page.wait_for_function("() => whiteboard.ui.info && whiteboard.ui.info.native === true", timeout=15000)
    return page


def test_the_library_has_settings_at_the_bottom_left(browser, server):
    mac = native_mac(browser, server.port)
    open_gallery(mac)
    rows = mac.locator(f"{LIBRARY} .lib-side .lib-nav .lib-nav-label").all_inner_texts()
    assert rows[-2:] == ["回收站", "设置"]
    mac.click(f'{LIBRARY} .lib-nav:has-text("设置")')
    mac.wait_for_selector(f"{LIBRARY} .lib-settings")
    assert mac.locator(f"{LIBRARY} .lib-title").inner_text() == "设置"
    assert mac.evaluate(SECTION_TITLES) == ["连接其他设备", "其他设备可以", "存储位置"]
    # 连接：二维码、地址、连着的设备都在这一页上，不用先打开一块白板
    mac.wait_for_function("() => document.querySelector('.lib-settings .connect-qr')?.naturalWidth > 0")
    assert mac.locator(".lib-settings .addr").first.inner_text().startswith("127.0.0.1:")
    assert mac.locator('.lib-settings .group-title:has-text("已连接")').count() == 1
    # 权限开关在这里也能改，改的就是本地进程里那一份
    mac.click('.lib-settings .perm-row:has-text("导出白板")')
    mac.wait_for_function("() => window.__perms.export === true")


def test_board_settings_keep_their_switches_and_lead_to_all_settings(browser, server):
    """白板设置面板像控制中心：总设置里有了，面板里也照样能改；所有设置…直接停在设置那一页。"""
    mac = native_mac(browser, server.port)
    mac.click('#topbar button[title="白板设置"]')
    mac.wait_for_selector(".panel-settings .group-title")
    titles = mac.evaluate(
        "() => [...document.querySelectorAll('.panel-settings .group-title')].map((h) => h.textContent)"
    )
    assert titles == ["背景", "其他设备可以", "存储位置"]
    mac.click('.panel-settings button:has-text("所有设置…")')
    mac.wait_for_selector(f"{LIBRARY} .lib-settings")
    gone(mac, ".panel")
    current = mac.locator(f"{LIBRARY} .lib-nav.current .lib-nav-label").inner_text()
    assert current == "设置"


def test_the_mac_calls_every_device_another_device(browser, server):
    mac = native_mac(browser, server.port)
    assert mac.locator('#topbar button[title="连接其他设备"]').count() == 1
    mac.click("#status")
    mac.wait_for_selector(".panel-connect")
    assert mac.locator(".panel-connect .panel-title").inner_text() == "连接其他设备"
    text = mac.locator(".panel-connect").inner_text()
    assert "iPad 可以" not in text and "连接 iPad" not in text


def test_a_tablet_sees_its_connection_in_library_settings(browser, server):
    tablet = open_touch(browser, server.port)
    open_gallery(tablet)
    tablet.click(f'{LIBRARY} .lib-nav:has-text("设置")')
    tablet.wait_for_selector(f"{LIBRARY} .lib-settings")
    assert tablet.evaluate(SECTION_TITLES) == ["连接"]
    assert tablet.locator(".lib-settings .panel-title").inner_text() == "已连接"
    assert tablet.locator(".lib-settings .panel-row-text").inner_text().endswith(f":{server.port}")


def test_the_ipad_shell_can_switch_macs_from_library_settings(browser, server):
    ipad = new_page(browser, user_agent=IPAD_UA, touch=True)
    ipad.add_init_script("window.webkit = { messageHandlers: { whiteboard: { postMessage() {} } } };")
    ipad.goto(f"http://127.0.0.1:{server.port}/?role=ipad")
    wait_online(ipad)
    open_gallery(ipad)
    ipad.click(f'{LIBRARY} .lib-nav:has-text("设置")')
    ipad.wait_for_selector('.lib-settings button:has-text("换一台 Mac")')

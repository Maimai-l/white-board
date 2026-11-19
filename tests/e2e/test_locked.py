"""读不全的白板以只读方式打开：提示、停止书写、在 Mac 上决定要不要仍然编辑。"""

from __future__ import annotations

import json
import zlib

from e2e.support import draw, open_pages, serve_with_perms, stroke_count, wait_strokes
from whiteboard.store import BoardStore


def serve_locked(make_server, content):
    """起一个服务端，当前那块白板的文件换成 ``content(原来的路径)`` 返回的字节。"""
    where = {}

    def prepare(config):
        store = BoardStore(config.data_dir)
        path = store.boards_dir / f"{store.current_id}.wbz"
        path.write_bytes(content(path))
        where["path"] = path

    thread = make_server(prepare=prepare)
    return thread, where["path"]


def damaged(_path):
    return b"not a board"


def newer_version(path):
    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["v"] = 99
    return zlib.compress(json.dumps(payload).encode("utf-8"))


def test_a_board_that_cannot_be_read_opens_read_only_until_unlocked(browser, make_server):
    """文件损坏的白板：两边都提示并停止书写；Mac 上选「仍然编辑」之后，
    先备份原文件，两边一起恢复书写。"""
    thread, board_file = serve_locked(make_server, damaged)
    mac, ipad = open_pages(browser, thread.port)
    for page in (mac, ipad):
        page.wait_for_selector(".dialog.locked")
        assert page.evaluate("() => whiteboard.locked.reason") == "corrupt"
    # 只有 Mac（有管理权限）才有「仍然编辑」；测试里两边都是本机，所以都有
    ipad.locator(".dialog.locked button.danger").wait_for()

    # 只读时落笔不生效，也不会发给服务端（合成事件直接发到画布上，不经过提示框）
    draw(ipad, [(300, 300), (360, 340), (430, 300)])
    assert stroke_count(ipad) == 0
    assert thread.hub.board().strokes == {}
    assert board_file.read_bytes() == b"not a board"

    mac.locator(".dialog.locked button.danger").click()
    for page in (mac, ipad):
        page.wait_for_function("() => whiteboard.locked === null")
    # 在 Mac 上选了之后，iPad 上还开着的那个提示也自己收起来
    for page in (mac, ipad):
        assert page.locator(".dialog.locked").count() == 0
    backups = list((thread.config.data_dir / "backups" / "locked").glob("*.wbz"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"not a board"

    draw(ipad, [(300, 300), (360, 340), (430, 300)])
    wait_strokes(mac, 1)


def test_keep_read_only_really_keeps_it_read_only(browser, make_server):
    thread, path = serve_locked(make_server, lambda _p: b"damaged")
    mac, _ipad = open_pages(browser, thread.port)
    mac.wait_for_selector(".dialog.locked")
    mac.click('.dialog.locked button[title="保持只读"]')
    assert mac.query_selector(".dialog.locked") is None
    draw(mac, [(300, 300), (380, 340)], pointer_type="mouse")
    assert stroke_count(mac) == 0
    assert thread.hub.board().locked
    assert path.read_bytes() == b"damaged"


def test_a_board_from_a_newer_version_says_so(browser, make_server):
    thread, _path = serve_locked(make_server, newer_version)
    mac, _ipad = open_pages(browser, thread.port)
    mac.wait_for_selector(".dialog.locked")
    assert "更新版本" in mac.inner_text(".dialog.locked")
    assert mac.evaluate("() => whiteboard.locked.reason") == "newer"


def test_a_device_without_manage_cannot_unlock(browser, make_server):
    """没有「管理白板」权限的设备只看得到说明，没有「仍然编辑」。"""
    thread, _path = serve_locked(make_server, damaged)
    _mac, ipad = open_pages(browser, thread.port)
    serve_with_perms(ipad, "clear export settings")
    ipad.wait_for_selector(".dialog.locked")
    assert ipad.query_selector(".dialog.locked button.danger") is None
    assert "需要在 Mac 上决定" in ipad.inner_text(".dialog.locked")

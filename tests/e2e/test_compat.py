"""升级时还开着的 1.0.x 页面要能接着用（docs/design/inksync-redesign.zh-CN.md 7.3 节）。

协议层的兼容在 tests/test_compat.py；这里让真的 1.0.1 前端连上新服务端。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.support import draw, new_page, open_touch, wait_online, wait_strokes

LEGACY_WEB = Path(__file__).resolve().parents[1] / "fixtures" / "web-1.0.1"


@pytest.fixture
def legacy_server(make_server, monkeypatch):
    """新的服务端，另外在 /v1/ 下提供 1.0.1 的前端文件：模拟升级时还开着的旧页面。"""
    from aiohttp import web

    import whiteboard.runner as runner

    original = runner.create_app

    def create_app(config, store=None):
        app = original(config, store)

        async def page(request):
            html = (LEGACY_WEB / "index.html").read_text("utf-8")
            html = html.replace("{{ROLE}}", request.query.get("role", "mac"))
            html = html.replace("{{PERMS}}", "manage settings clear export").replace("{{BUILD}}", "1.0.1")
            html = html.replace('"/static/', '"/v1/static/')
            return web.Response(text=html, content_type="text/html")

        app.router.add_get("/v1/", page)
        app.router.add_static("/v1/static/", LEGACY_WEB / "static")
        return app

    monkeypatch.setattr(runner, "create_app", create_app)
    return make_server()


def test_a_1x_page_keeps_working_against_the_2_0_server(browser, legacy_server):
    port = legacy_server.port
    old = new_page(browser, viewport=(1200, 800))
    old.goto(f"http://127.0.0.1:{port}/v1/?role=mac")
    wait_online(old)
    assert old.evaluate("() => whiteboard.state.meta.background") == "grid"  # 1.0.x 的字段
    ipad = open_touch(browser, port)

    # 两边互相看得到对方写的内容
    draw(old, [(300, 300), (360, 340), (420, 300)], pointer_type="mouse")
    wait_strokes(ipad, 1)
    draw(ipad, [(300, 420), (360, 460), (420, 420)])
    wait_strokes(old, 2)

    # 旧页面上新建笔记：新页面跟着切过去；旧页面看到的是 1.0.x 的元数据
    old.evaluate("() => whiteboard.net.send({ t: 'newboard', kind: 'note' })")
    ipad.wait_for_function("() => whiteboard.state.meta.canvas.mode === 'column'")
    old.wait_for_function("() => whiteboard.state.kind === 'note' && whiteboard.state.meta.kind === 'note'")
    board = old.evaluate("() => whiteboard.state.id")

    # 改名、归类
    old.evaluate(f"() => whiteboard.net.send({{ t: 'rename', board: '{board}', name: '旧页面改的名' }})")
    ipad.wait_for_function("() => whiteboard.state.meta.name === '旧页面改的名'")
    old.evaluate(f"() => whiteboard.net.send({{ t: 'folder', board: '{board}', folder: '数学' }})")
    old.wait_for_function(f"() => whiteboard.ui.boards.find(b => b.id === '{board}').folder === '数学'")
    ipad.wait_for_function("() => whiteboard.state.meta.data.folder === '数学'")

    # 新页面改背景，旧页面收到的是字符串形式
    ipad.evaluate("() => whiteboard.setMeta({ background: { pattern: 'dots' } })")
    old.wait_for_function("() => whiteboard.state.meta.background === 'dots'")

"""请求来源检查（inksync.guard）：其他网页发来的握手被拒绝，白板自己的页面和脚本照常连接。"""

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from inksync import FileStorage, Hub, guard_middleware, mount
from inksync.guard import host_allowed, normalize_origin, origin_allowed

from helpers import local


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("host", [
    "127.0.0.1:8848", "127.0.0.1", "[::1]:8848", "192.168.1.20:8848", "localhost:8848",
    "Mac-Mini.local:8848", "mac-mini.LOCAL",
])
def test_hosts_accepted_by_default(host):
    assert host_allowed(host)


@pytest.mark.parametrize("host", ["evil.example:8848", "evil.example", "127.0.0.1.evil.example", "", "local", "x.localhost.evil"])
def test_hosts_rejected_by_default(host):
    assert not host_allowed(host)


def test_extra_hosts_and_wildcard():
    assert host_allowed("qb.example.com", ["QB.example.com"])
    assert host_allowed("qb.example.com:443", "qb.example.com")
    assert not host_allowed("other.example.com", ["qb.example.com"])
    assert host_allowed("anything.example", "*")


def test_normalize_origin():
    assert normalize_origin("HTTP://Mac.Local:80") == "http://mac.local"
    assert normalize_origin("https://a.example:443/") == "https://a.example"
    assert normalize_origin("http://[::1]:8848") == "http://[::1]:8848"
    assert normalize_origin("chrome-extension://abcdefghijklmnop") == "chrome-extension://abcdefghijklmnop"
    assert normalize_origin("http://a.example/path") is None
    assert normalize_origin("not an origin") is None


@pytest.mark.parametrize("origin,host,ok", [
    (None, "127.0.0.1:8848", True),                          # 不是网页发的
    ("http://127.0.0.1:8848", "127.0.0.1:8848", True),       # 同源
    ("http://mac.local:8848", "Mac.local:8848", True),
    ("http://[::1]:8848", "[::1]:8848", True),
    ("https://qb.example.com", "qb.example.com", True),      # https 反向代理：Host 不写端口
    ("http://127.0.0.1:9000", "127.0.0.1:8848", False),      # 同一台机器的其他端口也是其他网页
    ("http://evil.example", "127.0.0.1:8848", False),
    ("null", "127.0.0.1:8848", False),                       # 沙盒 iframe、本地文件
    ("garbage", "127.0.0.1:8848", False),
])
def test_origin_rules(origin, host, ok):
    assert origin_allowed(origin, host) is ok


def test_allowed_origins_list():
    allowed = ["chrome-extension://abcdefghijklmnop"]
    assert origin_allowed("chrome-extension://abcdefghijklmnop", "127.0.0.1:8848", allowed)
    assert not origin_allowed("chrome-extension://zzzz", "127.0.0.1:8848", allowed)


def _app(tmp_path, **mount_args):
    app = web.Application()
    mount(app, Hub(FileStorage(tmp_path / "data")), authenticate=local, **mount_args)
    return app


async def _handshake(client, headers):
    try:
        ws = await client.ws_connect("/ws", headers=headers)
    except aiohttp.WSServerHandshakeError as exc:
        return exc.status
    await ws.close()
    return 101


def test_websocket_handshake_checks_origin_and_host(tmp_path):
    allowed = []

    async def main():
        client = TestClient(TestServer(_app(tmp_path, allowed_origins=allowed)))
        await client.start_server()
        try:
            own = f"http://{client.host}:{client.port}"
            assert await _handshake(client, {}) == 101
            assert await _handshake(client, {"Origin": own}) == 101
            assert await _handshake(client, {"Origin": "http://evil.example"}) == 403
            assert await _handshake(client, {"Origin": "null"}) == 403
            assert await _handshake(client, {"Host": f"evil.example:{client.port}", "Origin": f"http://evil.example:{client.port}"}) == 403
            # 允许名单可以在运行中修改（白板设置里添加插件之后立即生效）
            extension = "chrome-extension://abcdefghijklmnop"
            assert await _handshake(client, {"Origin": extension}) == 403
            allowed.append(extension)
            assert await _handshake(client, {"Origin": extension}) == 101
        finally:
            await client.close()

    run(main())


def test_allowed_hosts_for_a_public_name(tmp_path):
    async def main():
        client = TestClient(TestServer(_app(tmp_path, allowed_hosts=["qb.example.com"])))
        await client.start_server()
        try:
            headers = {"Host": "qb.example.com", "Origin": "https://qb.example.com"}
            assert await _handshake(client, headers) == 101
            assert await _handshake(client, {"Host": "evil.example"}) == 403
        finally:
            await client.close()

    run(main())


def test_guard_middleware_covers_http_routes(tmp_path):
    hits = []

    async def handle(request):
        hits.append(request.method)
        return web.Response(text="ok")

    async def main():
        app = web.Application(middlewares=[guard_middleware()])
        app.router.add_route("*", "/api/thing", handle)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            own = f"http://{client.host}:{client.port}"
            assert (await client.post("/api/thing", headers={"Origin": own})).status == 200
            assert (await client.post("/api/thing")).status == 200
            assert (await client.post("/api/thing", headers={"Origin": "http://evil.example"})).status == 403
            assert (await client.get("/api/thing", headers={"Host": "evil.example"})).status == 403
        finally:
            await client.close()
        assert hits == ["POST", "POST"]

    run(main())


def test_own_hostname_is_accepted():
    import socket

    name = socket.gethostname()
    assert host_allowed(f"{name}:8848")
    assert host_allowed(name.split(".")[0].upper())

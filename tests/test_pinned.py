"""固定白板的连接：其他应用嵌入手写板时，每个连接只收发自己指定的那块白板。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from whiteboard.config import Config
from whiteboard.server import HUB_KEY, create_app
from whiteboard.store import BoardStore


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def make_client(tmp_path, remote=False):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    app = create_app(config, BoardStore(config.data_dir))
    if remote:
        # 模拟局域网上的其他设备：只能写字，没有任何管理权限
        import whiteboard.server as server

        original = server.permissions
        server.permissions = lambda request: frozenset()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()
        if remote:
            server.permissions = original


def stroke(stroke_id, x=0.0):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3.0, "p": [x, 0.0, 0.5, x + 10, 0.0, 0.5]}


async def connect(client, client_id, pin=None, board=None, since=0, epoch=None):
    ws = await client.ws_connect("/ws")
    hello = {"t": "hello", "role": "ipad", "client": client_id, "board": board, "since": since, "epoch": epoch}
    if pin is not None:
        hello["pin"] = pin
    await ws.send_json(hello)
    return ws, await ws.receive_json()


async def receive(ws, kind, timeout=1.0):
    """收到类型为 ``kind`` 的消息为止；超时返回 None。"""
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


async def send_op(ws, cid, op):
    await ws.send_json({"t": "op", "cid": cid, "op": op})
    return await receive(ws, "ack")


PIN = {"board": "qb-9709-q3", "app": "qb", "name": "第 3 题", "folder": "刷题"}


def test_a_pinned_connection_creates_its_board_without_switching_others(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            before = hub.current_id
            mac, _ = await connect(client, "mac-one")
            pad, init = await connect(client, "pad-one", pin=PIN)

            assert init["t"] == "init"
            assert init["board"]["id"] == "qb-9709-q3"
            assert init["board"]["name"] == "第 3 题"
            assert init["board"]["folder"] == "刷题"
            assert init["board"]["app"] == "qb"
            assert hub.current_id == before  # 跟随的连接没有被切走
            assert "刷题" in hub.store.folders()
            assert await receive(mac, "switch", timeout=0.3) is None
            await mac.close()
            await pad.close()

    run(main())


def test_ops_and_live_ink_stay_on_their_own_board(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac, _ = await connect(client, "mac-one")
            pad_a, _ = await connect(client, "pad-aaa", pin=PIN)
            pad_a2, _ = await connect(client, "pad-a2", pin=PIN)
            pad_b, _ = await connect(client, "pad-bbb", pin=dict(PIN, board="qb-9709-q4"))

            ack = await send_op(pad_a, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert ack["op"]["strokes"][0]["id"] == "s1"
            got = await receive(pad_a2, "op")
            assert got["board"] == "qb-9709-q3"
            assert got["op"]["strokes"][0]["id"] == "s1"
            assert await receive(pad_b, "op", timeout=0.3) is None
            assert await receive(mac, "op", timeout=0.3) is None

            await pad_a.send_json({"t": "live", "id": "s2", "p": [1, 2, 0.5]})
            assert (await receive(pad_a2, "live"))["id"] == "s2"
            assert await receive(pad_b, "live", timeout=0.3) is None
            assert await receive(mac, "live", timeout=0.3) is None

            # 跟随当前白板的连接写的内容也不会发给固定连接
            await send_op(mac, "m1", {"op": "add", "strokes": [stroke("m1")]})
            assert await receive(pad_a2, "op", timeout=0.3) is None

            assert [s["id"] for s in hub.board("qb-9709-q3").stroke_list()] == ["s1"]
            assert [s["id"] for s in hub.board().stroke_list()] == ["m1"]
            for ws in (mac, pad_a, pad_a2, pad_b):
                await ws.close()

    run(main())


def test_a_follower_viewing_the_same_board_receives_pinned_ops(tmp_path):
    """Mac 在白板选择界面里打开刷题的那块白板时，iPad 上写的内容照常实时显示。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            pad, _ = await connect(client, "pad-one", pin=PIN)
            mac, _ = await connect(client, "mac-one")
            await mac.send_json({"t": "sel", "board": "qb-9709-q3"})
            switched = await receive(mac, "switch")
            assert switched["board"]["id"] == "qb-9709-q3"
            assert await receive(pad, "switch", timeout=0.3) is None  # 固定连接不跟随切换

            await send_op(pad, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert (await receive(mac, "op"))["op"]["strokes"][0]["id"] == "s1"
            await send_op(mac, "m1", {"op": "add", "strokes": [stroke("m1")]})
            assert (await receive(pad, "op"))["op"]["strokes"][0]["id"] == "m1"
            assert hub.current_id == "qb-9709-q3"
            await mac.close()
            await pad.close()

    run(main())


def test_reconnecting_to_a_pinned_board_sends_only_the_missing_ops(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            pad, init = await connect(client, "pad-one", pin=PIN)
            await send_op(pad, "c1", {"op": "add", "strokes": [stroke("s1")]})
            await pad.close()

            other, _ = await connect(client, "pad-two", pin=PIN)
            await send_op(other, "c2", {"op": "add", "strokes": [stroke("s2")]})
            await other.close()

            again, msg = await connect(
                client, "pad-one", pin=PIN, board="qb-9709-q3", since=1, epoch=init["epoch"]
            )
            assert msg["t"] == "sync"
            assert [op["strokes"][0]["id"] for op in msg["ops"]] == ["s2"]
            await again.close()

    run(main())


def test_pinned_boards_are_saved_and_reopened(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            pad, _ = await connect(client, "pad-one", pin=PIN)
            await send_op(pad, "c1", {"op": "add", "strokes": [stroke("s1")]})
            await pad.close()
            app[HUB_KEY].save_all()

    run(main())
    store = BoardStore(tmp_path / "data")
    meta, strokes, problem = store.open_board("qb-9709-q3")
    assert problem is None
    assert meta["app"] == "qb"
    assert [s["id"] for s in strokes] == ["s1"]


def test_a_device_without_permissions_may_pin_app_boards_only(tmp_path):
    async def main():
        async with make_client(tmp_path, remote=True) as (client, app):
            hub = app[HUB_KEY]
            own = hub.current_id  # 用户自己的白板

            pad, init = await connect(client, "pad-one", pin=PIN)
            assert init["t"] == "init"  # 应用的白板：能写字就能建、能打开
            await pad.close()

            ws, refused = await connect(client, "pad-two", pin={"board": own, "app": "qb"})
            assert refused == {"t": "error", "reason": "pin"}
            await ws.close()

    run(main())


def test_invalid_pins_are_refused(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            count = len(app[HUB_KEY].store.list_metas())
            for pin in (
                {"board": "a:b", "app": "qb"},  # 含有不能进文件名的字符
                {"board": "x" * 65, "app": "qb"},
                {"board": "qb-1", "app": "QB"},  # 应用名只能是小写字母、数字和 -
                {"board": "qb-1"},
                "qb-1",
            ):
                ws, refused = await connect(client, "pad-one", pin=pin)
                assert refused == {"t": "error", "reason": "pin"}, pin
                await ws.close()
            assert len(app[HUB_KEY].store.list_metas()) == count

    run(main())


def test_deleting_a_pinned_board_tells_its_connections_and_ignores_later_ops(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            pad, _ = await connect(client, "pad-one", pin=PIN)
            mac, _ = await connect(client, "mac-one")
            await mac.send_json({"t": "delboard", "board": "qb-9709-q3"})
            assert (await receive(pad, "deleted"))["board"] == "qb-9709-q3"

            ack = await send_op(pad, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert "op" not in ack
            hub.save_all()
            assert hub.store.get_meta("qb-9709-q3") is None
            assert not (hub.store.boards_dir / "qb-9709-q3.wbz").exists()
            await mac.close()
            await pad.close()

    run(main())


def test_switching_boards_does_not_reach_pinned_connections(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            pad, _ = await connect(client, "pad-one", pin=PIN)
            mac, _ = await connect(client, "mac-one")
            await mac.send_json({"t": "newboard", "kind": "note"})
            assert await receive(mac, "switch") is not None
            await mac.send_json({"t": "newfolder", "name": "数学"})
            assert await receive(mac, "boards") is not None
            assert await receive(pad, "switch", timeout=0.3) is None
            assert await receive(pad, "boards", timeout=0.3) is None
            await mac.close()
            await pad.close()

    run(main())

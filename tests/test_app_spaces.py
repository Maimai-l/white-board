"""已安装应用的空间（docs/design/inksync-2.zh-CN.md 7.4 节）：每个应用的白板单独保存，
不进入用户的白板列表；只能写字的设备可以打开和新建，Mac 可以分页查看。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from whiteboard import netinfo
from whiteboard.config import Config
from whiteboard.server import HUB_KEY, SPACES_KEY, create_app
from whiteboard.store import BoardStore


def run(coro):
    return asyncio.run(coro)


def install_app(config, name="qb"):
    folder = config.data_dir / "apps" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<p>app</p>", "utf-8")


@asynccontextmanager
async def make_client(tmp_path, monkeypatch=None, remote=False):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    app = create_app(config, BoardStore(config.data_dir))
    if remote:
        monkeypatch.setattr(netinfo, "is_local_request", lambda request: False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app, config
    finally:
        await client.close()


def stroke(stroke_id, x=0.0):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3.0, "p": [x, 0.0, 0.5, x + 10, 0.0, 0.5]}


QUESTION = {"name": "第 3 题", "canvas": {"mode": "fixed", "width": 800, "height": 1200},
            "layers": [{"src": "/apps/qb/q3.png", "x": 0, "y": 0, "width": 800}], "data": {"q": 3}}


async def hello(client, board, space="qb", create=QUESTION, client_id="pad-one", **extra):
    ws = await client.ws_connect("/ws")
    msg = {"t": "hello", "v": 2, "client": client_id, "space": space, "board": board, **extra}
    if create is not None:
        msg["create"] = create
    await ws.send_json(msg)
    return ws, await ws.receive_json()


async def receive(ws, kind, timeout=1.0):
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


def test_only_installed_apps_have_a_space(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app, config):
            ws, reply = await hello(client, "q3")
            assert reply == {"t": "error", "reason": "space"}
            await ws.close()
            install_app(config)  # 运行中安装，不用重启
            ws, init = await hello(client, "q3")
            assert init["t"] == "init" and init["board"]["canvas"]["mode"] == "fixed"
            await ws.close()
        assert (tmp_path / "data" / "spaces" / "qb" / "boards" / "q3.wbz").exists()

    run(main())


def test_app_boards_stay_out_of_the_user_board_list(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app, config):
            install_app(config)
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            await mac.send_json({"t": "hello", "v": 2, "client": "mac-one", "follow": True, "device": "mac"})
            first = await mac.receive_json()
            pad, _ = await hello(client, "q3")
            await pad.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke("s1")]}})
            assert (await receive(pad, "ack"))["op"]
            assert await receive(mac, "boards", timeout=0.3) is None
            assert await receive(mac, "op", timeout=0.3) is None
            assert [m["id"] for m in hub.store.list_metas()] == [m["id"] for m in first["boards"]]
            assert not hub.core.exists("q3")

            listed = await client.get("/api/spaces/qb/boards?limit=10")
            assert listed.status == 200
            body = await listed.json()
            assert body["total"] == 1 and body["boards"][0]["id"] == "q3"
            assert (await client.get("/api/spaces/nope/boards")).status == 404
            for ws in (mac, pad):
                await ws.close()

    run(main())


def test_a_device_that_can_only_write_can_answer_but_not_edit_meta(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch, remote=True) as (client, app, config):
            install_app(config)
            ws, init = await hello(client, "q3")
            assert init["caps"] == {"write": True, "clear": True, "meta": False, "unlock": False}
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke("s1")]}})
            assert (await receive(ws, "ack"))["op"]
            await ws.send_json({"t": "op", "cid": "c2", "op": {"op": "clear"}})
            assert (await receive(ws, "ack"))["op"] == {"op": "clear", "seq": 2}
            await ws.send_json({"t": "op", "cid": "c3", "op": {"op": "meta", "meta": {"name": "改名"}}})
            assert (await receive(ws, "ack"))["rejected"] == "denied"
            assert (await client.get("/api/spaces/qb/boards")).status == 403
            await ws.close()

    run(main())


def test_user_boards_are_not_reachable_by_id_without_manage(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch, remote=True) as (client, app, config):
            hub = app[HUB_KEY]
            other = hub.store.create_board("另一块", make_current=False)["id"]
            ws, reply = await hello(client, other, space="", create=None)
            assert reply == {"t": "error", "reason": "denied"}
            await ws.close()
            ws, reply = await hello(client, "new-one", space="", create={})
            assert reply == {"t": "error", "reason": "denied"}
            await ws.close()

    run(main())


def test_1x_pinned_hellos_are_refused(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app, config):
            install_app(config)
            ws = await client.ws_connect("/ws")
            await ws.send_json({"t": "hello", "role": "ipad", "client": "pad-one", "board": None,
                                "pin": {"board": "q3", "app": "qb"}})
            assert await ws.receive_json() == {"t": "error", "reason": "pin"}
            await ws.close()

    run(main())


def test_app_spaces_are_saved_on_shutdown(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app, config):
            install_app(config)
            ws, _ = await hello(client, "q3")
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke("s1")]}})
            await receive(ws, "ack")
            assert app[SPACES_KEY].peek("qb") is not None
            await ws.close()

    run(main())
    from inksync import FileStorage

    _meta, strokes, problem = FileStorage(tmp_path / "data" / "spaces" / "qb").read_file("q3")
    assert problem is None and [s["id"] for s in strokes] == ["s1"]

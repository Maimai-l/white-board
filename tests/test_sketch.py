"""速写（/sketch 与 Edge 插件的速写小窗口）：临时白板、跟随切换、关闭即丢弃、不留下任何文件。

角色：
* ``mac``：Mac 窗口，跟随当前白板（本机）；
* ``ipad``：iPad 外壳，跟随当前白板（局域网上的其他设备，没有「管理白板」权限）；
* ``panel``：速写页面里的手写板，直接打开速写板（本机）。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from whiteboard import netinfo
from whiteboard.config import Config
from whiteboard.hub import SKETCH_ID
from whiteboard.server import HUB_KEY, create_app
from whiteboard.store import BoardStore

REMOTE = {"X-Test-Remote": "1"}


def run(coro):
    return asyncio.run(coro)


def stroke(stroke_id, x=0.0):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3, "p": [x, 0, 0.5, x + 20, 10, 0.6]}


@asynccontextmanager
async def make_client(tmp_path, monkeypatch, grace=0.15):
    # 测试里的连接都来自回环：带 X-Test-Remote 头的请求当作局域网上的其他设备
    monkeypatch.setattr(netinfo, "is_local_request", lambda request: "X-Test-Remote" not in request.headers)
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    app = create_app(config, BoardStore(config.data_dir))
    app[HUB_KEY].sketch_grace = grace
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


async def receive(ws, kind, timeout=1.0):
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


async def follower(client, client_id, headers=None, legacy=False):
    ws = await client.ws_connect("/ws", headers=headers or {})
    if legacy:
        msg = {"t": "hello", "role": "ipad", "client": client_id, "board": None, "since": 0, "epoch": None}
    else:
        msg = {"t": "hello", "v": 2, "client": client_id, "follow": True, "since": 0, "epoch": None, "device": "ipad"}
    await ws.send_json(msg)
    return ws, await ws.receive_json()


async def panel(client, client_id="sketch-panel"):
    response = await client.post("/api/sketch")
    assert response.status == 200, await response.text()
    ws = await client.ws_connect("/ws")
    await ws.send_json({"t": "hello", "v": 2, "client": client_id, "board": SKETCH_ID, "since": 0, "epoch": None})
    return ws, await ws.receive_json()


async def write(ws, cid, strokes, board=None):
    msg = {"t": "op", "cid": cid, "op": {"op": "add", "strokes": strokes}}
    if board:
        msg["board"] = board
    await ws.send_json(msg)
    return await receive(ws, "ack")


def ipad_board(hub, client_id="ipad-shell"):
    return next(c.board for c in hub.core.connections() if c.id == client_id)


def files_under(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


def test_sketch_round_trip_leaves_no_trace(tmp_path, monkeypatch):
    """开始速写 → iPad 和 Mac 窗口切过去 → iPad 书写、面板实时收到 → 面板关闭 → 都回到原来的白板，
    磁盘上的文件与开始前完全一样。"""

    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            original = hub.current_id
            mac, mac_init = await follower(client, "mac-window")
            ipad, ipad_init = await follower(client, "ipad-shell", REMOTE)
            assert mac_init["board"]["id"] == ipad_init["board"]["id"] == original
            await hub.core.flush()
            before = files_under(tmp_path / "data")

            sketch, init = await panel(client)
            assert init["t"] == "init" and init["board"]["id"] == SKETCH_ID and init["transient"] is True
            for ws in (mac, ipad):
                switched = await receive(ws, "switch")
                assert switched["board"]["id"] == SKETCH_ID and switched["transient"] is True
                assert SKETCH_ID not in json.dumps(switched["boards"]), "速写板不出现在白板列表中"
            assert hub.current_id == original, "当前白板的记录不变"

            ack = await write(ipad, "i1", [stroke("ink-1"), stroke("ink-2", 40)])
            assert "rejected" not in ack, ack
            relayed = await receive(sketch, "op")
            assert [s["id"] for s in relayed["op"]["strokes"]] == ["ink-1", "ink-2"]
            assert (await receive(mac, "op"))["op"]["op"] == "add"
            # Mac 窗口照常上传缩略图：速写板的缩略图被忽略
            thumb = await client.post(f"/api/thumb/{SKETCH_ID}", data=b"\x89PNG fake")
            assert (await thumb.json()).get("skipped") is True
            for _ in range(3):
                hub.core.tick()
            await hub.core.flush()
            assert files_under(tmp_path / "data") == before

            await sketch.close()
            for ws in (mac, ipad):
                back = await receive(ws, "switch", timeout=2.0)
                assert back is not None and back["board"]["id"] == original
                assert "transient" not in back
            assert not hub.core.exists(SKETCH_ID)
            late = await write(ipad, "i2", [stroke("ink-3")], board=SKETCH_ID)
            assert late["rejected"] == "deleted"
            await hub.core.flush()
            assert files_under(tmp_path / "data") == before
            await mac.close()
            await ipad.close()

    run(main())


def test_reloading_the_panel_keeps_the_sketch(tmp_path, monkeypatch):
    """面板刷新（断开后马上重连）不会丢掉速写，iPad 也不会被切走再切回。"""

    async def main():
        async with make_client(tmp_path, monkeypatch, grace=0.4) as (client, app):
            hub = app[HUB_KEY]
            ipad, _ = await follower(client, "ipad-shell", REMOTE)
            sketch, _ = await panel(client)
            await receive(ipad, "switch")
            await write(ipad, "i1", [stroke("keep-me")])
            await sketch.close()
            await asyncio.sleep(0.1)
            sketch, init = await panel(client, "sketch-panel-2")
            assert [s["id"] for s in init["strokes"]] == ["keep-me"]
            await asyncio.sleep(0.6)
            assert hub.sketch_active
            assert await receive(ipad, "switch", timeout=0.3) is None
            await sketch.close()
            assert (await receive(ipad, "switch", timeout=2.0))["board"]["id"] == hub.current_id
            await ipad.close()

    run(main())


def test_two_panels_share_one_sketch_and_it_ends_after_both_close(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            first, _ = await panel(client, "panel-one")
            await write(first, "p1", [stroke("from-first")])
            second, init = await panel(client, "panel-two")
            assert [s["id"] for s in init["strokes"]] == ["from-first"], "第二次开始速写不清空已有内容"
            await first.close()
            await asyncio.sleep(0.4)
            assert hub.sketch_active, "还有一个面板开着"
            await second.close()
            for _ in range(40):
                if not hub.sketch_active:
                    break
                await asyncio.sleep(0.05)
            assert not hub.sketch_active

    run(main())


def test_choosing_another_board_on_the_mac_interrupts_following(tmp_path, monkeypatch):
    """速写中在 Mac 上选了别的白板：iPad 跟着去那块，重连也不再回速写板；
    之后面板关闭，iPad 不再被移动。"""

    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            other = hub.store.create_board("另一块", make_current=False)["id"]
            mac, _ = await follower(client, "mac-window")
            ipad, _ = await follower(client, "ipad-shell", REMOTE)
            sketch, _ = await panel(client)
            await receive(mac, "switch")
            await receive(ipad, "switch")

            await mac.send_json({"t": "sel", "board": other})
            assert (await receive(ipad, "switch"))["board"]["id"] == other
            assert hub.sketch_active, "面板还开着，速写板还在"
            await ipad.close()
            ipad, init = await follower(client, "ipad-shell", REMOTE)
            assert init["board"]["id"] == other
            # 面板上继续写不影响 iPad
            await write(sketch, "p1", [stroke("panel-only")])
            await sketch.close()
            await asyncio.sleep(0.5)
            assert not hub.sketch_active
            assert await receive(ipad, "switch", timeout=0.3) is None
            await mac.close()
            await ipad.close()

    run(main())


def test_devices_reconnecting_during_a_sketch_land_on_it(tmp_path, monkeypatch):
    """iPad 休眠唤醒后重连（新的 hello），速写还在进行：直接回到速写板，可以接着写。"""

    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            sketch, _ = await panel(client)
            await write(sketch, "p1", [stroke("before-sleep")])
            ipad, init = await follower(client, "ipad-shell", REMOTE)
            assert init["board"]["id"] == SKETCH_ID and init["transient"] is True
            assert [s["id"] for s in init["strokes"]] == ["before-sleep"]
            ack = await write(ipad, "i1", [stroke("after-wake")])
            assert "rejected" not in ack
            # 1.0.x 页面同样跟过来，收到的是 1.0.x 的元数据
            legacy, legacy_init = await follower(client, "old-page", REMOTE, legacy=True)
            assert legacy_init["board"]["id"] == SKETCH_ID and legacy_init["board"].get("kind") == "board"
            for ws in (sketch, ipad, legacy):
                await ws.close()

    run(main())


def test_only_devices_with_manage_can_start_or_end_a_sketch(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            assert (await client.post("/api/sketch", headers=REMOTE)).status == 403
            assert not hub.sketch_active
            evil = {"Origin": "http://evil.example"}
            assert (await client.post("/api/sketch", headers=evil)).status == 403
            assert not hub.sketch_active
            assert (await client.get("/sketch")).status == 200
            sketch, _ = await panel(client)
            assert (await client.delete("/api/sketch", headers=REMOTE)).status == 403
            assert hub.sketch_active
            ended = await client.delete("/api/sketch")
            assert (await ended.json())["ended"] is True
            assert (await receive(sketch, "deleted"))["board"] == SKETCH_ID
            assert (await (await client.delete("/api/sketch")).json())["ended"] is False
            await sketch.close()

    run(main())


def test_clearing_and_undo_on_the_sketch(tmp_path, monkeypatch):
    """清空在速写板上照常可用（iPad 没有「清空白板」权限时被拒绝，与普通白板相同）。"""

    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            sketch, _ = await panel(client)
            ipad, _ = await follower(client, "ipad-shell", REMOTE)
            await write(ipad, "i1", [stroke("a"), stroke("b", 30)])
            await ipad.send_json({"t": "op", "cid": "i2", "op": {"op": "clear"}})
            assert (await receive(ipad, "ack"))["rejected"] == "denied"
            await sketch.send_json({"t": "op", "cid": "p1", "op": {"op": "clear"}})
            assert "rejected" not in await receive(sketch, "ack")
            assert await hub.core.strokes(SKETCH_ID) == []
            for ws in (sketch, ipad):
                await ws.close()

    run(main())


def test_a_restarted_server_has_no_sketch(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            original = app[HUB_KEY].current_id
            sketch, _ = await panel(client)
            await write(sketch, "p1", [stroke("gone")])
            await sketch.close()
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            assert not hub.core.exists(SKETCH_ID) and hub.current_id == original
            ipad, init = await follower(client, "ipad-shell", REMOTE)
            assert init["board"]["id"] == original
            await ipad.close()

    run(main())


@pytest.mark.parametrize("closing", ["panel", "server"])
def test_shutdown_with_an_open_sketch_writes_nothing(tmp_path, monkeypatch, closing):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            await hub.core.flush()
            before = files_under(tmp_path / "data")
            sketch, _ = await panel(client)
            await write(sketch, "p1", [stroke("x")])
            if closing == "panel":
                await sketch.close()
                await asyncio.sleep(0.4)
        assert files_under(tmp_path / "data") == before

    run(main())


def test_rapid_open_close_cycles_stay_consistent(tmp_path, monkeypatch):
    """面板快速地关了又开五次，iPad 一直在写：每次都落在正确的白板上，结束后一切复原，
    不写任何文件，也没有遗留的定时任务把新开的速写误结束。"""

    async def main():
        async with make_client(tmp_path, monkeypatch, grace=0.2) as (client, app):
            hub = app[HUB_KEY]
            original = hub.current_id
            ipad, _ = await follower(client, "ipad-shell", REMOTE)
            await hub.core.flush()
            before = files_under(tmp_path / "data")
            for round_ in range(5):
                sketch, init = await panel(client, f"panel-{round_}")
                # 看服务端记录的 iPad 所在白板；收到的消息里可能还有上一轮结束时的切换
                assert ipad_board(hub) == SKETCH_ID, round_
                ack = await write(ipad, f"i{round_}", [stroke(f"r{round_}")], board=SKETCH_ID)
                assert "rejected" not in ack, (round_, ack)
                await sketch.close()
                # 有一半的轮次在宽限期内就重新打开，另一半等速写真正结束
                await asyncio.sleep(0.05 if round_ % 2 == 0 else 0.5)
            await asyncio.sleep(0.5)
            assert not hub.sketch_active
            assert ipad_board(hub) == original
            # 再开一次：之前留下的检查任务不能把它结束
            sketch, init = await panel(client, "panel-last")
            await asyncio.sleep(0.6)
            assert hub.sketch_active and init["strokes"] == []
            await sketch.close()
            await asyncio.sleep(0.5)
            await hub.core.flush()
            assert files_under(tmp_path / "data") == before
            assert await hub.core.strokes(original) == []
            await ipad.close()

    run(main())


def test_ops_meant_for_an_ended_sketch_never_reach_the_current_board(tmp_path, monkeypatch):
    """iPad 断线期间在速写板上写的笔画（带着速写板的 id）在速写结束后才送达：被拒绝，
    不会落到当前白板上。"""

    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            original = hub.current_id
            sketch, _ = await panel(client)
            ipad, init = await follower(client, "ipad-shell", REMOTE)
            assert init["board"]["id"] == SKETCH_ID
            await ipad.close()  # iPad 断线
            await sketch.close()
            await asyncio.sleep(0.5)
            assert not hub.sketch_active
            ipad, init = await follower(client, "ipad-shell", REMOTE)
            assert init["board"]["id"] == original
            ack = await write(ipad, "offline-1", [stroke("written-offline")], board=SKETCH_ID)
            assert ack["rejected"] == "deleted"
            assert await hub.core.strokes(original) == []
            await ipad.close()

    run(main())

"""服务端协议测试：握手、广播、断线补齐、权限与设备识别。"""

import asyncio
import io
import json
import re
import time
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from whiteboard import models
from whiteboard.config import Config
from whiteboard.server import CONFIG_KEY, HUB_KEY, create_app, detect_role
from whiteboard.store import BoardStore


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def make_client(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    store = BoardStore(config.data_dir)
    app = create_app(config, store)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


async def hello(ws, role="mac", board=None, since=0, client_id="client-one", epoch=None):
    await ws.send_json(
        {
            "t": "hello",
            "role": role,
            "client": client_id,
            "board": board,
            "since": since,
            "epoch": epoch,
        }
    )
    return await ws.receive_json()


def stroke(stroke_id="s1"):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3, "p": [0, 0, 0.5, 5, 5, 0.6]}


def test_index_and_role_detection(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.get("/", headers={"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0)"})
            body = await response.text()
            assert response.status == 200
            assert 'data-role="ipad"' in body

            response = await client.get("/", headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
            assert 'data-role="mac"' in await response.text()

    run(main())
    assert detect_role("ipad safari") == "ipad"
    assert detect_role("anything", override="ipad") == "ipad"


def test_profile_and_icon_endpoints(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.get("/profile.mobileconfig?host=mac.local")
            assert response.status == 200
            assert response.content_type == "application/x-apple-aspen-config"
            body = await response.read()
            assert b"com.apple.webClip.managed" in body
            assert b"http://mac.local:" in body

            icon = await client.get("/icon.png")
            assert (await icon.read()).startswith(b"\x89PNG")

    run(main())


def test_init_then_broadcast_between_two_clients(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            first = await hello(mac, "mac", client_id="mac-1")
            second = await hello(ipad, "ipad", client_id="ipad-1")
            assert first["t"] == "init" and first["strokes"] == []
            assert second["board"]["id"] == first["board"]["id"]
            assert first["info"]["port"]

            await ipad.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke()]}})
            ack = await ipad.receive_json()
            assert ack["t"] == "ack" and ack["cid"] == "c1" and ack["seq"] == 1
            assert ack["op"]["strokes"][0]["n"] == 0

            relayed = await mac.receive_json()
            assert relayed["t"] == "op"
            assert relayed["op"]["strokes"][0]["id"] == "s1"
            assert relayed["src"] == "ipad-1"

            # 实时笔迹只转发，不进操作日志
            await ipad.send_json({"t": "live", "id": "s2", "phase": "b", "color": "#000000"})
            live = await mac.receive_json()
            assert live["t"] == "live" and live["src"] == "ipad-1"

            await mac.close()
            await ipad.close()

    run(main())


def test_reconnect_receives_only_missing_ops(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws = await client.ws_connect("/ws")
            init = await hello(ws, "ipad", client_id="ipad-1")
            board_id = init["board"]["id"]
            epoch = init["epoch"]
            for i in range(3):
                await ws.send_json({"t": "op", "cid": f"c{i}", "op": {"op": "add", "strokes": [stroke(f"s{i}")]}})
                await ws.receive_json()
            await ws.close()

            again = await client.ws_connect("/ws")
            resumed = await hello(again, "ipad", board=board_id, since=1, client_id="ipad-1", epoch=epoch)
            assert resumed["t"] == "sync"
            assert [op["seq"] for op in resumed["ops"]] == [2, 3]
            assert "strokes" not in resumed

            fresh = await client.ws_connect("/ws")
            full = await hello(fresh, "ipad", board=board_id, since=0, client_id="ipad-2", epoch=epoch)
            assert full["t"] == "sync"
            # 序号 0 表示从头开始，历史仍然完整，补差量即可
            assert len(full["ops"]) == 3

            stale = await client.ws_connect("/ws")
            snapshot = await hello(stale, "ipad", board="another-board", since=2, client_id="ipad-3", epoch=epoch)
            assert snapshot["t"] == "init"
            assert len(snapshot["strokes"]) == 3
            await again.close()
            await fresh.close()
            await stale.close()

    run(main())


def test_duplicate_stroke_is_acknowledged_but_not_duplicated(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            for cid in ("a", "b"):
                await ws.send_json({"t": "op", "cid": cid, "op": {"op": "add", "strokes": [stroke()]}})
                ack = await ws.receive_json()
                assert ack["t"] == "ack" and ack["cid"] == cid
            assert len(app[HUB_KEY].board().strokes) == 1
            await ws.close()

    run(main())


def test_a_device_without_manage_cannot_switch_or_create_boards(tmp_path, remote):
    """权限只看对端地址，不看握手里报的 role：没给权限的设备做不了管理操作。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            before = hub.current_id

            await ws.send_json({"t": "newboard"})
            await ws.send_json({"t": "op", "cid": "m", "op": {"op": "meta", "meta": {"background": "dots"}}})
            ack = await ws.receive_json()
            assert ack["t"] == "ack"
            assert hub.current_id == before
            assert hub.board().meta["background"]["pattern"] == "grid"
            await ws.close()

    run(main())


def test_every_client_is_told_the_version(tmp_path):
    """版本号跟着 init 一起发。以前只有 pywebview 那条本地接口有，切一次白板就没了。"""
    from whiteboard import __version__

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws = await client.ws_connect("/ws")
            await ws.send_json({"t": "hello", "client": "mac-1", "role": "mac"})
            init = await ws.receive_json()
            assert init["info"]["version"] == __version__
            await ws.close()

    run(main())


def test_a_touch_device_with_manage_can_switch_boards(tmp_path, remote):
    """触摸设备的右上角也有管理入口，Mac 放开权限之后点了就得真的切过去。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            app[CONFIG_KEY].set_remote_permission("manage", True)
            other = (await hub.create_board())["id"]
            await hub.select_board(hub.store.list_metas()[-1]["id"])
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")

            await ws.send_json({"t": "sel", "board": other})
            msg = await ws.receive_json()
            assert msg["t"] == "switch" and msg["board"]["id"] == other
            assert hub.current_id == other
            await ws.close()

    run(main())


def test_mac_switches_board_and_ipad_follows(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await hello(ipad, "ipad", client_id="ipad-1")

            await mac.send_json({"t": "newboard"})
            mac_switch = await mac.receive_json()
            ipad_switch = await ipad.receive_json()
            assert mac_switch["t"] == "switch"
            assert ipad_switch["board"]["id"] == mac_switch["board"]["id"]
            assert len(mac_switch["boards"]) == 2

            await mac.send_json({"t": "op", "cid": "m1", "op": {"op": "meta", "meta": {"background": "dots"}}})
            ack = await mac.receive_json()
            assert ack["op"]["meta"]["background"] == "dots"
            relayed = await ipad.receive_json()
            assert relayed["op"]["meta"]["background"] == "dots"

            await mac.send_json({"t": "delboard", "board": mac_switch["board"]["id"]})
            after = await mac.receive_json()
            assert after["t"] == "switch"
            assert len(after["boards"]) == 1
            await mac.close()
            await ipad.close()

    run(main())


def test_mac_renames_any_board_and_ipad_hears_it(tmp_path):
    """改名可以改不是当前这块的白板，所以只广播列表，不重发整块白板。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await hello(ipad, "ipad", client_id="ipad-1")

            await mac.send_json({"t": "newboard"})
            switched = await mac.receive_json()
            await ipad.receive_json()
            second = switched["board"]["id"]
            first = next(m["id"] for m in switched["boards"] if m["id"] != second)

            # 改的是另一块（当前停在 second 上）
            await mac.send_json({"t": "rename", "board": first, "name": " 线性代数 "})
            for side in (mac, ipad):
                msg = await side.receive_json()
                assert msg["t"] == "boards"
                assert msg["board"]["id"] == second  # 当前白板没有被切走
                assert {m["id"]: m["name"] for m in msg["boards"]}[first] == "线性代数"
            assert hub.store.get_meta(first)["name"] == "线性代数"

            await mac.close()
            await ipad.close()

    run(main())


def test_mac_puts_a_board_in_a_folder_and_ipad_hears_it(tmp_path):
    """归类和改名走同一条路：只广播列表，不把当前白板切走。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await hello(ipad, "ipad", client_id="ipad-1")

            await mac.send_json({"t": "newboard"})
            switched = await mac.receive_json()
            await ipad.receive_json()
            second = switched["board"]["id"]
            first = next(m["id"] for m in switched["boards"] if m["id"] != second)

            await mac.send_json({"t": "folder", "board": first, "folder": " 数学 "})
            for side in (mac, ipad):
                msg = await side.receive_json()
                assert msg["t"] == "boards"
                assert msg["board"]["id"] == second  # 当前白板没有被切走
                assert {m["id"]: m.get("folder") for m in msg["boards"]}[first] == "数学"
            assert models.folder_of(hub.store.get_meta(first)) == "数学"

            # 移出来之后这个字段就没了，界面上那块白板回到没归类的那一段
            await mac.send_json({"t": "folder", "board": first, "folder": ""})
            for side in (mac, ipad):
                msg = await side.receive_json()
                assert "folder" not in {m["id"]: m for m in msg["boards"]}[first]

            await mac.close()
            await ipad.close()

    run(main())


def test_mac_reorders_boards_and_everyone_hears_it(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await hello(ipad, "ipad", client_id="ipad-1")

            await mac.send_json({"t": "newboard"})
            switched = await mac.receive_json()
            await ipad.receive_json()
            order = [m["id"] for m in switched["boards"]]

            await mac.send_json({"t": "order", "ids": list(reversed(order))})
            for side in (mac, ipad):
                msg = await side.receive_json()
                assert msg["t"] == "boards"
                assert [m["id"] for m in msg["boards"]] == list(reversed(order))
            assert [m["id"] for m in hub.store.list_metas()] == list(reversed(order))

            await mac.close()
            await ipad.close()

    run(main())


def test_a_device_without_manage_cannot_reorder_boards(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            await hub.create_board()
            before = [m["id"] for m in hub.store.list_metas()]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            await ws.send_json({"t": "order", "ids": list(reversed(before))})
            await ws.send_json({"t": "ping", "ts": 7})
            pong = await ws.receive_json()
            assert pong["t"] == "pong"  # 排序那条被丢掉了，下一条才是回音
            assert [m["id"] for m in hub.store.list_metas()] == before
            await ws.close()

    run(main())


def test_a_folder_can_be_created_renamed_and_deleted(tmp_path):
    """空文件夹只有索引里有记录，所以新建、改名、删除都得各有一条消息。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            board = hub.current_id

            await mac.send_json({"t": "newfolder", "name": " 数学 "})
            msg = await mac.receive_json()
            assert msg["t"] == "boards" and msg["folders"] == ["数学"]

            await mac.send_json({"t": "newfolder", "name": "数学"})  # 重名不会再发一条
            await mac.send_json({"t": "folder", "board": board, "folder": "数学"})
            msg = await mac.receive_json()
            assert {m["id"]: m.get("folder") for m in msg["boards"]}[board] == "数学"

            await mac.send_json({"t": "renamefolder", "name": "数学", "to": "线性代数"})
            msg = await mac.receive_json()
            assert msg["folders"] == ["线性代数"]
            # 名字就是身份：里面那块白板记的名字要跟着改
            assert {m["id"]: m.get("folder") for m in msg["boards"]}[board] == "线性代数"

            await mac.send_json({"t": "delfolder", "name": "线性代数"})
            msg = await mac.receive_json()
            assert msg["folders"] == []
            assert "folder" not in {m["id"]: m for m in msg["boards"]}[board]  # 白板还在
            assert len(msg["boards"]) == 1

            await mac.close()

    run(main())


def test_a_board_made_inside_a_folder_lands_in_it(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await mac.send_json({"t": "newfolder", "name": "数学"})
            await mac.receive_json()

            await mac.send_json({"t": "newboard", "kind": "note", "folder": "数学"})
            msg = await mac.receive_json()
            assert msg["board"]["folder"] == "数学"
            assert models.folder_of(hub.store.get_meta(msg["board"]["id"])) == "数学"

            # 没有这个文件夹的话就当没写，新白板照常建在外面
            await mac.send_json({"t": "newboard", "kind": "note", "folder": "查无此夹"})
            msg = await mac.receive_json()
            assert "folder" not in msg["board"]

            await mac.close()

    run(main())


def test_a_device_without_manage_cannot_make_or_delete_folders(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            hub.create_folder("数学")
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            await ws.send_json({"t": "newfolder", "name": "偷偷建"})
            await ws.send_json({"t": "delfolder", "name": "数学"})
            await ws.send_json({"t": "renamefolder", "name": "数学", "to": "改了"})
            await ws.send_json({"t": "ping", "ts": 7})
            pong = await ws.receive_json()
            assert pong["t"] == "pong"  # 前面三条都被丢掉了，下一条才是回音
            assert hub.store.folders() == ["数学"]
            await ws.close()

    run(main())


def test_a_device_without_manage_cannot_move_a_board_into_a_folder(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            await ws.send_json({"t": "folder", "board": hub.current_id, "folder": "偷偷归类"})
            await ws.send_json({"t": "ping", "ts": 7})
            pong = await ws.receive_json()
            assert pong["t"] == "pong"  # 归类那条被丢掉了，下一条才是回音
            assert models.folder_of(hub.store.get_meta(hub.current_id)) == ""
            await ws.close()

    run(main())


def test_a_device_without_manage_cannot_rename(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            await ws.send_json({"t": "rename", "board": hub.current_id, "name": "偷偷改"})
            await ws.send_json({"t": "ping", "ts": 7})
            pong = await ws.receive_json()
            assert pong["t"] == "pong"  # 改名那条被丢掉了，下一条才是回音
            assert hub.store.get_meta(hub.current_id)["name"] == ""
            await ws.close()

    run(main())


def test_unknown_messages_are_ignored(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws = await client.ws_connect("/ws")
            await ws.send_str("not json at all")
            await ws.send_json({"t": "op", "op": {"op": "clear"}})  # 未握手，应被忽略
            await hello(ws, "mac", client_id="mac-1")
            await ws.send_json({"t": "ping", "ts": 42})
            pong = await ws.receive_json()
            assert pong == {"t": "pong", "ts": 42}
            await ws.close()

    run(main())


def test_strokes_survive_server_restart(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            init = await hello(ws, "ipad", client_id="ipad-1")
            board_id = init["board"]["id"]
            await ws.send_json({"t": "op", "cid": "c", "op": {"op": "add", "strokes": [stroke("keep")]}})
            await ws.receive_json()
            await ws.close()
            app[HUB_KEY].save_all()
            return board_id

        # 退出上下文即触发 on_cleanup，白板已落盘

    board_id = run(main())

    async def again():
        async with make_client(tmp_path) as (client, _app):
            ws = await client.ws_connect("/ws")
            init = await hello(ws, "ipad", client_id="ipad-1")
            assert init["board"]["id"] == board_id
            assert [s["id"] for s in init["strokes"]] == ["keep"]
            await ws.close()

    run(again())


def test_thumbnail_upload_and_fetch(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            board_id = app[HUB_KEY].current_id
            blank = await client.get(f"/api/thumb/{board_id}")
            assert blank.status == 200  # 没有缩略图时返回空白图

            png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
            assert (await client.post(f"/api/thumb/{board_id}", data=png)).status == 200
            fetched = await client.get(f"/api/thumb/{board_id}")
            assert (await fetched.read()) == png

            assert (await client.post(f"/api/thumb/{board_id}", data=b"<svg/>")).status == 400
            assert (await client.post("/api/thumb/nope", data=png)).status == 404

    run(main())


def test_info_and_boards_endpoints(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            info = await (await client.get("/api/info")).json()
            assert info["hostname"].endswith(".local")
            assert info["urls"]
            boards = await (await client.get("/api/boards")).json()
            assert boards["current"] == app[HUB_KEY].current_id
            assert len(boards["boards"]) == 1

    run(main())


def test_stale_epoch_forces_full_snapshot(tmp_path):
    """服务端重启后序号归零，旧客户端必须拿到整块白板而不是空的差量。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws = await client.ws_connect("/ws")
            init = await hello(ws, "ipad", client_id="ipad-1")
            board_id = init["board"]["id"]
            await ws.send_json({"t": "op", "cid": "c", "op": {"op": "add", "strokes": [stroke("s")]}})
            await ws.receive_json()
            await ws.close()

            # 换一个 epoch 就相当于「服务端重启过」
            resumed = await client.ws_connect("/ws")
            answer = await hello(resumed, "ipad", board=board_id, since=9, client_id="ipad-1", epoch="stale-epoch")
            assert answer["t"] == "init"
            assert [s["id"] for s in answer["strokes"]] == ["s"]
            assert answer["epoch"] != "stale-epoch"
            await resumed.close()

    run(main())


def test_reconnect_with_same_client_id_keeps_broadcasting(tmp_path):
    """旧连接晚一步断开时，不能把同 id 的新连接从广播名单里摘掉。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            stale = await client.ws_connect("/ws")
            await hello(stale, "ipad", client_id="ipad-1")
            fresh = await client.ws_connect("/ws")
            await hello(fresh, "ipad", client_id="ipad-1")
            detached = asyncio.Event()
            hub.core.on("detached", lambda conn: detached.set())
            await stale.close()
            await asyncio.wait_for(detached.wait(), timeout=5)  # 服务端处理完旧连接的断开

            assert hub.clients.get("ipad-1") is not None
            mac = await client.ws_connect("/ws")
            await hello(mac, "mac", client_id="mac-1")
            await mac.send_json({"t": "op", "cid": "c", "op": {"op": "add", "strokes": [stroke()]}})
            await mac.receive_json()  # ack
            relayed = await fresh.receive_json()
            assert relayed["t"] == "op"
            await fresh.close()
            await mac.close()

    run(main())


def test_static_files_must_revalidate(tmp_path):
    """iPad 上的 Safari 会启发式缓存 js/css，更新后必须回源确认。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.get("/static/js/app.js")
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-cache"
            index = await client.get("/")
            assert index.headers["Cache-Control"] == "no-cache"

    run(main())


def test_debug_reports_are_logged(tmp_path, caplog):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.post("/api/debug", json={"role": "ipad", "最长帧": 180})
            assert response.status == 200
            assert (await client.post("/api/debug", data=b"not json")).status == 400

    with caplog.at_level("WARNING"):
        run(main())
    assert any("[诊断]" in record.message for record in caplog.records)


# ------------------------------------------------------------ 文档板（beta）


def _doc_bytes(sizes=((595, 842), (400, 600))):
    import pypdf

    writer = pypdf.PdfWriter()
    for width, height in sizes:
        writer.add_blank_page(width=width, height=height)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_upload_creates_doc_board_and_switches(tmp_path):
    """拖进来一份 PDF：建板、切过去，并且广播给已经连着的 iPad。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            await hello(ws, role="ipad", client_id="pad-one")

            response = await client.post("/api/doc?name=讲义.pdf", data=_doc_bytes())
            assert response.status == 200
            meta = (await response.json())["board"]
            assert models.kind_of(meta) == "doc"
            assert meta["data"]["doc"]["pages"] == [[595.0, 842.0], [400.0, 600.0]]

            switched = await ws.receive_json()
            assert switched["t"] == "switch"
            assert switched["board"]["id"] == meta["id"]
            assert app[HUB_KEY].current_id == meta["id"]
            await ws.close()

    run(main())


def test_a_textbook_sized_pdf_becomes_a_doc_board(tmp_path):
    """用户反馈：拖进两本几百页的物理教材，都提示打不开。所有页从上往下排成一张长画布，
    超过一百来页 A4 就超过了画布的高度上限，建板时被当成不合法拒掉。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            response = await client.post("/api/doc?name=教材.pdf", data=_doc_bytes([(595, 842)] * 300))
            assert response.status == 200, await response.text()
            body = await response.json()
            meta = body["board"]
            assert len(meta["data"]["doc"]["pages"]) == 300
            assert body["total"] == 300
            assert meta["canvas"]["height"] == 300 * 842 + 299 * models.DOC_PAGE_GAP
            assert len(meta["layers"]) == 300 and meta["layers"][-1]["y"] == 299 * (842 + models.DOC_PAGE_GAP)
            # 重新打开存储目录（重启应用）之后，这块白板和它的 300 页都还在
            app[HUB_KEY].save_now() if hasattr(app[HUB_KEY], "save_now") else None
            reloaded = BoardStore(app[HUB_KEY].store.data_dir).get_meta(meta["id"])
            assert reloaded["canvas"] == meta["canvas"]
            assert len(reloaded["layers"]) == 300

    run(main())


def test_a_pdf_over_the_page_limit_says_how_many_pages_it_has(tmp_path):
    """超过 400 页的 PDF 导入前 400 页，回复里带上一共有几页，界面据此提示，不悄悄截掉。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.post("/api/doc?name=大部头.pdf", data=_doc_bytes([(595, 842)] * 410))
            assert response.status == 200, await response.text()
            body = await response.json()
            assert len(body["board"]["data"]["doc"]["pages"]) == 400
            assert body["total"] == 410

    run(main())


def test_upload_rejects_unsupported_file(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            assert (await client.post("/api/doc?name=a.txt", data=b"hello")).status == 400
            assert (await client.post("/api/doc?name=a.pdf", data=b"broken")).status == 400

    run(main())


def test_doc_page_is_rendered_and_cacheable(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=a.pdf", data=_doc_bytes())).json())["board"]
            page = await client.get(f"/api/doc/{meta['id']}/0?w=320")
            assert page.status == 200
            assert page.content_type == "image/jpeg"
            assert len(await page.read()) > 0
            etag = page.headers["ETag"]

            cached = await client.get(f"/api/doc/{meta['id']}/0?w=320", headers={"If-None-Match": etag})
            assert cached.status == 304
            assert (await client.get(f"/api/doc/{meta['id']}/9?w=320")).status == 404

    run(main())


def test_doc_page_rejects_plain_boards(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            board_id = app[HUB_KEY].current_id
            assert (await client.get(f"/api/doc/{board_id}/0")).status == 404
            assert (await client.get("/api/doc/nope/0")).status == 404

    run(main())


def test_doc_board_thumbnail_falls_back_to_first_page(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=a.pdf", data=_doc_bytes())).json())["board"]
            thumb = await client.get(f"/api/thumb/{meta['id']}")
            assert thumb.status == 200
            assert thumb.content_type == "image/jpeg"  # 渲染的原件首页，不是空白 PNG

    run(main())


def test_doc_export_merges_strokes(tmp_path):
    """导出的 PDF 页数不变，笔迹进去了，体积也没有明显变大。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            source = _doc_bytes()
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=source)).json())["board"]
            ws = await client.ws_connect("/ws")
            await hello(ws, role="mac", board=meta["id"])
            await ws.send_json(
                {
                    "t": "op",
                    "cid": 1,
                    "op": {
                        "op": "add",
                        "stroke": {
                            "id": "x1",
                            "tool": "pen",
                            "color": "#1b1b1f",
                            "w": 3.0,
                            "p": [60, 100, 0.7, 120, 140, 0.7, 180, 100, 0.7],
                        },
                    },
                }
            )
            await ws.receive_json()

            response = await client.get(f"/api/export/{meta['id']}")
            assert response.status == 200
            assert "%E8%AE%B2%E4%B9%89-" in response.headers["Content-Disposition"]
            body = await response.read()
            await ws.close()

        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(body))
        assert len(reader.pages) == 2
        assert len(body) < len(source) + 4096

    run(main())


def test_export_rejects_plain_boards(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            assert (await client.get(f"/api/export/{app[HUB_KEY].current_id}")).status == 404

    run(main())


def test_large_upload_is_stored_byte_exact(tmp_path):
    """上传要按块读完：读成半截的话文件会坏掉。"""
    import os

    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.frombytes("RGB", (900, 700), os.urandom(900 * 700 * 3)).save(buffer, "PNG")
    source = buffer.getvalue()
    assert len(source) > 512 * 1024  # 一定跨多个读块

    async def main():
        async with make_client(tmp_path) as (client, app):
            response = await client.post("/api/doc?name=big.png", data=source)
            assert response.status == 200
            meta = (await response.json())["board"]
            stored = app[HUB_KEY].store.doc_path(meta["id"])
            assert stored.read_bytes() == source
            assert meta["data"]["doc"]["pages"] == [[900.0, 700.0]]

    run(main())


# --------------------------------------------------- 只有本机能做管理操作


@pytest.fixture
def remote(monkeypatch):
    """把连接伪装成局域网上的别的设备。

    真实判断看的是 TCP 对端地址，测试里全都是回环，所以这里换掉那个判断本身。
    """
    monkeypatch.setattr("whiteboard.server.netinfo.is_own_address", lambda remote: False)


def test_is_own_address_only_trusts_this_machine():
    from whiteboard import netinfo

    assert netinfo.is_own_address("127.0.0.1")
    assert netinfo.is_own_address("::1")
    assert netinfo.is_own_address("::ffff:127.0.0.1")
    assert not netinfo.is_own_address("192.0.2.77")
    assert not netinfo.is_own_address("")
    assert not netinfo.is_own_address(None)
    assert not netinfo.is_own_address("不是地址")


def test_remote_device_gets_no_permissions_by_default(tmp_path, remote):
    """页面上带着这台设备拿到的权限清单，界面照它决定露出哪些入口。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            assert 'data-perms=""' in await (await client.get("/?role=mac")).text()
            app[CONFIG_KEY].set_remote_permission("export", True)
            assert 'data-perms="export"' in await (await client.get("/?role=mac")).text()

    run(main())


def test_this_machine_gets_every_permission(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            body = await (await client.get("/")).text()
            assert 'data-perms="clear export manage settings"' in body

    run(main())


def test_remote_device_cannot_reach_management_apis(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            board_id = app[HUB_KEY].current_id
            for path in (
                "/api/info",
                "/api/boards",
                f"/api/thumb/{board_id}",
                f"/api/export/{board_id}",
            ):
                assert (await client.get(path)).status == 403, path
            assert (await client.post(f"/api/thumb/{board_id}", data=b"x")).status == 403
            assert (await client.post("/api/doc?name=a.pdf", data=b"x")).status == 403

            # 书写本身照常：页面、描述文件、诊断上报都还通
            assert (await client.get("/")).status == 200
            assert (await client.get("/profile.mobileconfig")).status == 200
            assert (await client.post("/api/debug", json={"role": "ipad"})).status == 200

    run(main())


def test_permissions_are_granted_one_by_one(tmp_path, remote):
    """放开导出不等于放开管理：两边互不牵连。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            board_id = app[HUB_KEY].current_id
            app[CONFIG_KEY].set_remote_permission("export", True)
            assert (await client.get(f"/api/export/{board_id}")).status != 403
            assert (await client.get("/api/boards")).status == 403

            app[CONFIG_KEY].set_remote_permission("export", False)
            app[CONFIG_KEY].set_remote_permission("manage", True)
            assert (await client.get("/api/boards")).status == 200
            assert (await client.get(f"/api/export/{board_id}")).status == 403

    run(main())


def test_remote_device_cannot_manage_over_websocket(tmp_path, remote):
    """握手里报 role=mac 也没用：权限在连接建立时按对端地址就定死了。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            before = hub.current_id
            ws = await client.ws_connect("/ws")
            await hello(ws, "mac", client_id="fake-mac")  # hello 已经把 init 收掉了

            await ws.send_json({"t": "newboard"})
            await ws.send_json({"t": "rename", "board": before, "name": "偷偷改"})
            await ws.send_json({"t": "delboard", "board": before})
            await ws.send_json({"t": "op", "cid": "m", "op": {"op": "meta", "meta": {"background": "dots"}}})
            ack = await ws.receive_json()
            assert ack["t"] == "ack"  # 前三条被丢掉了，这是第四条的回执

            assert hub.current_id == before
            assert len(hub.store.list_metas()) == 1
            assert hub.store.get_meta(before)["name"] == ""
            assert hub.board().meta["background"]["pattern"] == "grid"

            # 写字照常
            await ws.send_json(
                {
                    "t": "op",
                    "cid": "s",
                    "op": {
                        "op": "add",
                        "strokes": [
                            {"id": "a", "tool": "pen", "color": "#000000", "w": 2, "p": [0, 0, 0.5, 1, 1, 0.5]}
                        ],
                    },
                }
            )
            ack = await ws.receive_json()
            assert ack["op"]["op"] == "add"
            await ws.close()

    run(main())


def test_remote_device_cannot_clear_until_allowed(tmp_path, remote):
    """清空白板单独一项权限：没给之前，别的设备连 iPad 那套界面也清不掉。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="pad-1")
            add = {
                "t": "op",
                "cid": "a",
                "op": {
                    "op": "add",
                    "strokes": [{"id": "s1", "tool": "pen", "color": "#000000", "w": 2, "p": [0, 0, 0.5, 1, 1, 0.5]}],
                },
            }
            await ws.send_json(add)
            await ws.receive_json()
            assert len(hub.board().strokes) == 1

            await ws.send_json({"t": "op", "cid": "c", "op": {"op": "clear"}})
            ack = await ws.receive_json()
            assert "op" not in ack  # 被丢掉了
            assert len(hub.board().strokes) == 1

            app[CONFIG_KEY].set_remote_permission("clear", True)
            ws2 = await client.ws_connect("/ws")  # 权限在连接建立时定下，要重连才生效
            await hello(ws2, "ipad", client_id="pad-2")
            await ws2.send_json({"t": "op", "cid": "c", "op": {"op": "clear"}})
            ack = await ws2.receive_json()
            assert ack["op"]["op"] == "clear"
            assert not hub.board().strokes

            await ws.close()
            await ws2.close()

    run(main())


def test_opening_the_gate_lets_other_devices_manage(tmp_path, remote):
    """在 Mac 上放开「管理白板」之后，那道闸就开了。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            app[CONFIG_KEY].set_remote_permission("manage", True)
            assert (await client.get("/api/boards")).status == 200

            ws = await client.ws_connect("/ws")
            await hello(ws, "mac", client_id="remote-mac")
            await ws.send_json({"t": "newboard"})
            switched = await ws.receive_json()
            assert switched["t"] == "switch"
            assert len(app[HUB_KEY].store.list_metas()) == 2
            await ws.close()

    run(main())


def test_requests_from_other_web_pages_are_refused(tmp_path):
    """浏览器里其他网页发来的请求（跨站 POST、WebSocket 握手、DNS 重绑定）一律拒绝，
    而且不产生任何改动；白板自己的页面和不带 Origin 的脚本照常工作。"""
    import aiohttp

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            before = hub.core.count()
            own = f"http://{client.host}:{client.port}"
            evil = {"Origin": "http://evil.example"}

            response = await client.post("/api/doc?name=a.pdf", data=_doc_bytes(), headers=evil)
            assert response.status == 403
            assert hub.core.count() == before
            with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                await client.ws_connect("/ws", headers=evil)
            assert caught.value.status == 403
            rebinding = {"Host": f"evil.example:{client.port}", "Origin": f"http://evil.example:{client.port}"}
            assert (await client.get("/api/info", headers=rebinding)).status == 403
            assert (await client.post("/api/doc?name=a.pdf", data=_doc_bytes(), headers=rebinding)).status == 403
            assert hub.core.count() == before

            assert (await client.post("/api/doc?name=a.pdf", data=_doc_bytes(), headers={"Origin": own})).status == 200
            assert hub.core.count() == before + 1
            ws = await client.ws_connect("/ws", headers={"Origin": own})
            assert (await hello(ws))["t"] in ("init", "sync")
            await ws.close()
            assert (await client.get("/api/info")).status == 200

    run(main())


def test_allowed_hosts_from_config(tmp_path):
    """配置文件里的 allowed_hosts：例如用 VPN 分配的域名打开白板。"""

    async def main():
        config = Config(path=tmp_path / "config.json")
        config.data_dir = tmp_path / "data"
        config.values["allowed_hosts"] = ["mac.example.ts.net", 3, ""]
        assert config.allowed_hosts == ["mac.example.ts.net"]
        client = TestClient(TestServer(create_app(config, BoardStore(config.data_dir))))
        await client.start_server()
        try:
            assert (await client.get("/api/info", headers={"Host": "mac.example.ts.net:8848"})).status == 200
            assert (await client.get("/api/info", headers={"Host": "other.example.net"})).status == 403
        finally:
            await client.close()

    run(main())


# --------------------------------------------------- 回收站、复制、连接的设备、二维码


async def hello_v2(ws, client_id, device="mac", **fields):
    """1.1 起的页面：带协议版本，跟随当前白板。"""
    await ws.send_json({"t": "hello", "v": 2, "client": client_id, "device": device, "follow": True, **fields})
    return await ws.receive_json()


async def receive_kind(ws, kind, timeout=2.0):
    """收到某种消息为止，跳过别的；超时返回 None。"""
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


async def wait_until(condition, timeout=5.0):
    """等条件成立（轮询，不是固定等待）；超时返回 False。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def by_id(boards):
    return {m["id"]: m for m in boards}


def test_deleting_a_board_moves_it_to_the_trash(tmp_path):
    """删除不再是彻底删除：白板进回收站，列表里还在（带 data.trashed），设备都换到别的白板。
    1.0.x 页面根本看不到回收站里的白板。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            first = hub.current_id
            mac = await client.ws_connect("/ws")
            old = await client.ws_connect("/ws")
            await hello_v2(mac, "mac-1")
            await hello(old, "ipad", client_id="old-ipad")

            await mac.send_json({"t": "newboard"})
            second = (await receive_kind(mac, "switch"))["board"]["id"]
            await receive_kind(old, "switch")

            await mac.send_json({"t": "delboard", "board": second})
            switched = await receive_kind(mac, "switch")
            assert switched["board"]["id"] == first
            boards = by_id(switched["boards"])
            assert boards[second]["data"]["trashed"] > 0
            assert "trashed" not in boards[first]["data"]
            legacy = await receive_kind(old, "switch")
            assert [m["id"] for m in legacy["boards"]] == [first]
            assert hub.store.get_meta(second) is not None

            # 回收站里的白板选不了
            await mac.send_json({"t": "sel", "board": second})
            await mac.send_json({"t": "ping", "ts": 1})
            assert (await mac.receive_json())["t"] == "pong"
            assert hub.current_id == first
            await mac.close()
            await old.close()

    run(main())


def test_restore_purge_and_empty_the_trash_over_the_socket(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            store = hub.store
            keep = hub.current_id
            a = store.create_board(make_current=False)["id"]
            b = store.create_board(make_current=False)["id"]
            c = store.create_board(make_current=False)["id"]
            mac = await client.ws_connect("/ws")
            await hello_v2(mac, "mac-1")

            for board in (a, b, c):
                await mac.send_json({"t": "delboard", "board": board})
                listed = await receive_kind(mac, "boards")
                assert by_id(listed["boards"])[board]["data"]["trashed"] > 0

            await mac.send_json({"t": "restore", "board": a})
            listed = await receive_kind(mac, "boards")
            assert "trashed" not in by_id(listed["boards"])[a]["data"]
            assert listed["board"]["id"] == keep

            await mac.send_json({"t": "purge", "board": a})  # 不在回收站里：不理
            await mac.send_json({"t": "purge", "board": b})
            listed = await receive_kind(mac, "boards")
            assert set(by_id(listed["boards"])) == {keep, a, c}

            await mac.send_json({"t": "emptytrash"})
            listed = await receive_kind(mac, "boards")
            assert set(by_id(listed["boards"])) == {keep, a}
            assert store.get_meta(c) is None
            await mac.close()

    run(main())


def test_duplicate_over_the_socket_keeps_the_current_board(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            current = hub.current_id
            mac = await client.ws_connect("/ws")
            ipad = await client.ws_connect("/ws")
            await hello_v2(mac, "mac-1")
            await hello_v2(ipad, "ipad-1", device="ipad")
            await mac.send_json({"t": "op", "cid": "s", "op": {"op": "add", "strokes": [stroke("s1")]}})
            await receive_kind(mac, "ack")
            await mac.send_json({"t": "rename", "board": current, "name": "第一章"})
            await receive_kind(mac, "boards")
            await receive_kind(ipad, "boards")

            await mac.send_json({"t": "duplicate", "board": current})
            listed = await receive_kind(mac, "boards")
            assert listed["board"]["id"] == current
            ids = [m["id"] for m in listed["boards"]]
            assert ids[0] == current and len(ids) == 2
            copy = listed["boards"][1]
            assert copy["name"] == "第一章 副本"
            assert (await receive_kind(ipad, "boards"))["board"]["id"] == current
            assert hub.current_id == current
            assert [s["id"] for s in await hub.strokes_of(copy["id"])] == ["s1"]
            await mac.close()
            await ipad.close()

    run(main())


def test_a_device_without_manage_cannot_use_the_trash_or_duplicate(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            store = hub.store
            board = hub.current_id
            trashed = store.create_board(make_current=False)["id"]
            await hub.trash_board(trashed)
            ws = await client.ws_connect("/ws")
            await hello_v2(ws, "pad-1", device="ipad")
            for message in (
                {"t": "delboard", "board": board},
                {"t": "duplicate", "board": board},
                {"t": "restore", "board": trashed},
                {"t": "purge", "board": trashed},
                {"t": "emptytrash"},
            ):
                await ws.send_json(message)
            await ws.send_json({"t": "ping", "ts": 1})
            assert (await ws.receive_json())["t"] == "pong"
            assert len(store.list_metas()) == 2
            assert models.trashed_at(store.get_meta(board)) is None
            assert models.trashed_at(store.get_meta(trashed)) is not None
            await ws.close()

    run(main())


def test_the_trash_mark_cannot_be_set_with_a_meta_operation(tmp_path):
    """回收站只能经过 delboard / restore：设置白板的权限改不了它。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello_v2(ws, "mac-1")
            await ws.send_json({"t": "op", "cid": "m", "op": {"op": "meta", "meta": {"data": {"trashed": 1}}}})
            ack = await receive_kind(ws, "ack")
            assert ack.get("rejected")
            assert models.trashed_at(hub.store.get_meta(hub.current_id)) is None
            await ws.close()

    run(main())


def test_the_server_purges_old_trash_when_it_starts(tmp_path):
    store = BoardStore(tmp_path / "data")
    old = store.create_board(make_current=False)["id"]
    recent = store.create_board(make_current=False)["id"]
    store.edit_meta(old, data={"trashed": time.time() - 31 * 86400})
    store.edit_meta(recent, data={"trashed": time.time() - 29 * 86400})
    store.core.close()

    async def main():
        async with make_client(tmp_path) as (client, app):
            store = app[HUB_KEY].store
            assert await wait_until(lambda: store.get_meta(old) is None)
            assert store.get_meta(recent) is not None

    run(main())


def test_trashed_boards_still_have_thumbnails_and_exports(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            board = hub.store.create_board(make_current=False)["id"]
            await hub.trash_board(board)
            assert (await client.get(f"/api/thumb/{board}")).status == 200
            response = await client.post(f"/api/export/{board}", json={"format": "pdf", "frames": "whole"})
            assert response.status == 200

    run(main())


@pytest.fixture
def mixed(monkeypatch):
    """带 X-Test-Remote 头的请求当作局域网上的别的设备，其余是本机。"""
    monkeypatch.setattr(
        "whiteboard.server.netinfo.is_local_request", lambda request: "X-Test-Remote" not in request.headers
    )
    return {"X-Test-Remote": "1"}


def test_the_mac_page_hears_when_other_devices_connect_and_leave(tmp_path, mixed):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            mac = await client.ws_connect("/ws")
            init = await hello_v2(mac, "mac-1")
            assert init["devices"] == []

            # 只打开某一块白板的连接（速写页、嵌入的应用）不算
            viewer = await client.ws_connect("/ws", headers=mixed)
            await viewer.send_json({"t": "hello", "v": 2, "client": "viewer-1", "board": hub.current_id})
            assert (await viewer.receive_json())["t"] == "init"

            ipad = await client.ws_connect("/ws", headers=mixed)
            ipad_init = await hello_v2(ipad, "ipad-1", device="ipad")
            assert "devices" not in ipad_init  # 别的设备收不到这份名单
            joined = await receive_kind(mac, "devices")
            assert joined["devices"] == [{"id": "ipad-1", "role": "ipad", "label": "iPad"}]

            laptop = await client.ws_connect("/ws", headers=mixed)
            await hello_v2(laptop, "laptop-1", device="mac")
            joined = await receive_kind(mac, "devices")
            assert joined["devices"] == [
                {"id": "ipad-1", "role": "ipad", "label": "iPad"},
                {"id": "laptop-1", "role": "browser", "label": "浏览器"},
            ]

            # 本机的第二个页面一连上就拿到名单，本机的页面自己不在名单里
            second = await client.ws_connect("/ws")
            assert [d["id"] for d in (await hello_v2(second, "mac-2"))["devices"]] == ["ipad-1", "laptop-1"]

            await ipad.close()
            left = await receive_kind(mac, "devices")
            assert left["devices"] == [{"id": "laptop-1", "role": "browser", "label": "浏览器"}]
            assert (await receive_kind(second, "devices"))["devices"] == left["devices"]
            assert await receive_kind(laptop, "devices", timeout=0.2) is None
            for ws in (mac, viewer, laptop, second):
                await ws.close()

    run(main())


def test_qr_code_is_an_svg_that_css_can_size(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            response = await client.get("/api/qr", params={"text": "http://mac.local:8848/"})
            assert response.status == 200
            assert response.content_type == "image/svg+xml"
            body = await response.text()
            tag = re.search(r"<svg[^>]*>", body).group(0)
            assert "viewBox" in tag and " width=" not in tag and " height=" not in tag
            assert "#1c1c1e" in body.lower()

            assert (await client.get("/api/qr")).status == 400
            assert (await client.get("/api/qr", params={"text": ""})).status == 400
            assert (await client.get("/api/qr", params={"text": "x" * 1025})).status == 400
            assert (await client.get("/api/qr", params={"text": "汉" * 1000})).status == 400  # 放不进二维码

    run(main())


# ------------------------------------------------------------ 文档板：空白页


def _ink(stroke_id, y, x=100.0):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3, "p": [x, y, 0.5, x + 20, y + 10, 0.6]}


async def _receive(ws, kind):
    while True:
        message = await ws.receive_json()
        if message.get("t") == kind:
            return message


def test_inserting_a_blank_page_moves_later_pages_and_their_ink_down(tmp_path):
    """用户要在两页之间加一页空白写批注：后面的页和上面的笔迹都往下挪一页的高度，
    连着的设备马上收到整块白板，原件的页图地址不变。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=_doc_bytes([(595, 842)] * 3))).json())[
                "board"
            ]
            board_id = meta["id"]
            ws = await client.ws_connect("/ws")
            await hello(ws, role="ipad", client_id="pad", board=board_id)
            step = 842 + models.DOC_PAGE_GAP
            page_ink = [_ink("p1", 100), _ink("p2", step + 100), _ink("p3", 2 * step + 100)]
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": page_ink}})
            await _receive(ws, "ack")

            response = await client.post(f"/api/board/{board_id}/pages", json={"action": "insert-after", "page": 0})
            assert response.status == 200, await response.text()
            board = (await response.json())["board"]
            assert board["data"]["doc"]["pages"] == [[595.0, 842.0]] * 4
            assert board["data"]["doc"]["src"] == [0, None, 1, 2]
            assert board["canvas"]["height"] == 4 * 842 + 3 * models.DOC_PAGE_GAP
            assert "src" not in board["layers"][1] and board["layers"][1]["sheet"] is True
            assert board["layers"][2]["src"].startswith(f"/api/doc/{board_id}/1?")  # 原件第 2 页，地址没变

            switched = await _receive(ws, "switch")
            assert switched["board"]["id"] == board_id
            ys = {s["id"]: s["p"][1] for s in switched["strokes"]}
            assert ys == {"p1": 100, "p2": 2 * step + 100, "p3": 3 * step + 100}
            await ws.close()

            # 写进了文件：存盘后重新读，四页和挪过的笔迹都在
            app[HUB_KEY].core.save_all()
            meta_on_disk, strokes_on_disk, problem = BoardStore(app[HUB_KEY].store.data_dir).open_board(board_id)
            assert problem is None
            assert meta_on_disk["data"]["doc"]["src"] == [0, None, 1, 2]
            assert "src" not in meta_on_disk["layers"][1]
            assert sorted(round(s["p"][1]) for s in strokes_on_disk) == [100, 2 * step + 100, 3 * step + 100]

    run(main())


def test_deleting_a_blank_page_takes_its_ink_and_moves_the_rest_up(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=_doc_bytes([(595, 842)] * 2))).json())[
                "board"
            ]
            board_id = meta["id"]
            step = 842 + models.DOC_PAGE_GAP
            await client.post(f"/api/board/{board_id}/pages", json={"action": "insert-before", "page": 1})
            ws = await client.ws_connect("/ws")
            await hello(ws, role="ipad", client_id="pad", board=board_id)
            ink = [_ink("on-blank", step + 100), _ink("after", 2 * step + 100)]
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": ink}})
            await _receive(ws, "ack")

            # 原件的页不能删
            refused = await client.post(f"/api/board/{board_id}/pages", json={"action": "delete", "page": 0})
            assert refused.status == 400

            response = await client.post(f"/api/board/{board_id}/pages", json={"action": "delete", "page": 1})
            assert response.status == 200, await response.text()
            board = (await response.json())["board"]
            assert board["data"]["doc"]["pages"] == [[595.0, 842.0]] * 2
            assert "src" not in board["data"]["doc"]  # 又是原件的一页对一页
            switched = await _receive(ws, "switch")
            assert {s["id"]: s["p"][1] for s in switched["strokes"]} == {"after": step + 100}
            await ws.close()

    run(main())


def test_page_changes_need_the_manage_permission(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=_doc_bytes([(595, 842)] * 2))).json())[
                "board"
            ]
            app[CONFIG_KEY].set_remote_permission("manage", False)
            response = await client.post(
                f"/api/board/{meta['id']}/pages",
                json={"action": "insert-after", "page": 0},
                headers={"X-Forwarded-For": "192.168.1.20"},
            )
            assert response.status == 403

    run(main())


# ------------------------------------------------------------ 导入前选页


def test_a_staged_pdf_becomes_a_board_of_the_chosen_pages(tmp_path):
    """导入对话框：先暂存，看页数和缩略图，只要第 3 到 5 页、每页后面加一页空白。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            sizes = [(595, 842)] * 5 + [(842, 595)]
            staged = await client.post("/api/doc/stage?name=教材.pdf", data=_doc_bytes(sizes))
            assert staged.status == 200, await staged.text()
            info = await staged.json()
            assert info["total"] == 6 and info["name"] == "教材.pdf" and len(info["pages"]) == 6
            thumb = await client.get(f"/api/doc/stage/{info['stage']}/5?w=160")
            assert thumb.status == 200 and thumb.content_type == "image/jpeg"
            assert not app[HUB_KEY].core.exists(info["stage"])  # 还没建板

            created = await client.post(f"/api/doc/stage/{info['stage']}", json={"pages": [2, 3, 5], "blanks": True})
            assert created.status == 200, await created.text()
            board = (await created.json())["board"]
            doc = board["data"]["doc"]
            assert doc["src"] == [2, None, 3, None, 5, None]
            assert doc["pages"] == [[595.0, 842.0]] * 4 + [[842.0, 595.0]] * 2
            assert board["layers"][0]["src"].startswith(f"/api/doc/{board['id']}/2?")
            page = await client.get(f"/api/doc/{board['id']}/5?w=200")
            assert page.status == 200  # 原件搬到了 docs/ 下，原件第 6 页照样能取
            assert app[HUB_KEY].current_id == board["id"]
            # 用过的暂存不能再用
            again = await client.post(f"/api/doc/stage/{info['stage']}", json={"pages": [0]})
            assert again.status == 400

    run(main())


def test_cancelling_the_import_dialog_removes_the_staged_file(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            info = await (await client.post("/api/doc/stage?name=a.pdf", data=_doc_bytes())).json()
            store = app[HUB_KEY].store
            assert store.staged_path(info["stage"]) is not None
            assert (await client.delete(f"/api/doc/stage/{info['stage']}")).status == 200
            assert store.staged_path(info["stage"]) is None
            assert (await client.get(f"/api/doc/stage/{info['stage']}/0")).status == 404
            bad = await client.post("/api/doc/stage?name=a.pdf", data=b"broken")
            assert bad.status == 400

    run(main())


def test_staged_choices_are_checked(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            info = await (await client.post("/api/doc/stage?name=a.pdf", data=_doc_bytes())).json()
            for pages in ([], [5], ["1"], [True]):
                response = await client.post(f"/api/doc/stage/{info['stage']}", json={"pages": pages})
                assert response.status == 400, pages

    run(main())

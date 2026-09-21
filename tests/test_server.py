"""服务端协议测试：握手、广播、断线补齐、权限与设备识别。"""

import asyncio
import io
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

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


def test_ipad_cannot_manage_boards(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws = await client.ws_connect("/ws")
            await hello(ws, "ipad", client_id="ipad-1")
            before = hub.current_id

            await ws.send_json({"t": "newboard"})
            await ws.send_json(
                {"t": "op", "cid": "m", "op": {"op": "meta", "meta": {"background": "dots"}}}
            )
            ack = await ws.receive_json()
            assert ack["t"] == "ack"
            assert hub.current_id == before
            assert hub.board().meta["background"] == "grid"
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


def test_ipad_cannot_rename(tmp_path):
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
            answer = await hello(
                resumed, "ipad", board=board_id, since=9, client_id="ipad-1", epoch="stale-epoch"
            )
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
            await stale.close()
            await asyncio.sleep(0.05)

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
            assert meta["kind"] == "doc"
            assert meta["doc"]["pages"] == [[595.0, 842.0], [400.0, 600.0]]

            switched = await ws.receive_json()
            assert switched["t"] == "switch"
            assert switched["board"]["id"] == meta["id"]
            assert app[HUB_KEY].current_id == meta["id"]
            await ws.close()

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
            meta = (await (await client.post("/api/doc?name=a.pdf", data=_doc_bytes())).json())[
                "board"
            ]
            page = await client.get(f"/api/doc/{meta['id']}/0?w=320")
            assert page.status == 200
            assert page.content_type == "image/jpeg"
            assert len(await page.read()) > 0
            etag = page.headers["ETag"]

            cached = await client.get(
                f"/api/doc/{meta['id']}/0?w=320", headers={"If-None-Match": etag}
            )
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
            meta = (await (await client.post("/api/doc?name=a.pdf", data=_doc_bytes())).json())[
                "board"
            ]
            thumb = await client.get(f"/api/thumb/{meta['id']}")
            assert thumb.status == 200
            assert thumb.content_type == "image/jpeg"  # 渲染的原件首页，不是空白 PNG

    run(main())


def test_doc_export_merges_strokes(tmp_path):
    """导出的 PDF 页数不变，笔迹进去了，体积也没有明显变大。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            source = _doc_bytes()
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=source)).json())[
                "board"
            ]
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
            assert meta["doc"]["pages"] == [[900.0, 700.0]]

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
            assert 'data-perms="export manage settings"' in body

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
            await ws.send_json(
                {"t": "op", "cid": "m", "op": {"op": "meta", "meta": {"background": "dots"}}}
            )
            ack = await ws.receive_json()
            assert ack["t"] == "ack"  # 前三条被丢掉了，这是第四条的回执

            assert hub.current_id == before
            assert len(hub.store.list_metas()) == 1
            assert hub.store.get_meta(before)["name"] == ""
            assert hub.board().meta["background"] == "grid"

            # 写字照常
            await ws.send_json(
                {
                    "t": "op",
                    "cid": "s",
                    "op": {
                        "op": "add",
                        "strokes": [
                            {"id": "a", "tool": "pen", "color": "#000000", "w": 2,
                             "p": [0, 0, 0.5, 1, 1, 0.5]}
                        ],
                    },
                }
            )
            ack = await ws.receive_json()
            assert ack["op"]["op"] == "add"
            await ws.close()

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

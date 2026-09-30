"""权限：本机判断、默认只给本机、白板列表的可见范围、权限实时生效、文档页与录像。"""

from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from whiteboard import netinfo
from whiteboard.config import Config
from whiteboard.server import CONFIG_KEY, HUB_KEY, create_app, prune_recordings, refresh_permissions
from whiteboard.store import BoardStore


def run(coro):
    return asyncio.run(coro)


class FakeTransport:
    def __init__(self, sockname):
        self.sockname = sockname

    def get_extra_info(self, name):
        return self.sockname if name == "sockname" else None


def fake_request(remote, local=None, headers=None):
    return SimpleNamespace(remote=remote, headers=headers or {}, transport=FakeTransport((local, 8848) if local else None))


def test_local_requests_are_recognised_on_any_interface():
    assert netinfo.is_local_request(fake_request("127.0.0.1", "127.0.0.1"))
    assert netinfo.is_local_request(fake_request("::1", "::1"))
    # 机器连自己时对端就是本端地址：第二块网卡、IPv6 都算本机
    assert netinfo.is_local_request(fake_request("10.0.0.9", "10.0.0.9"))
    assert netinfo.is_local_request(fake_request("fe80::1%en0", "fe80::1%en0"))
    assert not netinfo.is_local_request(fake_request("192.0.2.7", "192.0.2.1"))
    assert not netinfo.is_local_request(fake_request(None, "192.0.2.1"))
    # 经过本机反向代理转来的请求不算本机，即使对端是回环
    assert not netinfo.is_local_request(fake_request("127.0.0.1", "127.0.0.1", {"X-Forwarded-For": "192.0.2.7"}))
    assert not netinfo.is_local_request(fake_request("127.0.0.1", "127.0.0.1", {"Forwarded": "for=192.0.2.7"}))


def test_inksync_grants_everything_only_to_this_machine():
    from inksync.ws import PERMISSIONS, local_only

    assert local_only(fake_request("127.0.0.1", "127.0.0.1")) == frozenset(PERMISSIONS)
    assert local_only(fake_request("192.0.2.7", "192.0.2.1")) == frozenset()


@asynccontextmanager
async def make_client(tmp_path, monkeypatch, remote=True):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    if remote:
        # 测试里的连接都来自回环，这里把本机判断换掉，模拟局域网上的其他设备
        monkeypatch.setattr(netinfo, "is_local_request", lambda request: False)
    app = create_app(config, BoardStore(config.data_dir))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


async def hello(client, client_id="pad-remote"):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"t": "hello", "role": "ipad", "client": client_id, "board": None, "since": 0, "epoch": None})
    return ws, await ws.receive_json()


async def receive(ws, kind, timeout=1.0):
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


def test_a_device_without_manage_sees_only_its_own_board(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            hub = app[HUB_KEY]
            other = hub.store.create_board("另一块", make_current=False)
            hub.create_folder("数学")
            ws, init = await hello(client)
            assert [b["id"] for b in init["boards"]] == [hub.current_id]
            assert other["id"] not in str(init["boards"]) and init["folders"] == []
            await ws.close()

    run(main())


def test_permission_changes_reach_connected_devices(tmp_path, monkeypatch):
    async def main():
        async with make_client(tmp_path, monkeypatch) as (client, app):
            config: Config = app[CONFIG_KEY]
            hub = app[HUB_KEY]
            second = hub.store.create_board("第二块", make_current=False)["id"]
            ws, _ = await hello(client)

            # 还没有管理权限：切换白板被忽略
            await ws.send_json({"t": "sel", "board": second})
            assert await receive(ws, "switch", timeout=0.3) is None

            config.set_remote_permission("manage", True)
            await refresh_permissions(app)
            perms = await receive(ws, "perms")
            assert perms["perms"] == ["manage"]
            listed = await receive(ws, "boards")
            assert {b["id"] for b in listed["boards"]} >= {second, hub.current_id}

            await ws.send_json({"t": "sel", "board": second})
            assert (await receive(ws, "switch"))["board"]["id"] == second

            # 收回之后立即失效，不用等重连
            config.set_remote_permission("manage", False)
            await refresh_permissions(app)
            assert (await receive(ws, "perms"))["perms"] == []
            await ws.send_json({"t": "newboard", "kind": "note"})
            assert await receive(ws, "switch", timeout=0.3) is None
            await ws.close()

    run(main())


def _pdf_bytes():
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_document_pages_of_other_boards_need_manage(tmp_path, monkeypatch):
    pytest.importorskip("pypdfium2")
    body = _pdf_bytes()

    async def main():
        async with make_client(tmp_path, monkeypatch, remote=False) as (client, app):
            hub = app[HUB_KEY]
            doc = hub.import_doc(body, "讲义.pdf")  # 建好后它就是当前白板
            hidden = hub.store.import_doc(body, "另一份.pdf")  # 不是当前白板
            monkeypatch.setattr(netinfo, "is_local_request", lambda request: False)
            assert (await client.get(f"/api/doc/{doc['id']}/0?w=320")).status == 200
            assert (await client.get(f"/api/doc/{hidden['id']}/0?w=320")).status == 403

    run(main())


def test_recordings_keep_only_the_newest(tmp_path, monkeypatch):
    import os
    import whiteboard.server as server

    monkeypatch.setattr(server, "MAX_RECORDINGS", 3)
    monkeypatch.setattr(server, "MAX_RECORDINGS_BYTES", 10_000)
    folder = tmp_path / "recordings"
    folder.mkdir()
    for index in range(5):
        path = folder / f"{index}.json"
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + index, 1000 + index))
    prune_recordings(folder)
    assert sorted(p.name for p in folder.glob("*.json")) == ["2.json", "3.json", "4.json"]

    big = folder / "9.json"
    big.write_bytes(b"x" * 20_000)
    os.utime(big, (2000, 2000))
    prune_recordings(folder)
    # 总大小超过上限：旧的都删掉，刚上传的那一份即使自己就超了也保留
    assert [p.name for p in folder.glob("*.json")] == ["9.json"]

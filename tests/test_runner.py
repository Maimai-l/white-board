"""服务端线程的启动 / 退出，重点是 mDNS 绝不能拖累启动。"""

import time
import urllib.request

from whiteboard import netinfo
from whiteboard.config import Config
from whiteboard.runner import ServerThread


def make_config(tmp_path, port):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    config.port = port
    return config


def fetch(port, path="/api/info", timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return response.status, response.read()


def test_starts_quickly_with_mdns_enabled(tmp_path):
    """开着 mDNS 也必须马上就绪：广播是启动之后的后台任务。"""
    server = ServerThread(make_config(tmp_path, 8471), advertise=True)
    started = time.monotonic()
    port = server.start()
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 5, f"启动耗时 {elapsed:.1f}s，mDNS 又跑到关键路径上了"
        assert fetch(port)[0] == 200
    finally:
        server.stop()


def test_mdns_failure_does_not_break_startup(tmp_path, monkeypatch):
    async def boom(self):
        raise RuntimeError("zeroconf 炸了")

    monkeypatch.setattr(netinfo.MDNSAdvertiser, "start", boom)
    server = ServerThread(make_config(tmp_path, 8473), advertise=True)
    port = server.start()
    try:
        assert fetch(port)[0] == 200
    finally:
        server.stop()


def test_port_in_use_falls_back_to_next_port(tmp_path):
    first = ServerThread(make_config(tmp_path, 8475), advertise=False)
    first.start()
    second = ServerThread(make_config(tmp_path / "other", 8475), advertise=False)
    try:
        assert second.start() == first.port + 1
        assert fetch(second.port)[0] == 200
    finally:
        second.stop()
        first.stop()


def test_stop_saves_boards(tmp_path):
    server = ServerThread(make_config(tmp_path, 8477), advertise=False)
    server.start()
    hub = server.hub
    hub.board().apply(
        {"op": "add", "strokes": [{"id": "a", "p": [0, 0, 0.5, 1, 1, 0.5], "color": "#000000"}]}
    )
    server.save_now()
    server.stop()
    from whiteboard.store import BoardStore

    store = BoardStore(tmp_path / "data")
    _, strokes = store.load_board(store.current_id)
    assert [s["id"] for s in strokes] == ["a"]


def test_mdns_default_is_off_on_macos(monkeypatch):
    monkeypatch.setattr(netinfo.sys, "platform", "darwin")
    assert netinfo.mdns_default() is False
    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    assert netinfo.mdns_default() is True


def test_shutdown_is_quick_even_with_a_live_websocket(tmp_path):
    """关窗口时不能干等：服务必须先把长连接断掉再收摊。"""
    import asyncio
    import threading

    import aiohttp

    server = ServerThread(make_config(tmp_path, 8479), advertise=False)
    port = server.start()

    connected = threading.Event()

    def hold():
        async def main():
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                    await ws.send_json({"t": "hello", "role": "ipad", "client": "ipad-hold"})
                    await ws.receive_json()
                    connected.set()
                    await asyncio.sleep(60)

        try:
            asyncio.run(main())
        except Exception:  # noqa: BLE001 - 连接被服务端关掉是预期结果
            pass

    threading.Thread(target=hold, daemon=True).start()
    assert connected.wait(10), "测试用的连接没建立起来"

    started = time.monotonic()
    server.save_now()
    server.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 3, f"关闭耗时 {elapsed:.1f}s，长连接又把关服务拖住了"

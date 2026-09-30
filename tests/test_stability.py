"""稳定版整理第一步：数据安全。

每个用例对应一个在旧代码上能复现的问题：先在旧代码上确认它失败，再改代码让它通过。
"""

import asyncio
import io
import json
import math
import threading
import zlib
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from whiteboard import models
from whiteboard.config import Config
from whiteboard.hub import Hub
from whiteboard.server import HUB_KEY, create_app
from whiteboard.store import FILE_VERSION, BoardStore


def run(coro):
    return asyncio.run(coro)


def stroke(stroke_id="s1", points=None, **extra):
    return {
        "id": stroke_id,
        "tool": "pen",
        "color": "#000000",
        "w": 3,
        "p": points if points is not None else [0, 0, 0.5, 5, 5, 0.6],
        **extra,
    }


@asynccontextmanager
async def make_client(tmp_path, store=None):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    store = store or BoardStore(config.data_dir)
    app = create_app(config, store)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


async def hello(ws, client_id="mac-1"):
    await ws.send_json({"t": "hello", "role": "mac", "client": client_id})
    return await ws.receive_json()


def write_payload(path, payload):
    path.write_bytes(zlib.compress(json.dumps(payload).encode("utf-8")))


def board_file(store, board_id):
    return store.boards_dir / f"{board_id}.wbz"


# ------------------------------------------------------------ 线上数据的校验


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan, True, False, "1", None])
def test_stroke_with_non_finite_or_non_numeric_point_is_rejected(bad):
    assert models.sanitize_stroke(stroke(points=[0, 0, 0.5, bad, 5, 0.5])) is None


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_non_finite_width_falls_back_to_default(bad):
    """宽度是 NaN 时 clamp 原样放行，json.dumps 会写出 NaN，浏览器的 JSON.parse 解析不了。"""
    clean = models.sanitize_stroke(stroke(w=bad))
    assert clean is not None and math.isfinite(clean["w"])


def test_boolean_layer_number_is_ignored():
    assert "n" not in models.sanitize_stroke(stroke(n=True))


def test_infinite_point_does_not_stop_the_board_from_saving(tmp_path):
    """旧代码：Infinity 过了校验，存盘时编码抛 OverflowError，这块板从此存不进去。"""
    store = BoardStore(tmp_path)
    hub = Hub(store)
    runtime = hub.board()
    runtime.apply({"op": "add", "strokes": [stroke("bad", points=[0, 0, 0.5, math.inf, 1, 0.5])]})
    runtime.apply({"op": "add", "strokes": [stroke("good")]})
    hub.save_all()
    _, strokes = store.load_board(hub.current_id)
    assert [s["id"] for s in strokes] == ["good"]


@pytest.mark.parametrize(
    "op",
    [
        {"op": "add", "strokes": {"id": "x"}},
        {"op": "add", "strokes": "abc"},
        {"op": "restore", "strokes": 5},
        {"op": "mask", "masks": {"id": "x"}},
        {"op": "mask", "masks": [{"id": ["unhashable"], "m": []}]},
        {"op": "remove", "ids": {"a": 1}},
        {"op": "meta", "meta": []},
    ],
)
def test_malformed_op_is_ignored_without_raising(tmp_path, op):
    hub = Hub(BoardStore(tmp_path))
    assert hub.board().apply(op)[0] is None


def test_malformed_message_is_acknowledged_and_keeps_the_connection(tmp_path):
    """旧代码：分派时抛异常，连接断开、回执没发。客户端的待发队列存在 IndexedDB 里，
    重连后原样重发，服务端再断——刷新页面也出不来。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            await hello(ws)
            await ws.send_json({"t": "op", "cid": "bad", "op": {"op": "add", "strokes": {"id": "x"}}})
            ack = await ws.receive_json(timeout=5)
            assert ack == {"t": "ack", "cid": "bad", "seq": app[HUB_KEY].board().seq}  # 1.x 页面看到的回执
            # 连接还活着，下一条照常处理
            await ws.send_json({"t": "op", "cid": "ok", "op": {"op": "add", "strokes": [stroke()]}})
            ack = await ws.receive_json(timeout=5)
            assert ack["cid"] == "ok" and ack["op"]["strokes"][0]["id"] == "s1"
            await ws.close()

    run(main())


def test_unexpected_error_in_a_handler_still_acknowledges(tmp_path, monkeypatch):
    """不管哪一步出了没想到的错，op 都要回执，连接都不能断。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            await hello(ws)

            def boom(raw, *args, **kwargs):
                raise RuntimeError("意外")

            monkeypatch.setattr(app[HUB_KEY].board(), "apply", boom)
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke()]}})
            ack = await ws.receive_json(timeout=5)
            assert ack["t"] == "ack" and ack["cid"] == "c1"
            await ws.send_json({"t": "ping", "ts": 1})
            assert (await ws.receive_json(timeout=5))["t"] == "pong"
            await ws.close()

    run(main())


# ------------------------------------------------------------ 自动保存


def test_one_board_failing_to_save_does_not_block_the_others(tmp_path, monkeypatch):
    store = BoardStore(tmp_path)
    hub = Hub(store)
    first = hub.current_id
    hub.board().apply({"op": "add", "strokes": [stroke("a")]})
    second = asyncio.run(hub.create_board())["id"]
    hub.board(first).apply({"op": "add", "strokes": [stroke("a2")]})
    hub.board().apply({"op": "add", "strokes": [stroke("b")]})

    original = store.core.write_file

    def flaky(meta, strokes, skip=None):
        if meta["id"] == first:
            raise OverflowError("坏数据")
        return original(meta, strokes, skip)

    monkeypatch.setattr(store.core, "write_file", flaky)
    hub.save_all()  # 不抛
    assert [s["id"] for s in store.load_board(second)[1]] == ["b"]
    assert hub.board(first).dirty  # 没存成的那块留着脏标记，下一轮再试


# ------------------------------------------------------------ 读不全的文件不能被覆盖


def test_corrupt_file_is_never_overwritten(tmp_path):
    """旧代码：读不出来就当空白板，下一笔一存，原文件就被覆盖掉了。"""
    store = BoardStore(tmp_path)
    board_id = store.current_id
    path = board_file(store, board_id)
    path.write_bytes(b"not zlib at all")
    original = path.read_bytes()

    hub = Hub(store)
    runtime = hub.board(board_id)
    assert runtime.locked and runtime.problem["reason"] == "corrupt"
    assert runtime.apply({"op": "add", "strokes": [stroke()]})[0] is None
    runtime.version += 1  # 即便别处把它标脏了
    hub.save_all()
    asyncio.run(hub.rename_board(board_id, "改个名"))
    hub.save_all()
    assert path.read_bytes() == original


def test_unreadable_strokes_lock_the_board_and_are_kept_on_disk(tmp_path):
    """旧代码：个别笔画解不出来就跳过，下次存盘它们就永远没了。"""
    store = BoardStore(tmp_path)
    meta = store.get_meta(store.current_id)
    store.save_board(meta, [models.sanitize_stroke(stroke("ok"))])
    path = board_file(store, meta["id"])
    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["strokes"].append({"id": "broken", "tool": "pen", "color": "#000000", "w": 3, "p": "!!!not-base64"})
    write_payload(path, payload)
    original = path.read_bytes()

    hub = Hub(store)
    runtime = hub.board(meta["id"])
    assert runtime.problem["reason"] == "partial"
    assert [s["id"] for s in runtime.stroke_list()] == ["ok"]  # 读得出来的照样显示
    runtime.version += 1
    hub.save_all()
    assert path.read_bytes() == original


def test_board_from_a_newer_version_opens_read_only(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.get_meta(store.current_id)
    store.save_board(meta, [models.sanitize_stroke(stroke("s1"))])
    path = board_file(store, meta["id"])
    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["v"] = FILE_VERSION + 1
    payload["future"] = {"something": "new"}
    write_payload(path, payload)
    original = path.read_bytes()

    hub = Hub(store)
    runtime = hub.board(meta["id"])
    assert runtime.problem["reason"] == "newer"
    assert runtime.problem["version"] == FILE_VERSION + 1
    assert [s["id"] for s in runtime.stroke_list()] == ["s1"]
    assert runtime.apply({"op": "add", "strokes": [stroke("s2")]})[0] is None
    runtime.version += 1
    hub.save_all()
    assert path.read_bytes() == original


def test_renaming_a_locked_board_keeps_everything_else_in_the_file(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.get_meta(store.current_id)
    store.save_board(meta, [models.sanitize_stroke(stroke("s1"))])
    path = board_file(store, meta["id"])
    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["v"] = FILE_VERSION + 1
    payload["future"] = {"something": "new"}
    write_payload(path, payload)

    hub = Hub(store)
    hub.board(meta["id"])
    assert asyncio.run(hub.rename_board(meta["id"], "新名字"))
    hub.save_all()
    after = json.loads(zlib.decompress(path.read_bytes()))
    assert after["future"] == {"something": "new"} and after["v"] == FILE_VERSION + 1
    assert after["meta"]["name"] == "新名字"
    assert store.get_meta(meta["id"])["name"] == "新名字"


def test_unlocking_backs_up_the_original_then_allows_editing(tmp_path):
    store = BoardStore(tmp_path)
    board_id = store.current_id
    path = board_file(store, board_id)
    path.write_bytes(b"garbage")

    hub = Hub(store)
    assert hub.board(board_id).locked
    assert asyncio.run(hub.unlock(board_id))
    backups = list((tmp_path / "backups" / "locked").glob(f"{board_id}-*.wbz"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"garbage"

    runtime = hub.board(board_id)
    assert not runtime.locked
    assert runtime.apply({"op": "add", "strokes": [stroke()]})[0] is not None
    hub.save_all()
    assert [s["id"] for s in store.load_board(board_id)[1]] == ["s1"]


def test_locked_state_is_sent_to_clients_and_unlock_needs_manage(tmp_path, monkeypatch):
    store = BoardStore(tmp_path / "data")
    board_file(store, store.current_id).write_bytes(b"garbage")

    async def main():
        async with make_client(tmp_path, store) as (client, app):
            ws = await client.ws_connect("/ws")
            init = await hello(ws)
            assert init["locked"]["reason"] == "corrupt"

            # 别的设备（没有管理权限）解不了锁
            remote = await client.ws_connect("/ws")
            monkeypatch.setattr("whiteboard.server.netinfo.is_own_address", lambda _addr: False)
            other = await client.ws_connect("/ws")
            monkeypatch.undo()
            await hello(other, "ipad-1")
            await other.send_json({"t": "unlock"})
            await other.send_json({"t": "ping", "ts": 1})
            assert (await other.receive_json(timeout=5))["t"] == "pong"
            assert app[HUB_KEY].board().locked

            await ws.send_json({"t": "unlock"})
            switched = await ws.receive_json(timeout=5)
            assert switched["t"] == "switch" and switched["locked"] is None
            assert not app[HUB_KEY].board().locked
            for sock in (ws, remote, other):
                await sock.close()

    run(main())


def test_selecting_a_locked_board_again_rereads_the_file(tmp_path):
    """读不出来可能只是暂时的（外置盘没挂上）；再切回来时要重新读一次。"""
    store = BoardStore(tmp_path)
    first = store.current_id
    good = store.load_board(first)
    path = board_file(store, first)
    saved = path.read_bytes()
    path.write_bytes(b"garbage")

    hub = Hub(store)
    assert hub.board(first).locked
    second = asyncio.run(hub.create_board())["id"]
    path.write_bytes(saved)
    assert asyncio.run(hub.select_board(first))
    assert not hub.board(first).locked
    assert hub.board(first).stroke_list() == good[1]
    assert second != first


# ------------------------------------------------------------ 索引与配置文件


@pytest.mark.parametrize("content", ["[]", '"x"', '{"boards": {"a": 1}}', '{"boards": [1, "x", null]}'])
def test_index_with_wrong_shape_is_rebuilt_instead_of_crashing(tmp_path, content):
    store = BoardStore(tmp_path)
    board_id = store.current_id
    store.index_path.write_text(content, "utf-8")
    again = BoardStore(tmp_path)
    assert board_id in [m["id"] for m in again.list_metas()]


def test_board_file_with_wrong_shape_does_not_crash_startup(tmp_path):
    store = BoardStore(tmp_path)
    write_payload(store.boards_dir / "weird.wbz", ["not", "a", "dict"])
    store.index_path.unlink()
    again = BoardStore(tmp_path)  # 重建索引时读到它
    assert again.list_metas()
    assert Hub(again).board("weird").locked


def test_config_save_is_atomic(tmp_path, monkeypatch):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "boards"
    config.save()
    before = config.path.read_text("utf-8")

    def broken(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr("os.replace", broken)
    config.data_dir = tmp_path / "elsewhere"
    config.save()  # 写失败只记日志
    assert config.path.read_text("utf-8") == before
    assert not list(tmp_path.glob(".tmp-*"))


def test_unreadable_config_is_kept_aside_before_defaults_are_written(tmp_path):
    """配置读不出来时用默认值，但原文件先挪开：里面记着白板存在哪。"""
    path = tmp_path / "config.json"
    path.write_text('{"data_dir": "/Volumes/x", ', "utf-8")
    config = Config(path=path)
    config.save()
    kept = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text("utf-8") == '{"data_dir": "/Volumes/x", '


def test_runtime_overrides_are_not_saved(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.set_runtime(port=9100, data_dir=tmp_path / "cli")
    assert config.port == 9100 and config.data_dir == tmp_path / "cli"
    config.save()
    saved = json.loads(config.path.read_text("utf-8"))
    assert saved["port"] == 8848
    assert saved["data_dir"] != str(tmp_path / "cli")

    # 界面上换存储目录是用户的决定，要记住，并且盖过命令行那一次
    config.data_dir = tmp_path / "chosen"
    assert config.data_dir == tmp_path / "chosen"
    config.save()
    assert json.loads(config.path.read_text("utf-8"))["data_dir"] == str(tmp_path / "chosen")


def test_port_fallback_is_used_but_not_saved(tmp_path):
    import socket

    from whiteboard.runner import ServerThread

    blocker = socket.socket()
    blocker.bind(("0.0.0.0", 0))
    blocker.listen()
    busy = blocker.getsockname()[1]
    try:
        config = Config(path=tmp_path / "config.json")
        config.data_dir = tmp_path / "data"
        config.set_runtime(port=busy)
        server = ServerThread(config, advertise=False, bonjour=False)
        port = server.start()
        try:
            assert port != busy
            assert config.port == port  # 页面上显示的地址用实际端口
            config.save()
            assert json.loads(config.path.read_text("utf-8"))["port"] == 8848
        finally:
            server.stop()
    finally:
        blocker.close()


# ------------------------------------------------------------ 线程


def test_importing_a_document_touches_hub_state_only_on_the_event_loop(tmp_path, monkeypatch):
    """旧代码：整个 hub.import_doc 在工作线程里跑，同时事件循环还在改同一份状态。

    现在读原件在工作线程，建板（改索引、内存中的白板）回到事件循环线程。"""
    pytest.importorskip("pypdfium2")
    threads = {}

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            loop_thread = threading.current_thread()
            original_create = hub.core.create_board
            original_put = hub.store.core.put_meta

            async def create_board(*args, **kwargs):
                threads.setdefault("create_board", set()).add(threading.current_thread() is loop_thread)
                return await original_create(*args, **kwargs)

            def put_meta(*args, **kwargs):
                threads.setdefault("put_meta", set()).add(threading.current_thread() is loop_thread)
                return original_put(*args, **kwargs)

            monkeypatch.setattr(hub.core, "create_board", create_board)
            monkeypatch.setattr(hub.store.core, "put_meta", put_meta)

            buffer = io.BytesIO()
            Image.new("RGB", (80, 60), (200, 200, 200)).save(buffer, "PNG")
            response = await client.post("/api/doc?name=page.png", data=buffer.getvalue())
            assert response.status == 200
            meta = (await response.json())["board"]
            assert hub.current_id == meta["id"]
            assert models.kind_of(hub.store.get_meta(meta["id"])) == "doc"

    run(main())
    assert threads == {"create_board": {True}, "put_meta": {True}}


# ------------------------------------------------------------ 升级前备份


def test_boards_are_backed_up_once_per_version_change(tmp_path):
    from whiteboard import backup

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    store = BoardStore(config.data_dir)
    board_id = store.current_id

    first = backup.backup_if_upgraded(config, "1.0.0")
    assert first is not None
    assert (first / "boards" / f"{board_id}.wbz").exists()
    assert (first / "index.sqlite").exists() and (first / "space.json").exists()
    assert backup.backup_if_upgraded(config, "1.0.0") is None  # 同一个版本只备份一次

    assert backup.backup_if_upgraded(config, "1.0.1") is not None
    assert json.loads(config.path.read_text("utf-8"))["last_version"] == "1.0.1"


def test_converting_a_1x_store_refuses_to_start_without_a_backup(tmp_path, monkeypatch):
    """1.x 的存储目录要转换格式：备份做不成就不启动，也不碰任何文件。"""
    from whiteboard import backup

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    boards = config.data_dir / "boards"
    boards.mkdir(parents=True)
    (boards / "abc.wbz").write_bytes(zlib.compress(json.dumps({"v": 1, "meta": {"id": "abc"}, "strokes": []}).encode()))
    (config.data_dir / "index.json").write_text('{"boards": [], "folders": [], "current": "abc"}')
    assert backup.needs_conversion(config.data_dir)

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "_copy", fail)
    with pytest.raises(backup.BackupError, match="没有启动"):
        backup.backup_if_upgraded(config, "2.0.0")
    assert (config.data_dir / "index.json").exists() and not (config.data_dir / "index.sqlite").exists()
    monkeypatch.undo()
    assert backup.backup_if_upgraded(config, "2.0.0") is not None


def test_only_the_latest_backups_are_kept(tmp_path):
    from whiteboard import backup

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    BoardStore(config.data_dir)
    for i in range(backup.KEEP + 3):
        assert backup.backup_if_upgraded(config, f"1.0.{i}") is not None
    kept = sorted(p.name for p in (config.data_dir / "backups" / "upgrade").iterdir())
    assert len(kept) == backup.KEEP
    assert kept[-1].endswith(f"1.0.{backup.KEEP + 2}")

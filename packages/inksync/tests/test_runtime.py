"""Hub 的保存、删除、释放与并发（docs/design/inksync-redesign.zh-CN.md 4.3 节）。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import zlib

from helpers import hello, receive, send_op, serve, stroke

from inksync import FileStorage, Hub, Spaces
from inksync.storage import FileStorage as _FS


def run(coro):
    return asyncio.run(coro)


def saved_ids(tmp_path, board_id):
    payload = json.loads(zlib.decompress((tmp_path / "data" / "boards" / f"{board_id}.wbz").read_bytes()))
    return [s["id"] for s in payload["strokes"]]


class SlowStorage(FileStorage):
    """写盘时停在编码之后，等测试放行：用来制造「保存进行中」。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.slow = False

    def write_file(self, meta, strokes, skip=None):
        if self.slow:
            blob = self.encode(meta, strokes)
            self.entered.set()
            self.gate.wait(5)
            if skip is not None and skip():
                return None
            path = self.board_path(meta["id"])
            from inksync.storage import atomic_write

            atomic_write(path, blob)
            return path.stat().st_mtime
        return super().write_file(meta, strokes, skip)


def test_ops_arriving_during_a_save_are_not_lost(tmp_path):
    async def main():
        storage = SlowStorage(tmp_path / "data")
        hub = Hub(storage, autosave=3600)
        async with serve(tmp_path, hub=hub) as (client, _):
            ws, _ = await hello(client, "q", {})
            await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s1")]})
            storage.slow = True
            hub.tick()  # 开始保存 s1
            await asyncio.get_running_loop().run_in_executor(None, storage.entered.wait, 5)
            await send_op(ws, "c2", {"op": "add", "strokes": [stroke("s2")]})
            storage.gate.set()
            for _ in range(100):
                if hub.runtime("q").saving is None:
                    break
                await asyncio.sleep(0.01)
            assert hub.runtime("q").dirty  # s2 还没写盘
            storage.slow = False
            hub.tick()
            for _ in range(100):
                if not hub.runtime("q").dirty and hub.runtime("q").saving is None:
                    break
                await asyncio.sleep(0.01)
            assert saved_ids(tmp_path, "q") == ["s1", "s2"]
            await ws.close()

    run(main())


def test_a_board_deleted_during_a_save_does_not_come_back(tmp_path):
    async def main():
        storage = SlowStorage(tmp_path / "data")
        hub = Hub(storage, autosave=3600)
        async with serve(tmp_path, hub=hub) as (client, _):
            ws, _ = await hello(client, "q", {})
            await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s1")]})
            storage.slow = True
            hub.tick()
            await asyncio.get_running_loop().run_in_executor(None, storage.entered.wait, 5)
            deleting = asyncio.ensure_future(hub.delete_board("q"))
            await asyncio.sleep(0.05)
            storage.gate.set()
            assert await deleting
            await hub.flush()
            assert not (tmp_path / "data" / "boards" / "q.wbz").exists()
            assert not hub.exists("q")
            # 已删除的白板收到操作也不会被重新载入、写回
            ack = await send_op(ws, "c2", {"op": "add", "strokes": [stroke("s2")]})
            assert ack["rejected"] == "deleted"
            await hub.flush()
            assert not (tmp_path / "data" / "boards" / "q.wbz").exists()
            await ws.close()
        assert _FS(tmp_path / "data").count() == 0

    run(main())


def test_unloaded_boards_keep_epoch_and_seq(tmp_path):
    async def main():
        hub = Hub(FileStorage(tmp_path / "data"), autosave=3600, idle_unload=0)
        async with serve(tmp_path, hub=hub) as (client, _):
            ws, init = await hello(client, "q", {})
            await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s1")]})
            await ws.close()
            await asyncio.sleep(0.05)
            await hub.flush()
            hub.tick()
            assert hub.runtime("q") is None  # 已释放
            ws, sync = await hello(client, "q", {}, since=1, epoch=init["epoch"])
            assert sync["t"] == "sync" and sync["ops"] == []
            await ws.close()
            await asyncio.sleep(0.05)
            hub.tick()
            ws, again = await hello(client, "q", {}, since=0, epoch=init["epoch"])
            assert again["t"] == "init" and [s["id"] for s in again["strokes"]] == ["s1"]
            await ws.close()

    run(main())


def test_boards_with_connections_or_changes_are_not_unloaded(tmp_path):
    async def main():
        hub = Hub(FileStorage(tmp_path / "data"), autosave=3600, idle_unload=0)
        async with serve(tmp_path, hub=hub) as (client, _):
            ws, _ = await hello(client, "q", {}, readonly=True)
            hub.tick()
            assert hub.runtime("q") is not None
            await ws.close()

    run(main())


def test_saving_happens_off_the_event_loop(tmp_path):
    """一万笔的白板保存期间，其他白板的回执照常很快。"""

    async def main():
        hub = Hub(FileStorage(tmp_path / "data"), autosave=3600)
        async with serve(tmp_path, hub=hub) as (client, _):
            big, _ = await hello(client, "big", {}, client_id="pad-big")
            batch = [dict(stroke(f"s{i}", i), p=[float(i), float(j), 0.5] * 1 + [float(i + 1), float(j), 0.5] * 49)
                     for i in range(2000) for j in (0,)]
            for start in range(0, 10000, 2000):
                chunk = [dict(s, id=f"{s['id']}-{start}") for s in batch]
                await send_op(big, f"c{start}", {"op": "add", "strokes": chunk})
            small, _ = await hello(client, "small", {}, client_id="pad-small")
            hub.tick()
            worst = 0.0
            for index in range(20):
                began = time.perf_counter()
                await send_op(small, f"x{index}", {"op": "add", "strokes": [stroke(f"t{index}")]})
                worst = max(worst, time.perf_counter() - began)
            assert worst < 0.5, worst  # CI 上留足余量；本机实测远低于此
            await hub.flush()
            for ws in (big, small):
                await ws.close()

    run(main())


def test_spaces_are_created_on_demand_and_isolated(tmp_path):
    async def main():
        created = []

        def factory(name):
            if name not in ("", "qb"):
                return None
            created.append(name)
            return Hub(FileStorage(tmp_path / "spaces" / (name or "default")))

        spaces = Spaces(factory)
        async with serve(tmp_path, spaces=spaces) as (client, _):
            a, _ = await hello(client, "q", {}, space="qb", client_id="pad-qb")
            b, _ = await hello(client, "q", {}, space="", client_id="pad-default")
            await send_op(a, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert await receive(b, "op", timeout=0.3) is None
            ws, reply = await hello(client, "q", {}, space="nope")
            assert reply["reason"] == "space"
            assert sorted(created) == ["", "qb"]
            await spaces.close("qb")
            assert (tmp_path / "spaces" / "qb" / "boards" / "q.wbz").exists()
            for w in (a, b, ws):
                await w.close()

    run(main())


def test_server_side_create_strokes_and_import(tmp_path):
    async def main():
        hub = Hub(FileStorage(tmp_path / "data"), autosave=3600)
        async with serve(tmp_path, hub=hub) as (client, _):
            meta = await hub.create_board("srv", {"name": "服务端", "canvas": {"mode": "column", "width": 700}})
            assert meta["canvas"] == {"mode": "column", "width": 700}
            ws, init = await hello(client, "srv", None)
            await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert [s["id"] for s in await hub.strokes("srv")] == ["s1"]
            assert await hub.strokes("missing") is None
            await hub.flush()
            outside = tmp_path / "copy.wbz"
            outside.write_bytes((tmp_path / "data" / "boards" / "srv.wbz").read_bytes())
            imported, _raw, old_id = await hub.import_file(outside)
            assert old_id == "srv" and imported["id"] != "srv" and imported["name"] == "服务端"
            assert [s["id"] for s in await hub.strokes(imported["id"])] == ["s1"]
            await ws.close()

    run(main())

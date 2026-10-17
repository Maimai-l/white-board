"""临时白板（Hub.create_transient）：只在内存中，从不写盘、不进索引、不被释放。"""

from __future__ import annotations

import asyncio

import pytest
from helpers import hello, receive, send_op, serve, stroke

from inksync import FileStorage, Hub


def run(coro):
    return asyncio.run(coro)


def board_files(tmp_path):
    folder = tmp_path / "data" / "boards"
    return sorted(p.name for p in folder.glob("*")) if folder.exists() else []


def test_transient_board_syncs_but_never_touches_disk(tmp_path):
    async def main():
        async with serve(tmp_path, idle_unload=0) as (client, hub):
            hub.create_transient("scratch", {"name": "速写", "background": {"pattern": "blank"}})
            assert hub.exists("scratch") and hub.is_transient("scratch")
            assert hub.count() == 0 and hub.list_boards() == []
            assert hub.board_meta("scratch")["name"] == "速写"

            a, init_a = await hello(client, board="scratch", create=None, client_id="pad-aaaa")
            b, init_b = await hello(client, board="scratch", create=None, client_id="pad-bbbb")
            assert init_a["t"] == "init" and init_b["board"]["id"] == "scratch"
            ack = await send_op(a, "c1", {"op": "add", "strokes": [stroke("s1"), stroke("s2", 30)]})
            assert "rejected" not in ack
            relayed = await receive(b, "op")
            assert [s["id"] for s in relayed["op"]["strokes"]] == ["s1", "s2"]
            ack = await send_op(b, "c2", {"op": "meta", "meta": {"name": "改过的名字"}})
            assert "rejected" not in ack

            # 自动保存、强制保存、释放都不碰它：没有文件、没有索引行，内容还在
            for _ in range(3):
                hub.tick()
            await hub.flush()
            hub.save_all()
            assert board_files(tmp_path) == []
            assert hub.count() == 0
            assert [s["id"] for s in await hub.strokes("scratch")] == ["s1", "s2"]
            assert hub.board_meta("scratch")["name"] == "改过的名字"
            await a.close()
            await b.close()
            await asyncio.sleep(0.05)
            hub.tick()
            assert hub.runtime("scratch") is not None, "没有连接也不能释放：释放之后无处读回"
            assert hub.idle(), "临时白板的改动不算未保存的改动"
        # 重新打开同一个存储目录：临时白板不存在
        fresh = Hub(FileStorage(tmp_path / "data"))
        assert not fresh.exists("scratch") and fresh.count() == 0

    run(main())


def test_dropped_transient_board_rejects_late_ops_and_hellos(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            hub.create_transient("scratch")
            ws, _ = await hello(client, board="scratch", create=None)
            assert hub.drop_transient("scratch")
            assert not hub.drop_transient("scratch")
            assert not hub.exists("scratch") and hub.runtime("scratch") is None
            ack = await send_op(ws, "late", {"op": "add", "strokes": [stroke("s9")]})
            assert ack["rejected"] == "deleted"
            other, reply = await hello(client, board="scratch", create=None, client_id="pad-late")
            assert reply["t"] == "error" and reply["reason"] == "board"
            assert board_files(tmp_path) == []
            # 丢掉之后可以用同一个 id 重新建一块空白的
            hub.create_transient("scratch")
            assert await hub.strokes("scratch") == []

    run(main())


def test_transient_ids_do_not_collide_with_stored_boards(tmp_path):
    async def main():
        async with serve(tmp_path) as (_client, hub):
            await hub.create_board("kept", {"name": "存着的"})
            with pytest.raises(ValueError):
                hub.create_transient("kept")
            hub.create_transient("scratch")
            with pytest.raises(ValueError):
                hub.create_transient("scratch")
            with pytest.raises(ValueError):
                await hub.create_board("scratch", {})
            with pytest.raises(ValueError):
                hub.create_transient("bad id!")
            assert hub.count() == 1 and [m["id"] for m in hub.list_boards()] == ["kept"]
            hub.forget("scratch")
            assert hub.runtime("scratch") is not None, "forget 不能丢掉临时白板"

    run(main())


def test_deleting_a_transient_board_notifies_viewers(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            seen = []
            hub.on("deleted", lambda board_id, meta: seen.append(board_id))
            hub.create_transient("scratch")
            ws, _ = await hello(client, board="scratch", create=None)
            assert await hub.delete_board("scratch")
            assert (await receive(ws, "deleted"))["board"] == "scratch"
            assert seen == ["scratch"] and not hub.exists("scratch")

    run(main())


def test_detached_event_reports_each_connection(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            gone = []
            hub.on("detached", lambda conn: gone.append((conn.id, conn.board)))
            ws, _ = await hello(client, board="b1", client_id="pad-gone")
            await ws.close()
            for _ in range(50):
                if gone:
                    break
                await asyncio.sleep(0.02)
            assert gone == [("pad-gone", "b1")]

    run(main())

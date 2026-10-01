"""协议 v2：握手、新建、打开、操作、回执、只读、规则、切换白板。"""

from __future__ import annotations

import asyncio

from helpers import hello, local, receive, remote, send_op, serve, stroke

from inksync import DefaultPolicy

FIXED = {"name": "第 3 题", "canvas": {"mode": "fixed", "width": 800, "height": 1200},
         "background": {"pattern": "blank", "paper": "#FFFFFF"},
         "layers": [{"src": "/static/q3.png", "x": 0, "y": 0, "width": 800}],
         "data": {"paper": "9709", "question": 3}}


def run(coro):
    return asyncio.run(coro)


def test_hello_creates_the_board_and_returns_a_snapshot(tmp_path):
    async def main():
        async with serve(tmp_path, info=lambda r: {"app": "qb"}) as (client, hub):
            ws, init = await hello(client, "q-3", FIXED)
            assert init["t"] == "init" and init["v"] == 2
            assert init["server"]["name"] == "inksync" and init["server"]["protocol"] == 2
            assert init["info"] == {"app": "qb"}
            board = init["board"]
            assert board["id"] == "q-3" and board["name"] == "第 3 题"
            assert board["canvas"] == {"mode": "fixed", "width": 800, "height": 1200}
            assert board["background"] == {"pattern": "blank", "paper": "#ffffff"}
            assert board["layers"] == [{"src": "/static/q3.png", "x": 0, "y": 0, "width": 800}]
            assert board["data"] == {"paper": "9709", "question": 3}
            assert init["caps"] == {"write": True, "clear": True, "meta": True, "unlock": True}
            assert init["strokes"] == [] and init["seq"] == 0
            assert hub.board_meta("q-3")["name"] == "第 3 题"
            await ws.close()

    run(main())


def test_missing_board_without_create_is_refused(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, _hub):
            ws, reply = await hello(client, "nope", None)
            assert reply == {"t": "error", "reason": "board"}
            await ws.close()

    run(main())


def test_invalid_create_and_ids_are_refused(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            for board, create, reason in (
                ("a:b", {}, "board"),
                ("x" * 65, {}, "board"),
                ("ok", {"canvas": {"mode": "fixed", "width": 10}}, "create"),
                ("ok", {"layers": [{"src": "https://evil.example/x.png", "x": 0, "y": 0, "width": 5}]}, "create"),
                ("ok", {"layers": [{"src": "/a/../b.png", "x": 0, "y": 0, "width": 5}]}, "create"),
                ("ok", {"data": {"big": "x" * 20000}}, "create"),
                ("ok", "text", "create"),
            ):
                ws, reply = await hello(client, board, create)
                assert reply == {"t": "error", "reason": reason}, (board, create)
                await ws.close()
            assert hub.count() == 0

    run(main())


def test_an_existing_board_is_not_changed_by_create(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, _hub):
            ws, first = await hello(client, "q", {"name": "原名"})
            await ws.close()
            ws, again = await hello(client, "q", {"name": "新名", "canvas": {"mode": "column", "width": 500}})
            assert again["board"]["name"] == "原名" and again["board"]["canvas"] == {"mode": "infinite"}
            await ws.close()

    run(main())


def test_ops_are_acked_broadcast_and_resynced(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, _hub):
            a, init = await hello(client, "q", {}, client_id="pad-aaa")
            b, _ = await hello(client, "q", {}, client_id="pad-bbb")
            other, _ = await hello(client, "r", {}, client_id="pad-ccc")
            ack = await send_op(a, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert ack["board"] == "q" and ack["seq"] == 1 and ack["op"]["strokes"][0]["n"] == 0
            got = await receive(b, "op")
            assert got["board"] == "q" and got["src"] == "pad-aaa"
            assert await receive(other, "op", timeout=0.3) is None

            await a.send_json({"t": "live", "id": "s2", "phase": "b", "tool": "pen", "color": "#000000", "w": 3})
            assert (await receive(b, "live"))["src"] == "pad-aaa"
            assert await receive(other, "live", timeout=0.3) is None

            # 重复的 add：回执不带 op，也不算被拒绝
            again = await send_op(a, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert "op" not in again and "rejected" not in again

            await b.close()
            c, sync = await hello(client, "q", {}, client_id="pad-bbb", since=0, epoch=init["epoch"])
            assert sync["t"] == "sync" and [op["strokes"][0]["id"] for op in sync["ops"]] == ["s1"]
            for ws in (a, c, other):
                await ws.close()

    run(main())


def test_invalid_ops_are_rejected_with_a_reason(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, _hub):
            ws, _ = await hello(client, "q", {})
            assert (await send_op(ws, "c1", {"op": "add", "strokes": [{"id": "bad"}]}))["rejected"] == "invalid"
            assert (await send_op(ws, "c2", {"op": "unknown"}))["rejected"] == "invalid"
            assert (await send_op(ws, "c3", "text"))["rejected"] == "invalid"
            ok = await send_op(ws, "c4", {"op": "meta", "meta": {"layers": [{"src": "http://x/y", "width": 3}]}})
            assert ok["rejected"] == "invalid"
            await ws.close()

    run(main())


def test_ops_carry_their_board_across_open(tmp_path):
    """切换白板之前没送达的操作，带着自己的 board 照样落到原来那块。"""

    async def main():
        async with serve(tmp_path) as (client, hub):
            ws, _ = await hello(client, "q1", {})
            await ws.send_json({"t": "open", "board": "q2", "create": {"name": "第 2 题"}})
            opened = await receive(ws, "init")
            assert opened["board"]["id"] == "q2"
            ack = await send_op(ws, "c1", {"op": "add", "strokes": [stroke("old")]}, board="q1")
            assert ack["board"] == "q1" and ack["op"]
            ack = await send_op(ws, "c2", {"op": "add", "strokes": [stroke("new")]})
            assert ack["board"] == "q2"
            assert [s["id"] for s in await hub.strokes("q1")] == ["old"]
            assert [s["id"] for s in await hub.strokes("q2")] == ["new"]
            await ws.close()

    run(main())


def test_readonly_connections_cannot_write(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            writer, _ = await hello(client, "q", {}, client_id="pad-writer")
            viewer, init = await hello(client, "q", {}, client_id="pad-viewer", readonly=True)
            assert init["readonly"] is True and init["caps"]["write"] is False
            ack = await send_op(viewer, "c1", {"op": "add", "strokes": [stroke("s1")]})
            assert ack["rejected"] == "denied"
            await viewer.send_json({"t": "live", "id": "s1", "phase": "b"})
            assert await receive(writer, "live", timeout=0.3) is None
            await send_op(writer, "w1", {"op": "add", "strokes": [stroke("w1")]})
            assert (await receive(viewer, "op"))["op"]["strokes"][0]["id"] == "w1"
            for ws in (writer, viewer):
                await ws.close()

    run(main())


class PerUser(DefaultPolicy):
    protected_data_keys = frozenset({"score"})

    def can_open(self, who, board_id, meta):
        return who.local or (who.id is not None and board_id.startswith(f"u{who.id}-"))

    def can_create(self, who, board_id, spec):
        return self.can_open(who, board_id, None)

    def can_edit_meta(self, who, meta, patch):
        return True


def test_policy_decides_open_create_and_protected_data(tmp_path):
    async def main():
        async with serve(tmp_path, policy=PerUser(), authenticate=remote("42")) as (client, hub):
            ws, reply = await hello(client, "u7-q1", {})
            assert reply == {"t": "error", "reason": "denied"}
            await ws.close()
            ws, init = await hello(client, "u42-q1", {"data": {"score": 0}})
            assert init["t"] == "init" and init["caps"]["meta"] is True and init["caps"]["unlock"] is False
            # 受保护的键只能由服务端修改
            ack = await send_op(ws, "c1", {"op": "meta", "meta": {"data": {"score": 10}}})
            assert ack["rejected"] == "denied"
            ok = await send_op(ws, "c2", {"op": "meta", "meta": {"data": {"note": "看过"}}})
            assert ok["op"]["meta"]["data"] == {"score": 0, "note": "看过"}
            # 服务端修改不受限制，连接收到 meta 操作
            meta = await hub.edit_meta("u42-q1", {"data": {"score": 7, "note": None}})
            assert meta["data"] == {"score": 7}
            got = await receive(ws, "op")
            assert got["op"]["op"] == "meta" and got["op"]["meta"]["data"] == {"score": 7}
            # 没有权限的白板也不能靠 op 的 board 字段写进去
            await hub.create_board("u7-q2", {})
            ack = await send_op(ws, "c3", {"op": "add", "strokes": [stroke("s")]}, board="u7-q2")
            assert ack["rejected"] == "denied"
            await ws.close()

    run(main())


def test_default_policy_limits_remote_meta_and_creation_rate(tmp_path):
    class Tight(DefaultPolicy):
        def create_limit(self, who):
            return None if who.local else (2, 60.0)

    async def main():
        async with serve(tmp_path, policy=Tight(), authenticate=remote()) as (client, _hub):
            for index in (1, 2):
                ws, init = await hello(client, f"q{index}", {})
                assert init["caps"] == {"write": True, "clear": True, "meta": False, "unlock": False}
                await ws.close()
            ws, reply = await hello(client, "q3", {})
            assert reply == {"t": "error", "reason": "rate"}
            await ws.close()
            ws, init = await hello(client, "q1", {})   # 已有的照样能打开
            assert init["t"] == "init"
            ack = await send_op(ws, "c1", {"op": "meta", "meta": {"name": "改名"}})
            assert ack["rejected"] == "denied"
            await ws.close()

    run(main())


def test_version_and_space_errors(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, _hub):
            ws = await client.ws_connect("/ws")
            await ws.send_json({"t": "hello", "v": 3, "client": "pad-one", "board": "q"})
            assert await ws.receive_json() == {"t": "error", "reason": "version", "supported": [2]}
            await ws.close()
            ws = await client.ws_connect("/ws")
            await ws.send_json({"t": "hello", "client": "pad-one", "board": "q"})   # 1.0.x
            assert (await ws.receive_json())["reason"] == "version"
            await ws.close()
            ws, reply = await hello(client, "q", {}, space="other")
            assert reply == {"t": "error", "reason": "space"}
            await ws.close()

    run(main())


def test_delete_tells_viewers_and_later_ops_are_rejected(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            ws, _ = await hello(client, "q", {})
            assert await hub.delete_board("q")
            assert (await receive(ws, "deleted"))["board"] == "q"
            ack = await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s")]})
            assert ack["rejected"] == "deleted"
            await hub.flush()
            assert not (tmp_path / "data" / "boards" / "q.wbz").exists()
            await ws.close()

    run(main())


def test_ping_and_extension_messages(tmp_path):
    async def main():
        async with serve(tmp_path) as (client, hub):
            seen = []

            async def echo(conn, msg):
                seen.append(msg["x"])
                await conn.send({"t": "qb.echo", "x": msg["x"]})

            hub.register("qb.echo", echo)
            ws, _ = await hello(client, "q", {})
            await ws.send_json({"t": "ping", "ts": 5})
            assert (await receive(ws, "pong"))["ts"] == 5
            await ws.send_json({"t": "qb.echo", "x": 1})
            assert (await receive(ws, "qb.echo"))["x"] == 1 and seen == [1]
            await ws.send_json({"t": "unknown"})
            await ws.close()

    run(main())


def test_events_fire(tmp_path):
    async def main():
        async with serve(tmp_path, autosave=0.05) as (client, hub):
            events = []
            hub.on("created", lambda b, m: events.append(("created", b)))
            hub.on("changed", lambda b, m, op: events.append(("changed", b, op["op"])))
            hub.on("saved", lambda b, m: events.append(("saved", b)))
            hub.on("deleted", lambda b, m: events.append(("deleted", b)))
            ws, _ = await hello(client, "q", {})
            await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s")]})
            for _ in range(50):
                if ("saved", "q") in events:
                    break
                await asyncio.sleep(0.05)
            await hub.delete_board("q")
            assert events[0] == ("created", "q") and ("changed", "q", "add") in events
            assert ("saved", "q") in events and events[-1] == ("deleted", "q")
            await ws.close()

    run(main())


def test_caps_follow_refresh(tmp_path):
    class Switchable(DefaultPolicy):
        writable = True

        def can_write(self, who, meta):
            return self.writable

    async def main():
        policy = Switchable()
        async with serve(tmp_path, policy=policy, authenticate=remote()) as (client, hub):
            ws, init = await hello(client, "q", {})
            assert init["caps"]["write"] is True
            policy.writable = False
            await hub.refresh()
            assert (await receive(ws, "caps"))["caps"]["write"] is False
            assert (await send_op(ws, "c1", {"op": "add", "strokes": [stroke("s")]}))["rejected"] == "denied"
            await ws.close()

    run(main())


def test_local_helper_is_used_by_default(tmp_path):
    assert local(None).local is True

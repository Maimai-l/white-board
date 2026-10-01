"""examples/qb-server：只用 inksync 公开接口的刷题服务端（docs/design/inksync-redesign.zh-CN.md 第 8 节 A1）。"""

from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "qb-server" / "server.py"


def load_example():
    spec = importlib.util.spec_from_file_location("qb_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(coro):
    return asyncio.run(coro)


def test_the_example_only_uses_the_public_interface():
    source = EXAMPLE.read_text("utf-8")
    assert "whiteboard" not in source
    imports = [line for line in source.splitlines() if line.startswith(("from inksync", "import inksync"))]
    assert imports == [
        "from inksync import DefaultPolicy, FileStorage, Hub, Principal, mount, serve_sdk",
        "from inksync.netinfo import advertise, is_local_request",
    ]


@asynccontextmanager
async def serve(tmp_path, monkeypatch, remote=True):
    qb = load_example()
    if remote:
        monkeypatch.setattr(qb, "is_local_request", lambda request: False)
    client = TestClient(TestServer(qb.create_app(tmp_path)))
    await client.start_server()
    try:
        yield client, qb
    finally:
        await client.close()


async def hello(client, board, user, create={}):
    ws = await client.ws_connect("/ws", headers={"Cookie": f"qb_user={user}"})
    await ws.send_json({"t": "hello", "v": 2, "client": f"pad-{user}", "board": board, "create": create})
    return ws, await ws.receive_json()


async def receive(ws, kind, timeout=1.0):
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


STROKE = {"id": "s1", "tool": "pen", "color": "#000000", "w": 3.0, "p": [0, 0, 0.5, 10, 0, 0.5]}


def test_students_only_open_their_own_boards(tmp_path, monkeypatch):
    async def main():
        async with serve(tmp_path, monkeypatch) as (client, _qb):
            ws, init = await hello(client, "u42-q1", "42", {"canvas": {"mode": "fixed", "width": 800, "height": 1100}})
            assert init["t"] == "init" and init["board"]["canvas"]["mode"] == "fixed"
            await ws.close()
            ws, refused = await hello(client, "u42-q1", "7")
            assert refused == {"t": "error", "reason": "denied"}
            await ws.close()
            ws, refused = await hello(client, "u42-q1", "not-a-number")
            assert refused == {"t": "error", "reason": "denied"}
            await ws.close()

    run(main())


def test_scores_are_written_by_the_server_only(tmp_path, monkeypatch):
    async def main():
        async with serve(tmp_path, monkeypatch) as (client, qb):
            ws, _ = await hello(client, "u42-q1", "42", {"data": {"question": 1}})
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [STROKE]}})
            assert (await receive(ws, "ack"))["op"]
            await ws.send_json({"t": "op", "cid": "c2", "op": {"op": "meta", "meta": {"data": {"score": 99}}}})
            assert (await receive(ws, "ack"))["rejected"] == "denied"
            await ws.send_json({"t": "op", "cid": "c3", "op": {"op": "meta", "meta": {"data": {"done": True}}}})
            assert (await receive(ws, "ack"))["op"]["meta"]["data"] == {"question": 1, "done": True}

            # 批改只能本机调用
            assert (await client.post("/grade/u42-q1")).status == 403
            monkeypatch.setattr(qb, "is_local_request", lambda request: True)
            graded = await (await client.post("/grade/u42-q1")).json()
            assert graded["strokes"] == 1 and graded["score"] == 1
            relayed = await receive(ws, "op")
            assert relayed["op"]["meta"]["data"]["score"] == 1
            await ws.close()

    run(main())


def test_login_and_whoami(tmp_path, monkeypatch):
    async def main():
        async with serve(tmp_path, monkeypatch) as (client, _qb):
            response = await client.get("/", allow_redirects=False)
            assert response.status == 302 and response.headers["Location"] == "/login?user=1"
            response = await client.get("/login?user=42", allow_redirects=False)
            assert response.status == 302
            who = await (await client.get("/whoami")).json()
            assert who == {"id": "42"}
            assert (await client.get("/inksync/inkpad.js")).status == 200
            version = await (await client.get("/inksync/version.js")).text()
            assert 'VERSION = "0.2.0"' in version

    run(main())

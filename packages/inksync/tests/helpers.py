"""测试用的小工具：起一个挂了 inksync 的服务，连上去收发消息。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from inksync import FileStorage, Hub, Principal, mount


def stroke(stroke_id: str, x: float = 0.0) -> Dict[str, Any]:
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3.0, "p": [x, 0.0, 0.5, x + 10, 0.0, 0.5]}


def remote(user: Optional[str] = None, address: str = "192.0.2.7"):
    """模拟局域网上的其他设备。"""
    return lambda request: Principal(id=user, local=False, address=address)


def local(request) -> Principal:
    return Principal(id=None, local=True, address="127.0.0.1")


@asynccontextmanager
async def serve(tmp_path, policy=None, authenticate=None, hub: Optional[Hub] = None, spaces=None, info=None, **hub_args):
    app = web.Application()
    if spaces is None:
        hub = hub or Hub(FileStorage(tmp_path / "data"), policy=policy, **hub_args)
        mount(app, hub, authenticate=authenticate or local, info=info)
    else:
        mount(app, spaces, authenticate=authenticate or local, info=info)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, hub
    finally:
        await client.close()


async def hello(client, board: Optional[str] = "b1", create: Any = {}, client_id: str = "pad-one", **extra):
    ws = await client.ws_connect("/ws")
    msg = {"t": "hello", "v": 2, "client": client_id, "board": board, "since": 0, "epoch": None, **extra}
    if create is not None:
        msg["create"] = create
    await ws.send_json(msg)
    return ws, await ws.receive_json()


async def receive(ws, kind: str, timeout: float = 1.0):
    """收到类型为 ``kind`` 的消息为止；超时返回 None。"""
    try:
        while True:
            msg = await ws.receive_json(timeout=timeout)
            if msg.get("t") == kind:
                return msg
    except asyncio.TimeoutError:
        return None


async def send_op(ws, cid: str, op: Dict[str, Any], board: Optional[str] = None):
    msg = {"t": "op", "cid": cid, "op": op}
    if board is not None:
        msg["board"] = board
    await ws.send_json(msg)
    return await receive(ws, "ack")

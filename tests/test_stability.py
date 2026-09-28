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
    assert hub.board().apply(op) is None


def test_malformed_message_is_acknowledged_and_keeps_the_connection(tmp_path):
    """旧代码：分派时抛异常，连接断开、回执没发。客户端的待发队列存在 IndexedDB 里，
    重连后原样重发，服务端再断——刷新页面也出不来。"""

    async def main():
        async with make_client(tmp_path) as (client, app):
            ws = await client.ws_connect("/ws")
            await hello(ws)
            await ws.send_json({"t": "op", "cid": "bad", "op": {"op": "add", "strokes": {"id": "x"}}})
            ack = await ws.receive_json(timeout=5)
            assert ack == {"t": "ack", "cid": "bad", "seq": app[HUB_KEY].board().seq}
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

            def boom(raw):
                raise RuntimeError("意外")

            monkeypatch.setattr(app[HUB_KEY].board(), "apply", boom)
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke()]}})
            ack = await ws.receive_json(timeout=5)
            assert ack["t"] == "ack" and ack["cid"] == "c1"
            await ws.send_json({"t": "ping", "ts": 1})
            assert (await ws.receive_json(timeout=5))["t"] == "pong"
            await ws.close()

    run(main())

"""inksync 单独使用：不依赖白板应用，挂到任意 aiohttp 服务上就能同步。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "inksync"


def test_inksync_does_not_import_the_whiteboard_app():
    for path in (PACKAGE / "inksync").glob("*.py"):
        source = path.read_text("utf-8")
        assert "import whiteboard" not in source and "from whiteboard" not in source, path.name

    # 只把 packages/inksync 放进路径，白板应用完全不在：照样能导入并建起服务
    code = (
        "import sys; sys.path[:0] = [sys.argv[1]];"
        "import inksync, aiohttp.web as web;"
        "from inksync import BoardStore, Hub, mount;"
        "app = web.Application(); mount(app, Hub(BoardStore(sys.argv[2])));"
        "assert 'whiteboard' not in sys.modules; print('ok')"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(PACKAGE), "/tmp/inksync-standalone"],
        cwd="/", capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.stdout.strip() == "ok", result.stderr


def test_a_plain_aiohttp_app_can_mount_the_sync_protocol(tmp_path):
    sys.path.insert(0, str(PACKAGE))
    from inksync import BoardStore, Hub, mount

    async def main():
        app = web.Application()
        hub = Hub(BoardStore(tmp_path / "data"))
        # 例如刷题项目：只放开书写和应用自己的白板，其他权限都不给
        mount(app, hub, path="/sync", permissions=lambda request: frozenset(), info=lambda r: {"app": "qb"})
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/sync")
            await ws.send_json({"t": "hello", "role": "ipad", "client": "pad-one", "board": None, "since": 0,
                                "epoch": None, "pin": {"board": "qb-1", "app": "qb", "name": "第 1 题"}})
            init = await ws.receive_json()
            assert init["t"] == "init" and init["board"]["id"] == "qb-1" and init["info"] == {"app": "qb"}
            stroke = {"id": "s1", "tool": "pen", "color": "#000000", "w": 3.0, "p": [0, 0, 0.5, 10, 0, 0.5]}
            await ws.send_json({"t": "op", "cid": "c1", "op": {"op": "add", "strokes": [stroke]}})
            ack = await ws.receive_json()
            assert ack["t"] == "ack" and ack["op"]["strokes"][0]["id"] == "s1"
            # 没有 clear 权限，但应用自己的白板可以清空
            await ws.send_json({"t": "op", "cid": "c2", "op": {"op": "clear"}})
            assert (await ws.receive_json())["op"] == {"op": "clear", "seq": 2}
            await ws.close()
        finally:
            await client.close()   # 关服务时 mount 装的清理钩子会存盘

    asyncio.run(main())
    blob = (tmp_path / "data" / "boards" / "qb-1.wbz").read_bytes()
    saved = json.loads(zlib.decompress(blob))
    assert saved["meta"]["app"] == "qb" and saved["strokes"] == []


def test_the_core_store_refuses_document_boards_it_cannot_complete(tmp_path):
    sys.path.insert(0, str(PACKAGE))
    from inksync.store import BoardFileError, BoardStore

    payload = {"v": 1, "meta": {"id": "d1", "kind": "doc", "doc": {"type": "image", "name": "a.png",
                                                               "pages": [[100, 100]]}}, "strokes": []}
    path = tmp_path / "d1.wbz"
    path.write_bytes(zlib.compress(json.dumps(payload).encode()))
    store = BoardStore(tmp_path / "data")
    with pytest.raises(BoardFileError):
        store.import_board_file(path)
    assert not (tmp_path / "data" / "thumbs").exists()  # 缩略图、原件目录属于白板应用

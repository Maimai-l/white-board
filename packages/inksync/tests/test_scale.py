"""很多块白板时的开销（docs/design/inksync-redesign.zh-CN.md 2.3 节 N2、N3）。"""

from __future__ import annotations

import asyncio
import time

from inksync import FileStorage, Hub, models

COUNT = 20000


def make_boards(root, count=COUNT):
    boards = root / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        meta = models.new_board_meta(f"u{index % 500}-q{index}", {"name": f"第 {index} 题"})
        (boards / f"{meta['id']}.wbz").write_bytes(FileStorage.encode(meta, []))


def test_twenty_thousand_boards(tmp_path):
    make_boards(tmp_path)
    FileStorage(tmp_path).close()  # 第一次打开：从白板文件建索引

    began = time.perf_counter()
    store = FileStorage(tmp_path)
    opened = time.perf_counter() - began
    assert store.count() == COUNT
    assert opened < 1.0, opened  # 有索引时启动不读白板文件

    began = time.perf_counter()
    for _ in range(1000):
        assert store.get_meta("u7-q7") is not None
    assert (time.perf_counter() - began) / 1000 < 0.001

    async def create():
        hub = Hub(store)
        began = time.perf_counter()
        await hub.create_board("new-board", {"name": "新的一块"})
        return time.perf_counter() - began

    created = asyncio.run(create())
    assert created < 0.05, created  # 规格 20 ms；CI 上留出余量
    assert store.count() == COUNT + 1
    assert len(store.list(prefix="u7-", limit=None)) == COUNT // 500
    store.close()


def test_idle_boards_are_unloaded(tmp_path):
    async def main():
        hub = Hub(FileStorage(tmp_path), idle_unload=0)
        for index in range(1000):
            await hub.create_board(f"b{index}", {})
        await hub.flush()
        assert len(hub.loaded()) == 1000
        hub.tick()
        assert len(hub.loaded()) == 0
        assert (await hub.strokes("b5")) == []  # 用到时重新读入
        assert hub.loaded() == ["b5"]

    asyncio.run(main())

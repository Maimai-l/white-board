"""存储兼容性：1.x 写出来的存储目录，2.0 必须读出同样的内容。

夹具在 tests/fixtures/compat/，由 1.x 的代码生成，见 make_compat.py。
2.0 的元数据换了字段（docs/design/inksync-2.zh-CN.md 4.6 节），所以元数据按
``models.to_v1`` 换回 1.x 的字段再比对：换过去再换回来一样，说明没有丢任何信息。
笔画、文件夹、排序和当前白板必须完全一样。
"""

import json
import shutil
import zlib
from pathlib import Path

import pytest

from whiteboard import models
from whiteboard.hub import Hub
from whiteboard.store import BoardStore

FIXTURE = Path(__file__).parent / "fixtures" / "compat"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text("utf-8"))
BOARD_IDS = sorted(EXPECTED["boards"])


@pytest.fixture()
def data_dir(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(FIXTURE / "data", target)
    return target


def read_payload(path):
    return json.loads(zlib.decompress(path.read_bytes()).decode("utf-8"))


def test_index_reads_the_same(data_dir):
    store = BoardStore(data_dir)
    assert [models.to_v1(m) for m in store.list_metas()] == EXPECTED["index"]["boards"]
    assert store.folders() == EXPECTED["index"]["folders"]
    assert store.current_id == EXPECTED["index"]["current"]


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_board_loads_the_same(data_dir, board_id):
    store = BoardStore(data_dir)
    meta, strokes = store.load_board(board_id)
    assert models.to_v1(meta) == EXPECTED["boards"][board_id]["meta"]
    assert strokes == EXPECTED["boards"][board_id]["strokes"]


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_board_saves_back_unchanged(data_dir, board_id):
    """读出来原样存回去：文件升到 v2，笔画数据一个字节不变，元数据换回 1.x 字段后不变。"""
    store = BoardStore(data_dir)
    meta, strokes = store.load_board(board_id)
    store.save_board(meta, strokes)
    saved = read_payload(data_dir / "boards" / f"{board_id}.wbz")
    expected = EXPECTED["boards"][board_id]["file"]
    assert saved["v"] == 2
    assert saved["strokes"] == expected["strokes"]
    assert models.to_v1(saved["meta"]) == expected["meta"]
    # 再读一遍，内容与第一次读出来的相同
    again_meta, again_strokes = BoardStore(data_dir).load_board(board_id)
    assert again_meta == meta and again_strokes == strokes


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_hub_serves_the_same_strokes(data_dir, board_id):
    hub = Hub(BoardStore(data_dir))
    runtime = hub.board(board_id)
    assert runtime.stroke_list() == EXPECTED["boards"][board_id]["strokes"]
    assert models.to_v1(runtime.meta) == EXPECTED["boards"][board_id]["meta"]


def test_index_json_is_retired_after_migration(data_dir):
    BoardStore(data_dir)
    assert not (data_dir / "index.json").exists()
    assert (data_dir / "index.v1.json").exists() and (data_dir / "index.sqlite").exists()
    # 第二次打开不再迁移，文件夹、排序、当前白板照样在
    store = BoardStore(data_dir)
    assert store.folders() == EXPECTED["index"]["folders"]
    assert [m["id"] for m in store.list_metas()] == [m["id"] for m in EXPECTED["index"]["boards"]]
    assert store.current_id == EXPECTED["index"]["current"]

"""存储兼容性：旧版本写出来的文件，新代码必须读出一模一样的内容。

夹具在 tests/fixtures/compat/，由整理开始之前的代码生成，见 make_compat.py。
这里不关心实现细节，只比对结果：索引、每块白板读出来的元数据与笔画、
以及读出来再原样存回去之后文件里的内容。
"""

import json
import shutil
import zlib
from pathlib import Path

import pytest

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
    assert store.list_metas() == EXPECTED["index"]["boards"]
    assert store.folders() == EXPECTED["index"]["folders"]
    assert store.current_id == EXPECTED["index"]["current"]


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_board_loads_the_same(data_dir, board_id):
    store = BoardStore(data_dir)
    meta, strokes = store.load_board(board_id)
    assert meta == EXPECTED["boards"][board_id]["meta"]
    assert strokes == EXPECTED["boards"][board_id]["strokes"]


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_board_saves_back_unchanged(data_dir, board_id):
    """读出来原样存回去，文件内容不变：新代码不能在存盘时丢字段或改数值。"""
    store = BoardStore(data_dir)
    meta, strokes = store.load_board(board_id)
    store.save_board(meta, strokes)
    assert read_payload(data_dir / "boards" / f"{board_id}.wbz") == EXPECTED["boards"][board_id]["file"]


@pytest.mark.parametrize("board_id", BOARD_IDS)
def test_hub_serves_the_same_strokes(data_dir, board_id):
    hub = Hub(BoardStore(data_dir))
    runtime = hub.board(board_id)
    assert runtime.stroke_list() == EXPECTED["boards"][board_id]["strokes"]
    assert runtime.meta == EXPECTED["boards"][board_id]["meta"]

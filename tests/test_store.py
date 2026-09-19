import json
import zlib
from pathlib import Path

from whiteboard import models
from whiteboard.store import BoardStore


def make_strokes(count=3):
    return [
        {
            "id": f"s{i}",
            "tool": "pen",
            "color": "#123456",
            "w": 2.0 + i,
            "p": [i, i, 0.5, i + 1, i + 2, 0.75],
            "n": i,
            "dev": "ipad",
        }
        for i in range(count)
    ]


def test_new_store_creates_one_board(tmp_path):
    store = BoardStore(tmp_path)
    metas = store.list_metas()
    assert len(metas) == 1
    assert store.current_id == metas[0]["id"]


def test_save_and_load_roundtrip(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.list_metas()[0]
    store.save_board(meta, make_strokes())
    loaded_meta, strokes = BoardStore(tmp_path).load_board(meta["id"])
    assert loaded_meta["id"] == meta["id"]
    assert [s["id"] for s in strokes] == ["s0", "s1", "s2"]
    assert strokes[1]["color"] == "#123456"
    assert strokes[1]["p"][0] == 1.0


def test_content_is_compressed_not_an_image(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.list_metas()[0]
    store.save_board(meta, make_strokes(50))
    blob = (tmp_path / "boards" / f"{meta['id']}.wbz").read_bytes()
    assert not blob.startswith(b"\x89PNG")
    payload = json.loads(zlib.decompress(blob))
    assert isinstance(payload["strokes"][0]["p"], str)  # 点数据是压缩后的字节流
    assert len(blob) < len(json.dumps(payload).encode())


def test_index_is_rebuilt_when_missing(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.create_board("第二块")
    store.save_board(meta, make_strokes())
    (tmp_path / "index.json").unlink()
    rebuilt = BoardStore(tmp_path)
    assert {m["id"] for m in rebuilt.list_metas()} == {m["id"] for m in store.list_metas()}


def test_corrupt_board_file_does_not_crash(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.list_metas()[0]
    (tmp_path / "boards" / f"{meta['id']}.wbz").write_bytes(b"not zlib at all")
    _, strokes = BoardStore(tmp_path).load_board(meta["id"])
    assert strokes == []


def test_delete_keeps_at_least_one_board(tmp_path):
    store = BoardStore(tmp_path)
    first = store.list_metas()[0]["id"]
    assert store.delete_board(first)
    assert len(store.list_metas()) == 1
    assert store.current_id is not None


def test_thumb_requires_png(tmp_path):
    store = BoardStore(tmp_path)
    board_id = store.current_id
    try:
        store.save_thumb(board_id, b"<svg/>")
    except ValueError:
        pass
    else:
        raise AssertionError("非 PNG 应当被拒绝")
    store.save_thumb(board_id, b"\x89PNG\r\n\x1a\nrest")
    assert store.thumb_path(board_id).exists()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = BoardStore(tmp_path)
    store.save_board(store.list_metas()[0], make_strokes())
    assert not list(Path(tmp_path / "boards").glob(".tmp-*"))


def test_unknown_board_returns_blank(tmp_path):
    store = BoardStore(tmp_path)
    meta, strokes = store.load_board("doesnotexist")
    assert strokes == []
    assert meta["cols"] == models.DEFAULT_COLS

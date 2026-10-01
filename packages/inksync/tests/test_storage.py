"""存储：索引、重建、空间级数据、1.0.x 文件、只读判断。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import zlib

from inksync import codec, models
from inksync.storage import FileStorage


def write_v1(path, meta, strokes=()):
    payload = {"v": 1, "meta": meta, "strokes": [
        {**s, "p": codec.encode_points_b64(s["p"])} for s in strokes
    ]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(json.dumps(payload).encode()))


def test_empty_storage_has_no_boards(tmp_path):
    store = FileStorage(tmp_path)
    assert store.count() == 0 and store.list() == []
    assert (tmp_path / "index.sqlite").exists()


def test_write_read_and_index(tmp_path):
    store = FileStorage(tmp_path)
    meta = models.new_board_meta("b1", {"name": "一"})
    stroke = {"id": "s1", "tool": "pen", "color": "#000000", "w": 3.0, "p": [0.0, 0.0, 0.5, 1.0, 1.0, 0.5], "n": 0}
    mtime = store.write_file(meta, [stroke])
    store.put_meta(meta, mtime)
    store.flush()
    read_meta, strokes, problem = store.read_file("b1")
    assert problem is None and read_meta["name"] == "一" and strokes[0]["p"][0::3] == stroke["p"][0::3]
    assert store.get_meta("b1")["name"] == "一"
    store.close()
    # 重新打开：索引行的修改时间与文件一致，不读文件
    again = FileStorage(tmp_path)
    assert again.get_meta("b1")["name"] == "一"


def test_index_catches_up_with_files_changed_while_closed(tmp_path):
    store = FileStorage(tmp_path)
    for board_id in ("a", "b"):
        meta = models.new_board_meta(board_id, {"name": board_id})
        store.put_meta(meta, store.write_file(meta, []))
    store.close()
    # 进程在索引写入之前退出：文件已经改了名、多了一块、少了一块
    meta = models.new_board_meta("a", {"name": "改过"})
    time.sleep(0.02)
    FileStorage.encode  # noqa: B018
    (tmp_path / "boards" / "a.wbz").write_bytes(FileStorage.encode(meta, []))
    os.utime(tmp_path / "boards" / "a.wbz", (time.time() + 5, time.time() + 5))
    (tmp_path / "boards" / "b.wbz").unlink()
    c = models.new_board_meta("c", {"name": "c"})
    (tmp_path / "boards" / "c.wbz").write_bytes(FileStorage.encode(c, []))
    again = FileStorage(tmp_path)
    assert sorted(again.ids()) == ["a", "c"]
    assert again.get_meta("a")["name"] == "改过"


def test_a_corrupt_index_is_rebuilt(tmp_path):
    store = FileStorage(tmp_path)
    meta = models.new_board_meta("a", {"name": "甲"})
    store.put_meta(meta, store.write_file(meta, []))
    store.close()
    (tmp_path / "index.sqlite").write_bytes(b"not a database" * 100)
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"index.sqlite{suffix}").unlink(missing_ok=True)
    again = FileStorage(tmp_path)
    assert again.get_meta("a")["name"] == "甲"


def test_kv_survives_index_rebuild(tmp_path):
    store = FileStorage(tmp_path)
    store.kv.set("folders", ["数学", "空的"])
    store.close()
    (tmp_path / "index.sqlite").unlink()
    again = FileStorage(tmp_path)
    assert again.kv.get("folders") == ["数学", "空的"]


def test_list_orders_and_prefix(tmp_path):
    store = FileStorage(tmp_path)
    for index, board_id in enumerate(("u1-a", "u1-b", "u2-a")):
        meta = models.new_board_meta(board_id, {"name": f"n{2 - index}"})
        meta["created"] = meta["updated"] = 1000 + index
        store.put_meta(meta, 0)
    assert [m["id"] for m in store.list()] == ["u2-a", "u1-b", "u1-a"]
    assert [m["id"] for m in store.list(prefix="u1-")] == ["u1-b", "u1-a"]
    assert [m["id"] for m in store.list(order="name")] == ["u2-a", "u1-b", "u1-a"]
    assert [m["id"] for m in store.list(order=["u1-a", "u1-b"])] == ["u2-a", "u1-a", "u1-b"]
    assert [m["id"] for m in store.list(offset=1, limit=1)] == ["u1-b"]
    assert store.count("u1-") == 2


def test_v1_files_are_converted_on_read(tmp_path):
    boards = tmp_path / "boards"
    write_v1(boards / "note1.wbz", {"id": "note1", "name": "笔记", "kind": "note", "background": "lines",
                                     "folder": "数学", "created": 1, "updated": 2})
    write_v1(boards / "doc1.wbz", {"id": "doc1", "kind": "doc", "background": "blank",
                                    "doc": {"type": "pdf", "name": "a.pdf", "ext": ".pdf",
                                            "pages": [[600, 800], [500, 700]]}})
    write_v1(boards / "q1.wbz", {"id": "q1", "kind": "board", "app": "qb",
                                  "underlay": {"src": "/apps/qb/q.png", "width": 800}},
             [{"id": "s1", "tool": "pen", "color": "#000000", "w": 3.0, "p": [0.0, 0.0, 0.5], "n": 0}])
    added = []
    store = FileStorage(tmp_path, convert_meta=lambda m: added.append(m["id"]) or m)
    note = store.get_meta("note1")
    assert note["canvas"] == {"mode": "column", "width": 1000.0}
    assert note["background"] == {"pattern": "lines"} and note["data"] == {"folder": "数学"}
    doc = store.get_meta("doc1")
    assert doc["canvas"] == {"mode": "fixed", "width": 600.0, "height": 1524.0}
    assert doc["data"]["doc"]["name"] == "a.pdf"
    meta, strokes, problem = store.read_file("q1")
    assert problem is None and len(strokes) == 1
    assert meta["layers"] == [{"src": "/apps/qb/q.png", "x": 0.0, "y": 0.0, "width": 800.0}]
    assert meta["data"] == {"app": "qb"}
    assert sorted(set(added)) == ["doc1", "note1", "q1"]
    # 写回之后是 v2
    store.write_file(meta, strokes)
    payload = json.loads(zlib.decompress((boards / "q1.wbz").read_bytes()))
    assert payload["v"] == 2 and payload["meta"]["canvas"] == {"mode": "infinite"}


def test_legacy_index_is_offered_once(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"boards": [], "folders": ["空的"], "current": "x"}))
    store = FileStorage(tmp_path)
    assert store.legacy_index["folders"] == ["空的"]
    store.close()
    assert FileStorage(tmp_path).legacy_index is None  # 已经有 index.sqlite


def test_unreadable_files_are_listed_and_open_read_only(tmp_path):
    boards = tmp_path / "boards"
    boards.mkdir(parents=True)
    (boards / "bad.wbz").write_bytes(b"garbage")
    newer = {"v": 99, "meta": {"id": "new", "name": "新版", "canvas": {"mode": "infinite"}}, "strokes": []}
    (boards / "new.wbz").write_bytes(zlib.compress(json.dumps(newer).encode()))
    store = FileStorage(tmp_path)
    assert sorted(store.ids()) == ["bad", "new"]
    assert store.read_file("bad")[2]["reason"] == "corrupt"
    assert store.read_file("new")[2] == {"reason": "newer", "version": 99}


def test_rewrite_meta_keeps_strokes(tmp_path):
    store = FileStorage(tmp_path)
    meta = models.new_board_meta("a", {"name": "旧"})
    stroke = {"id": "s1", "tool": "pen", "color": "#000000", "w": 3.0, "p": [0.0, 0.0, 0.5], "n": 0}
    store.write_file(meta, [stroke])
    store.rewrite_meta({**meta, "name": "新"})
    read_meta, strokes, _ = store.read_file("a")
    assert read_meta["name"] == "新" and [s["id"] for s in strokes] == ["s1"]


def test_the_index_uses_sqlite_rows(tmp_path):
    store = FileStorage(tmp_path)
    meta = models.new_board_meta("a", {})
    store.put_meta(meta, 1.0)
    store.flush()
    rows = sqlite3.connect(str(tmp_path / "index.sqlite")).execute("SELECT id, mtime FROM boards").fetchall()
    assert rows == [("a", 1.0)]

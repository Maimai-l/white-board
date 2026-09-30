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
    store.core.close()
    (tmp_path / "index.sqlite").unlink()
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
    assert meta["background"] == {"pattern": "grid"}


def test_rename_updates_index_and_file(tmp_path):
    """改名要同时写索引和 .wbz：只改索引的话，索引一重建名字就没了。"""
    store = BoardStore(tmp_path)
    meta = store.create_board()
    store.save_board(meta, make_strokes())
    assert store.rename_board(meta["id"], "  第三章 草稿  ") is True  # 首尾空白要去掉
    assert store.get_meta(meta["id"])["name"] == "第三章 草稿"

    store.core.close()
    (tmp_path / "index.sqlite").unlink()
    rebuilt = BoardStore(tmp_path)
    assert rebuilt.get_meta(meta["id"])["name"] == "第三章 草稿"
    _, strokes = rebuilt.load_board(meta["id"])
    assert [s["id"] for s in strokes] == ["s0", "s1", "s2"]  # 笔画原样搬过去，没丢没变


def test_rename_to_the_same_name_is_a_no_op(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.create_board("讲义")
    assert store.rename_board(meta["id"], "讲义") is False
    assert store.rename_board("没有这块", "随便") is False


def test_rename_to_empty_falls_back_to_the_default_name(tmp_path):
    """清空名字是允许的：界面上会回到「白板」「笔记」这种默认叫法。"""
    store = BoardStore(tmp_path)
    meta = store.create_board("讲义")
    assert store.rename_board(meta["id"], "") is True
    assert store.get_meta(meta["id"])["name"] == ""


def test_a_folder_survives_losing_the_index(tmp_path):
    """文件夹名只存在白板自己的 meta 里和索引里两份，索引重建时要能从 .wbz 找回来。"""
    store = BoardStore(tmp_path)
    meta = store.create_board()
    store.save_board(meta, make_strokes())
    assert store.move_board(meta["id"], "  数学  ") is True  # 首尾空白要去掉
    assert models.folder_of(store.get_meta(meta["id"])) == "数学"

    store.core.close()
    (tmp_path / "index.sqlite").unlink()
    rebuilt = BoardStore(tmp_path)
    assert models.folder_of(rebuilt.get_meta(meta["id"])) == "数学"


def test_moving_a_board_out_of_a_folder_clears_it(tmp_path):
    """没有单独的文件夹记录：里面的白板都移走，这个文件夹自己就不在了。"""
    store = BoardStore(tmp_path)
    meta = store.create_board()
    assert store.move_board(meta["id"], "数学") is True
    assert store.move_board(meta["id"], "数学") is False  # 已经在里面了
    assert store.move_board(meta["id"], "") is True
    assert "folder" not in store.get_meta(meta["id"])["data"]
    assert store.move_board("没有这块", "数学") is False


def test_reordering_only_moves_the_boards_you_name(tmp_path):
    """界面送来的是当前这一层的顺序，别的白板不能跟着动。"""
    store = BoardStore(tmp_path)
    ids = [store.create_board(name)["id"] for name in ("一", "二", "三", "四")]
    # create_board 插在最前面，所以现在是倒着的；最后那块没名字的是开店时自带的
    assert [m["name"] for m in store.list_metas()] == ["四", "三", "二", "一", ""]

    # 只排「三」和「一」这两块：它们占着第 2、第 4 个位置，换过来之后别的不动
    assert store.set_order([ids[0], ids[2]]) is True
    assert [m["name"] for m in store.list_metas()] == ["四", "一", "二", "三", ""]

    assert store.set_order([ids[0], ids[2]]) is False  # 顺序没变就不写盘
    assert store.set_order([ids[0]]) is False  # 一块白板谈不上顺序
    assert store.set_order(["没有这块", ids[0]]) is False  # 认不出的先滤掉，剩一块
    assert store.set_order("不是个列表") is False
    assert [m["name"] for m in store.list_metas()] == ["四", "一", "二", "三", ""]


def test_the_order_survives_a_restart_and_an_index_rebuild(tmp_path):
    """顺序存在 space.json 里，不属于可以重建的索引：索引没了顺序照样在。"""
    store = BoardStore(tmp_path)
    ids = [store.create_board(name)["id"] for name in ("一", "二", "三")]
    store.set_order([ids[0], ids[1], ids[2]])
    assert [m["name"] for m in BoardStore(tmp_path).list_metas()] == ["一", "二", "三", ""]

    store.core.close()
    (tmp_path / "index.sqlite").unlink()
    assert [m["name"] for m in BoardStore(tmp_path).list_metas()] == ["一", "二", "三", ""]


def test_an_empty_folder_survives_losing_the_index(tmp_path):
    """空文件夹没有白板可依附，名单存在 space.json 里，索引丢了也在。"""
    store = BoardStore(tmp_path)
    assert store.create_folder(" 数学 ") == "数学"
    assert store.create_folder("数学") == ""  # 重名不再建一个
    assert store.create_folder("   ") == ""
    meta = store.create_board()
    store.move_board(meta["id"], "物理")
    assert store.folders() == ["数学", "物理"]  # 归类时顺手把名字记进名单

    store.core.close()
    (tmp_path / "index.sqlite").unlink()
    rebuilt = BoardStore(tmp_path)
    assert rebuilt.folders() == ["数学", "物理"]


def test_renaming_a_folder_moves_everything_in_it(tmp_path):
    store = BoardStore(tmp_path)
    first = store.create_board()
    second = store.create_board()
    store.move_board(first["id"], "数学")
    store.move_board(second["id"], "物理")

    assert store.rename_folder("数学", " 线性代数 ") is True
    assert models.folder_of(store.get_meta(first["id"])) == "线性代数"
    assert models.folder_of(store.get_meta(second["id"])) == "物理"  # 别的文件夹没动
    assert store.rename_folder("线性代数", "物理") is False  # 重名会把两个并成一个
    assert store.rename_folder("查无此夹", "随便") is False
    assert store.rename_folder("线性代数", "  ") is False


def test_deleting_a_folder_does_not_delete_the_boards(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.create_board()
    store.move_board(meta["id"], "数学")
    assert store.delete_folder("数学") is True
    assert store.delete_folder("数学") is False
    assert store.folders() == []
    assert store.get_meta(meta["id"]) is not None
    assert "folder" not in store.get_meta(meta["id"])["data"]


def test_renaming_a_board_keeps_its_folder(tmp_path):
    store = BoardStore(tmp_path)
    meta = store.create_board()
    store.move_board(meta["id"], "数学")
    store.rename_board(meta["id"], "第三章")
    after = store.get_meta(meta["id"])
    assert (after["name"], models.folder_of(after)) == ("第三章", "数学")


def test_cut_ends_survive_a_save_and_load(tmp_path):
    """橡皮切出来的端头标记要跟着笔画落盘，不然重开一次板切口又变回圆笔尖。"""
    store = BoardStore(tmp_path)
    meta = store.create_board()
    strokes = make_strokes()
    strokes[0]["cut"] = 2
    strokes[1]["cut"] = 3
    store.save_board(meta, strokes)

    _, loaded = store.load_board(meta["id"])
    assert [s.get("cut") for s in loaded] == [2, 3, None]

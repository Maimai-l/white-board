import asyncio

from whiteboard import models
from whiteboard.hub import BoardRuntime, Hub
from whiteboard.models import new_board_meta
from whiteboard.store import BoardStore


def stroke(stroke_id, n=None):
    data = {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 2, "p": [0, 0, 0.5, 1, 1, 0.5]}
    if n is not None:
        data["n"] = n
    return data


def make_runtime():
    return BoardRuntime(new_board_meta(), [])


def apply(runtime, raw):
    """应用一个操作，只要规范化之后的操作（没有改动或不合法时为 None）。"""
    return runtime.apply(raw)[0]


def run(coro):
    return asyncio.run(coro)


def test_add_assigns_increasing_order():
    runtime = make_runtime()
    op = apply(runtime, {"op": "add", "strokes": [stroke("a"), stroke("b")]})
    assert op["seq"] == 1
    assert [s["n"] for s in op["strokes"]] == [0, 1]


def test_duplicate_id_is_ignored():
    runtime = make_runtime()
    apply(runtime, {"op": "add", "strokes": [stroke("a")]})
    assert runtime.apply({"op": "add", "strokes": [stroke("a")]}) == (None, None)  # 重复不算不合法
    assert len(runtime.strokes) == 1


def test_remove_and_restore_keeps_z_order():
    runtime = make_runtime()
    apply(runtime, {"op": "add", "strokes": [stroke("a"), stroke("b"), stroke("c")]})
    apply(runtime, {"op": "remove", "ids": ["b"]})
    assert [s["id"] for s in runtime.stroke_list()] == ["a", "c"]
    apply(runtime, {"op": "restore", "strokes": [stroke("b", n=1)]})
    assert [s["id"] for s in runtime.stroke_list()] == ["a", "b", "c"]


def test_noop_and_invalid_operations():
    runtime = make_runtime()
    assert runtime.apply({"op": "clear"}) == (None, None)
    assert runtime.apply({"op": "remove", "ids": ["ghost"]}) == (None, None)
    assert runtime.apply({"op": "nonsense"}) == (None, "invalid")
    assert runtime.apply("not a dict") == (None, "invalid")


def test_clear_empties_board():
    runtime = make_runtime()
    apply(runtime, {"op": "add", "strokes": [stroke("a")]})
    assert apply(runtime, {"op": "clear"})["op"] == "clear"
    assert runtime.stroke_list() == []


def test_meta_op_is_sanitized_and_deduplicated():
    runtime = make_runtime()
    op = apply(runtime, {"op": "meta", "meta": {"background": "lines", "name": "笔记"}})
    assert op["meta"]["background"] == {"pattern": "lines"}
    assert op["meta"]["name"] == "笔记"
    # 非法取值回落到默认背景，而不是被忽略
    assert apply(runtime, {"op": "meta", "meta": {"background": "nope"}})["meta"]["background"] == {"pattern": "grid"}
    assert apply(runtime, {"op": "meta", "meta": {"background": {"pattern": "grid"}}}) is None


def test_meta_op_merges_data_by_key():
    runtime = make_runtime()
    apply(runtime, {"op": "meta", "meta": {"data": {"a": 1, "b": 2}}})
    op = apply(runtime, {"op": "meta", "meta": {"data": {"b": None, "c": 3}}})
    assert op["meta"]["data"] == {"a": 1, "c": 3}
    # 画布不能改
    assert apply(runtime, {"op": "meta", "meta": {"canvas": {"mode": "column", "width": 5}}}) is None


def test_ops_since_returns_delta_or_none():
    runtime = make_runtime()
    for i in range(5):
        apply(runtime, {"op": "add", "strokes": [stroke(f"s{i}")]})
    assert runtime.ops_since(5) == []
    assert [op["seq"] for op in runtime.ops_since(3)] == [4, 5]
    runtime.ops.clear()  # 模拟历史窗口滑走
    assert runtime.ops_since(1) is None


def test_invalid_stroke_does_not_break_the_batch():
    runtime = make_runtime()
    op = apply(runtime, {"op": "add", "strokes": [{"id": "bad id"}, stroke("ok")]})
    assert [s["id"] for s in op["strokes"]] == ["ok"]


def test_hub_board_switching(tmp_path):
    async def main():
        hub = Hub(BoardStore(tmp_path))
        first = hub.current_id
        created = await hub.create_board()
        assert hub.current_id == created["id"]
        assert await hub.select_board(first) is True
        assert await hub.select_board(first) is False  # 已经是当前白板
        assert await hub.select_board("ghost") is False

    run(main())


def test_hub_saves_dirty_boards(tmp_path):
    store = BoardStore(tmp_path)
    hub = Hub(store)
    apply(hub.board(), {"op": "add", "strokes": [stroke("a")]})
    hub.save_all()
    _, strokes = BoardStore(tmp_path).load_board(hub.current_id)
    assert [s["id"] for s in strokes] == ["a"]


def test_hub_delete_falls_back_to_another_board(tmp_path):
    async def main():
        hub = Hub(BoardStore(tmp_path))
        first = hub.current_id
        await hub.create_board()
        assert await hub.delete_board(first)
        assert hub.current_id != first
        assert hub.store.get_meta(first) is None

    run(main())


def test_deleting_the_last_board_leaves_a_new_blank_one(tmp_path):
    async def main():
        hub = Hub(BoardStore(tmp_path))
        only = hub.current_id
        assert await hub.delete_board(only)
        assert hub.current_id != only and len(hub.store.list_metas()) == 1

    run(main())


def test_rename_loaded_board_survives_autosave(tmp_path):
    """已经在内存里的白板改名要经过内存：自动保存会拿内存里那份覆盖回去。"""

    async def main():
        store = BoardStore(tmp_path)
        hub = Hub(store)
        board_id = hub.current_id
        runtime = hub.board(board_id)  # 载入内存
        apply(runtime, {"op": "add", "strokes": [stroke("a")]})
        updated = runtime.meta["updated"]

        assert await hub.rename_board(board_id, "线性代数") is True
        assert store.get_meta(board_id)["name"] == "线性代数"  # 索引立刻就对
        assert runtime.meta["updated"] == updated  # 改名不算编辑
        hub.save_all()
        assert BoardStore(tmp_path).load_board(board_id)[0]["name"] == "线性代数"

    run(main())


def test_deleting_or_renaming_a_folder_reaches_loaded_boards(tmp_path):
    """改文件夹要把内存里那几块也改到，否则自动保存会把文件夹写回去。"""

    async def main():
        store = BoardStore(tmp_path)
        hub = Hub(store)
        board_id = hub.current_id
        apply(hub.board(board_id), {"op": "add", "strokes": [stroke("a")]})  # 载入内存
        await hub.move_board(board_id, "数学")

        assert await hub.rename_folder("数学", "线性代数") is True
        assert await hub.rename_folder("线性代数", "线性代数") is False
        hub.save_all()
        assert models.folder_of(BoardStore(tmp_path).load_board(board_id)[0]) == "线性代数"

        assert await hub.delete_folder("线性代数") is True
        assert hub.store.folders() == []
        hub.save_all()
        assert models.folder_of(BoardStore(tmp_path).load_board(board_id)[0]) == ""

    run(main())


def test_moving_a_loaded_board_into_a_folder_survives_autosave(tmp_path):
    """和改名一样：内存里那块要经过内存改，否则自动保存会把文件夹覆盖掉。"""

    async def main():
        store = BoardStore(tmp_path)
        hub = Hub(store)
        board_id = hub.current_id
        apply(hub.board(board_id), {"op": "add", "strokes": [stroke("a")]})

        assert await hub.move_board(board_id, "数学") is True
        assert models.folder_of(store.get_meta(board_id)) == "数学"  # 索引立刻就对
        hub.save_all()
        assert models.folder_of(BoardStore(tmp_path).load_board(board_id)[0]) == "数学"

        assert await hub.move_board(board_id, "") is True
        hub.save_all()
        assert "folder" not in BoardStore(tmp_path).load_board(board_id)[0]["data"]

    run(main())


def test_rename_board_that_is_not_loaded(tmp_path):
    async def main():
        store = BoardStore(tmp_path)
        hub = Hub(store)
        other = store.create_board()
        assert hub.core.runtime(other["id"]) is None
        assert await hub.rename_board(other["id"], "第二块") is True
        assert BoardStore(tmp_path).get_meta(other["id"])["name"] == "第二块"
        assert await hub.rename_board(other["id"], "第二块") is False

    run(main())

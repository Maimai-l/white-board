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


def test_add_assigns_increasing_order():
    runtime = make_runtime()
    op = runtime.apply({"op": "add", "strokes": [stroke("a"), stroke("b")]})
    assert op["seq"] == 1
    assert [s["n"] for s in op["strokes"]] == [0, 1]


def test_duplicate_id_is_ignored():
    runtime = make_runtime()
    runtime.apply({"op": "add", "strokes": [stroke("a")]})
    assert runtime.apply({"op": "add", "strokes": [stroke("a")]}) is None
    assert len(runtime.strokes) == 1


def test_remove_and_restore_keeps_z_order():
    runtime = make_runtime()
    runtime.apply({"op": "add", "strokes": [stroke("a"), stroke("b"), stroke("c")]})
    runtime.apply({"op": "remove", "ids": ["b"]})
    assert [s["id"] for s in runtime.stroke_list()] == ["a", "c"]
    runtime.apply({"op": "restore", "strokes": [stroke("b", n=1)]})
    assert [s["id"] for s in runtime.stroke_list()] == ["a", "b", "c"]


def test_noop_operations_return_none():
    runtime = make_runtime()
    assert runtime.apply({"op": "clear"}) is None
    assert runtime.apply({"op": "remove", "ids": ["ghost"]}) is None
    assert runtime.apply({"op": "nonsense"}) is None
    assert runtime.apply("not a dict") is None


def test_clear_empties_board():
    runtime = make_runtime()
    runtime.apply({"op": "add", "strokes": [stroke("a")]})
    assert runtime.apply({"op": "clear"})["op"] == "clear"
    assert runtime.stroke_list() == []


def test_meta_op_is_sanitized_and_deduplicated():
    runtime = make_runtime()
    op = runtime.apply({"op": "meta", "meta": {"background": "lines", "name": "笔记"}})
    assert op["meta"]["background"] == "lines"
    assert op["meta"]["name"] == "笔记"
    # 非法取值回落到默认背景，而不是被忽略
    assert runtime.apply({"op": "meta", "meta": {"background": "nope"}})["meta"]["background"] == "grid"
    assert runtime.apply({"op": "meta", "meta": {"background": "grid"}}) is None


def test_ops_since_returns_delta_or_none():
    runtime = make_runtime()
    for i in range(5):
        runtime.apply({"op": "add", "strokes": [stroke(f"s{i}")]})
    assert runtime.ops_since(5) == []
    assert [op["seq"] for op in runtime.ops_since(3)] == [4, 5]
    runtime.ops.clear()  # 模拟历史窗口滑走
    assert runtime.ops_since(1) is None


def test_invalid_stroke_does_not_break_the_batch():
    runtime = make_runtime()
    op = runtime.apply({"op": "add", "strokes": [{"id": "bad id"}, stroke("ok")]})
    assert [s["id"] for s in op["strokes"]] == ["ok"]


def test_hub_board_switching(tmp_path):
    hub = Hub(BoardStore(tmp_path))
    first = hub.current_id
    created = hub.create_board()
    assert hub.current_id == created["id"]
    assert hub.select_board(first) is True
    assert hub.select_board(first) is False  # 已经是当前白板
    assert hub.select_board("ghost") is False


def test_hub_saves_dirty_boards(tmp_path):
    store = BoardStore(tmp_path)
    hub = Hub(store)
    hub.board().apply({"op": "add", "strokes": [stroke("a")]})
    hub.save_all()
    _, strokes = BoardStore(tmp_path).load_board(hub.current_id)
    assert [s["id"] for s in strokes] == ["a"]


def test_hub_delete_falls_back_to_another_board(tmp_path):
    hub = Hub(BoardStore(tmp_path))
    first = hub.current_id
    hub.create_board()
    assert hub.delete_board(first)
    assert hub.current_id != first
    assert hub.store.get_meta(first) is None

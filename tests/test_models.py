from whiteboard import models


def test_meta_is_clamped():
    meta = models.sanitize_meta({"id": "abc", "cols": 99, "rows": -3, "background": "hack"})
    assert meta["cols"] == models.MAX_GRID
    assert meta["rows"] == models.MIN_GRID
    assert meta["background"] == "grid"


def test_board_size_uses_unit():
    meta = models.new_board_meta(cols=2, rows=1)
    assert models.board_size(meta) == (2 * models.UNIT_W, models.UNIT_H)


def test_stroke_validation():
    good = models.sanitize_stroke(
        {"id": "a-1", "tool": "marker", "color": "#ABCDEF", "w": 4, "p": [0, 0, 0.5, 1, 1, 0.5]}
    )
    assert good["tool"] == "marker" and good["color"] == "#ABCDEF"
    assert models.sanitize_stroke({"id": "a b", "p": [0, 0, 0.5]}) is None
    assert models.sanitize_stroke({"id": "a", "p": [0, 0]}) is None
    assert models.sanitize_stroke({"id": "a", "p": [0, 0, float("nan")]}) is None
    assert models.sanitize_stroke("nope") is None


def test_stroke_defaults_and_limits():
    stroke = models.sanitize_stroke({"id": "a", "tool": "x", "w": 1e6, "p": [0, 0, 2]})
    assert stroke["tool"] == "pen"
    assert stroke["w"] == models.MAX_WIDTH
    assert stroke["color"].startswith("#")


def test_sanitize_ids_filters():
    assert models.sanitize_ids(["ok-1", 3, "bad id", None]) == ["ok-1"]
    assert models.sanitize_ids("nope") == []

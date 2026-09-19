from whiteboard import models


def test_meta_is_sanitized():
    meta = models.sanitize_meta({"id": "abc", "background": "hack", "name": 5})
    assert meta["background"] == "grid"
    assert meta["name"] == ""
    assert meta["id"] == "abc"


def test_meta_drops_legacy_board_size():
    """老文件里可能还留着 cols / rows / unit：画布已经无限，直接丢掉。"""
    meta = models.sanitize_meta({"id": "abc", "cols": 3, "rows": 3, "unit": [1180, 820]})
    assert "cols" not in meta and "rows" not in meta and "unit" not in meta


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

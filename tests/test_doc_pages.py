"""文档板插入、删除空白页时元数据和笔迹怎么变（whiteboard/doc_pages.py）。"""

from __future__ import annotations

import pytest

from whiteboard import doc_pages, models

GAP = models.DOC_PAGE_GAP


def doc_meta(pages, src=None):
    doc = {"type": "pdf", "name": "讲义.pdf", "ext": ".pdf", "pages": pages}
    if src is not None:
        doc["src"] = src
    clean = models.sanitize_doc(doc)
    return {
        "id": "board1",
        "name": "讲义",
        "canvas": models.doc_canvas(clean),
        "background": {"pattern": "grid"},
        "layers": models.doc_layers("board1", clean),
        "data": {"doc": clean, "folder": "物理"},
    }


def ink(stroke_id, y, mask_y=None):
    stroke = {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3, "p": [10, y, 0.5, 30, y + 10, 0.5]}
    if mask_y is not None:
        stroke["m"] = [[4, 12, mask_y, 20, mask_y + 4]]
    return stroke


def test_inserting_moves_ink_and_its_erased_parts_together():
    meta = doc_meta([[600, 800]] * 2)
    strokes = [ink("first", 100), ink("second", 800 + GAP + 100, mask_y=800 + GAP + 102)]
    new_meta, moved = doc_pages.insert_blank(meta, strokes, 0, after=True)
    dy = 800 + GAP
    assert moved[0] is strokes[0]  # 前面的页不动
    assert moved[1]["p"][1] == strokes[1]["p"][1] + dy and moved[1]["p"][4] == strokes[1]["p"][4] + dy
    assert moved[1]["m"][0][2] == strokes[1]["m"][0][2] + dy and moved[1]["m"][0][4] == strokes[1]["m"][0][4] + dy
    assert moved[1]["m"][0][1] == strokes[1]["m"][0][1]  # x 和半径不动
    assert new_meta["data"]["doc"]["src"] == [0, None, 1]
    assert new_meta["data"]["folder"] == "物理"  # 其余的元数据原样保留
    assert new_meta["canvas"] == {"mode": "fixed", "width": 600, "height": 3 * 800 + 2 * GAP}


def test_the_blank_page_takes_the_size_of_the_page_it_was_added_next_to():
    meta = doc_meta([[600, 800], [842, 595]])
    new_meta, _ = doc_pages.insert_blank(meta, [], 1, after=False)
    assert new_meta["data"]["doc"]["pages"] == [[600, 800], [842, 595], [842, 595]]
    assert new_meta["data"]["doc"]["src"] == [0, None, 1]


def test_ink_in_the_gap_belongs_to_the_nearest_page():
    meta = doc_meta([[600, 800]] * 2)
    near_second = ink("gap", 800 + GAP - 6)  # 空隙里、靠下一页
    _, moved = doc_pages.insert_blank(meta, [near_second], 0, after=True)
    assert moved[0]["p"][1] == near_second["p"][1] + 800 + GAP


def test_deleting_a_blank_page_drops_its_ink_and_only_blank_pages_go():
    meta = doc_meta([[600, 800]] * 3, src=[0, None, 1])
    on_blank = ink("blank", 800 + GAP + 50)
    after = ink("after", 2 * (800 + GAP) + 50)
    new_meta, kept = doc_pages.delete_blank(meta, [ink("first", 50), on_blank, after], 1)
    assert [s["id"] for s in kept] == ["first", "after"]
    assert kept[1]["p"][1] == after["p"][1] - (800 + GAP)
    assert "src" not in new_meta["data"]["doc"]
    with pytest.raises(doc_pages.PageError):
        doc_pages.delete_blank(meta, [], 0)  # 原件的页


def test_a_board_at_the_page_limit_takes_no_more_pages():
    meta = doc_meta([[100, 100]] * models.MAX_DOC_PAGES)
    with pytest.raises(doc_pages.PageError):
        doc_pages.insert_blank(meta, [], 0)


def test_old_boards_without_a_page_map_read_as_one_to_one():
    doc = models.sanitize_doc({"type": "pdf", "name": "a.pdf", "ext": ".pdf", "pages": [[1, 2], [3, 4]]})
    assert models.doc_sources(doc) == [0, 1]
    assert "src" not in doc
    layers = models.doc_layers("b", doc)
    assert [layer["src"] for layer in layers] == ["/api/doc/b/0?w={w}", "/api/doc/b/1?w={w}"]


def test_a_page_map_must_keep_at_least_one_original_page():
    assert models.sanitize_doc({"type": "pdf", "pages": [[1, 1]], "src": [None]}) is None
    assert models.sanitize_doc({"type": "pdf", "pages": [[1, 1]], "src": [-1]}) is None
    assert models.sanitize_doc({"type": "pdf", "pages": [[1, 1], [1, 1]], "src": [4, None]})["src"] == [4, None]

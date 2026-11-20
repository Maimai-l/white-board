"""导出分页（whiteboard/pagination.py）和导出参数（whiteboard/export.py）的白盒测试。"""

import random

import pytest

from whiteboard import export, pagination as pg

A4 = pg.PAPERS["a4"]
AW, AH = pg.content_area(A4)  # 竖放 A4 的内容区：523.28 × 769.89
SHEETS = {(round(A4[0], 2), round(A4[1], 2)), (round(A4[1], 2), round(A4[0], 2))}


def frame(page):
    return (page["x"], page["y"], page["x"] + page["w"], page["y"] + page["h"])


def inside(box, page, slack=1e-6):
    x0, y0, x1, y1 = frame(page)
    return x0 - slack <= box[0] and y0 - slack <= box[1] and box[2] <= x1 + slack and box[3] <= y1 + slack


def word(x, y, w=60.0, h=20.0):
    return (x, y, x + w, y + h)


# ------------------------------------------------------------------ 纸面

def test_placement_centres_horizontally_and_aligns_to_the_top():
    page = {"x": 0, "y": 0, "w": 100, "h": 100, "paper": [600, 800]}
    scale, left, top = pg.placement(page)
    assert scale == pytest.approx((600 - 2 * pg.MARGIN) / 100)
    assert left == pytest.approx(pg.MARGIN)
    assert top == pg.MARGIN
    tall = {"x": 0, "y": 0, "w": 100, "h": 400, "paper": [600, 800]}
    scale, left, top = pg.placement(tall)
    assert scale == pytest.approx((800 - 2 * pg.MARGIN) / 400)
    assert left == pytest.approx((600 - 100 * scale) / 2)
    assert pg.placement(dict(tall, margin=0))[2] == 0


def test_flow_pages_are_deterministic():
    rng = random.Random(11)
    boxes = [word(rng.uniform(-5000, 5000), rng.uniform(-5000, 5000)) for _ in range(200)]
    assert pg.flow_pages(boxes, A4) == pg.flow_pages(list(boxes), A4)


def test_empty_board_gets_one_blank_page():
    pages = pg.flow_pages([], A4)
    assert len(pages) == 1
    assert pages[0]["x"] == pytest.approx(-AW / 2, abs=1e-3)


def test_auto_pages_dispatches_by_kind():
    stroke = {"id": "a", "tool": "pen", "w": 4, "p": [10, 10, 0.5, 200, 10, 0.5]}
    infinite = pg.auto_pages({"canvas": {"mode": "infinite"}}, [stroke])
    # 大白板从上往下按段分（flow_pages），纸张竖放，一页至少一张纸的内容宽
    assert len(infinite) == 1 and infinite[0]["paper"] == [595.28, 841.89]
    assert infinite[0]["w"] == pytest.approx(AW, abs=1e-2)
    content_note = pg.auto_pages({"canvas": {"mode": "column", "width": 1000}}, [stroke], "content")
    assert content_note[0]["paper"] == [1000 + 2 * pg.MARGIN, round(content_note[0]["h"] + 2 * pg.MARGIN, 2)]
    note = pg.auto_pages({"canvas": {"mode": "column", "width": 1000}}, [stroke])
    assert note == [{"x": 0.0, "y": 0.0, "w": 1000.0, "h": pytest.approx(1000 * AH / AW, abs=1e-2), "paper": [595.28, 841.89]}]
    doc_meta = {"data": {"doc": {"type": "pdf", "pages": [[600, 800], [400, 500]]}}}
    doc = pg.auto_pages(doc_meta, [stroke])
    assert doc[1] == {"x": 100.0, "y": 824.0, "w": 400.0, "h": 500.0, "paper": [400.0, 500.0], "margin": 0.0}
    letter = pg.auto_pages({"canvas": {"mode": "infinite"}}, [stroke], "letter")
    assert sorted(letter[0]["paper"]) == [612.0, 792.0]


# ------------------------------------------------------------------ 笔记


def test_column_pages_cut_in_the_gap_nearest_the_paper_length():
    full = 1000 * AH / AW  # 1471.3
    lines = [(50, y, 900, y + 30) for y in range(0, 3000, 70)]
    pages = pg.column_pages(1000, lines, A4)
    assert len(pages) == 3
    for upper, lower in zip(pages, pages[1:]):
        cut = upper["y"] + upper["h"]
        assert lower["y"] == cut
        assert full * (1 - pg.NOTE_CUT_SEARCH) <= upper["h"] <= full
        assert not any(box[1] < cut < box[3] for box in lines)
    assert pages[-1]["h"] == pytest.approx(full, abs=1e-2)


def test_column_pages_cut_at_paper_length_when_there_is_no_gap():
    full = 1000 * AH / AW
    pages = pg.column_pages(1000, [(0, 0, 1000, 4000)], A4)
    assert [round(p["h"], 1) for p in pages[:-1]] == [round(full, 1)] * (len(pages) - 1)
    assert pages[-1]["y"] + pages[-1]["h"] >= 4000


def test_empty_note_gets_one_page():
    assert len(pg.column_pages(1000, [], A4)) == 1


def test_free_gaps():
    assert pg.free_gaps([(0, 0, 1, 10), (0, 5, 1, 20), (0, 30, 1, 40)]) == [(20, 30), (40, float("inf"))]


# ------------------------------------------------------------------ 纸张与参数


def test_paper_size():
    assert pg.paper_size("A4") == A4
    assert pg.paper_size([800, 600]) == (600.0, 800.0)
    for bad in ("a0", [10, 10], [600], None):
        with pytest.raises(ValueError):
            pg.paper_size(bad)


def test_parse_options_defaults_and_normalisation():
    options = export.parse_options({"format": "zip", "select": [3, 1, 3]})
    assert options.select == [1, 3]
    assert options.frames is None and options.dpi is None and options.background is True
    assert options.paper == A4
    custom = export.parse_options({"format": "pdf", "frames": [{"x": 0, "y": 0, "w": 300, "h": 100}]})
    assert custom.frames[0]["paper"][0] > custom.frames[0]["paper"][1]  # 宽的页框横放
    named = export.parse_options({"format": "pdf", "frames": [{"x": 0, "y": 0, "w": 300, "h": 100, "paper": "letter"}]})
    assert named.frames[0]["paper"] == [792.0, 612.0]


@pytest.mark.parametrize(
    "raw",
    [
        {"format": "pdf", "select": [True]},
        {"format": "pdf", "select": [-1]},
        {"format": "pdf", "dpi": "high"},
        {"format": "pdf", "background": "yes"},
        {"format": "pdf", "frames": [{"x": 0, "y": 0, "w": 10}]},
        {"format": "pdf", "frames": [{"x": 0, "y": 0, "w": 10, "h": 10, "margin": 500}]},
        {"format": "pdf", "frames": [{"x": float("nan"), "y": 0, "w": 10, "h": 10}]},
        {"format": "pdf", "frames": []},
        ["pdf"],
    ],
)
def test_parse_options_rejects(raw):
    with pytest.raises(export.ExportError):
        export.parse_options(raw)


def test_options_from_query():
    options = export.options_from_query({"format": "png", "select": "2", "dpi": "300", "background": "0"})
    assert options.select == [2] and options.dpi == 300 and options.background is False
    with pytest.raises(export.ExportError):
        export.options_from_query({"format": "pdf", "select": "a,b"})


def test_background_of_follows_the_renderer():
    assert export.background_of({}) == ("grid", "#ffffff")
    assert export.background_of({"background": "dots"}) == ("dots", "#ffffff")
    assert export.background_of({"background": {"pattern": "lines", "paper": "#1E1E1E"}}) == ("lines", "#1e1e1e")
    assert export.background_of({"background": {"pattern": "weird", "paper": "red"}}) == ("grid", "#ffffff")


def test_base_name_is_safe_for_files():
    assert export.base_name({"name": "a/b:c"}) == "a-b-c"
    assert export.base_name({"name": ""}).startswith("whiteboard-")
    doc = {"data": {"doc": {"type": "pdf", "name": "讲义.pdf", "ext": ".pdf", "pages": [[1, 1]]}}}
    assert export.base_name(doc) == "讲义-批注"


def test_raw_name_is_the_board_name_for_every_kind():
    assert export.raw_name({"name": "课堂/笔记"}) == "课堂-笔记.wbz"
    assert export.raw_name({"name": "讲义", "data": {"doc": {"type": "pdf", "name": "讲义.pdf"}}}) == "讲义.wbz"
    assert export.raw_name({}).startswith("whiteboard-") and export.raw_name({}).endswith(".wbz")


# ------------------------------------------------------------------ 整块


def stroke_at(x0, y0, x1, y1, w=10.0):
    return {"id": "s", "tool": "pen", "color": "#000000", "w": w, "p": [x0, y0, 0.5, x1, y1, 0.5]}


def test_whole_page_frames_all_ink_at_scale_one():
    page = pg.whole_page({}, [stroke_at(0, 0, 100, 0), stroke_at(-50.5, 300, 20, 400)])
    # 包围盒外扩半个笔宽再取整：x -55.5→-56，右边 105；y -5，405
    assert (page["x"], page["y"], page["w"], page["h"]) == (-56, -5, 161, 410)
    assert page["paper"] == [161 + 2 * pg.MARGIN, 410 + 2 * pg.MARGIN] and page["margin"] == pg.MARGIN
    assert pg.placement(page) == (1.0, pg.MARGIN, pg.MARGIN)


def test_whole_page_of_a_note_spans_the_note_width():
    meta = {"canvas": {"mode": "column", "width": 900}}
    page = pg.whole_page(meta, [stroke_at(100, 50, 200, 50)])
    assert (page["x"], page["w"], page["y"], page["h"]) == (0, 900, 45, 10)


def test_whole_page_shrinks_to_the_largest_paper():
    page = pg.whole_page({}, [stroke_at(0, 0, 100000, 0), stroke_at(0, 50000, 1, 50000)])
    assert max(page["paper"]) == pytest.approx(pg.MAX_PAPER, abs=0.01)
    scale, _left, _top = pg.placement(page)
    assert scale == pytest.approx(pg.MAX_PAPER / (page["w"] + 2 * pg.MARGIN), rel=1e-4)
    assert page["w"] * scale <= page["paper"][0] - 2 * page["margin"] + 0.01


def test_whole_page_of_an_empty_board_is_one_sheet():
    assert pg.whole_page({}, []) == pg.blank_page()


def test_whole_and_wbz_options():
    assert export.parse_options({"format": "png", "frames": "whole"}).frames == "whole"
    assert export.options_from_query({"format": "pdf", "frames": "whole"}).frames == "whole"
    with pytest.raises(export.ExportError):
        export.options_from_query({"format": "pdf", "frames": "some"})
    # 白板文件不看其余参数
    assert export.parse_options({"format": "wbz", "dpi": "ignored"}).format == "wbz"


def test_background_pattern_is_thinned_on_a_huge_frame():
    page = {"x": 0, "y": 0, "w": 14000, "h": 14000, "paper": [14000, 14000], "margin": 0}
    _xs, _ys, dots = export._pattern_lines(page, "dots", 1.0)
    assert 0 < len(dots) <= export.MAX_PATTERN_DOTS
    xs, _ys, _dots = export._pattern_lines(page, "grid", 1.0, pixels=0.05)
    assert (xs[1] - xs[0]) * 0.05 >= export.MIN_PATTERN_PX


# ------------------------------------------------------------------ 从上往下按段分页（backlog 010）

import json  # noqa: E402
from pathlib import Path  # noqa: E402

LONG_NOTES = json.loads(
    (Path(__file__).parent / "fixtures" / "layouts" / "long-notes.json").read_text("utf-8")
)["boxes"]


def _inside(box, page):
    return (page["x"] - 1e-6 <= box[0] and box[2] <= page["x"] + page["w"] + 1e-6
            and page["y"] - 1e-6 <= box[1] and box[3] <= page["y"] + page["h"] + 1e-6)


def test_flow_keeps_every_stroke_of_the_real_board_on_a_page():
    pages = pg.flow_pages(LONG_NOTES, "content")
    lost = [box for box in LONG_NOTES if not any(_inside(box, page) for page in pages)]
    assert lost == []


def test_flow_splits_the_real_board_into_a_few_pages_top_to_bottom():
    """用户的白板：第一版切成 83 页，按段往下切是 7 页，一页接一页往下、不重叠。"""
    pages = pg.flow_pages(LONG_NOTES, "content")
    assert len(pages) == 7
    for upper, lower in zip(pages, pages[1:]):
        assert upper["y"] + upper["h"] <= lower["y"] + 1e-6


def test_flow_pages_are_as_wide_as_their_own_section():
    narrow = [(0, 0, 300, 40), (0, 60, 280, 100)]
    wide = [(-900, 3000, 1500, 3040), (-900, 3060, 1400, 3100)]
    first, second = pg.flow_pages(narrow + wide, "content")
    assert first["w"] < 600 and first["y"] + first["h"] < 3000
    assert second["w"] == pytest.approx(2400 + 2 * pg.FLOW_PAD)


def test_flow_cuts_a_long_section_in_the_gap_between_lines():
    lines = [(0, y, 500, y + 30) for y in range(0, 3000, 50)]
    pages = pg.flow_pages(lines, "content")
    assert len(pages) > 1
    for page in pages[:-1]:
        cut = page["y"] + page["h"]
        assert not any(box[1] < cut < box[3] for box in lines)


def test_content_paper_is_the_frame_plus_margins():
    page = pg.flow_pages([(0, 0, 500, 300)], "content")[0]
    assert page["paper"] == [round(page["w"] + 2 * pg.MARGIN, 2), round(page["h"] + 2 * pg.MARGIN, 2)]


def test_fixed_paper_keeps_the_sheet_and_scales_the_frame():
    page = pg.flow_pages([(0, 0, 2000, 300)], pg.PAPERS["a4"])[0]
    assert page["paper"] == [595.28, 841.89]
    scale, _, _ = pg.placement(page)
    assert scale < 0.3


def test_auto_pages_on_an_infinite_board_flows_top_to_bottom():
    strokes = [{"p": [x, y, 0.5, x + 300, y + 20, 0.5]} for x, y in ((0, 0), (0, 3000))]
    pages = pg.auto_pages({"canvas": {"mode": "infinite"}}, strokes, "content")
    assert len(pages) == 2 and pages[0]["y"] < pages[1]["y"]

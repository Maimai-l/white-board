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


# ------------------------------------------------------------------ 聚组


def test_cluster_joins_near_strokes_and_keeps_far_ones_apart():
    boxes = [word(0, 0), word(80, 0), word(160, 30), word(1000, 0), word(1000, 60)]
    assert pg.cluster(boxes) == [[0, 1, 2], [3, 4]]


def test_cluster_gap_is_inclusive_on_both_axes():
    gap = pg.CLUSTER_GAP
    assert pg.cluster([word(0, 0), word(60 + gap, 0)]) == [[0, 1]]
    assert pg.cluster([word(0, 0), word(60 + gap + 1, 0)]) == [[0], [1]]
    # 斜着离开：两个方向都没超过 gap 才算近
    assert pg.cluster([word(0, 0), word(60 + gap - 1, 20 + gap - 1)]) == [[0, 1]]


def test_cluster_chains_through_neighbours():
    boxes = [word(i * 100, 0) for i in range(30)]
    assert pg.cluster(boxes) == [list(range(30))]


def test_cluster_does_not_depend_on_input_order():
    rng = random.Random(7)
    boxes = [word(rng.uniform(-3000, 3000), rng.uniform(-3000, 3000)) for _ in range(300)]
    shuffled = boxes[:]
    rng.shuffle(shuffled)

    def shapes(items):
        return sorted(tuple(sorted(items[i] for i in group)) for group in pg.cluster(items))

    assert shapes(boxes) == shapes(shuffled)


def test_pack_merges_small_groups_that_fit_one_page():
    boxes = [word(0, 0), word(300, 0), word(0, 400)]
    groups = pg.cluster(boxes)
    assert len(groups) == 3
    assert pg.pack(boxes, groups, A4) == [[0, 1, 2]]


def test_pack_keeps_groups_that_do_not_fit_together_apart():
    boxes = [word(0, 0), word(900, 0), word(0, 1200)]
    assert pg.pack(boxes, pg.cluster(boxes), A4) == [[0], [1], [2]]


# ------------------------------------------------------------------ 放进一页


def test_best_fit_turns_the_paper_to_the_shape_of_the_group():
    scale, sheet = pg.best_fit((0, 0, 700, 300), A4)
    assert scale == 1 and sheet[0] > sheet[1]  # 宽的横放
    scale, sheet = pg.best_fit((0, 0, 300, 700), A4)
    assert scale == 1 and sheet[0] < sheet[1]
    scale, sheet = pg.best_fit((0, 0, 100, 100), A4)
    assert scale == 1 and sheet[0] < sheet[1]  # 一样宽高时竖放


def test_best_fit_never_enlarges_and_shrinks_only_as_needed():
    scale, sheet = pg.best_fit((0, 0, 900, 500), A4)
    assert sheet[0] > sheet[1]
    assert scale == pytest.approx(min(AH / 900, AW / 500))
    assert pg.best_fit((0, 0, 10, 10), A4)[0] == 1


def test_a_group_that_fits_gets_one_page_centred_on_it():
    box = (100, 200, 400, 300)
    pages = pg.fit_group(box, A4)
    assert len(pages) == 1
    page = pages[0]
    assert inside(box, page)
    assert page["x"] + page["w"] / 2 == pytest.approx(250, abs=1e-3)
    assert page["y"] + page["h"] / 2 == pytest.approx(250, abs=1e-3)
    assert (page["w"], page["h"]) == (pytest.approx(AH, abs=1e-3), pytest.approx(AW, abs=1e-3))


def test_a_slightly_too_big_group_is_shrunk_onto_one_page():
    box = (0, 0, AH * 1.2, AW * 1.1)
    pages = pg.fit_group(box, A4)
    assert len(pages) == 1
    scale, _, _ = pg.placement(pages[0])
    assert pg.MIN_FIT_SCALE <= scale < 1
    assert inside(box, pages[0], slack=1e-2)


def test_a_huge_group_is_cut_into_a_grid_at_full_size():
    box = (0, 0, 3000, 1000)
    pages = pg.fit_group(box, A4)
    # 横放：3000 / 769.89 → 4 列，1000 / 523.28 → 2 行；竖放要 6 × 2 = 12 页
    assert len(pages) == 8
    for page in pages:
        assert pg.placement(page)[0] == pytest.approx(1)
        assert page["paper"][0] > page["paper"][1]
    # 网格铺满整组，页与页之间不重叠
    xs = sorted({page["x"] for page in pages})
    ys = sorted({page["y"] for page in pages})
    assert len(xs) == 4 and len(ys) == 2
    assert xs[0] <= 0 and xs[-1] + pages[0]["w"] >= 3000
    assert ys[0] <= 0 and ys[-1] + pages[0]["h"] >= 1000
    for a, b in zip(xs, xs[1:]):
        assert b - a == pytest.approx(pages[0]["w"], abs=1e-2)
    # 行优先：先排完第一行
    assert [page["y"] for page in pages[:4]] == [ys[0]] * 4


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


def test_settle_moves_the_frame_off_a_neighbour():
    own = (0, 0, 200, 100)
    page = pg.fit_group(own, A4)[0]
    neighbour = (page["x"] + page["w"] - 50, 0, page["x"] + page["w"] + 200, 80)
    assert pg.overlap_area(frame(page), neighbour) > 0
    moved = pg.settle(page, own, [neighbour])
    assert pg.overlap_area(frame(moved), neighbour) == 0
    assert inside(own, moved)


def test_settle_leaves_the_frame_alone_when_nothing_is_cut():
    own = (0, 0, 200, 100)
    page = pg.fit_group(own, A4)[0]
    assert pg.settle(page, own, [(5000, 5000, 5100, 5100)]) == page


def test_reading_order_is_rows_then_left_to_right():
    units = [(500, 0, 600, 100), (0, 10, 100, 110), (0, 500, 100, 600), (300, -20, 400, 80)]
    assert pg.reading_order(units) == [1, 3, 0, 2]


# ------------------------------------------------------------------ 大白板


def test_board_pages_keep_each_cluster_whole():
    rng = random.Random(3)
    clusters = []
    boxes = []
    for cx, cy in [(0, 0), (5000, 0), (0, 5000), (-7000, 3000)]:
        members = []
        for _ in range(25):
            x, y = cx + rng.uniform(-200, 150), cy + rng.uniform(-150, 100)
            members.append(len(boxes))
            boxes.append(word(x, y, 50, 18))
        clusters.append(members)
    pages = pg.board_pages(boxes, A4)
    assert len(pages) == 4
    for members in clusters:
        holders = {i for i, page in enumerate(pages) if all(inside(boxes[m], page) for m in members)}
        assert holders
    for page in pages:
        assert tuple(page["paper"]) in SHEETS


def test_board_pages_are_deterministic():
    rng = random.Random(11)
    boxes = [word(rng.uniform(-5000, 5000), rng.uniform(-5000, 5000)) for _ in range(200)]
    assert pg.board_pages(boxes, A4) == pg.board_pages(list(boxes), A4)


def test_empty_board_gets_one_blank_page():
    pages = pg.board_pages([], A4)
    assert len(pages) == 1
    assert pages[0]["x"] == pytest.approx(-AW / 2, abs=1e-3)


def test_auto_pages_dispatches_by_kind():
    stroke = {"id": "a", "tool": "pen", "w": 4, "p": [10, 10, 0.5, 200, 10, 0.5]}
    infinite = pg.auto_pages({"canvas": {"mode": "infinite"}}, [stroke])
    assert len(infinite) == 1 and infinite[0]["w"] == pytest.approx(AH, abs=1e-2)  # 横的一笔，横放
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

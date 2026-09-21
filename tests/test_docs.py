"""文档板（beta）：读原件、排版、归页与导出。"""

import io
import math

import pytest

from whiteboard import docs, inkpdf, models
from whiteboard.store import BoardStore

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")


def make_pdf(path, sizes=((595, 842), (595, 842), (400, 600)), rotations=None):
    writer = pypdf.PdfWriter()
    for index, (width, height) in enumerate(sizes):
        page = writer.add_blank_page(width=width, height=height)
        if rotations:
            page.rotation = rotations[index]
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def make_image(path, size=(800, 600), color=(240, 240, 240)):
    Image.new("RGB", size, color).save(path)
    return path


def stroke(points, tool="pen", color="#1b1b1f", width=3.0, stroke_id="s1"):
    flat = []
    for x, y in points:
        flat += [float(x), float(y), 0.7]
    return {"id": stroke_id, "tool": tool, "color": color, "w": width, "p": flat}


def wave(x0, y0, count=60, span=300, stroke_id="s"):
    return stroke(
        [(x0 + i * span / count, y0 + math.sin(i / 5) * 12) for i in range(count)],
        stroke_id=stroke_id,
    )


# ------------------------------------------------------------------ 读与排版


def test_probe_pdf(tmp_path):
    info = docs.probe(make_pdf(tmp_path / "a.pdf"))
    assert info["type"] == "pdf"
    assert [[round(w), round(h)] for w, h in info["pages"]] == [[595, 842], [595, 842], [400, 600]]


def test_probe_image(tmp_path):
    info = docs.probe(make_image(tmp_path / "a.png"))
    assert info["type"] == "image"
    assert info["pages"] == [[800.0, 600.0]]
    assert info["ext"] == ".png"


def test_probe_rejects_other_types(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("nope")
    with pytest.raises(docs.DocError):
        docs.probe(path)


def test_layout_centers_and_gaps():
    boxes = docs.layout([[600, 800], [400, 500]])
    assert boxes[0] == {"x": 0.0, "y": 0.0, "w": 600.0, "h": 800.0}
    assert boxes[1]["x"] == 100.0
    assert boxes[1]["y"] == 800.0 + docs.PAGE_GAP


def test_bounds_covers_all_pages():
    meta = {"doc": {"type": "pdf", "pages": [[600, 800], [400, 500]]}}
    assert docs.bounds(meta) == {
        "x0": 0.0,
        "x1": 600.0,
        "y0": 0.0,
        "y1": 800.0 + docs.PAGE_GAP + 500.0,
    }


def test_group_by_page_uses_nearest_page():
    boxes = docs.layout([[600, 800], [600, 800]])
    on_first = wave(50, 100, stroke_id="a")
    on_second = wave(50, boxes[1]["y"] + 100, stroke_id="b")
    in_gap = wave(50, boxes[0]["h"] + docs.PAGE_GAP / 2, stroke_id="c")
    groups = docs.group_by_page([on_first, on_second, in_gap], boxes)
    assert [s["id"] for s in groups[0]] == ["a", "c"]
    assert [s["id"] for s in groups[1]] == ["b"]


# ------------------------------------------------------------------ 几何编码


def test_simplify_drops_redundant_points():
    line = [(x, 0.0, 0.5) for x in range(100)]
    assert len(inkpdf.simplify(line, 0.35)) == 2


def test_simplify_keeps_shape():
    curve = [(x, math.sin(x / 4) * 20, 0.5) for x in range(100)]
    kept = inkpdf.simplify(curve, 0.35)
    assert 10 < len(kept) < len(curve)
    assert kept[0] == curve[0] and kept[-1] == curve[-1]


def test_outline_is_closed_band():
    points = [(0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (20.0, 0.0, 1.0)]
    poly = inkpdf.flatten(inkpdf.outline_path(points, "pen", 4.0))
    assert len(poly) >= 2 * len(points)
    ys = [y for _, y in poly]
    assert max(ys) > 0 > min(ys)  # 两侧都有


def test_every_stroke_is_one_filled_outline():
    """不透明的笔也走填充轮廓：分段描边会在线宽变化处露出一串圆饼。"""
    stroke = {"id": "p", "tool": "pen", "color": "#1b1b1f", "w": 9.0,
              "p": [20, 20, 0.2, 40, 30, 0.9, 60, 20, 0.35, 80, 32, 0.8]}
    raw, _ = inkpdf.content_stream([stroke])
    assert raw.count(b"h f\n") == 1  # 整笔只填一次
    assert b" S\n" not in raw and b" w\n" not in raw  # 没有描边、没有线宽
    assert b" c\n" in raw  # 两侧是曲线，不是折线


@pytest.mark.parametrize(
    "rotation, point, expected",
    [
        (0, (10, 20), (10, 822)),  # 左上角 → PDF 左上
        (90, (10, 20), (20, 10)),
        (180, (10, 20), (585, 20)),
        (270, (10, 20), (575, 832)),
    ],
)
def test_page_matrix_maps_display_corner(rotation, point, expected):
    box = pypdf.generic.RectangleObject([0, 0, 595, 842])
    matrix, _, _ = inkpdf.page_matrix(rotation, box)
    a, b, c, d, e, f = matrix
    x, y = point
    assert (round(a * x + c * y + e), round(b * x + d * y + f)) == expected


def test_page_matrix_reports_rotated_size():
    box = pypdf.generic.RectangleObject([0, 0, 595, 842])
    _, width, height = inkpdf.page_matrix(90, box)
    assert (round(width), round(height)) == (842, 595)


def test_content_stream_is_integer_coordinates():
    raw, alphas = inkpdf.content_stream([wave(20, 20)])
    assert alphas == {100}
    assert raw.startswith(b"q ") and raw.endswith(b"Q\n")
    points = [line for line in raw.split(b"\n") if line.endswith((b" m", b" l"))]
    assert points
    for line in points:  # 坐标经 cm 缩放后一律是整数，压缩率才上得去
        assert all(part.lstrip(b"-").isdigit() for part in line.split()[:2])


def test_highlighter_is_filled_once():
    raw, alphas = inkpdf.content_stream([wave(20, 20, stroke_id="h") | {"tool": "highlighter"}])
    assert alphas == {30}
    assert raw.count(b"f\n") == 1
    assert b"S\n" not in raw


# -------------------------------------------------------------------- 导出


def test_export_pdf_keeps_pages_and_adds_ink(tmp_path):
    src = make_pdf(tmp_path / "src.pdf")
    out = tmp_path / "out.pdf"
    boxes = docs.layout(docs.probe(src)["pages"])
    strokes = [wave(60, box["y"] + 100, stroke_id="p%d" % i) for i, box in enumerate(boxes)]
    docs.export_pdf(src, strokes, out)

    reader = pypdf.PdfReader(out)
    assert len(reader.pages) == 3
    for page in reader.pages:
        blob = b"".join(
            part.get_object().get_data() for part in page[pypdf.generic.NameObject("/Contents")]
        )
        assert b" l\n" in blob or b" m\n" in blob
        assert "/WBa100" in page["/Resources"]["/ExtGState"]


def test_export_pdf_without_strokes_does_not_grow(tmp_path):
    src = make_pdf(tmp_path / "src.pdf")
    out = tmp_path / "out.pdf"
    docs.export_pdf(src, [], out)
    assert out.stat().st_size <= src.stat().st_size + 1024
    assert len(pypdf.PdfReader(out).pages) == 3


def test_export_pdf_stays_small(tmp_path):
    """体积不能增加太多：一笔平均不超过 450 字节。

    这里的 wave 是 300pt 长、来回拐了十来次的一笔，比真写字要费。真机上用
    马克笔连写 800 笔测下来约每笔 370 字节。
    """
    src = make_pdf(tmp_path / "src.pdf")
    out = tmp_path / "out.pdf"
    boxes = docs.layout(docs.probe(src)["pages"])
    strokes = []
    for index in range(150):
        box = boxes[index % len(boxes)]
        strokes.append(wave(40, box["y"] + 40 + (index // 3) * 5, stroke_id="s%d" % index))
    docs.export_pdf(src, strokes, out)
    grew = out.stat().st_size - src.stat().st_size
    assert grew / len(strokes) < 450


def test_thick_strokes_simplify_harder(tmp_path):
    """粗笔的抽稀阈值跟着笔宽走，不然马克笔的点数会白白撑大体积。"""
    assert inkpdf.epsilon(3.0) == inkpdf.SIMPLIFY
    assert inkpdf.epsilon(33.8) == inkpdf.SIMPLIFY_MAX
    points = inkpdf.points_of(wave(40, 100))
    assert len(inkpdf.simplify(points, inkpdf.epsilon(33.8))) < len(
        inkpdf.simplify(points, inkpdf.epsilon(3.0))
    )


def test_export_image_draws_ink(tmp_path):
    src = make_image(tmp_path / "a.png", (400, 300), (255, 255, 255))
    out = tmp_path / "out.png"
    docs.export_image(src, [wave(50, 150, span=200, stroke_id="i")], out)
    with Image.open(out) as image:
        assert image.size == (400, 300)
        assert min(image.convert("L").tobytes()) < 128  # 真的画上去了


def test_export_image_without_strokes_matches_source(tmp_path):
    src = make_image(tmp_path / "a.png", (120, 90), (200, 210, 220))
    out = tmp_path / "out.png"
    docs.export_image(src, [], out)
    with Image.open(src) as a, Image.open(out) as b:
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()


def test_export_dispatches_by_type(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", sizes=((300, 400),))
    png = make_image(tmp_path / "a.png", (100, 100))
    assert docs.export(pdf, [], tmp_path / "o.pdf").exists()
    assert docs.export(png, [], tmp_path / "o.png").exists()


def test_default_export_name():
    meta = {"doc": {"type": "pdf", "name": "讲义.pdf", "ext": ".pdf", "pages": [[1, 1]]}}
    assert docs.default_export_name(meta) == "讲义-批注.pdf"


# -------------------------------------------------------------------- 渲染


def test_render_pdf_page(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    body, content_type = docs.render_page(src, 0, 400)
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(body)) as image:
        assert abs(image.width - 400) <= 1  # pdfium 会把宽度向上取整


def test_render_image_page(tmp_path):
    src = make_image(tmp_path / "a.png", (800, 400))
    body, content_type = docs.render_page(src, 0, 300)
    with Image.open(io.BytesIO(body)) as image:
        assert image.size == (300, 150)


def test_render_clamps_width(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", sizes=((595, 842),))
    body, _ = docs.render_page(src, 0, 99999)
    with Image.open(io.BytesIO(body)) as image:
        assert image.width == docs.MAX_RENDER_WIDTH


def test_render_rejects_bad_index(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", sizes=((595, 842),))
    with pytest.raises(docs.DocError):
        docs.render_page(src, 5, 400)


# -------------------------------------------------------------- 模型与存储


def test_meta_keeps_doc_info():
    meta = models.sanitize_meta(
        {
            "kind": "doc",
            "doc": {"type": "pdf", "name": "a.pdf", "ext": ".pdf", "pages": [[595, 842]]},
        }
    )
    assert meta["kind"] == "doc"
    assert meta["doc"]["pages"] == [[595.0, 842.0]]


def test_meta_without_doc_falls_back_to_board():
    assert models.sanitize_meta({"kind": "doc"})["kind"] == "board"
    assert models.sanitize_meta({"kind": "doc", "doc": {"type": "pdf", "pages": []}})["kind"] == "board"


def test_meta_drops_doc_on_plain_board():
    meta = models.sanitize_meta({"kind": "board", "doc": {"type": "pdf", "pages": [[1, 1]]}})
    assert "doc" not in meta


def test_store_import_and_delete(tmp_path):
    store = BoardStore(tmp_path / "data")
    data = make_pdf(tmp_path / "a.pdf").read_bytes()
    meta = store.import_doc(data, "讲义.pdf")
    assert meta["kind"] == "doc" and meta["name"] == "讲义"
    path = store.doc_path(meta["id"])
    assert path is not None and path.read_bytes() == data
    assert store.load_board(meta["id"])[0]["doc"]["pages"]

    store.delete_board(meta["id"])
    assert store.doc_path(meta["id"]) is None


def test_store_rejects_unsupported_file(tmp_path):
    store = BoardStore(tmp_path / "data")
    with pytest.raises(docs.DocError):
        store.import_doc(b"hello", "notes.txt")
    assert not list(store.docs_dir.iterdir())


def test_store_cleans_up_broken_file(tmp_path):
    store = BoardStore(tmp_path / "data")
    with pytest.raises(docs.DocError):
        store.import_doc(b"not really a pdf", "broken.pdf")
    assert not list(store.docs_dir.iterdir())


def test_page_geometry_reads_inherited_rotate():
    """/Rotate 与页面框写在 Pages 节点上时也要认，否则笔迹会落到旋转前的坐标上。"""
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

    parent = DictionaryObject()
    parent[NameObject("/Rotate")] = NumberObject(90)
    parent[NameObject("/MediaBox")] = ArrayObject([NumberObject(v) for v in (0, 0, 595, 842)])
    page = DictionaryObject()
    page[NameObject("/Parent")] = parent

    assert inkpdf.inherited(page, "/Rotate") == 90
    matrix, width, height = inkpdf.page_geometry(page)
    assert (round(width), round(height)) == (842, 595)
    assert matrix == [0.0, 1.0, 1.0, 0.0, 0.0, 0.0]


def test_page_geometry_falls_back_to_letter():
    from pypdf.generic import DictionaryObject

    _, width, height = inkpdf.page_geometry(DictionaryObject())
    assert (round(width), round(height)) == (612, 792)


def test_export_matches_rendered_orientation(tmp_path):
    """导出的坐标系必须和渲染出来的页面一致（各个 /Rotate 都要对得上）。"""
    import pypdfium2 as pdfium

    src = make_pdf(
        tmp_path / "a.pdf",
        sizes=((595, 842), (595, 842), (595, 842), (595, 842)),
        rotations=(0, 90, 180, 270),
    )
    boxes = docs.layout(docs.probe(src)["pages"])
    strokes = []
    for index, box in enumerate(boxes):
        # 每页左上角画一小段，渲染出来应该也在左上角
        points = [(box["x"] + 20 + i * 4, box["y"] + 20) for i in range(20)]
        strokes.append(stroke(points, width=8.0, stroke_id="r%d" % index))
    out = tmp_path / "out.pdf"
    docs.export_pdf(src, strokes, out)

    pdf = pdfium.PdfDocument(str(out))
    try:
        for index in range(4):
            page = pdf[index]
            image = page.render(scale=0.5).to_pil().convert("L")
            width, height = image.size
            corner = image.crop((0, 0, width // 3, height // 4))
            rest = image.crop((width // 2, height // 2, width, height))
            assert min(corner.tobytes()) < 100, f"第 {index} 页左上角没有笔迹"
            assert min(rest.tobytes()) > 200, f"第 {index} 页笔迹跑到别处去了"
    finally:
        pdf.close()

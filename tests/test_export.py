"""分页导出（docs/backlog/001-export-redesign.md）：只走 HTTP 和 WebSocket，像用户一样。

笔画从 WebSocket 画上去，导出走 ``/api/export/{board}``，结果用 pdfium 渲染出来再看
像素：笔有多粗、哪一页上有哪种颜色。
"""

import asyncio
import io
import socket
import zipfile
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from whiteboard.config import Config
from whiteboard.server import CONFIG_KEY, HUB_KEY, create_app
from whiteboard.store import BoardStore

pypdf = pytest.importorskip("pypdf")
pdfium = pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")

COLORS = ["#d01010", "#10a010", "#1030d0", "#d0a000", "#a000c0"]


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def make_client(tmp_path):
    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    app = create_app(config, BoardStore(config.data_dir))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


@pytest.fixture
def remote(monkeypatch):
    """把连接伪装成局域网上的别的设备（同 tests/test_server.py）。"""
    monkeypatch.setattr("whiteboard.server.netinfo.is_own_address", lambda remote: False)


async def connect(client, board=None):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"t": "hello", "role": "ipad", "client": "ipad-1", "board": board, "since": 0})
    init = await ws.receive_json()
    assert init["t"] == "init"
    return ws, init["board"]["id"]


async def until(ws, kind):
    while True:
        msg = await ws.receive_json()
        if msg["t"] == kind:
            return msg


async def draw(ws, strokes):
    await ws.send_json({"t": "op", "cid": "c-" + strokes[0]["id"], "op": {"op": "add", "strokes": strokes}})
    await until(ws, "ack")


def line(stroke_id, x0, y0, x1, y1, color="#1b1b1f", width=12.0, tool="marker", steps=12, pressure=0.5):
    """一条直线。马克笔的粗细不随压感变，量得出准确的宽度。"""
    flat = []
    for i in range(steps + 1):
        t = i / steps
        flat += [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, pressure]
    return {"id": stroke_id, "tool": tool, "color": color, "w": width, "p": flat}


def scribble(prefix, x, y, color, rows=4):
    """一小段「字」：几行短横线，行距 40，横向 300 宽。"""
    return [line(f"{prefix}{r}", x, y + r * 40, x + 300, y + r * 40, color=color, width=4.0) for r in range(rows)]


async def export(client, board_id, **body):
    response = await client.post(f"/api/export/{board_id}", json=body)
    return response


def pdf_images(body, scale=1.0):
    """PDF 每页渲染成 RGB 图；scale=1 时一个像素就是 1 pt。"""
    pdf = pdfium.PdfDocument(body)
    try:
        images = []
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil().convert("RGB").copy())
            bitmap.close()
            page.close()
        return images
    finally:
        pdf.close()


def rgb(color):
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def count_color(image, color, tolerance=30):
    """每个通道都和 ``color`` 相差不超过 ``tolerance`` 的像素数。"""
    from PIL import ImageChops

    masks = [
        channel.point(lambda v, t=target: 255 if abs(v - t) <= tolerance else 0)
        for channel, target in zip(image.convert("RGB").split(), rgb(color))
    ]
    mask = ImageChops.multiply(ImageChops.multiply(masks[0], masks[1]), masks[2])
    return mask.histogram()[255]


def dark_run_through_middle(image):
    """深色墨迹包围盒的中间那一列上，连续深色像素有多少（横线的粗细）。"""
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    dark = [(x, y) for y in range(height) for x in range(width) if pixels[x, y] < 128]
    assert dark, "page has no ink"
    xs = [x for x, _ in dark]
    middle = (min(xs) + max(xs)) // 2
    column = [y for y in range(height) if pixels[middle, y] < 128]
    return len(column)


# ------------------------------------------------------------------ 验收


def test_a_spread_out_board_exports_strokes_at_full_width(tmp_path):
    """笔画分散的大白板导出 PDF：每页上的笔宽（1 pt = 1 像素）和 100% 缩放时画出来的一样。

    以前只有前端导出 PNG，长边限 4096 像素，内容铺得越开笔就被缩得越细。
    """

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            # 上下隔得很远的四组：从上往下分页，各占一页
            spots = [(0, 0), (0, 7000), (0, 14000), (0, -6000)]
            await draw(ws, [line(f"l{i}", x, y, x + 400, y) for i, (x, y) in enumerate(spots)])
            response = await export(client, board_id, format="pdf")
            assert response.status == 200, await response.text()
            assert response.content_type == "application/pdf"
            body = await response.read()
            await ws.close()
        return body

    body = run(main())
    images = pdf_images(body)
    assert len(images) == 4
    for image in images:
        assert abs(dark_run_through_middle(image) - 12) <= 1.5


def test_selecting_pages_two_and_four_exports_exactly_those(tmp_path):
    """选第 2、4 页导出：PDF 只有两页，各自带着自己那一组的墨迹。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            for index, color in enumerate(COLORS):
                await draw(ws, scribble(f"g{index}-", 0, index * 4000, color))
            pages = await (await client.get(f"/api/export/{board_id}/pages")).json()
            response = await export(client, board_id, format="pdf", select=[1, 3])
            assert response.status == 200, await response.text()
            body = await response.read()
            await ws.close()
        return pages["pages"], body

    pages, body = run(main())
    assert len(pages) == 5

    def color_on(page):
        for index, color in enumerate(COLORS):
            x, y = 150, index * 4000 + 60
            if page["x"] <= x <= page["x"] + page["w"] and page["y"] <= y <= page["y"] + page["h"]:
                return color
        raise AssertionError(f"no group inside {page}")

    expected = [color_on(pages[1]), color_on(pages[3])]
    images = pdf_images(body)
    assert len(images) == 2
    for image, color in zip(images, expected):
        assert count_color(image, color) > 200
        for other in COLORS:
            if other != color:
                assert count_color(image, other) == 0


def test_auto_pagination_does_not_split_a_tight_cluster(tmp_path):
    """一组挨得很近的笔画整组落在同一页里；离得很远的另一组单独一页。

    这一组跨过原点，按固定网格从原点切页的话会被切成四块。
    """
    cluster = []
    for row in range(8):
        for col in range(3):
            x, y = -280 + col * 190, -190 + row * 50
            cluster.append(line(f"c{row}-{col}", x, y, x + 170, y, width=4.0, tool="pen"))
    far = scribble("far-", 12000, 9000, "#1030d0")

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, cluster)
            await draw(ws, far)
            pages = (await (await client.get(f"/api/export/{board_id}/pages")).json())["pages"]
            response = await export(client, board_id, format="pdf")
            body = await response.read()
            await ws.close()
        return pages, body

    pages, body = run(main())

    def holder(stroke):
        xs, ys = stroke["p"][0::3], stroke["p"][1::3]
        r = stroke["w"] / 2
        inside = [
            index
            for index, page in enumerate(pages)
            if page["x"] <= min(xs) - r
            and max(xs) + r <= page["x"] + page["w"]
            and page["y"] <= min(ys) - r
            and max(ys) + r <= page["y"] + page["h"]
        ]
        assert inside, f"stroke {stroke['id']} is cut by every page"
        return inside[0]

    assert len(pages) == 2
    assert {holder(s) for s in cluster} == {0}
    assert {holder(s) for s in far} == {1}
    assert len(pdf_images(body, scale=0.25)) == 2


def test_png_zip_holds_one_file_per_selected_page_in_page_order(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            for index, color in enumerate(COLORS[:4]):
                await draw(ws, scribble(f"g{index}-", 0, index * 5000, color))
            await ws.send_json({"t": "op", "cid": "name", "op": {"op": "meta", "meta": {"name": "周会"}}})
            await until(ws, "ack")
            response = await export(client, board_id, format="zip", select=[3, 0, 2], dpi=72)
            assert response.status == 200, await response.text()
            assert response.content_type == "application/zip"
            disposition = response.headers["Content-Disposition"]
            body = await response.read()
            await ws.close()
        return disposition, body

    disposition, body = run(main())
    assert disposition.endswith("%E5%91%A8%E4%BC%9A.zip")  # 周会.zip
    archive = zipfile.ZipFile(io.BytesIO(body))
    names = archive.namelist()
    assert names == ["周会-01.png", "周会-03.png", "周会-04.png"]
    for name in names:
        image = Image.open(io.BytesIO(archive.read(name)))
        assert image.format == "PNG"
        assert sorted(image.size) == [595, 842]  # A4 在 72 dpi


# ------------------------------------------------------------------ 其余行为


def test_png_page_is_sharp_at_the_requested_resolution(tmp_path):
    """单张 PNG：144 dpi 时 12 宽的笔是 24 像素，不会因为白板大而变细。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, [line("a", 0, 0, 400, 0), line("b", 20000, 15000, 20400, 15000)])
            response = await export(client, board_id, format="png", select=[1], dpi=144)
            assert response.status == 200, await response.text()
            assert response.content_type == "image/png"
            body = await response.read()
            several = await export(client, board_id, format="png")
            assert several.status == 400
            await ws.close()
        return body

    image = Image.open(io.BytesIO(run(main()))).convert("RGB")
    assert sorted(image.size) == [1191, 1684]
    assert abs(dark_run_through_middle(image) - 24) <= 2


def test_pages_endpoint_previews_auto_pages(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, [line("a", 0, 0, 300, 0)])
            response = await client.get(f"/api/export/{board_id}/pages")
            assert response.status == 200
            payload = await response.json()
            letter = await (await client.get(f"/api/export/{board_id}/pages?paper=letter")).json()
            bad = await client.get(f"/api/export/{board_id}/pages?paper=a0")
            assert bad.status == 400
            assert (await client.get("/api/export/nope/pages")).status == 404
            await ws.close()
        return payload, letter

    payload, letter = run(main())
    assert payload["kind"] == "board"
    assert payload["paper"] == [595.28, 841.89]
    assert len(payload["pages"]) == 1
    page = payload["pages"][0]
    assert page["x"] < 0 and page["x"] + page["w"] > 300
    assert set(page) >= {"x", "y", "w", "h", "paper"}
    assert sorted(letter["pages"][0]["paper"]) == [612.0, 792.0]


def test_note_pages_are_cut_by_paper_length_between_lines(tmp_path):
    """笔记按纸张长度往下切，每页和笔记一样宽，切口不穿过任何一行字。"""
    lines = [line(f"n{i}", 60, 50 + i * 55, 900, 50 + i * 55, width=4.0, tool="pen") for i in range(70)]

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, _ = await connect(client)
            await ws.send_json({"t": "newboard", "kind": "note"})
            board_id = (await until(ws, "switch"))["board"]["id"]
            await draw(ws, lines)
            payload = await (await client.get(f"/api/export/{board_id}/pages")).json()
            response = await export(client, board_id, format="pdf")
            body = await response.read()
            await ws.close()
        return payload, body

    payload, body = run(main())
    pages = payload["pages"]
    assert payload["kind"] == "note"
    assert len(pages) >= 2
    assert all(page["x"] == 0 and page["w"] == 1000 for page in pages)
    for upper, lower in zip(pages, pages[1:]):
        cut = upper["y"] + upper["h"]
        assert lower["y"] == pytest.approx(cut)
        for stroke in lines:
            y = stroke["p"][1]
            assert not (y - 2 < cut < y + 2), f"page break at {cut} cuts the line at {y}"
    assert len(pdf_images(body, scale=0.25)) == len(pages)


def test_adjusted_frames_are_exported_as_given(tmp_path):
    """对话框里调整过的页框：给几个就导出几页，框外的墨迹不画。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, [line("r", 0, 0, 200, 0, color=COLORS[0]), line("b", 600, 0, 800, 0, color=COLORS[2])])
            frames = [{"x": -100, "y": -300, "w": 400, "h": 600}, {"x": -100, "y": -300, "w": 1000, "h": 600}]
            response = await export(client, board_id, format="pdf", frames=frames)
            assert response.status == 200, await response.text()
            body = await response.read()
            await ws.close()
        return body

    narrow, wide = pdf_images(run(main()))
    assert count_color(narrow, COLORS[0]) > 100 and count_color(narrow, COLORS[2]) == 0
    assert count_color(wide, COLORS[0]) > 100 and count_color(wide, COLORS[2]) > 100
    assert narrow.size[0] < narrow.size[1] and wide.size[0] > wide.size[1]  # 横竖跟着页框走


def test_get_with_format_exports_like_post(tmp_path):
    """GET 带 ``format`` 也能导出（下载链接用）；不带时还是原来的文档板导出。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, scribble("a", 0, 0, COLORS[0]) + scribble("b", 0, 6000, COLORS[1]))
            response = await client.get(f"/api/export/{board_id}?format=pdf&select=1")
            assert response.status == 200, await response.text()
            body = await response.read()
            assert (await client.get(f"/api/export/{board_id}")).status == 404
            await ws.close()
        return body

    images = pdf_images(run(main()))
    assert len(images) == 1
    assert count_color(images[0], COLORS[1]) > 100 and count_color(images[0], COLORS[0]) == 0


def test_document_board_exports_only_the_selected_original_pages(tmp_path):
    source = io.BytesIO()
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=595, height=842)
    writer.write(source)

    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=source.getvalue())).json())["board"]
            ws, board_id = await connect(client, board=meta["id"])
            second_page_top = 842 + 24
            await draw(ws, [line("d", 100, second_page_top + 200, 400, second_page_top + 200, color=COLORS[0])])
            pages = (await (await client.get(f"/api/export/{board_id}/pages")).json())["pages"]
            pdf = await export(client, board_id, format="pdf", select=[1])
            assert pdf.status == 200, await pdf.text()
            assert "%E8%AE%B2%E4%B9%89-" in pdf.headers["Content-Disposition"]
            pdf_body = await pdf.read()
            png = await export(client, board_id, format="png", select=[1], dpi=72)
            assert png.status == 200, await png.text()
            png_body = await png.read()
            framed = await export(client, board_id, format="pdf", frames=[{"x": 0, "y": 0, "w": 10, "h": 10}])
            assert framed.status == 400
            assert (await export(client, board_id, format="png", frames="whole")).status == 400
            assert (await (await client.get(f"/api/export/{board_id}/pages")).json())["whole"] is None
            await ws.close()
        return pages, pdf_body, png_body

    pages, pdf_body, png_body = run(main())
    assert [(p["w"], p["h"]) for p in pages] == [(595, 842)] * 3
    images = pdf_images(pdf_body)
    assert len(images) == 1
    assert count_color(images[0], COLORS[0]) > 1000
    page_png = Image.open(io.BytesIO(png_body)).convert("RGB")
    assert page_png.size == (595, 842)
    assert count_color(page_png, COLORS[0]) > 1000


def test_a_document_board_with_a_blank_page_exports_its_own_pages(tmp_path):
    """插入了空白页的文档板：导出按白板上的页来，空白页是一页同样大的纸，上面的墨迹在它自己那页上，
    后面原件的页还是原件的内容。以前导出假定白板第几页就是原件第几页，墨迹会落到别的页上。"""
    source = io.BytesIO()
    writer = pypdf.PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=595, height=842)
    writer.write(source)

    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=讲义.pdf", data=source.getvalue())).json())["board"]
            board_id = meta["id"]
            inserted = await client.post(f"/api/board/{board_id}/pages", json={"action": "insert-after", "page": 0})
            assert inserted.status == 200, await inserted.text()
            ws, _ = await connect(client, board=board_id)
            step = 842 + 24
            await draw(ws, [line("blank", 100, step + 200, 400, step + 200, color=COLORS[0])])
            await draw(ws, [line("after", 100, 2 * step + 200, 400, 2 * step + 200, color=COLORS[1])])
            pdf = await export(client, board_id, format="pdf")
            assert pdf.status == 200, await pdf.text()
            pdf_body = await pdf.read()
            png = await export(client, board_id, format="png", select=[1], dpi=72)
            assert png.status == 200, await png.text()
            png_body = await png.read()
            legacy = await client.get(f"/api/export/{board_id}")
            assert legacy.status == 200, await legacy.text()
            legacy_body = await legacy.read()
            await ws.close()
        return pdf_body, png_body, legacy_body

    pdf_body, png_body, legacy_body = run(main())
    for body in (pdf_body, legacy_body):
        reader = pypdf.PdfReader(io.BytesIO(body))
        assert [tuple(round(float(v)) for v in page.mediabox[2:]) for page in reader.pages] == [(595, 842)] * 3
    images = pdf_images(pdf_body)
    assert count_color(images[0], COLORS[0]) == 0 and count_color(images[0], COLORS[1]) == 0
    assert count_color(images[1], COLORS[0]) > 1000 and count_color(images[1], COLORS[1]) == 0
    assert count_color(images[2], COLORS[1]) > 1000 and count_color(images[2], COLORS[0]) == 0
    blank_png = Image.open(io.BytesIO(png_body)).convert("RGB")
    assert blank_png.size == (595, 842)
    assert count_color(blank_png, COLORS[0]) > 1000


def test_image_document_exports_as_pdf_with_ink(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (240, 240, 240)).save(buffer, "PNG")

    async def main():
        async with make_client(tmp_path) as (client, _app):
            meta = (await (await client.post("/api/doc?name=板书.png", data=buffer.getvalue())).json())["board"]
            ws, board_id = await connect(client, board=meta["id"])
            await draw(ws, [line("d", 100, 300, 700, 300, color=COLORS[2])])
            response = await export(client, board_id, format="pdf")
            assert response.status == 200, await response.text()
            body = await response.read()
            await ws.close()
        return body

    images = pdf_images(run(main()))
    assert len(images) == 1 and images[0].size == (800, 600)
    assert images[0].getpixel((20, 20)) == (240, 240, 240)  # 原图还在
    assert count_color(images[0], COLORS[2]) > 3000


def test_bad_export_requests_are_rejected(tmp_path):
    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, [line("a", 0, 0, 300, 0)])
            for body in (
                {},
                {"format": "gif"},
                {"format": "pdf", "select": [5]},
                {"format": "pdf", "select": []},
                {"format": "pdf", "frames": "some"},
                {"format": "pdf", "frames": [{"x": 0, "y": 0, "w": -1, "h": 10}]},
                {"format": "zip", "dpi": 5000},
                {"format": "pdf", "paper": "a0"},
            ):
                response = await export(client, board_id, **body)
                assert response.status == 400, body
            assert (await client.post(f"/api/export/{board_id}", data=b"not json")).status == 400
            assert (await export(client, "nope", format="pdf")).status == 404
            await ws.close()

    run(main())


def test_export_needs_the_export_permission(tmp_path, remote):
    async def main():
        async with make_client(tmp_path) as (client, app):
            board_id = app[HUB_KEY].current_id
            assert (await client.get(f"/api/export/{board_id}/pages")).status == 403
            assert (await export(client, board_id, format="pdf")).status == 403
            assert (await client.get(f"/api/export/{board_id}?format=pdf")).status == 403
            assert (await export(client, board_id, format="wbz")).status == 403
            assert (await client.get(f"/api/export/{board_id}?format=wbz")).status == 403

            app[CONFIG_KEY].set_remote_permission("export", True)
            assert (await client.get(f"/api/export/{board_id}/pages")).status == 200
            assert (await export(client, board_id, format="pdf")).status == 200
            assert (await export(client, board_id, format="wbz")).status == 200

    run(main())


# ------------------------------------------------------------------ 白板文件、整块（docs/backlog/010-export-v2.md）


def stroke_fields(strokes):
    return [(s["id"], s["tool"], s["color"], s["w"], list(s["p"])) for s in strokes]


def test_board_file_download_is_current_and_reopens_with_the_same_strokes(tmp_path):
    """下载白板文件发给开发者：刚画的笔画（还没到自动保存）也在里面，导入成新白板后笔画一模一样。

    坐标取整数、压感取 1，存盘的量化不改变数值，所以能逐个比较。
    """
    first = [
        line("a", 0, 0, 400, 0, steps=4, pressure=1.0),
        line("b", 100, 50, 300, 250, color=COLORS[0], tool="pen", width=3.0, steps=4, pressure=1.0),
    ]
    later = line("c", -200, 600, 200, 600, color=COLORS[2], steps=4, pressure=1.0)

    async def main():
        async with make_client(tmp_path) as (client, app):
            hub = app[HUB_KEY]
            ws, board_id = await connect(client)
            await draw(ws, first)
            await hub.rename_board(board_id, "课堂/笔记")
            response = await export(client, board_id, format="wbz")
            assert response.status == 200, await response.text()
            assert response.content_type == "application/octet-stream"
            disposition = response.headers["Content-Disposition"]
            assert disposition == "attachment; filename*=UTF-8''%E8%AF%BE%E5%A0%82-%E7%AC%94%E8%AE%B0.wbz"
            downloads = [await response.read()]
            await draw(ws, [later])
            again = await client.get(f"/api/export/{board_id}?format=wbz")  # 下载链接用的 GET
            assert again.status == 200, await again.text()
            downloads.append(await again.read())
            original = await hub.strokes_of(board_id)

            outside = tmp_path / "outside"
            outside.mkdir()
            imported = []
            for index, body in enumerate(downloads):
                path = outside / f"{index}.wbz"
                path.write_bytes(body)
                meta = await hub.import_board_file(path)
                assert meta["id"] != board_id
                imported.append((meta["name"], await hub.strokes_of(meta["id"])))
            await ws.close()
        return original, imported

    original, imported = run(main())
    (name1, strokes1), (name2, strokes2) = imported
    assert name1 == name2 == "课堂/笔记"
    assert stroke_fields(strokes1) == stroke_fields(first)
    assert stroke_fields(strokes2) == stroke_fields(first + [later]) == stroke_fields(original)


def corner_lines():
    """四个角各一条颜色不同的横线，左边再远远地放一条。"""
    spots = [(0, 0), (3000, 0), (0, 2000), (3000, 2000), (-1500, 1000)]
    return [line(f"k{i}", x, y, x + 400, y, color=COLORS[i]) for i, (x, y) in enumerate(spots)], spots


def test_whole_board_png_is_one_image_of_all_ink(tmp_path):
    """整块导出 PNG：不分页，一张图框住全部墨迹，大小是（墨迹包围盒 + 四周留白）× 清晰度。"""
    strokes, spots = corner_lines()

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, strokes)
            whole = (await (await client.get(f"/api/export/{board_id}/pages")).json())["whole"]
            response = await export(client, board_id, format="png", frames="whole", dpi=36)
            assert response.status == 200, await response.text()
            assert response.headers["X-Export-Dpi"] == "36"
            body = await response.read()
            by_get = await client.get(f"/api/export/{board_id}?format=png&frames=whole&dpi=36")
            assert by_get.status == 200 and await by_get.read() == body
            await ws.close()
        return whole, body

    whole, body = run(main())
    # 包围盒是笔画外扩半个笔宽（6）再取整
    assert (whole["x"], whole["y"], whole["w"], whole["h"]) == (-1506, -6, 4912, 2012)
    assert whole["paper"] == [4912 + 72, 2012 + 72] and whole["margin"] == 36
    image = Image.open(io.BytesIO(body)).convert("RGB")
    k = 36 / 72
    assert abs(image.width - whole["paper"][0] * k) <= 1 and abs(image.height - whole["paper"][1] * k) <= 1
    for index, (x, y) in enumerate(spots):
        left = (x - whole["x"] + 36) * k
        top = (y - whole["y"] + 36) * k
        near = image.crop((int(left) - 4, int(top) - 8, int(left + 400 * k) + 4, int(top) + 8))
        assert count_color(near, COLORS[index]) > 100, (index, x, y)


def test_whole_board_pdf_is_one_page_the_size_of_the_ink(tmp_path):
    strokes, _spots = corner_lines()

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, strokes)
            response = await export(client, board_id, format="pdf", frames="whole")
            assert response.status == 200, await response.text()
            assert "X-Export-Dpi" not in response.headers
            body = await response.read()
            await ws.close()
        return body

    body = run(main())
    pages = pypdf.PdfReader(io.BytesIO(body)).pages
    assert len(pages) == 1
    box = pages[0].mediabox
    assert (round(float(box.width), 2), round(float(box.height), 2)) == (4912 + 72, 2012 + 72)
    image = pdf_images(body, scale=0.5)[0]
    for color in COLORS:
        assert count_color(image, color) > 100, color


def solid(prefix, x, y, color, size=3000):
    """一大块实心墨迹（最粗的笔一行挨一行）：缩到很小也看得出颜色。"""
    return [
        line(f"{prefix}{r}", x, y + r * 80, x + size, y + r * 80, color=color, width=96.0, steps=4)
        for r in range(size // 80 + 1)
    ]


def test_a_huge_board_is_shrunk_to_fit_instead_of_failing(tmp_path, monkeypatch):
    """二十万单位宽的白板整块导出：PNG 降清晰度到像素上限以内（响应头说实际的 dpi），
    PDF 的纸张缩到阅读器打得开的大小；两边的墨迹都在。"""
    monkeypatch.setattr("whiteboard.export.MAX_PAGE_PIXELS", 2_000_000)  # 上限调小，测试跑得快
    strokes = solid("p", 0, 0, COLORS[0]) + solid("q", 200000, 150000, COLORS[2])

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            for start in range(0, len(strokes), 20):
                await draw(ws, strokes[start : start + 20])
            png = await export(client, board_id, format="png", frames="whole", dpi=144)
            assert png.status == 200, await png.text()
            dpi = float(png.headers["X-Export-Dpi"])
            png_body = await png.read()
            pdf = await export(client, board_id, format="pdf", frames="whole")
            assert pdf.status == 200, await pdf.text()
            pdf_body = await pdf.read()
            await ws.close()
        return dpi, png_body, pdf_body

    dpi, png_body, pdf_body = run(main())
    image = Image.open(io.BytesIO(png_body)).convert("RGB")
    assert image.width * image.height <= 2_000_000 and dpi < 144
    assert image.width / image.height == pytest.approx((200000 + 3000) / (150000 + 3000), rel=0.02)
    assert count_color(image, COLORS[0]) > 20 and count_color(image, COLORS[2]) > 20
    pages = pypdf.PdfReader(io.BytesIO(pdf_body)).pages
    assert len(pages) == 1
    assert max(float(pages[0].mediabox.width), float(pages[0].mediabox.height)) == pytest.approx(14400, abs=0.5)
    page_image = pdf_images(pdf_body, scale=0.1)[0]
    assert count_color(page_image, COLORS[0]) > 20 and count_color(page_image, COLORS[2]) > 20


# ------------------------------------------------------------------ Mac 窗口


def test_the_mac_window_saves_the_dialogs_export_under_the_servers_name(tmp_path, monkeypatch):
    """Mac 窗口里导出走本地接口：把对话框的选择 POST 给本机服务端，存到保存对话框给的位置。"""
    from whiteboard.app import NativeApi
    from whiteboard.runner import ServerThread

    config = Config(path=tmp_path / "config.json")
    config.data_dir = tmp_path / "data"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        config.port = probe.getsockname()[1]
    server = ServerThread(config, advertise=False)
    server.start()
    try:
        server.hub.board().apply({"op": "add", "strokes": [line("a", 0, 0, 300, 0), line("b", 0, 9000, 300, 9000)]})
        api = NativeApi(config, server)
        asked = []

        def ask(suggested):
            asked.append(suggested)
            return str(tmp_path / suggested)

        monkeypatch.setattr(api, "_ask_save_path", ask)
        board_id = server.hub.current_id
        path = api.export_doc(board_id, {"format": "zip", "select": [0, 1], "dpi": 72})
        assert path == str(tmp_path / asked[0]) and asked[0].endswith(".zip")
        assert len(zipfile.ZipFile(path).namelist()) == 2
        assert api.export_doc(board_id, {"format": "png"}) is False  # 两页不能导出成单张 PNG

        monkeypatch.setattr(api, "_ask_save_path", lambda suggested: None)
        assert api.export_doc(board_id, {"format": "pdf"}) is None  # 用户取消
    finally:
        server.stop()


def test_content_paper_exports_each_page_at_its_own_size(tmp_path):
    """纸张「跟随内容」：自动分页的每页 PDF 就是那一段墨迹的大小（加四周留白），字不缩小。"""

    async def main():
        async with make_client(tmp_path) as (client, _app):
            ws, board_id = await connect(client)
            await draw(ws, [line("n", 0, 0, 300, 0), line("w", -1000, 5000, 1400, 5000)])
            pages = await (await client.get(f"/api/export/{board_id}/pages?paper=content")).json()
            response = await export(client, board_id, format="pdf", paper="content")
            assert response.status == 200, await response.text()
            body = await response.read()
            await ws.close()
        return pages, body

    pages, body = run(main())
    assert pages["paper"] == "content" and len(pages["pages"]) == 2
    sizes = [
        (round(float(p.mediabox.width)), round(float(p.mediabox.height)))
        for p in pypdf.PdfReader(io.BytesIO(body)).pages
    ]
    expected = [(round(p["paper"][0]), round(p["paper"][1])) for p in pages["pages"]]
    assert sizes == expected
    # 宽的那一段 2400 宽：纸宽就是 2400 + 两边留白，没有缩到 A4
    assert sizes[1][0] > 2400


def test_png_sizes_match_the_numbers_the_dialog_shows():
    """导出对话框按同一份数预告 PNG 的像素尺寸（tests/js/export-pages.test.mjs 也读这份）。"""
    import json
    from pathlib import Path

    from whiteboard import export

    cases = json.loads((Path(__file__).parent / "fixtures" / "png-sizes.json").read_text("utf-8"))["cases"]
    for case in cases:
        size, _ = export.png_size({"paper": case["paper"]}, case["dpi"])
        assert list(size) == case["size"], case
        assert size[0] * size[1] <= export.MAX_PAGE_PIXELS

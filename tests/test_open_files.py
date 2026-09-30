"""Finder「打开方式」交给应用的本地文件：PDF / 图片建文档板，.wbz 打开或导入。"""

from __future__ import annotations

import asyncio
import io
import json
import zlib
from pathlib import Path

import pytest

from whiteboard import models
from whiteboard.config import Config
from whiteboard.hub import Hub
from whiteboard.server import OpenFileError, open_local_file
from whiteboard.store import BoardStore


def make_hub(tmp_path: Path, name: str = "data") -> Hub:
    return Hub(BoardStore(tmp_path / name))


def run(hub: Hub, path: Path, folder: str = ""):
    return asyncio.run(open_local_file(hub, path, folder))


def stroke(stroke_id: str, x: float = 0.0):
    return {"id": stroke_id, "tool": "pen", "color": "#000000", "w": 3.0, "p": [x, 0.0, 0.5, x + 10, 0.0, 0.5]}


def test_an_image_opens_as_a_document_board_in_the_given_folder(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    hub = make_hub(tmp_path)
    hub.create_folder("课件")
    image = tmp_path / "照片.png"
    PIL.new("RGB", (40, 30), (200, 200, 200)).save(image)

    meta = run(hub, image, "课件")
    assert models.kind_of(meta) == "doc"
    assert meta["name"] == "照片"
    assert models.folder_of(meta) == "课件"
    assert hub.current_id == meta["id"]
    assert hub.store.doc_path(meta["id"]).read_bytes() == image.read_bytes()


def test_a_pdf_opens_as_a_document_board(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=400)
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf = tmp_path / "讲义.pdf"
    pdf.write_bytes(buffer.getvalue())

    meta = run(make_hub(tmp_path), pdf)
    assert models.kind_of(meta) == "doc"
    assert models.folder_of(meta) == ""  # 没给文件夹就放在最外层


def test_a_board_file_of_this_store_switches_to_that_board(tmp_path):
    hub = make_hub(tmp_path)
    first = hub.store.list_metas()[0]["id"]
    second = asyncio.run(hub.create_board())["id"]
    hub.save_all()
    count = len(hub.store.list_metas())

    meta = run(hub, hub.store.boards_dir / f"{first}.wbz")
    assert meta["id"] == first
    assert hub.current_id == first
    assert len(hub.store.list_metas()) == count  # 没有多出一块
    assert second != first


def test_a_board_file_from_elsewhere_is_imported_as_a_copy(tmp_path):
    source = make_hub(tmp_path, "other")
    board_id = source.current_id
    source.board().apply({"op": "add", "strokes": [stroke("s1")]})
    asyncio.run(source.move_board(board_id, "旧文件夹"))
    asyncio.run(source.rename_board(board_id, "周报"))
    source.save_all()
    backup = tmp_path / "备份.wbz"
    backup.write_bytes((source.store.boards_dir / f"{board_id}.wbz").read_bytes())

    hub = make_hub(tmp_path)
    hub.create_folder("导入")
    meta = run(hub, backup, "导入")
    assert meta["id"] != board_id
    assert meta["name"] == "周报"
    assert models.folder_of(meta) == "导入"  # 原文件里的文件夹不带过来
    assert hub.current_id == meta["id"]
    runtime = hub.board()
    assert [s["id"] for s in runtime.stroke_list()] == ["s1"]
    assert runtime.meta["id"] == meta["id"]
    assert runtime.problem is None
    assert backup.read_bytes() == (source.store.boards_dir / f"{board_id}.wbz").read_bytes()  # 原件不动


def test_a_board_file_written_by_a_newer_version_opens_read_only(tmp_path):
    source = make_hub(tmp_path, "other")
    source.board().apply({"op": "add", "strokes": [stroke("s1")]})
    source.save_all()
    path = source.store.boards_dir / f"{source.current_id}.wbz"
    payload = json.loads(zlib.decompress(path.read_bytes()))
    payload["v"] = 99
    payload["future"] = {"keep": True}
    newer = tmp_path / "newer.wbz"
    newer.write_bytes(zlib.compress(json.dumps(payload).encode()))

    hub = make_hub(tmp_path)
    meta = run(hub, newer)
    assert hub.board().problem == {"reason": "newer", "version": 99}
    stored = json.loads(zlib.decompress((hub.store.boards_dir / f"{meta['id']}.wbz").read_bytes()))
    assert stored["future"] == {"keep": True}  # 这一版不认识的内容原样保留


def test_a_corrupt_board_file_is_refused(tmp_path):
    broken = tmp_path / "broken.wbz"
    broken.write_bytes(b"not a board")
    hub = make_hub(tmp_path)
    count = len(hub.store.list_metas())
    with pytest.raises(OpenFileError, match="损坏"):
        run(hub, broken)
    assert len(hub.store.list_metas()) == count


def test_a_document_board_file_needs_its_original(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    source = make_hub(tmp_path, "other")
    image = tmp_path / "图.png"
    PIL.new("RGB", (40, 30), (255, 255, 255)).save(image)
    doc = asyncio.run(source.import_doc(image.read_bytes(), "图.png"))
    source.save_all()
    board_file = source.store.boards_dir / f"{doc['id']}.wbz"

    # 在原存储目录的 boards/ 旁边能找到 docs/ 里的原件
    hub = make_hub(tmp_path)
    meta = run(hub, board_file)
    assert models.kind_of(meta) == "doc"
    # 页面图片层指向新 id 的原件
    assert all(f"/api/doc/{meta['id']}/" in layer["src"] for layer in meta["layers"])
    assert hub.store.doc_path(meta["id"]).read_bytes() == image.read_bytes()

    # 单独拷出来的文件找不到原件：拒绝，不建一块打不开的文档板
    lonely = tmp_path / "lonely" / "doc.wbz"
    lonely.parent.mkdir()
    lonely.write_bytes(board_file.read_bytes())
    count = len(hub.store.list_metas())
    with pytest.raises(OpenFileError, match="原件"):
        run(hub, lonely)
    assert len(hub.store.list_metas()) == count


def test_unsupported_files_are_refused(tmp_path):
    text = tmp_path / "a.txt"
    text.write_text("hello")
    with pytest.raises(OpenFileError, match="不支持"):
        run(make_hub(tmp_path), text)


class _FakeServer:
    def __init__(self, fail=()):
        self.opened = []
        self.fail = set(fail)

    def open_file(self, path, folder=""):
        if path in self.fail:
            raise OpenFileError("坏了")
        self.opened.append((path, folder))
        return {}


class _FakeWindow:
    def __init__(self, folder="课件"):
        self.folder = folder
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)
        return self.folder if "dropFolder" in script else None


def _drain(opener):
    import threading
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        if not opener._pending and not [t for t in threading.enumerate() if t.name == "open-files"]:
            return
        time.sleep(0.01)


def test_files_opened_before_the_page_loads_wait_for_it(tmp_path):
    from whiteboard.app import FileOpener

    server = _FakeServer(fail={"/tmp/b.pdf"})
    opener = FileOpener(server)
    opener.window = _FakeWindow()
    opener.open(["/tmp/a.png", "/tmp/b.pdf", "/tmp/c.wbz"])
    assert server.opened == []  # 网页还没加载完，先排队

    opener.on_loaded()
    _drain(opener)
    assert server.opened == [("/tmp/a.png", "课件"), ("/tmp/c.wbz", "课件")]
    # 出错的那个给网页一条提示，不挡住后面的文件
    assert any("打不开 b.pdf：坏了" in script for script in opener.window.scripts)


def test_the_open_files_handler_is_only_installed_on_macos(monkeypatch):
    from whiteboard import app

    monkeypatch.setattr(app.sys, "platform", "linux")
    assert app.install_open_files_handler(lambda paths: None) is False


def test_the_app_declares_document_types_for_open_with():
    spec = (Path(__file__).resolve().parents[1] / "packaging" / "whiteboard.spec").read_text()
    assert '"public.filename-extension": ["wbz"]' in spec
    # PDF 和图片只作为备选，不抢系统默认的打开方式
    for uti in ("com.adobe.pdf", "public.png", "public.jpeg"):
        block = spec[: spec.index(f'"{uti}"')]
        assert block.rfind('"LSHandlerRank": "Alternate"') > block.rfind('"LSHandlerRank": "Owner"')

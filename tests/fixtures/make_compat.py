"""生成兼容性夹具 tests/fixtures/compat/。

这份夹具是用提交 0a1c6f9（稳定版整理开始之前）的代码生成的：``data/`` 是那一版
写出来的存储目录，``expected.json`` 是那一版打开之后报告的索引、读出来的内容、再存一遍写出的文件。
tests/test_compat.py 要求之后的代码对同一份文件读出一模一样的内容。

**不要用新代码重新生成**，否则就成了拿新代码去验证新代码。只有在有意改变
存储格式时才重新生成，并且要在提交说明里写清楚改了什么。

    python tests/fixtures/make_compat.py
"""

from __future__ import annotations

import json
import shutil
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from whiteboard import models  # noqa: E402
from whiteboard.store import BoardStore  # noqa: E402

OUT = Path(__file__).resolve().parent / "compat"


def stroke(sid, points, **extra):
    return {"id": sid, "tool": "pen", "color": "#1b1b1f", "w": 3.0, "p": points, "dev": "ipad", **extra}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    data = OUT / "data"
    store = BoardStore(data)
    automatic = [meta["id"] for meta in store.list_metas()]  # 空目录会自动建一块，最后删掉

    board = models.new_board_meta("大白板", id="compatboard01", background="grid", created=1700000000.0, updated=1700000100.0)
    board_strokes = [
        stroke("s-pen", [0, 0, 0.5, 10.25, -3.5, 0.62, 20.5, 4.75, 1.0], n=0),
        {**stroke("s-marker", [-500.5, 200, 0, -480, 210.125, 0, -460, 230, 0], n=1), "tool": "marker", "color": "#ff3b30", "w": 8.0},
        {**stroke("s-hl", [100, 100, 0.3, 300, 100, 0.3, 500, 100, 0.3], n=2), "tool": "highlighter", "color": "#ffcc00", "w": 24.0, "dev": "mac"},
        stroke("s-mask", [0, 50, 0.4, 50, 50, 0.4, 100, 50, 0.4, 150, 50, 0.4], n=3, m=[[6.0, 40, 50, 60, 50], [4.5, 90, 45, 95, 55, 100, 60]]),
        stroke("s-cut", [0, 80, 0.4, 30, 80, 0.4, 60, 80, 0.4], n=4, cut=1),
        stroke("s-big", [1e5, -1e5, 0.01, 100000.5, -99999.5, 0.99, 100001, -99999, 0.5], n=5),
        stroke("s-order", [1, 1, 0.5, 2, 2, 0.5, 3, 3, 0.5], n=9),
        stroke("s-order2", [4, 4, 0.5, 5, 5, 0.5, 6, 6, 0.5], n=7),
    ]
    store.update_meta(board)
    store.save_board(board, board_strokes)

    note = models.new_board_meta("", id="compatnote01", kind="note", background="lines", folder="课程", created=1700000200.0, updated=1700000300.0)
    store.update_meta(note)
    store.save_board(note, [stroke("n-1", [10, 10, 0.5, 20, 20, 0.5, 30, 30, 0.5], n=0)])

    doc_info = {"type": "pdf", "name": "讲义.pdf", "ext": ".pdf", "pages": [[595.28, 841.89], [595.28, 841.89]]}
    doc = models.new_board_meta("讲义", id="compatdoc001", kind="doc", background="blank", doc=doc_info, folder="课程", created=1700000400.0, updated=1700000500.0)
    store.update_meta(doc)
    store.save_board(doc, [stroke("d-1", [100, 900, 0.5, 120, 910, 0.5, 140, 920, 0.5], n=0, m=[[3.0, 110, 905, 130, 915]])])

    empty = models.new_board_meta("空的", id="compatempty1", created=1700000600.0, updated=1700000600.0)
    store.update_meta(empty)
    store.save_board(empty, [])

    for board_id in automatic:
        store.delete_board(board_id)
    store.create_folder("空文件夹")
    store.set_current("compatboard01")

    # 以当时的代码重新打开一遍：索引、读板、再存一遍，把结果全部记下来。
    # 存盘在一份副本上做，data/ 里留的是原样的旧文件。
    scratch = OUT / "_resave"
    shutil.copytree(data, scratch)
    reopened = BoardStore(data)
    resaver = BoardStore(scratch)
    expected = {
        "index": {"boards": reopened.list_metas(), "folders": reopened.folders(), "current": reopened.current_id},
        "boards": {},
    }
    for meta in reopened.list_metas():
        loaded_meta, loaded_strokes = reopened.load_board(meta["id"])
        resaver.save_board(*resaver.load_board(meta["id"]))
        path = scratch / "boards" / f"{meta['id']}.wbz"
        resaved = json.loads(zlib.decompress(path.read_bytes()).decode("utf-8"))
        expected["boards"][meta["id"]] = {"meta": loaded_meta, "strokes": loaded_strokes, "file": resaved}
    shutil.rmtree(scratch)
    (OUT / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    print(f"写好了 {len(expected['boards'])} 块白板：{OUT}")


if __name__ == "__main__":
    main()

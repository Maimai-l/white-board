"""Python 那份 perfect-freehand 移植必须和原版给出一模一样的点。

屏幕用的是原版（``packages/inksync/inksync/web/vendor/perfect-freehand.js``），导出用的是
``whiteboard/freehand.py``——因为导出在服务端跑，碰不到浏览器。两份实现一旦
漂开，导出的 PDF 和屏幕就不是一个形状，而这种偏差只有把 PDF 打开才看得见。

所以这里不是「差不多就行」：拿真机录的两条笔画，逐点比对，容差 1e-9。升级
vendor 里那个文件之后第一个要跑的就是这条。JS 那一边用 node 直接跑 stroke.js 的
``strokeOutline``，不用开浏览器；没装 node 时跳过（CI 上有）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.recorded_strokes import REAL_FAST_STROKE, REAL_HOOK_STROKE
from whiteboard import inkpdf

ROOT = Path(__file__).resolve().parents[1]
STROKE_JS = ROOT / "packages" / "inksync" / "inksync" / "web" / "stroke.js"

# 从标准输入读一组笔画，逐条算 strokeOutline，结果写到标准输出
OUTLINE_SCRIPT = """
const { strokeOutline } = await import(process.argv[1]);
let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).map(([flat, width, cut]) =>
  strokeOutline({ id: "x", tool: "pen", color: "#000", w: width, cut, p: flat }));
process.stdout.write(JSON.stringify(out));
"""

CASES = [
    ("真机-快笔", [list(p) for p in REAL_FAST_STROKE], 13.0, 0),
    ("真机-回钩", [list(p) for p in REAL_HOOK_STROKE], 13.0, 0),
    ("切口两头", [[0.0, 0.0, 1.0], [40.0, 0.0, 1.0], [80.0, 0.0, 1.0]], 16.0, 3),
    ("单点", [[5.0, 5.0, 0.4]], 9.0, 0),
]


def js_outlines(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("没有 node")
    payload = [[[v for p in pts for v in p], width, cut] for _name, pts, width, cut in cases]
    done = subprocess.run(
        [node, "--input-type=module", "-e", OUTLINE_SCRIPT, STROKE_JS.as_uri()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def py_outline(pts, width, cut):
    # 压感通道和 stroke.js 的 outlinePressure 一样：半径除以笔宽
    k = inkpdf.INK_SCALE
    return [
        (x / k, y / k)
        for x, y in inkpdf.freehand.get_stroke(
            [(x * k, y * k, inkpdf.radius("pen", width, pr) / max(width, 1e-6)) for x, y, pr in pts],
            size=max(width, 0.6) * k,
            thinning=1.0,
            smoothing=inkpdf.OUTLINE_SMOOTHING,
            streamline=inkpdf.OUTLINE_STREAMLINE,
            cap_start=not cut & 1,
            cap_end=not cut & 2,
            last=True,
            start_noise=inkpdf.START_NOISE * k,
        )
    ]


def test_python_outline_matches_perfect_freehand():
    for (name, pts, width, cut), js in zip(CASES, js_outlines(CASES)):
        py = py_outline(pts, width, cut)
        assert len(js) == len(py), (name, len(js), len(py))
        for i, (a, b) in enumerate(zip(js, py)):
            assert a[0] == pytest.approx(b[0], abs=1e-9), (name, i, a, b)
            assert a[1] == pytest.approx(b[1], abs=1e-9), (name, i, a, b)

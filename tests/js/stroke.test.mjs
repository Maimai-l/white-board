// stroke.js 里的纯计算：不开浏览器，node --test 直接跑。
//
// 这里只钉行为约定（单调、边界、切分后内容不丢），具体的数值曲线由
// tests/test_docs.py 与 Python 那一份对照。

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MASK_LIMIT,
  MAX_STROKE_POINTS,
  addMask,
  eraseKind,
  maskSize,
  penForce,
  pressureForFactor,
  simplifyMask,
  splitLongStroke,
  strokeBBox,
  strokeRadius,
} from "../../whiteboard/web/static/js/stroke.js";

function line(id, x0, y0, x1, y1, count = 20, pressure = 0.5, width = 6) {
  const p = [];
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1);
    p.push(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, pressure);
  }
  return { id, tool: "pen", color: "#000000", w: width, p, n: 0 };
}

test("penForce 把压感展开到 0～1，单调递增", () => {
  assert.equal(penForce(0), 0);
  assert.ok(Math.abs(penForce(1) - 1) < 1e-12);
  assert.equal(penForce(-1), 0);
  assert.ok(Math.abs(penForce(2) - 1) < 1e-12);
  let last = -1;
  for (let i = 0; i <= 100; i++) {
    const f = penForce(i / 100);
    assert.ok(f >= last);
    last = f;
  }
});

test("pressureForFactor 反解出来的压感正好给出想要的粗细", () => {
  for (const factor of [0.3, 0.5, 0.75, 1]) {
    const pressure = pressureForFactor(factor);
    const r = strokeRadius("pen", 10, pressure);
    assert.ok(Math.abs(r - 5 * factor) < 1e-9, `factor ${factor} → ${r}`);
  }
});

test("马克笔和荧光笔等宽，不跟压感走", () => {
  for (const tool of ["marker", "highlighter"]) {
    assert.equal(strokeRadius(tool, 8, 0.1), strokeRadius(tool, 8, 0.9));
  }
});

test("包围盒框得住所有采样点，并带上最大半径", () => {
  const stroke = line("s", -40, 10, 120, 80);
  const box = strokeBBox(stroke);
  for (let i = 0; i < stroke.p.length; i += 3) {
    assert.ok(stroke.p[i] >= box.x0 && stroke.p[i] <= box.x1);
    assert.ok(stroke.p[i + 1] >= box.y0 && stroke.p[i + 1] <= box.y1);
  }
  assert.ok(box.r > 0);
});

test("没超长的笔画原样返回；超长的切成几段，接缝共用一个点，点一个不少", () => {
  const short = line("a", 0, 0, 10, 10, 50);
  assert.deepEqual(splitLongStroke(short, () => "x"), [short]);

  const count = MAX_STROKE_POINTS * 2 + 10;
  const long = line("b", 0, 0, 1000, 0, count);
  let serial = 0;
  const pieces = splitLongStroke(long, () => `b${serial++}`);
  assert.ok(pieces.length >= 3);
  for (const piece of pieces) assert.ok(piece.p.length / 3 <= MAX_STROKE_POINTS);
  // 去掉接缝处重复的那个点之后，拼回来就是原来的点列
  const joined = pieces[0].p.slice();
  for (const piece of pieces.slice(1)) {
    assert.deepEqual(piece.p.slice(0, 3), joined.slice(-3));
    joined.push(...piece.p.slice(3));
  }
  assert.deepEqual(joined, long.p);
  assert.equal(new Set(pieces.map((p) => p.id)).size, pieces.length);
});

test("eraseKind：离得远就不碰，擦过笔画就是记遮罩", () => {
  const stroke = line("s", 0, 0, 100, 0);
  assert.equal(eraseKind(stroke, 0, 200, 100, 200, 5), null);
  assert.equal(eraseKind(stroke, 50, -20, 50, 20, 5), "bite");
});

test("遮罩：每擦一下多一段；抽稀不会让段数变多", () => {
  const stroke = line("s", 0, 0, 400, 0, 100);
  assert.equal(maskSize(stroke), 0);
  for (let x = 10; x < 390; x += 3) addMask(stroke, x, -2, x + 3, 2, 4);
  const before = maskSize(stroke);
  assert.ok(before > 0);
  simplifyMask(stroke);
  assert.ok(maskSize(stroke) <= before);
  assert.ok(MASK_LIMIT > 0);
});

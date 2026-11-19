// shape.js 里的纯计算：停顿判定、直线容差、拉直。不开浏览器，node --test 直接跑。
//
// 用户看得到的行为（停住变直、两端同步、撤销）在 tests/e2e/test_shapes.py。

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LINE_DETOUR,
  LINE_MIN_PX,
  LINE_TOLERANCE,
  PAUSE_MS,
  PAUSE_RADIUS_PX,
  PauseTracker,
  isLine,
  straighten,
  thinPoints,
} from "../../packages/inksync/inksync/web/shape.js";

/** 从 (x0, y0) 到 (x1, y1) 的一条弧：中点向法线方向鼓出 ``bulge``（占连线长度的比例）。 */
function arc(x0, y0, x1, y1, bulge = 0, count = 40, pressure = (i) => 0.5) {
  const p = [];
  const len = Math.hypot(x1 - x0, y1 - y0);
  const nx = -(y1 - y0) / len;
  const ny = (x1 - x0) / len;
  for (let i = 0; i < count; i++) {
    const t = i / (count - 1);
    const off = 4 * bulge * len * t * (1 - t); // 抛物线，中点正好鼓出 bulge × 长度
    p.push(x0 + (x1 - x0) * t + nx * off, y0 + (y1 - y0) * t + ny * off, pressure(i));
  }
  return p;
}

/* ------------------------------------------------------------ 停顿 */

test("a pen that stays put for the pause time is paused", () => {
  const pause = new PauseTracker();
  pause.reset(0, 0, 1000);
  assert.equal(pause.feed(100, 0, 1100), false); // 走开了，锚点挪到这里
  // 之后一直在原地附近晃（小于半径），时间到了才算停住
  assert.equal(pause.feed(101, 1, 1300), false);
  assert.equal(pause.feed(99, -1, 1599), false);
  assert.equal(pause.held(1599), false);
  assert.equal(pause.held(1100 + PAUSE_MS), true);
  assert.equal(pause.feed(100, 2, 1100 + PAUSE_MS), true);
});

test("the pause counts even when no sample arrives while the pen is still", () => {
  // 鼠标停住不再发事件：下一个事件（或抬笔）的时间戳就够判断
  const pause = new PauseTracker();
  pause.reset(10, 10, 0);
  pause.feed(80, 10, 200);
  assert.equal(pause.held(200 + PAUSE_MS - 1), false);
  assert.equal(pause.held(200 + PAUSE_MS + 100), true);
});

test("moving on after a full pause still reports the pause once", () => {
  const pause = new PauseTracker();
  pause.reset(0, 0, 0);
  pause.feed(50, 0, 100);
  // 停够了之后才走开：这一下报停住，然后锚点换到新位置，重新计时
  assert.equal(pause.feed(90, 0, 100 + PAUSE_MS + 50), true);
  assert.equal(pause.feed(91, 0, 100 + PAUSE_MS + 60), false);
});

test("drifting beyond the radius restarts the pause", () => {
  const pause = new PauseTracker();
  pause.reset(0, 0, 0);
  let t = 0;
  // 慢慢挪，每次都挪出半径：永远不算停住
  for (let i = 1; i < 40; i++) {
    t += 100;
    assert.equal(pause.feed(i * (PAUSE_RADIUS_PX + 1), 0, t), false);
  }
});

test("a short hesitation is not a pause", () => {
  const pause = new PauseTracker();
  pause.reset(0, 0, 0);
  pause.feed(100, 0, 100);
  pause.feed(100, 0, 400);
  assert.equal(pause.held(400), false);
  assert.equal(pause.feed(160, 0, 420), false);
});

/* ------------------------------------------------------------ 容差 */

test("a straight or slightly curved stroke is a line", () => {
  assert.equal(isLine(arc(0, 0, 300, 0)), true);
  assert.equal(isLine(arc(0, 0, 300, 120, 0.05)), true);
  assert.equal(isLine(arc(0, 0, 300, 0, LINE_TOLERANCE * 0.9)), true);
});

test("a clearly curved stroke is not a line", () => {
  assert.equal(isLine(arc(0, 0, 300, 0, LINE_TOLERANCE * 1.3)), false);
  assert.equal(isLine(arc(0, 0, 300, 0, 0.4)), false);
});

test("a hook at the end is not a line", () => {
  const p = arc(0, 0, 300, 0);
  p.push(300, 30, 0.5, 280, 50, 0.5);
  assert.equal(isLine(p), false);
});

test("scribbling back and forth along a line is not a line", () => {
  const p = [...arc(0, 0, 300, 0, 0, 20), ...arc(300, 0, 0, 0, 0, 20), ...arc(0, 0, 300, 0, 0, 20)];
  assert.ok(900 > 300 * LINE_DETOUR);
  assert.equal(isLine(p), false);
});

test("a tiny stroke is not a line, measured on screen", () => {
  const p = arc(0, 0, LINE_MIN_PX - 2, 0);
  assert.equal(isLine(p, 1), false);
  // 同一笔放大两倍看就够长了
  assert.equal(isLine(p, 2), true);
  assert.equal(isLine([5, 5, 0.5]), false);
});

test("short lines get a few pixels of slack for hand jitter", () => {
  // 30 像素长、中间抖了 3.5 像素：比例上超了，绝对余量之内
  const p = [0, 0, 0.5, 15, 3.5, 0.5, 30, 0, 0.5];
  assert.equal(isLine(p, 1), true);
  // 放大四倍看，同样的世界坐标偏差在屏幕上是 14 像素，比例也超了
  assert.equal(isLine(p, 4), false);
});

/* ------------------------------------------------------------ 拉直 */

test("straightening keeps the end points and the pressure", () => {
  const p = arc(10, 20, 310, 140, 0.06, 30, (i) => 0.2 + i / 100);
  const s = straighten(p);
  assert.equal(s.length, p.length);
  assert.deepEqual(s.slice(0, 3), p.slice(0, 3));
  assert.deepEqual(s.slice(-3), p.slice(-3));
  for (let i = 0; i < 30; i++) assert.equal(s[i * 3 + 2], p[i * 3 + 2]);
  assert.notDeepEqual(p.slice(45, 47), s.slice(45, 47)); // 原来的点没被改
});

test("straightened points lie on the chord, in order", () => {
  const p = arc(0, 0, 400, 300, 0.08, 50);
  const s = straighten(p);
  let previous = -1;
  for (let i = 0; i < 50; i++) {
    const x = s[i * 3];
    const y = s[i * 3 + 1];
    assert.ok(Math.abs(x * 300 - y * 400) / 500 < 1e-9, `第 ${i} 个点不在连线上`);
    const along = Math.hypot(x, y);
    assert.ok(along >= previous, "点的顺序乱了");
    previous = along;
  }
});

test("straightening spaces points by the distance drawn", () => {
  // 前半段点密、后半段点疏：拉直之后各点在连线上的位置跟着路程走
  const p = [0, 0, 0.5, 10, 2, 0.5, 20, 3, 0.5, 100, 1, 0.5, 200, 0, 0.5];
  const s = straighten(p);
  const lengths = [0];
  for (let i = 1; i < 5; i++) {
    lengths.push(lengths[i - 1] + Math.hypot(p[i * 3] - p[i * 3 - 3], p[i * 3 + 1] - p[i * 3 - 2]));
  }
  for (let i = 1; i < 4; i++) {
    assert.ok(Math.abs(s[i * 3] - (200 * lengths[i]) / lengths[4]) < 1e-9);
    assert.equal(s[i * 3 + 1], 0);
  }
});

test("degenerate strokes come back unchanged", () => {
  assert.deepEqual(straighten([1, 2, 0.5]), [1, 2, 0.5]);
  assert.deepEqual(straighten([1, 2, 0.5, 1, 2, 0.6]), [1, 2, 0.5, 1, 2, 0.6]);
});

test("thinning keeps both ends and at most the asked number of points", () => {
  const p = straighten(arc(0, 0, 500, 0, 0, 300, (i) => i / 300));
  const t = thinPoints(p, 48);
  assert.equal(t.length, 48 * 3);
  assert.deepEqual(t.slice(0, 3), p.slice(0, 3));
  assert.deepEqual(t.slice(-3), p.slice(-3));
  for (let i = 1; i < 48; i++) assert.ok(t[i * 3] > t[i * 3 - 3], "顺序乱了");
  assert.deepEqual(thinPoints(p.slice(0, 30), 48), p.slice(0, 30));
});

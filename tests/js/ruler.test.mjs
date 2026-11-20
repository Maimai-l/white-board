// ruler.js：尺子的几何——投影到尺边、起笔贴不贴边、两指拖动旋转、显示的角度。

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ANGLE_SNAP_DEG,
  RULER_WIDTH,
  Ruler,
  SNAP_DISTANCE,
  displayAngle,
  distanceToLine,
  edgeLine,
  fromLocal,
  moveRuler,
  normalizeAngle,
  projectOntoLine,
  rulerContains,
  snapAngle,
  snapEdge,
  toLocal,
} from "../../packages/inksync/inksync/web/ruler.js";
import { installRuler } from "../../packages/inksync/inksync/web/input-ruler.js";
import { Viewport } from "../../packages/inksync/inksync/web/viewport.js";

const deg = (d) => (d * Math.PI) / 180;
const close = (actual, expected, eps = 1e-9, msg) =>
  assert.ok(Math.abs(actual - expected) <= eps, `${msg || ""} ${actual} ≠ ${expected}`);

function ruler(cx, cy, angleDeg, width = 80) {
  return { cx, cy, angle: deg(angleDeg), width };
}

test("本地坐标：u 沿尺子、v 垂直于尺子，来回换算一致", () => {
  const r = ruler(100, 200, 30);
  const { u, v } = toLocal(r, 100 + Math.cos(deg(30)) * 50, 200 + Math.sin(deg(30)) * 50);
  close(u, 50);
  close(v, 0);
  const [x, y] = fromLocal(r, 12, -7);
  const back = toLocal(r, x, y);
  close(back.u, 12);
  close(back.v, -7);
});

test("投影到直线上：落在线上、和原点的连线垂直于直线", () => {
  const line = { x: 10, y: 20, ux: Math.cos(deg(30)), uy: Math.sin(deg(30)) };
  const [px, py] = projectOntoLine(line, 80, -15);
  close(distanceToLine(line, px, py), 0, 1e-9);
  // 原点到投影点的方向就是直线方向（或反向），偏移量垂直于直线
  close((80 - px) * line.ux + (-15 - py) * line.uy, 0, 1e-9);
  // 已经在线上的点不动
  const [qx, qy] = projectOntoLine(line, 10 + line.ux * 33, 20 + line.uy * 33);
  close(qx, 10 + line.ux * 33);
  close(qy, 20 + line.uy * 33);
});

test("两条长边：角度 0 时在中心上下各半个尺宽", () => {
  const r = ruler(300, 400, 0);
  const top = edgeLine(r, -1);
  const bottom = edgeLine(r, 1);
  close(top.y, 360);
  close(bottom.y, 440);
  close(top.ux, 1);
  close(top.uy, 0);
  // 转 90° 之后两条边是竖线，上边转到了右边（屏幕 y 朝下，正角度顺时针）
  const turned = edgeLine(ruler(300, 400, 90), -1);
  close(turned.x, 340, 1e-9);
  close(turned.y, 400, 1e-9);
});

test("尺身判定只看垂直方向：尺子没有尽头", () => {
  const r = ruler(300, 400, 0);
  assert.equal(rulerContains(r, 300, 400), true);
  assert.equal(rulerContains(r, 5000, 439), true);
  assert.equal(rulerContains(r, 300, 441), false);
  assert.equal(rulerContains(r, 300, 359), false);
});

test("起笔贴边：尺身上贴近的那条边；外面离边不超过 SNAP_DISTANCE 才贴", () => {
  const r = ruler(300, 400, 0);
  assert.equal(snapEdge(r, 300, 390).y, 360, "尺身上半截 → 上边");
  assert.equal(snapEdge(r, 300, 430).y, 440, "尺身下半截 → 下边");
  assert.equal(snapEdge(r, 300, 360 - SNAP_DISTANCE).y, 360, "正好在门槛上");
  assert.equal(snapEdge(r, 300, 360 - SNAP_DISTANCE - 0.5), null, "过了门槛");
  assert.equal(snapEdge(r, 300, 440 + 10).y, 440);
  // 斜着的尺子按垂直距离算，沿尺子方向多远都一样
  const slanted = ruler(0, 0, 30);
  const [x, y] = fromLocal(slanted, 900, -40 - 20);
  const edge = snapEdge(slanted, x, y);
  assert.ok(edge);
  close(distanceToLine(edge, ...fromLocal(slanted, -500, -40)), 0, 1e-9);
});

// ------------------------------------------------ 输入这一侧（input-ruler.js）

/** 只带 input-ruler.js 用得到的那几样：画布在 (0, 0)，视口可以缩放平移。 */
function controller({ scale = 1, x = 0, y = 0 } = {}) {
  const proto = {};
  installRuler(proto);
  const input = Object.create(proto);
  const viewport = new Viewport();
  Object.assign(viewport, { scale, x, y });
  const started = [];
  Object.assign(input, {
    viewport,
    pointers: new Map(),
    gesture: null,
    draw: null,
    erase: null,
    toScreen: (event) => [event.clientX, event.clientY],
    startGesture: (event) => started.push(event),
  });
  input.started = started;
  const r = new Ruler({ width: 80 });
  r.setBounds(1000, 800); // 中心 (500, 400)，角度 0：上边 y = 360，下边 y = 440
  r.show(true);
  input.setRuler(r);
  return input;
}

const touch = (pointerId, x, y) => ({ pointerId, clientX: x, clientY: y, pointerType: "touch" });
const pen = (x, y) => ({ pointerId: 1, clientX: x, clientY: y, pointerType: "pen" });

test("起笔贴边的门槛按屏幕像素算：放大四倍还是同样的屏幕距离", () => {
  for (const scale of [0.25, 1, 4]) {
    const input = controller({ scale, x: 30, y: -70 });
    const near = input.rulerSnap(pen(200, 360 - SNAP_DISTANCE + 1));
    assert.ok(near, `scale ${scale}`);
    // 返回的是白板坐标里的尺边：换回屏幕正好是 y = 360 那条线
    const [sx, sy] = input.viewport.toScreen(near.x, near.y);
    close(sy, 360, 1e-9);
    assert.ok(Number.isFinite(sx));
    close(near.ux, 1);
    close(near.uy, 0);
    assert.equal(input.rulerSnap(pen(200, 360 - SNAP_DISTANCE - 1)), null, `scale ${scale}`);
  }
});

test("贴边的这一笔：白板坐标里的点投影到尺边上；没贴边的原样返回", () => {
  const input = controller({ scale: 2, x: 100, y: 50 });
  input.draw = { snap: input.rulerSnap(pen(300, 350)) };
  const [wx, wy] = input.snapToRuler([123, 456]);
  const [, sy] = input.viewport.toScreen(wx, wy);
  close(sy, 360, 1e-9);
  input.draw = { snap: null };
  assert.deepEqual(input.snapToRuler([123, 456]), [123, 456]);
});

test("尺子收起或者没接：手指和笔都照旧", () => {
  const input = controller();
  input.ruler.show(false);
  assert.equal(input.routeRulerTouch(touch(5, 500, 400), "gesture"), "gesture");
  assert.equal(input.rulerSnap(pen(500, 400)), null);
  input.setRuler(null);
  assert.equal(input.routeRulerTouch(touch(5, 500, 400), "draw"), "draw");
});

test("手指分给谁：落在尺身上归尺子，外面照旧；笔不归尺子", () => {
  const input = controller();
  assert.equal(input.routeRulerTouch(touch(5, 500, 420), "gesture"), "ruler");
  assert.equal(input.routeRulerTouch(touch(5, 500, 300), "gesture"), "gesture");
  // 打开了手指书写：尺身上的手指还是拖尺子，外面的手指照旧写字
  assert.equal(input.routeRulerTouch(touch(5, 500, 420), "draw"), "ruler");
  assert.equal(input.routeRulerTouch(touch(5, 500, 300), "draw"), "draw");
  assert.equal(input.routeRulerTouch(pen(500, 400), "draw"), "draw");
  // 画布手势已经开始：后来的手指落在尺子上也归画布
  input.gesture = {};
  assert.equal(input.routeRulerTouch(touch(6, 500, 400), "gesture"), "gesture");
});

test("两根手指都在尺身上：一起转尺子", () => {
  const input = controller();
  input.pointers.set(5, { type: "touch", role: "ruler" });
  input.startRulerPointer(touch(5, 400, 400));
  assert.equal(input.routeRulerTouch(touch(6, 600, 400), "gesture"), "ruler");
  input.pointers.set(6, { type: "touch", role: "ruler" });
  input.startRulerPointer(touch(6, 600, 400));
  input.moveRulerPointer(touch(6, 500 + 100 * Math.cos(deg(30)), 400 + 100 * Math.sin(deg(30))));
  input.moveRulerPointer(touch(5, 500 - 100 * Math.cos(deg(30)), 400 - 100 * Math.sin(deg(30))));
  close(input.ruler.angle, deg(30), 1e-9);
  close(input.ruler.cx, 500, 1e-9);
  close(input.ruler.cy, 400, 1e-9);
  // 第三根手指也算尺子的，但不参与
  assert.equal(input.routeRulerTouch(touch(7, 10, 10), "gesture"), "ruler");
});

test("第二根手指落在尺子外面：先前那根交给画布手势，尺子留在原地", () => {
  const input = controller();
  input.pointers.set(5, { type: "touch", role: "ruler" });
  input.startRulerPointer(touch(5, 500, 420));
  input.moveRulerPointer(touch(5, 520, 420));
  close(input.ruler.cx, 520);
  assert.equal(input.routeRulerTouch(touch(6, 500, 700), "gesture"), "gesture");
  assert.equal(input.pointers.get(5).role, "gesture");
  assert.deepEqual(
    input.started.map((e) => [e.pointerId, e.clientX, e.clientY]),
    [[5, 520, 420]],
  );
  assert.equal(input.rulerGrab, null);
  close(input.ruler.cx, 520);
});

test("沿尺边写的时候尺子不跟手，抬笔之后从当时的位置接着走，不跳", () => {
  const input = controller();
  input.pointers.set(5, { type: "touch", role: "ruler" });
  input.startRulerPointer(touch(5, 500, 420));
  input.draw = { snap: null };
  input.moveRulerPointer(touch(5, 560, 420));
  close(input.ruler.cx, 500);
  input.draw = null;
  input.moveRulerPointer(touch(5, 570, 420));
  close(input.ruler.cx, 510);
  input.endRulerPointer(5);
  assert.equal(input.rulerGrab, null);
});

test("一根手指：只平移", () => {
  const start = { cx: 100, cy: 100, angle: deg(20) };
  const next = moveRuler(start, [[150, 100]], [[190, 70]]);
  close(next.cx, 140);
  close(next.cy, 70);
  close(next.angle, deg(20));
});

test("两根手指：绕中点转多少，尺子就转多少；中点相对尺子的位置不变", () => {
  const start = { cx: 300, cy: 400, angle: 0 };
  const from = [[200, 400], [400, 400]];
  const a = deg(30);
  const to = [
    [300 - 100 * Math.cos(a), 400 - 100 * Math.sin(a)],
    [300 + 100 * Math.cos(a), 400 + 100 * Math.sin(a)],
  ];
  const next = moveRuler(start, from, to);
  close(next.angle, a);
  close(next.cx, 300, 1e-9);
  close(next.cy, 400, 1e-9);

  // 手指不在尺子中心：尺子跟着手指既转又移，手指底下那一点一直在手指底下
  const off = { cx: 0, cy: 0, angle: 0 };
  const fingers = [[100, 0], [200, 0]];
  const moved = [[50, 50], [50, 150]]; // 转 90°，挪到别处
  const result = moveRuler(off, fingers, moved);
  close(result.angle, deg(90));
  const local = toLocal({ ...off, width: 80 }, 150, 0); // 两指中点在尺子上的位置
  const [mx, my] = fromLocal({ ...result, width: 80 }, local.u, local.v);
  close(mx, 50, 1e-9);
  close(my, 100, 1e-9);
});

test("两根手指一起平移不转", () => {
  const next = moveRuler({ cx: 10, cy: 10, angle: deg(45) }, [[0, 0], [10, 0]], [[5, 7], [15, 7]]);
  close(next.angle, deg(45));
  close(next.cx, 15);
  close(next.cy, 17);
});

test("转到 45° 整数倍附近吸过去，中心跟着吸附后的角度算", () => {
  close(snapAngle(deg(44)), deg(45));
  close(snapAngle(deg(-91)), deg(-90));
  close(snapAngle(deg(1)), 0);
  close(snapAngle(deg(30)), deg(30));
  close(snapAngle(deg(45 + ANGLE_SNAP_DEG + 0.1)), deg(45 + ANGLE_SNAP_DEG + 0.1));
  close(snapAngle(deg(179)), deg(180), 1e-9);

  const from = [[200, 400], [400, 400]];
  const a = deg(1.5);
  const to = [
    [300 - 100 * Math.cos(a), 400 - 100 * Math.sin(a)],
    [300 + 100 * Math.cos(a), 400 + 100 * Math.sin(a)],
  ];
  const next = moveRuler({ cx: 300, cy: 400, angle: 0 }, from, to);
  close(next.angle, 0);
  close(next.cx, 300, 1e-9);
  close(next.cy, 400, 1e-9);
});

test("跨过 ±180° 转不会多转一圈", () => {
  const from = [[0, 0], [-100, 1]]; // 指向左，atan2 接近 +180°
  const to = [[0, 0], [-100, -1]]; // 接近 -180°
  const next = moveRuler({ cx: 0, cy: 0, angle: deg(10) }, from, to);
  close(next.angle, deg(10) + normalizeAngle(Math.atan2(-1, -100) - Math.atan2(1, -100)), 1e-9);
  assert.ok(Math.abs(next.angle - deg(10)) < deg(2));
});

test("尺子中心不出画布区域", () => {
  const next = moveRuler({ cx: 100, cy: 100, angle: 0 }, [[100, 100]], [[-500, 900]], { w: 800, h: 600 });
  assert.equal(next.cx, 0);
  assert.equal(next.cy, 600);
});

test("显示的角度：0–90 的整数度数，尺子没有头尾", () => {
  assert.equal(displayAngle(0), 0);
  assert.equal(displayAngle(deg(30)), 30);
  assert.equal(displayAngle(deg(-30)), 30);
  assert.equal(displayAngle(deg(150)), 30);
  assert.equal(displayAngle(deg(210)), 30);
  assert.equal(displayAngle(deg(90)), 90);
  assert.equal(displayAngle(deg(-90)), 90);
  assert.equal(displayAngle(deg(180)), 0);
  assert.equal(displayAngle(deg(-179.6)), 0);
  assert.equal(displayAngle(deg(44.6)), 45);
  assert.equal(displayAngle(deg(91)), 89);
});

test("Ruler：第一次知道画布大小时摆到正中，之后改大小只把中心收回画布里", () => {
  const r = new Ruler();
  const seen = [];
  r.onChange((ruler) => seen.push(ruler.state()));
  r.setBounds(1000, 800);
  assert.deepEqual(r.state(), { cx: 500, cy: 400, angle: 0 });
  r.set({ cx: 900, cy: 700, angle: deg(400) });
  close(r.angle, deg(40));
  r.setBounds(600, 500);
  assert.deepEqual([r.cx, r.cy], [600, 500]);
  assert.equal(r.visible, false);
  r.show(true);
  assert.equal(r.visible, true);
  assert.equal(seen.length, 4);
  assert.equal(r.width, RULER_WIDTH);
});

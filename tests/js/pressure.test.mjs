// 笔的输入怎么变成笔迹的粗细：倾角、压感曲线、起笔。不开浏览器，node --test 直接跑。

import assert from "node:assert/strict";
import { test } from "node:test";

import { penAltitude } from "../../packages/inksync/inksync/web/input.js";
import { pressureForFactor, strokeOutline, strokeRadius } from "../../packages/inksync/inksync/web/stroke.js";

const degrees = (event) => Math.round((penAltitude(event) * 180) / Math.PI);

test("pen_altitude_reads_both_tilt_apis：tiltX / tiltY 优先，altitudeAngle 兜底", () => {
  // 只认 altitudeAngle 是不行的——规范规定设备报不出倾斜时它返回 π/2（竖直），
  // 不支持这个属性的浏览器于是看起来「笔一直立着」，倾斜永远读不出来。

  // Level 2 的 tiltX / tiltY，从竖直方向算起
  assert.equal(degrees({ tiltX: 0, tiltY: 0 }), 90);
  assert.equal(degrees({ tiltX: 45, tiltY: 0 }), 45);
  assert.equal(degrees({ tiltX: 60, tiltY: 0 }), 30);
  assert.equal(degrees({ tiltX: 0, tiltY: 55 }), 35);
  assert.equal(degrees({ tiltX: -60, tiltY: 0 }), 30); // 往哪边倒都一样

  // 没有 tiltX / tiltY 时才看 altitudeAngle，从屏幕平面算起
  assert.equal(degrees({ altitudeAngle: Math.PI / 2 }), 90);
  assert.equal(degrees({ altitudeAngle: Math.PI / 4 }), 45);
  assert.equal(degrees({ altitudeAngle: 0 }), 0); // 平贴屏幕，不是「没数据」
  assert.equal(degrees({}), 90); // 两套都没有，按竖直算

  // 两套都在时以 tiltX / tiltY 为准
  assert.equal(degrees({ tiltX: 60, tiltY: 0, altitudeAngle: Math.PI / 2 }), 30);
});

// 真机录像里一整段手写的压感读数（20260926-204816 / 231400 / 20260927-105648 三份
// 录像，2124 个非零采样）分位数。iPad Safari 根本不把 0～1 用满。
const RECORDED_PRESSURE = { p1: 0.0092, p25: 0.0229, p50: 0.0282, p75: 0.0365, p95: 0.08, max: 0.1253 };

test("pressure_actually_changes_how_thick_the_pen_is：粗细曲线照真机的压感量程来", () => {
  // 这条曲线以前是 `0.42 + 0.58 * p^0.8`，假设设备把 0～1 用满。真机上常用的
  // 0.023～0.080 一段算出来是 0.448～0.497——差 11%，看不出来有压感。
  // 直接照搬 atrament 的 `#getWeightWithPressure` 更糟：它以 0.5 为轴，0.5 以下
  // 是 `weight * 2p`，0.028 算出来是设定线宽的 5.6%，一条头发丝。
  const f = (p) => strokeRadius("pen", 2, p); // 设定线宽 2，半径即倍数
  const at = Object.fromEntries(Object.entries(RECORDED_PRESSURE).map(([k, p]) => [k, f(p)]));

  // 常用的 p25～p95 一段至少要差一半以上，才叫看得出压感
  assert.ok(at.p95 / at.p25 > 1.5, JSON.stringify(at));
  // 整个量程（p1～max）要差两倍以上
  assert.ok(at.max / at.p1 > 2.0, JSON.stringify(at));
  // 单调
  const keys = ["p1", "p25", "p50", "p75", "p95", "max"];
  for (let i = 1; i < keys.length; i++) assert.ok(at[keys[i - 1]] < at[keys[i]], keys[i]);
  // 「放松写字」的 p50 还是设定线宽的 0.45 倍上下：改的是动态范围，不是整体粗细
  assert.ok(at.p50 > 0.42 && at.p50 < 0.48, String(at.p50));
  // 用满力正好是设定的线宽——线宽滑块的含义没变
  assert.ok(Math.abs(f(1) - 1) < 1e-6, String(f(1)));
});

test("a_pointer_without_pressure_keeps_the_width_it_had：没有压感的输入粗细不跟着曲线变", () => {
  // 鼠标、手指和不带压感的笔的粗细是按速度算的（走得快就细），可是文件里每个点
  // 只有一个压感字段，所以粗细得折回压感值再存。曲线一换，折算也得跟着换，不然
  // Mac 上用鼠标画出来的线会莫名变粗变细。判据是和旧管线（`clamp(1 - speed / 3.2,
  // 0.38, 1)` 配旧曲线 `0.42 + 0.58 * p^0.8`）算出来的粗细逐点比对，全程差不到 2%。
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  for (const speed of [0, 0.25, 0.5, 0.75, 1, 1.5, 1.984, 3, 6]) {
    const before = 0.42 + 0.58 * Math.pow(clamp(1 - speed / 3.2, 0.38, 1), 0.8);
    const stored = pressureForFactor(clamp(1 - speed * 0.157, 0.69, 1));
    const after = strokeRadius("pen", 2, stored);
    assert.ok(Math.abs(after / before - 1) < 0.02, `速度 ${speed}：${before} → ${after}`);
  }
});

test("the_start_of_a_stroke_is_not_swallowed：起笔那一小截立刻出墨，不等笔走够一个笔宽", () => {
  // perfect-freehand 的 getStroke 把同一个 size 同时当成「起笔处先丢掉多长一段」
  // 和「笔有多粗」。那一段是用来挡落笔抖动的，按笔宽算就太大了：13 宽的笔要走满
  // 13 个单位才开始留下点，写小字时每个笔画的头都被吞掉一截。所以 getStrokePoints
  // 和 getStrokeOutlinePoints 分开调，前者传一个小得多的阈值。
  //
  // 判据：一条总长 24 个单位的小笔画（写小字时一笔就这么长），墨迹必须从起点附近
  // 就开始。起点在 x=0，笔半径约 3，圆头会往左伸出一点，所以最左应当是负的。
  const p = [];
  for (let i = 0; i < 13; i++) p.push(i * 2, 0, 0.25);
  const pts = strokeOutline({ id: "x", tool: "pen", color: "#000", w: 13, p });
  const left = Math.min(...pts.map((q) => q[0]));
  assert.ok(left < 0.5, String(left));
  assert.ok(pts.length > 20, String(pts.length));
});

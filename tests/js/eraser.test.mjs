// 橡皮的纯计算：半径曲线、切分几何、命中判定、遮罩抽稀。不开浏览器，node --test 直接跑。
//
// 这些以前在浏览器用例里，开两个页面只为了调一个函数。要看到输入、同步、渲染的那部分
// 仍在 tests/e2e/test_eraser.py。

import assert from "node:assert/strict";
import { test } from "node:test";

import { ERASER_TIP, eraserDiameter, eraserRadiusFor } from "../../packages/inksync/inksync/web/input-erase.js";
import { simplifyMask, splitStroke, strokeHit } from "../../packages/inksync/inksync/web/stroke.js";

const rad = (deg) => (deg * Math.PI) / 180;
const pixelDiameter = (deg) => eraserRadiusFor("pixel", "pen", rad(deg)) * 2;

// iPad 原生 PencilKit 像素橡皮的实测印记：(笔身与屏幕的夹角°, 直径)。
//
// 采集方式见 docs/eraser.md：在一大片实心墨迹上点一排孤立的橡皮点，每个点固定
// 一个角度，再把 PKStroke.mask 里对应那个洞的面积换算成等效直径。
// 沿拖动路径量的**垂直宽度**：擦痕在屏幕上有多宽，就是这个数。曲线按它来。
const NATIVE_DRAG_WIDTH = [
  [67.8, 16.5], [64.8, 16.5],
  [58.8, 15.5], [57.7, 15.5], [54.4, 16.0], [53.4, 14.5], [52.6, 16.0],
  [50.7, 15.5], [45.3, 16.5], [44.9, 18.0], [44.0, 16.5],
  // 会话 a 的五条，把平台段的下端定在 37°
  [43.0, 15.5], [40.9, 15.5], [39.6, 15.5], [38.9, 16.0], [38.4, 16.0],
  // 会话 c，zoom 0.25：322 个 drawing 单位乘回缩放是 80.5 个屏幕单位
  [10.4, 80.5],
];

// 孤立点的**洞面积换算的等效直径**。和垂直宽度只有在落笔是圆的时候才是同一个数，
// 所以不能直接拿来定曲线——原来就是把两者接在一起，才在 37° 处接出一道悬崖。
const NATIVE_DAB_AREA = [
  [83.3, 6.9], [82.0, 6.3], [81.1, 7.2], [80.0, 9.0], [79.1, 8.1],
  [50.3, 16.5], [50.1, 17.7], [49.8, 17.6], [49.7, 18.2], [49.7, 15.9],
  [35.0, 35.2], [34.4, 40.7], [33.7, 45.0], [32.5, 50.5], [31.8, 54.1],
  [28.5, 72.2], [27.3, 78.7],
  [20.3, 80.7], [14.8, 82.1], [12.6, 81.2], [12.1, 80.3],
];

test("eraser_width_follows_the_native_curve：像素橡皮的直径跟着倾角走，对得上原生实测", () => {
  // 原来是「20° 以上一律笔尖 6、15° 以下一律 45」，全是拍脑袋定的。实测下来原生从
  // 80° 就开始变粗、25° 左右饱和在 81；常握笔大约 50°，那里原生已经是 17，而原来的
  // 实现还停在 6——细得没法用橡皮写字，这就是那个「擦痕像针」的直接原因。

  // 垂直宽度：曲线就是按它定的，要贴得紧。同一角度原生自己就有散布
  // （50° 那五个点是 15.9～18.2），所以按相对误差比
  for (const [deg, native] of NATIVE_DRAG_WIDTH) {
    const ours = pixelDiameter(deg);
    assert.ok(Math.abs(ours - native) / native < 0.18, `${deg}°：原生 ${native}，这里 ${ours}`);
  }

  // 等效直径：只在落笔接近圆形的两端（≥44° 和 ≤25°）和曲线一致
  for (const [deg, native] of NATIVE_DAB_AREA) {
    if (!(deg >= 44 || deg <= 25)) continue;
    const ours = pixelDiameter(deg);
    assert.ok(Math.abs(ours - native) / native < 0.18, `${deg}°：原生 ${native}，这里 ${ours}`);
  }

  // 中间那一段等效直径一律比曲线大：这正是「落笔是拉长的椭圆」的样子，
  // 反过来说，曲线在这里绝不能去追等效直径，否则就把悬崖接回来了
  for (const [deg, native] of NATIVE_DAB_AREA) {
    if (deg > 25 && deg < 44) assert.ok(pixelDiameter(deg) < native, `${deg}°`);
  }
});

test("像素橡皮单调：笔越平擦得越宽，两端分别贴着笔尖和饱和值", () => {
  const widths = [];
  for (let deg = 85; deg > 5; deg -= 5) widths.push(pixelDiameter(deg));
  assert.deepEqual(widths, [...widths].sort((a, b) => a - b));
  assert.ok(widths[0] < 8 && widths[widths.length - 1] > 78, String(widths));
  // 37° 以下这一段没有拖动样本，只在两个信得过的锚点之间连直线，所以不拿具体数值当判据
});

test("对象橡皮不管倾角都是笔尖：它是整笔删除，作用点本来就只是一个点", () => {
  for (const deg of [88, 60, 45, 35, 15]) {
    assert.equal(eraserRadiusFor("object", "pen", rad(deg)), ERASER_TIP / 2);
  }
  assert.equal(ERASER_TIP / 2, 3);
});

test("报不出倾斜的笔、鼠标和手指按常握笔那一档给，不然一直是笔尖等于没法用", () => {
  for (const pointer of ["mouse", "touch", null]) {
    const diameter = eraserRadiusFor("pixel", pointer, 0) * 2;
    assert.ok(diameter > 8 && diameter < 26, `${pointer}: ${diameter}`);
    assert.equal(diameter, eraserDiameter(50));
  }
});

// ----------------------------------------------------------------- 切分

/** 沿 x 轴每隔 spacing 一个点的直线笔画。 */
function row(length, spacing, width, pressure = 1) {
  const p = [];
  for (let x = 0; x <= length; x += spacing) p.push(x, 0, pressure);
  return { id: "a", tool: "pen", color: "#000000", w: width, p };
}

test("split_stroke_geometry：没碰到返回 null，整条被盖住返回空，只剩一个点的碎屑不留", () => {
  // 每一段带 cut，标出哪一头是切出来的（1 = 起点，2 = 终点），渲染时那一头画平口
  const stroke = row(190, 10, 3, 0.6);
  const runs = (...args) => {
    const out = splitStroke(stroke, ...args);
    return out === null ? null : out.map((run) => [run.p.length / 3, run.cut]);
  };
  assert.equal(runs(95, 400, 95, 500, 8), null); // 离得远，没碰到
  // 从中间切一刀：两头各留 9 个原采样点，再各加一个落在交点上的新点
  assert.deepEqual(runs(95, -5, 95, 5, 8), [[10, 2], [10, 1]]);
  assert.deepEqual(runs(-50, 0, 250, 0, 30), []); // 整条都被扫掉
  // 起点那头切掉：留下交点 + 后面 17 个点，起点是切口
  assert.deepEqual(runs(8, 0, 8, 0, 14), [[18, 1]]);
});

test("eraser_gap_matches_its_diameter：缺口宽度等于橡皮直径，跟采样密度无关", () => {
  // 以前切口只能落在采样点上，缺口因此跟着采样密度走：同样是直径 16 的橡皮，
  // 密采样缺口 42、疏采样 84。判定里还额外加了笔画自己的半宽，橡皮只要蹭到外沿
  // 就把整个截面切断。两条都改了，现在缺口恒等于直径。
  const gap = (spacing, r) => {
    const runs = splitStroke(row(400, spacing, 16), 200, -60, 200, 60, r);
    const left = runs[0].p;
    return runs[1].p[0] - left[left.length - 3];
  };
  for (const spacing of [4, 14, 28, 60]) {
    assert.ok(Math.abs(gap(spacing, 8) - 16) < 0.1, `间距 ${spacing}，半径 8`);
    assert.ok(Math.abs(gap(spacing, 3) - 6) < 0.1, `间距 ${spacing}，半径 3`);
  }

  // 只蹭到外沿：中心线没被盖住，这一笔不该动。笔半宽 15、橡皮半径 7：圆心在 20 时
  // 圆已经压进外沿 2，但离中心线还有 20（老代码比的是 radius + half = 22 ≥ 20）
  const graze = (offset) => splitStroke(row(400, 10, 30), 200, offset, 200, offset, 7);
  assert.equal(graze(20), null);
  assert.notEqual(graze(6), null); // 盖住中心线了才切
});

// ----------------------------------------------------------------- 遮罩

test("object_eraser_ignores_bitten_away_ink：点在已经啃掉的地方不算命中", () => {
  // 那里看着是空的，点下去却删掉一整条会很突兀
  const stroke = { ...row(400, 10, 40), id: "wide", m: [[8, 100, 0, 300, 0]] };
  assert.equal(strokeHit(stroke, 200, 0, 3), false); // 正在缺口里
  assert.equal(strokeHit(stroke, 200, 14, 3), true); // 缺口旁边还有墨迹
  assert.equal(strokeHit(stroke, 20, 0, 3), true); // 没被啃过的一段
});

test("抽稀本身不改形状：离直线比容差远的点留着", () => {
  // 容差取 r/6，和 inkpdf.py 导出时用的是同一个，屏幕和导出才对得上。
  // 30,0 和 0,0 之间那个 20,3 离直线 3 个单位，容差是 8/6，留着
  const stroke = { m: [[8, 0, 0, 10, 0, 20, 3, 30, 0]] };
  const before = JSON.stringify(stroke.m);
  simplifyMask(stroke);
  assert.equal(JSON.stringify(stroke.m), before);
});

// 笔画几何：把采样点变成可填充的轮廓。
//
// 每条笔画生成「一条闭合轮廓」而不是若干段线段的叠加，原因是荧光笔是半透明的：
// 一次 fill 才不会在重叠处出现更深的色块。轮廓在世界坐标里生成并缓存成 Path2D，
// 缩放和平移时直接复用。

import { TAU, clamp } from "./util.js";

export const TOOLS = {
  pen: { alpha: 1, scale: 1 },
  marker: { alpha: 1, scale: 2.6 },
  highlighter: { alpha: 0.3, scale: 6 },
};

// 折角阈值：相邻两段方向差超过这个角度就当硬角处理——轮廓在这里断开、转一段
// 圆弧再继续，而不是让样条把角磨圆。真实手写在屏幕采样密度下，非折角处每个顶点
// 的转角远低于这个值，所以它只会在真的拐角上触发。
const CORNER = (45 * Math.PI) / 180;
// 斜接偏移的最大倍数。折角已经单独处理，剩下的转角都不超过 CORNER，
// 所以这个值就是 1 / cos(CORNER / 2)，包围盒按它留余量。
const MITER_MAX = 1 / Math.cos(CORNER / 2);

/** 把角差折算到 (-π, π]，用来判断转了多少、往哪边转。 */
function turnOf(from, to) {
  let delta = to - from;
  while (delta > Math.PI) delta -= TAU;
  while (delta <= -Math.PI) delta += TAU;
  return delta;
}

/** 单点半径：钢笔跟随压感，马克笔和荧光笔等宽（和 iPad 上的手感一致）。 */
export function strokeRadius(tool, width, pressure) {
  const half = Math.max(0.3, width / 2);
  if (tool !== "pen") return half;
  const p = clamp(pressure, 0, 1);
  return half * (0.42 + 0.58 * Math.pow(p, 0.8));
}

/** 去掉重合点：方向角要靠相邻点算，两点重合会得到无意义的角度。 */
function samplesOf(stroke) {
  const flat = stroke.p;
  const count = (flat.length / 3) | 0;
  const out = [];
  for (let i = 0; i < count; i++) {
    const x = flat[i * 3];
    const y = flat[i * 3 + 1];
    if (out.length) {
      const last = out[out.length - 1];
      if (Math.abs(x - last.x) < 1e-7 && Math.abs(y - last.y) < 1e-7) continue;
    }
    out.push({ x, y, r: Math.max(0.35, strokeRadius(stroke.tool, stroke.w, flat[i * 3 + 2])) });
  }
  return out;
}

/**
 * 一侧的轮廓：连续的偏移点画成穿过每一个点的三次贝塞尔（Catmull-Rom），
 * 遇到折角就收尾、沿笔尖圆转过去、再重新起头。
 *
 * 以前这里用的是「穿过相邻两点中点」的二次曲线，控制点是偏移点本身——二次贝塞尔
 * 永远不经过自己的控制点，所以每个采样点处的轮廓都被往内侧拽，转角越急、采样越疏
 * 削得越平。换成插值曲线之后轮廓真的经过每一个偏移点。
 */
function sideOutline(path, items, startHere) {
  let started = !startHere;
  const step = (p) => {
    if (started) path.lineTo(p.x, p.y);
    else {
      path.moveTo(p.x, p.y);
      started = true;
    }
  };
  let run = [];
  const flush = () => {
    if (!run.length) return;
    step(run[0]);
    for (let i = 0; i + 1 < run.length; i++) {
      const p0 = run[i > 0 ? i - 1 : 0];
      const p1 = run[i];
      const p2 = run[i + 1];
      const p3 = run[i + 2 < run.length ? i + 2 : run.length - 1];
      path.bezierCurveTo(
        p1.x + (p2.x - p0.x) / 6,
        p1.y + (p2.y - p0.y) / 6,
        p2.x - (p3.x - p1.x) / 6,
        p2.y - (p3.y - p1.y) / 6,
        p2.x,
        p2.y
      );
    }
    run = [];
  };
  for (const item of items) {
    if (!item.corner) {
      run.push(item);
      continue;
    }
    flush();
    const { x, y, r } = item.corner;
    if (!started) {
      path.moveTo(x + Math.cos(item.a0) * r, y + Math.sin(item.a0) * r);
      started = true;
    }
    // Path2D.arc 是真圆弧，画布在绘制时按当时的缩放展平，放大不会看出棱
    path.arc(x, y, r, item.a0, item.a1, turnOf(item.a0, item.a1) < 0);
  }
  flush();
}

export function buildPath(stroke) {
  if (stroke._path) return stroke._path;
  const pts = samplesOf(stroke);
  const count = pts.length;
  const path = new Path2D();

  if (count === 0) {
    stroke._path = path;
    return path;
  }
  if (count === 1) {
    const { x, y, r } = pts[0];
    path.moveTo(x + r, y);
    path.arc(x, y, r, 0, TAU);
    stroke._path = path;
    return path;
  }

  // 每一段自己的方向角。法线 = 方向 + 90°。
  const dir = new Array(count - 1);
  for (let i = 0; i + 1 < count; i++) {
    dir[i] = Math.atan2(pts[i + 1].y - pts[i].y, pts[i + 1].x - pts[i].x);
  }

  // 每个采样点在两侧各给一项：普通点是一个斜接偏移点，折角是一段圆弧。
  //
  // 斜接偏移量是 r / cos(转角/2)，不是 r。以前用前后差分的法线配上 r，
  // 等于把外侧的偏移点往里收了 cos(转角/2) 倍——笔画在每个转角都会缩细，
  // 90° 转角只剩 76% 宽，采样一疏更少。
  const left = new Array(count);
  const right = new Array(count);
  for (let i = 0; i < count; i++) {
    const before = dir[i > 0 ? i - 1 : 0];
    const after = dir[i < count - 1 ? i : count - 2];
    const turn = turnOf(before, after);
    const point = pts[i];
    if (Math.abs(turn) > CORNER && i > 0 && i < count - 1) {
      left[i] = { corner: point, a0: before + Math.PI / 2, a1: after + Math.PI / 2 };
      right[i] = { corner: point, a0: before - Math.PI / 2, a1: after - Math.PI / 2 };
      continue;
    }
    const half = turn / 2;
    const reach = point.r / Math.cos(half);
    const normal = before + half + Math.PI / 2;
    const dx = Math.cos(normal) * reach;
    const dy = Math.sin(normal) * reach;
    left[i] = { x: point.x + dx, y: point.y + dy };
    right[i] = { x: point.x - dx, y: point.y - dy };
  }

  // cut 的两位分别表示「这一头是橡皮切出来的」：1 = 起点，2 = 终点。
  // 切口不补半圆笔尖，直接连过去就是一道平口——橡皮扫过去时留下的本来就是
  // 胶囊的直边，补个圆头反而会把缺口填回去一大半。
  const cut = stroke.cut | 0;
  const first = dir[0] + Math.PI / 2;
  const last = dir[count - 2] + Math.PI / 2;
  const tail = pts[count - 1];
  const head = pts[0];

  sideOutline(path, left, true);
  // 切口不画笔尖：下一侧的第一条线段（以及 closePath）自然把平口连出来
  if (!(cut & 2)) path.arc(tail.x, tail.y, tail.r, last, last - Math.PI, true);
  // 反着走另一侧：到达和离开的角度对调
  sideOutline(
    path,
    right
      .slice()
      .reverse()
      .map((item) => (item.corner ? { corner: item.corner, a0: item.a1, a1: item.a0 } : item)),
    false
  );
  if (!(cut & 1)) path.arc(head.x, head.y, head.r, first + Math.PI, first, true);
  path.closePath();

  stroke._path = path;
  return path;
}

export function strokeBBox(stroke) {
  if (stroke._bbox) return stroke._bbox;
  const flat = stroke.p;
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  let maxR = 0;
  for (let i = 0; i < flat.length; i += 3) {
    if (flat[i] < x0) x0 = flat[i];
    if (flat[i] > x1) x1 = flat[i];
    if (flat[i + 1] < y0) y0 = flat[i + 1];
    if (flat[i + 1] > y1) y1 = flat[i + 1];
    const r = strokeRadius(stroke.tool, stroke.w, flat[i + 2]);
    if (r > maxR) maxR = r;
  }
  // 斜接偏移最多到 r * MITER_MAX，包围盒按它留余量，不然折角处会漏出脏矩形
  maxR *= MITER_MAX;
  const bbox = { x0: x0 - maxR, y0: y0 - maxR, x1: x1 + maxR, y1: y1 + maxR, r: maxR };
  stroke._bbox = bbox;
  return bbox;
}

export function drawStroke(ctx, stroke) {
  const style = TOOLS[stroke.tool] || TOOLS.pen;
  ctx.save();
  ctx.fillStyle = stroke.color;
  if (style.alpha < 1) ctx.globalAlpha = style.alpha;
  ctx.fill(buildPath(stroke));
  ctx.restore();
}

function pointSegmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  let t = lengthSq > 0 ? ((px - ax) * dx + (py - ay) * dy) / lengthSq : 0;
  t = clamp(t, 0, 1);
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

/** 橡皮擦命中判定：整笔擦除，判定用点到线段的距离。 */
export function strokeHit(stroke, x, y, radius) {
  const bbox = strokeBBox(stroke);
  if (x < bbox.x0 - radius || x > bbox.x1 + radius) return false;
  if (y < bbox.y0 - radius || y > bbox.y1 + radius) return false;
  const flat = stroke.p;
  const reach = radius + bbox.r;
  if (flat.length === 3) return Math.hypot(flat[0] - x, flat[1] - y) <= reach;
  for (let i = 0; i + 5 < flat.length; i += 3) {
    if (pointSegmentDistance(x, y, flat[i], flat[i + 1], flat[i + 3], flat[i + 4]) <= reach) {
      return true;
    }
  }
  return false;
}

// ------------------------------------------------------- 像素橡皮擦（切笔画）

/**
 * 像素橡皮擦：橡皮从 ``(x0,y0)`` 扫到 ``(x1,y1)``、半径 ``radius``，
 * 把一条笔画切成还活着的几段。
 *
 * 返回 ``null`` 表示这一笔没被碰到，调用方什么都不用做；返回数组表示原来那一笔
 * 要换成这几段（空数组就是整笔都没了）。每一段是 ``{ p, cut }``，``cut`` 标出
 * 哪一头是切出来的，渲染时那一头画平口而不是圆笔尖。
 *
 * 两条判定规则，和以前不一样：
 *
 * * 判「橡皮圆盘盖没盖住**中心线**」，不再加笔画自己的半宽。加了半宽的后果是
 *   橡皮只蹭到笔画外沿，中心线上那个点就被判死，而删掉一个中心线点等于把整个
 *   截面切断——擦到边缘就消失一整截。
 * * 切口落在**真正的交点**上，用二分求出来，不再只能落在采样点上。以前缺口宽度
 *   取决于采样密度（同样的橡皮，密采样缺口 42、疏采样 84），现在恒等于橡皮直径。
 *
 * 笔迹始终是矢量的，所谓「像素橡皮擦」是把笔画切开，不是往位图上抹——位图在
 * 无限画布上没有分辨率可言，导出 PDF 时也会退化成一张栅格图。
 *
 * 切笔画只能整个截面一起断，所以「把一条粗笔画削掉半边」这种它表达不了，
 * 那一类要靠遮罩，见 docs/format.md。
 */
export function splitStroke(stroke, x0, y0, x1, y1, radius) {
  const flat = stroke.p;
  const count = Math.floor(flat.length / 3);
  if (count < 1) return null;

  const bbox = strokeBBox(stroke);
  if (Math.max(x0, x1) + radius < bbox.x0 || Math.min(x0, x1) - radius > bbox.x1) return null;
  if (Math.max(y0, y1) + radius < bbox.y0 || Math.min(y0, y1) - radius > bbox.y1) return null;

  const inside = (px, py) => pointSegmentDistance(px, py, x0, y0, x1, y1) <= radius;

  const runs = [];
  let run = null;
  let touched = false;
  const open = (cutStart) => {
    run = { p: [], cut: cutStart ? 1 : 0 };
    runs.push(run);
  };
  const add = (x, y, pressure) => run.p.push(x, y, pressure);

  let cur = inside(flat[0], flat[1]);
  if (cur) touched = true;
  else {
    open(false);
    add(flat[0], flat[1], flat[2]);
  }

  for (let i = 0; i + 1 < count; i++) {
    const ax = flat[i * 3];
    const ay = flat[i * 3 + 1];
    const ap = flat[i * 3 + 2];
    const bx = flat[i * 3 + 3];
    const by = flat[i * 3 + 4];
    const bp = flat[i * 3 + 5];
    // 在这一段上撒点找出入边界。撒多密按橡皮大小定：一段里橡皮最多进出一次，
    // 半个橡皮的步长足够不漏。
    const span = Math.hypot(bx - ax, by - ay);
    const steps = clamp(Math.ceil(span / Math.max(0.5, radius / 2)), 1, 32);
    let curT = 0;
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      const next = inside(ax + (bx - ax) * t, ay + (by - ay) * t);
      if (next === cur) continue;
      touched = true;
      // 二分收窄到内外分界：lo 在外、hi 在内
      let lo = next ? curT : t;
      let hi = next ? t : curT;
      for (let step = 0; step < 12; step++) {
        const mid = (lo + hi) / 2;
        if (inside(ax + (bx - ax) * mid, ay + (by - ay) * mid)) hi = mid;
        else lo = mid;
      }
      const tc = (lo + hi) / 2;
      const cx = ax + (bx - ax) * tc;
      const cy = ay + (by - ay) * tc;
      const cp = ap + (bp - ap) * tc;
      if (next) {
        if (run) {
          add(cx, cy, cp);
          run.cut |= 2;
        }
        run = null;
      } else {
        open(true);
        add(cx, cy, cp);
      }
      cur = next;
      curT = t;
    }
    if (cur) touched = true;
    else {
      if (!run) open(false);
      add(bx, by, bp);
    }
  }
  if (!touched) return null;
  // 只剩一个点的碎屑不留，擦完一地小点比没擦干净还难看
  return runs.filter((r) => r.p.length >= 6);
}

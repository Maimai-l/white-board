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

const CAP_STEPS = 12;

/** 单点半径：钢笔跟随压感，马克笔和荧光笔等宽（和 iPad 上的手感一致）。 */
export function strokeRadius(tool, width, pressure) {
  const half = Math.max(0.3, width / 2);
  if (tool !== "pen") return half;
  const p = clamp(pressure, 0, 1);
  return half * (0.42 + 0.58 * Math.pow(p, 0.8));
}

function sidePath(path, points, start) {
  if (start) path.moveTo(points[0].x, points[0].y);
  else path.lineTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length - 1; i++) {
    const mx = (points[i].x + points[i + 1].x) / 2;
    const my = (points[i].y + points[i + 1].y) / 2;
    path.quadraticCurveTo(points[i].x, points[i].y, mx, my);
  }
  const last = points[points.length - 1];
  path.lineTo(last.x, last.y);
}

/** 半圆笔尖：从 angle 起顺着行笔方向转半圈。 */
function cap(path, cx, cy, radius, angle) {
  for (let i = 1; i <= CAP_STEPS; i++) {
    const a = angle - (Math.PI * i) / CAP_STEPS;
    path.lineTo(cx + Math.cos(a) * radius, cy + Math.sin(a) * radius);
  }
}

export function buildPath(stroke) {
  if (stroke._path) return stroke._path;
  const flat = stroke.p;
  const count = (flat.length / 3) | 0;
  const path = new Path2D();

  if (count === 0) {
    stroke._path = path;
    return path;
  }
  if (count === 1) {
    const r = Math.max(0.35, strokeRadius(stroke.tool, stroke.w, flat[2]));
    path.moveTo(flat[0] + r, flat[1]);
    path.arc(flat[0], flat[1], r, 0, TAU);
    stroke._path = path;
    return path;
  }

  const pts = new Array(count);
  for (let i = 0; i < count; i++) {
    pts[i] = {
      x: flat[i * 3],
      y: flat[i * 3 + 1],
      r: Math.max(0.35, strokeRadius(stroke.tool, stroke.w, flat[i * 3 + 2])),
    };
  }

  const left = new Array(count);
  const right = new Array(count);
  let nx = 0;
  let ny = -1;
  let firstAngle = 0;
  let lastAngle = 0;
  for (let i = 0; i < count; i++) {
    const prev = pts[Math.max(0, i - 1)];
    const next = pts[Math.min(count - 1, i + 1)];
    const dx = next.x - prev.x;
    const dy = next.y - prev.y;
    const len = Math.hypot(dx, dy);
    if (len > 1e-6) {
      // 法线 = 切线逆时针转 90°
      nx = -dy / len;
      ny = dx / len;
    }
    const p = pts[i];
    left[i] = { x: p.x + nx * p.r, y: p.y + ny * p.r };
    right[i] = { x: p.x - nx * p.r, y: p.y - ny * p.r };
    if (i === 0) firstAngle = Math.atan2(ny, nx);
    lastAngle = Math.atan2(ny, nx);
  }

  // cut 的两位分别表示「这一头是橡皮切出来的」：1 = 起点，2 = 终点。
  // 切口不补半圆笔尖，直接连过去就是一道平口——橡皮扫过去时留下的本来就是
  // 胶囊的直边，补个圆头反而会把缺口填回去一大半。
  const cut = stroke.cut | 0;
  sidePath(path, left, true);
  if (!(cut & 2)) cap(path, pts[count - 1].x, pts[count - 1].y, pts[count - 1].r, lastAngle);
  sidePath(path, right.reverse(), false);
  if (!(cut & 1)) cap(path, pts[0].x, pts[0].y, pts[0].r, firstAngle + Math.PI);
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

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

  sidePath(path, left, true);
  cap(path, pts[count - 1].x, pts[count - 1].y, pts[count - 1].r, lastAngle);
  sidePath(path, right.reverse(), false);
  cap(path, pts[0].x, pts[0].y, pts[0].r, firstAngle + Math.PI);
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

/** 两条线段是否相交。用叉积定向，共线的退化情况交给下面的端点距离兜底。 */
function segmentsCross(ax, ay, bx, by, cx, cy, dx, dy) {
  const side = (px, py, qx, qy, rx, ry) =>
    Math.sign((qx - px) * (ry - py) - (qy - py) * (rx - px));
  const d1 = side(ax, ay, bx, by, cx, cy);
  const d2 = side(ax, ay, bx, by, dx, dy);
  const d3 = side(cx, cy, dx, dy, ax, ay);
  const d4 = side(cx, cy, dx, dy, bx, by);
  return d1 !== d2 && d3 !== d4 && d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0;
}

/** 两条线段之间的最短距离。 */
function segmentDistance(ax, ay, bx, by, cx, cy, dx, dy) {
  if (segmentsCross(ax, ay, bx, by, cx, cy, dx, dy)) return 0;
  return Math.min(
    pointSegmentDistance(ax, ay, cx, cy, dx, dy),
    pointSegmentDistance(bx, by, cx, cy, dx, dy),
    pointSegmentDistance(cx, cy, ax, ay, bx, by),
    pointSegmentDistance(dx, dy, ax, ay, bx, by)
  );
}

/**
 * 像素橡皮擦：橡皮从 ``(x0,y0)`` 扫到 ``(x1,y1)``、半径 ``radius``，
 * 把一条笔画切成还活着的几段。
 *
 * 返回 ``null`` 表示这一笔没被碰到，调用方什么都不用做；返回数组表示原来那一笔
 * 要换成这几段点列（空数组就是整笔都没了）。笔迹始终是矢量的，所谓「像素橡皮擦」
 * 是把笔画切开，不是往位图上抹——位图在无限画布上没有分辨率可言，导出 PDF 时
 * 也会退化成一张栅格图。
 *
 * 判定用的是「笔迹够不够得着橡皮扫过的那条线段」，也就是中心线的距离要减去
 * 笔自己的半宽。所以比橡皮粗的笔会被整条切断，而不是被啃掉一半——切笔画这个
 * 做法本来就只能整个截面一起断。
 */
export function splitStroke(stroke, x0, y0, x1, y1, radius) {
  const flat = stroke.p;
  const count = Math.floor(flat.length / 3);
  if (count < 1) return null;

  const bbox = strokeBBox(stroke);
  const lo = Math.min(x0, x1) - radius;
  const hi = Math.max(x0, x1) + radius;
  if (hi < bbox.x0 || lo > bbox.x1) return null;
  const top = Math.min(y0, y1) - radius;
  const bottom = Math.max(y0, y1) + radius;
  if (bottom < bbox.y0 || top > bbox.y1) return null;

  const half = (i) => strokeRadius(stroke.tool, stroke.w, flat[i * 3 + 2]);
  const hit = new Array(count).fill(false);
  let any = false;
  for (let i = 0; i < count; i++) {
    const d = pointSegmentDistance(flat[i * 3], flat[i * 3 + 1], x0, y0, x1, y1);
    if (d <= radius + half(i)) {
      hit[i] = true;
      any = true;
    }
  }
  // 采样点之间可能隔得很开（写得快的时候），橡皮从两点中间穿过去时两端都没命中，
  // 这里再按线段判一次，否则快速划过只会留下一串没断开的笔画。
  for (let i = 0; i + 1 < count; i++) {
    // 只补「两端都没命中、橡皮却从中间穿过去」这一种；有一端已经命中就不用再判，
    // 否则会把另一端那个离得很远的点也一起标掉，切口白白宽出一截
    if (hit[i] || hit[i + 1]) continue;
    const reach = radius + Math.max(half(i), half(i + 1));
    const d = segmentDistance(
      flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 3], flat[i * 3 + 4],
      x0, y0, x1, y1
    );
    if (d <= reach) {
      hit[i] = true;
      hit[i + 1] = true;
      any = true;
    }
  }
  if (!any) return null;

  const runs = [];
  let run = null;
  for (let i = 0; i < count; i++) {
    if (hit[i]) {
      run = null;
      continue;
    }
    if (!run) {
      run = [];
      runs.push(run);
    }
    run.push(flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]);
  }
  // 只剩一个点的碎屑不留，擦完一地小点比没擦干净还难看
  return runs.filter((points) => points.length >= 6);
}

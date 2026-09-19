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

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

/**
 * 画一条笔画。遮罩不在这里处理——擦掉的地方由 renderer.js 在这一笔画完之后
 * 把背景重新画回去，见 paintStrokes。
 */
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

/** 这个位置的墨迹是不是已经被遮罩啃掉了。 */
export function maskCovers(stroke, x, y) {
  for (const chain of stroke.m || []) {
    const r = chain[0];
    const count = (chain.length - 1) / 2;
    if (count === 1) {
      if (Math.hypot(chain[1] - x, chain[2] - y) <= r) return true;
      continue;
    }
    for (let i = 0; i + 1 < count; i++) {
      const d = pointSegmentDistance(
        x, y, chain[1 + i * 2], chain[2 + i * 2], chain[3 + i * 2], chain[4 + i * 2]
      );
      if (d <= r) return true;
    }
  }
  return false;
}

/**
 * 橡皮擦命中判定：整笔擦除，判定用点到线段的距离。
 *
 * 点在已经被啃掉的地方不算命中——那里看着是空的，点下去却删掉一整条笔画
 * 会很突兀。
 */
export function strokeHit(stroke, x, y, radius) {
  if (stroke.m && stroke.m.length && maskCovers(stroke, x, y)) return false;
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

// ------------------------------------------------------------- 遮罩（啃边）
//
// 切笔画只能整个截面一起断，所以橡皮比笔细的时候它表达不了「削掉一条边」
// 「正中啃一个坑」。这一类改成给笔画挂一个遮罩：记下橡皮扫过的胶囊，渲染时用
// even-odd 裁剪把它们从轮廓里抠掉。笔画本身不动，一次填充照旧，荧光笔重叠
// 不变深这个性质保住；PDF 里有同一套语义的 `W* n`。
//
// 遮罩必须稀少：实测三千笔整屏重绘，带遮罩的笔每条每帧约多 5.5 µs，两成带遮罩
// 是 3.2 ms，全都带遮罩就要 22 ms。所以能切断的一律切断（不留遮罩），
// 只有切不断的才记遮罩。

/**
 * 一条笔画最多挂多少段胶囊。超了就把遮罩落实成切分，见 app.js 的 bakeMask。
 *
 * 渲染改成「在胶囊的并集里把背景画回去」之后，开销不再和带遮罩的笔画数成正比，
 * 只和路径本身的大小有关，所以这个上限可以放得比原来宽很多。落实那一步会把
 * 贴边的细条一起清掉，是看得见的变化，要尽量少触发。
 */
export const MASK_LIMIT = 400;

/** 两条线段是否相交。用叉积定向，共线的退化情况交给端点距离兜底。 */
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
 * 中心线离这条扫掠线段最近有多远，以及那里的半宽。
 *
 * 量的是**线段到线段**的距离，不是采样点到线段——采样点之间能隔十几个单位，
 * 橡皮从两点中间穿过去时，两个端点离它都很远，只看点会得出「没碰到」。
 */
function halfWidthNear(stroke, x0, y0, x1, y1) {
  const flat = stroke.p;
  const count = (flat.length / 3) | 0;
  const radiusAt = (i) => Math.max(0.35, strokeRadius(stroke.tool, stroke.w, flat[i * 3 + 2]));
  if (count === 1) {
    return {
      half: radiusAt(0),
      dist: pointSegmentDistance(flat[0], flat[1], x0, y0, x1, y1),
    };
  }
  let best = Infinity;
  let half = radiusAt(0);
  for (let i = 0; i + 1 < count; i++) {
    const d = segmentDistance(
      flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 3], flat[i * 3 + 4],
      x0, y0, x1, y1
    );
    if (d < best) {
      best = d;
      half = Math.max(radiusAt(i), radiusAt(i + 1));
    }
  }
  return { half, dist: best };
}

/**
 * 这一下橡皮有没有碰到这条笔画的墨迹。
 *
 * 像素橡皮只有一种处理方式：记进遮罩。曾经按「橡皮半径是否不小于笔画半宽」
 * 分成「切断」和「啃」两种走法，那是错的——压感沿笔画变化，局部半宽跟着变，
 * 同一次拖动走到一半判定就会跨过阈值：前半截被切出平口断面、后半截变成啃，
 * 来回跳。实测一条压感由轻到重的笔画，笔宽 26 时一次拖动里 11 个事件走切断、
 * 77 个走啃，中途换了两次。
 *
 * 原生也是只记遮罩：笔画断开是遮罩把它截断的结果，不是另一种模式，断开之后
 * 两段各自还带着自己的遮罩。
 */
export function eraseKind(stroke, x0, y0, x1, y1, radius) {
  const bbox = strokeBBox(stroke);
  if (Math.max(x0, x1) + radius < bbox.x0 || Math.min(x0, x1) - radius > bbox.x1) return null;
  if (Math.max(y0, y1) + radius < bbox.y0 || Math.min(y0, y1) - radius > bbox.y1) return null;
  const { half, dist } = halfWidthNear(stroke, x0, y0, x1, y1);
  return dist > radius + half ? null : "bite";
}

/** 一段胶囊（``[r, x0, y0, x1, y1, ...]``）的包围盒。 */
function chainBBox(chain) {
  const r = chain[0];
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let i = 1; i + 1 < chain.length; i += 2) {
    if (chain[i] < x0) x0 = chain[i];
    if (chain[i] > x1) x1 = chain[i];
    if (chain[i + 1] < y0) y0 = chain[i + 1];
    if (chain[i + 1] > y1) y1 = chain[i + 1];
  }
  return { x0: x0 - r, y0: y0 - r, x1: x1 + r, y1: y1 + r };
}

/** 这段胶囊是不是整条都落在 ``(x0,y0)-(x1,y1)`` 半径 ``radius`` 的胶囊里。 */
function chainInside(chain, x0, y0, x1, y1, radius) {
  if (chain[0] > radius) return false;
  const slack = radius - chain[0];
  for (let i = 1; i + 1 < chain.length; i += 2) {
    if (pointSegmentDistance(chain[i], chain[i + 1], x0, y0, x1, y1) > slack) return false;
  }
  return true;
}

/**
 * 一条笔画最多多少个采样点。超了就在提交时切成几段。
 *
 * 服务端对超长点列是**整条丢掉**而不是截断（`models.sanitize_stroke`）。不切的话
 * 一笔画得够久就会本机看得见、对端和存档里没有——和遮罩被截断是同一类问题：
 * 本机显示和存下来的东西不一致，而且只有重新载入才看得出来。
 *
 * 这个值必须不大于服务端的 `MAX_POINTS_PER_STROKE`，`tests/test_models.py` 里有
 * 用例把两边钉在一起。
 */
export const MAX_STROKE_POINTS = 20000;

/**
 * 太长的一笔切成几段，返回一个数组；没超长就原样返回 ``[stroke]``。
 *
 * 接缝处两段共用同一个采样点，两端都是默认的圆头，叠在一起看不出接缝。
 * 切出来的段不打 ``cut`` 标记——那是橡皮切出来的平口，这里不是。
 */
export function splitLongStroke(stroke, makeId) {
  const flat = stroke.p;
  const count = flat.length / 3;
  if (count <= MAX_STROKE_POINTS) return [stroke];
  const out = [];
  // 每段末尾那个点也是下一段的开头，所以每段实际前进 MAX-1 个点
  const step = MAX_STROKE_POINTS - 1;
  for (let start = 0; start < count - 1; start += step) {
    const end = Math.min(start + MAX_STROKE_POINTS, count);
    const piece = { ...stroke, p: flat.slice(start * 3, end * 3) };
    if (out.length) piece.id = makeId();
    out.push(piece);
  }
  return out;
}

/**
 * 判「同一次扫掠」时半径允许差多少（相对值）。
 *
 * 橡皮的粗细每个采样点都重新平滑一次，收敛是指数的，永远差那么一点点，所以
 * 拿严格相等去判等于判不出来：一份真机录像里一次擦除攒出 113 条链、82 个互不
 * 相同的半径，而它们在两位小数上全是同一个值。链上记的半径不跟着动，所以链里
 * 每一段和它本来的半径最多差这么多，也不会随着链变长一路漂上去。
 */
const SWEEP_RADIUS_TOLERANCE = 0.01;

/**
 * 往笔画的遮罩里加一段橡皮扫掠。改了返回 ``true``。
 *
 * 同一次拖动里的连续几段会接成一条链（上一段的终点就是这一段的起点），
 * 顺带把被新胶囊整个盖住的旧段丢掉——来回涂同一块地方是遮罩堆积的主要来源。
 */
export function addMask(stroke, x0, y0, x1, y1, radius) {
  const chains = (stroke.m || []).filter((c) => !chainInside(c, x0, y0, x1, y1, radius));
  const last = chains[chains.length - 1];
  const sameSweep =
    last &&
    Math.abs(last[0] - radius) <= last[0] * SWEEP_RADIUS_TOLERANCE &&
    Math.abs(last[last.length - 2] - x0) < 1e-6 &&
    Math.abs(last[last.length - 1] - y0) < 1e-6;
  if (sameSweep) last.push(x1, y1);
  else chains.push([radius, x0, y0, x1, y1]);
  stroke.m = chains;
  return true;
}

/**
 * 一串点按 Ramer-Douglas-Peucker 抽稀，容差是 tol。
 *
 * 用循环而不是递归：一次长擦除的链能有上千个点，递归深度不可控。
 */
function thinPoints(pts, tol) {
  const n = pts.length / 2;
  if (n < 3) return pts.slice();
  const keep = new Uint8Array(n);
  keep[0] = 1;
  keep[n - 1] = 1;
  const stack = [0, n - 1];
  while (stack.length) {
    const b = stack.pop();
    const a = stack.pop();
    let far = -1;
    let best = tol;
    for (let i = a + 1; i < b; i++) {
      const d = pointSegmentDistance(
        pts[i * 2], pts[i * 2 + 1],
        pts[a * 2], pts[a * 2 + 1], pts[b * 2], pts[b * 2 + 1]
      );
      if (d > best) {
        best = d;
        far = i;
      }
    }
    if (far < 0) continue;
    keep[far] = 1;
    stack.push(a, far, far, b);
  }
  const out = [];
  for (let i = 0; i < n; i++) {
    if (keep[i]) out.push(pts[i * 2], pts[i * 2 + 1]);
  }
  return out;
}

/**
 * 把遮罩抽稀。改了返回 ``true``。
 *
 * 一次擦除每来一个采样点就多一段胶囊，而相邻几段在近乎笔直的一段上几乎完全
 * 重合。裁剪的开销随段数是平方涨的——实测一条笔画挂 400 段整屏重画 2.9 ms、
 * 1000 段 15 ms、2000 段 57 ms，所以段数不能靠放宽上限来解决，只能真的变少。
 *
 * 容差取 ``r / 6``，和导出 PDF 时 ``inkpdf.py`` 的 ``_capsules`` 是同一个尺度：
 * 那边一直是这么抽的，形状看不出变化，两边用同一个容差屏幕和导出才对得上。
 */
export function simplifyMask(stroke) {
  const chains = stroke.m;
  if (!chains || !chains.length) return false;
  let changed = false;
  const out = [];
  for (const chain of chains) {
    const radius = chain[0];
    const thin = thinPoints(chain.slice(1), radius / 6);
    if (thin.length + 1 < chain.length) changed = true;
    out.push([radius, ...thin]);
  }
  if (changed) stroke.m = out;
  return changed;
}

/** 遮罩里总共有多少段胶囊。 */
export function maskSize(stroke) {
  let total = 0;
  for (const chain of stroke.m || []) total += Math.max(1, (chain.length - 3) / 2);
  return total;
}

/** 把一段胶囊加进路径：两侧直边 + 两头半圆。 */
function capsule(path, chain) {
  const r = chain[0];
  const count = (chain.length - 1) / 2;
  if (count === 1) {
    path.moveTo(chain[1] + r, chain[2]);
    path.arc(chain[1], chain[2], r, 0, TAU);
    return;
  }
  for (let i = 0; i + 1 < count; i++) {
    const ax = chain[1 + i * 2];
    const ay = chain[2 + i * 2];
    const bx = chain[3 + i * 2];
    const by = chain[4 + i * 2];
    const a = Math.atan2(by - ay, bx - ax) + Math.PI / 2;
    // 一段一个独立子路径，绕向一致，even-odd 下正好抠掉它们的并集
    path.moveTo(ax + Math.cos(a) * r, ay + Math.sin(a) * r);
    path.lineTo(bx + Math.cos(a) * r, by + Math.sin(a) * r);
    path.arc(bx, by, r, a, a - Math.PI, true);
    path.lineTo(ax + Math.cos(a - Math.PI) * r, ay + Math.sin(a - Math.PI) * r);
    path.arc(ax, ay, r, a + Math.PI, a, true);
    path.closePath();
  }
}

/**
 * 把这条笔画被啃掉的那几块**并集**加进路径，用 nonzero 填充。
 *
 * 原来这里是「外框减去胶囊、按 even-odd 裁剪」，那是错的：even-odd 算的是对称差
 * 不是并集，而一次拖动里相邻两段胶囊在共用的那个圆端点处必然重叠，重叠区域被算
 * 两次成了偶数，于是判定成「不擦」——擦痕每隔一个采样点就留下一块正好等于橡皮
 * 直径的没擦掉的方块，看上去是一排断开的小块。
 *
 * 现在不减了，改成把并集画出来：同向绕的子路径用 nonzero 正好是并集，重叠多少次
 * 都不会互相抵消。渲染时在这块并集里把背景重新画一遍，见 renderer.js 的
 * paintStrokes。
 */
export function addMaskPath(path, stroke) {
  for (const chain of stroke.m || []) capsule(path, chain);
  return path;
}

/** 遮罩覆盖到的范围，用来标脏矩形：啃一口不用重画整条笔画。 */
export function maskBounds(chains) {
  let box = null;
  for (const chain of chains) {
    const b = chainBBox(chain);
    if (!box) box = { ...b };
    else {
      box.x0 = Math.min(box.x0, b.x0);
      box.y0 = Math.min(box.y0, b.y0);
      box.x1 = Math.max(box.x1, b.x1);
      box.y1 = Math.max(box.y1, b.y1);
    }
  }
  return box;
}

/** 把遮罩里和这一段点列不沾边的胶囊去掉：切笔画之后每一段只留自己用得上的。 */
export function maskFor(chains, bbox) {
  const kept = [];
  for (const chain of chains) {
    const b = chainBBox(chain);
    if (b.x1 < bbox.x0 || b.x0 > bbox.x1 || b.y1 < bbox.y0 || b.y0 > bbox.y1) continue;
    kept.push(chain.slice());
  }
  return kept;
}

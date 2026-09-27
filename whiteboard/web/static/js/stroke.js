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

/**
 * 丢掉那些「自己的圆已经被前后两点的凸包整个包住」的采样点。
 *
 * 形状一点不变——被包住就是被包住——但路径能小一半还多。真机上一笔的采样密度
 * 常常是笔半径的六倍，直路段上一串点画出来的凸包几乎完全重合，全是白给的指令。
 * 屏幕上少花建路径的时间，导出的 PDF 直接少一半体积。
 */
function dropCoveredPoints(pts) {
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  let anchor = pts[0];
  let i = 1;
  while (i < pts.length - 1) {
    // 往前看，能跳多远跳多远；看太远收益递减，也会把复杂度推成平方
    let reach = i;
    for (let end = i + 1; end < pts.length && end - i <= LOOK_AHEAD; end++) {
      let covered = true;
      for (let k = i; k < end; k++) {
        if (!discInsideHull(pts[k], anchor, pts[end])) {
          covered = false;
          break;
        }
      }
      if (!covered) break;
      reach = end;
    }
    if (reach > i) {
      anchor = pts[reach];
      out.push(anchor);
      i = reach + 1;
    } else {
      anchor = pts[i];
      out.push(anchor);
      i += 1;
    }
  }
  if (out[out.length - 1] !== pts[pts.length - 1]) out.push(pts[pts.length - 1]);
  return out;
}

const LOOK_AHEAD = 24;

/**
 * 点 c 的圆是不是整个落在 a、b 两个圆的凸包里。
 *
 * 凸包就是「圆心沿 ab 线性插值、半径也线性插值」扫出来的那一片，所以把 c 投影到
 * ab 上取出那一处的半径，比一下就够了。投影落在两端之外就不算包住。
 */
function discInsideHull(c, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq < 1e-12) return c.r <= Math.max(a.r, b.r);
  let t = ((c.x - a.x) * dx + (c.y - a.y) * dy) / lengthSq;
  if (t < 0 || t > 1) return false;
  const rt = a.r + (b.r - a.r) * t;
  return Math.hypot(c.x - (a.x + dx * t), c.y - (a.y + dy * t)) + c.r <= rt;
}

/**
 * 一段笔画的形状：圆沿着路径扫过去扫出来的那一片。
 *
 * 画法是「逐段求并」——每个采样点一个圆，每两点之间一个梯形（两个圆的外公切线
 * 围出来的），全部同一个绕向，用 nonzero 填充。同向的子路径在 nonzero 下正好是
 * 并集，所以这就是扫掠区域的准确形状。
 *
 * 之前是另一种画法：两侧各算一条斜接偏移线，接成**一条**闭合回路。那条路在采样
 * 比笔粗密的时候必然出问题——斜接偏移量是 ``r / cos(转角/2)``，真机上笔半径约
 * 2.9 而采样间距只有 0.47，内侧偏移点被推出去的距离是相邻点间距的六倍，一转弯
 * 就折回去自交；自交出来的小环绕向和主体相反，nonzero 下算 0，于是笔画里出现
 * 白色缺口。同一批采样点还会让方向估计很抖（iPad 报的坐标是整像素的），4%～11%
 * 的转角被判成大于 45° 的硬角，轮廓在那里断开接直线，边上就出现大棱。
 *
 * 逐段求并没有这两个问题：并集是逐段算的，不存在「一条回路」，自交无从谈起；
 * 外边界就是两圆的公切线，也不需要任何斜接或折角处理。
 *
 * 绕向必须全部一致。圆用 ``anticlockwise = true`` 是为了和梯形的点序对上——
 * 反了的话 nonzero 会把它们互相抵消，画出来是一堆黑白相间的碎块。
 */
/**
 * 一段笔画的形状：圆沿着路径扫过去扫出来的那一片。
 *
 * 画法是逐段求并——每两个相邻采样点画一段「两圆的凸包」（两条外公切线加前端那个
 * 大圆弧），全部同一个绕向，用 nonzero 填充。同向的子路径在 nonzero 下正好是并集，
 * 所以这就是扫掠区域的准确形状，不需要任何「这个圆是不是多余」的判断。
 *
 * 之前是另一种画法：两侧各算一条斜接偏移线，接成**一条**闭合回路。那条路在采样
 * 比笔粗密的时候必然出问题——斜接偏移量是 ``r / cos(转角/2)``，真机上笔半径约
 * 2.9 而采样间距只有 0.47，内侧偏移点被推出去的距离是相邻点间距的六倍，一转弯
 * 就折回去自交；自交出来的小环绕向和主体相反，nonzero 下算 0，笔画里就出现白色
 * 缺口。同一批采样点还会让方向估计很抖（iPad 报的坐标是整像素的），4%～11% 的
 * 转角被判成大于 45° 的硬角，轮廓在那里断开接直线，边上就出现大棱。
 *
 * 逐段求并没有这两个问题：并集是逐段算的，不存在「一条回路」，自交无从谈起；
 * 外边界就是两圆的公切线，也不需要任何斜接或折角处理。
 */
export function buildPath(stroke) {
  if (stroke._path) return stroke._path;
  const pts = dropCoveredPoints(samplesOf(stroke));
  const count = pts.length;
  const path = new Path2D();
  stroke._path = path;
  if (count === 0) return path;

  if (count === 1) {
    const { x, y, r } = pts[0];
    path.moveTo(x + r, y);
    path.arc(x, y, r, 0, TAU, true);
    return path;
  }

  // cut 的两位分别表示「这一头是橡皮切出来的」：1 = 起点，2 = 终点。
  // 切口不画那一头的圆弧，留下的直边就是橡皮胶囊的切口。
  const cut = stroke.cut | 0;
  // 每一段只画前端那个圆帽：后一段的起点圆已经被前一段的末端圆帽盖住了，
  // 两边都画等于每个中间的圆画两遍。开头那个圆没人盖，单独补一个。
  if (!(cut & 1)) {
    const head = pts[0];
    path.moveTo(head.x + head.r, head.y);
    path.arc(head.x, head.y, head.r, 0, TAU, true);
  }
  for (let i = 0; i + 1 < count; i++) {
    hullPath(path, pts[i], pts[i + 1], !(i === count - 2 && cut & 2));
  }
  return path;
}

/**
 * 两个圆的凸包（只画前端的圆帽）：两条外公切线，加绕过 b 的那段大圆弧。
 *
 * a 那头的圆由上一段的圆帽盖住，所以这里不画；整笔开头那个圆在 buildPath 里补。
 *
 * 半径不同时切点不在法线上，要沿着连线方向偏 ``(r0 - r1) / d``。一个圆整个套在
 * 另一个里面时没有外公切线，退化成画大的那个圆——那本来就是这一段的形状。
 *
 * 绕向固定是顺着「左切线 → 绕过 b → 右切线 → 回到 a」这一圈，和方向无关，
 * 所以每一段绕向都一样，nonzero 下才是并集而不是互相抵消。
 */
function hullPath(path, a, b, roundEnd) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const d = Math.hypot(dx, dy);
  if (d <= Math.abs(a.r - b.r)) {
    const big = a.r >= b.r ? a : b;
    path.moveTo(big.x + big.r, big.y);
    path.arc(big.x, big.y, big.r, 0, TAU, true);
    return;
  }
  const dir = Math.atan2(dy, dx);
  // 切点相对于方向偏开的角：半径相等时是 90°
  const spread = Math.acos(clamp((a.r - b.r) / d, -1, 1));
  const left = dir + spread;
  const right = dir - spread;
  path.moveTo(a.x + Math.cos(left) * a.r, a.y + Math.sin(left) * a.r);
  path.lineTo(b.x + Math.cos(left) * b.r, b.y + Math.sin(left) * b.r);
  if (roundEnd) path.arc(b.x, b.y, b.r, left, right, true);
  else path.lineTo(b.x + Math.cos(right) * b.r, b.y + Math.sin(right) * b.r);
  path.lineTo(a.x + Math.cos(right) * a.r, a.y + Math.sin(right) * a.r);
  path.closePath();
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
  // 逐段求并之后，墨迹最远就到采样点外 r（圆本身），不再有斜接的额外外扩
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

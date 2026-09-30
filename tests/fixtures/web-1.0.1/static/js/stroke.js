// 笔画几何：把采样点变成可填充的轮廓。
//
// 每条笔画生成「一条闭合轮廓」而不是若干段线段的叠加，原因是荧光笔是半透明的：
// 一次 fill 才不会在重叠处出现更深的色块。轮廓在世界坐标里生成并缓存成 Path2D，
// 缩放和平移时直接复用。

import { getStrokeOutlinePoints, getStrokePoints } from "./vendor/perfect-freehand.js";
import { TAU, clamp } from "./util.js";

export const TOOLS = {
  pen: { alpha: 1, scale: 1 },
  marker: { alpha: 1, scale: 2.6 },
  highlighter: { alpha: 0.3, scale: 6 },
};

// 压感通道的膝点。iPad Safari 报的 `event.pressure` 不是铺满 0～1 的：真机录像里
// 一整段手写都挤在 0.007～0.125（中位数 0.028），也就是说有用的量程只有八分之一。
// 所以先把读数按膝点展开再算粗细，`KNEE` 取 0.05——正好让「放松写字」的 0.028 落在
// 0.38 左右，往上还留着加力的余量。
const PEN_KNEE = 0.05;

// 展开之后的曲线。`FLOOR` 是压感趋零时还剩多少（不能真收到 0，中途一次低读数会
// 把线掐断），`FLOOR + (1 - FLOOR) = 1` 保证「用满力就是设定的线宽」——这一条和
// 以前一样，线宽滑块的含义没变。指数 1.2 是按「0.028 仍然画出设定线宽的 0.45 倍」
// 定的，所以这次改动不会让已经写好的字整体变粗或变细，只是把粗细的动态范围拉开：
// 真机常用的 0.023～0.080 一段，以前是 0.448～0.497（差 11%），现在是 0.41～0.67
// （差 63%）。
const PEN_FLOOR = 0.2;
const PEN_GAMMA = 1.2;

/**
 * 压感读数展开成 0～1 的「力度」。
 *
 * 顺带说明为什么没有直接照搬 atrament：它的 `#getWeightWithPressure` 以 0.5 为轴，
 * 0.5 以下是 `weight * 2p`。这支笔报的是 0.028，照它算出来是设定线宽的 5.6%，
 * 一条头发丝。那条曲线假设设备把 0～1 用满，而 iPad Safari 并没有。
 */
export function penForce(pressure) {
  const p = clamp(pressure, 0, 1);
  return (p * (1 + PEN_KNEE)) / (p + PEN_KNEE);
}

/** 单点半径：钢笔跟随压感，马克笔和荧光笔等宽（和 iPad 上的手感一致）。 */
export function strokeRadius(tool, width, pressure) {
  const half = Math.max(0.3, width / 2);
  if (tool !== "pen") return half;
  const force = penForce(pressure);
  return half * (PEN_FLOOR + (1 - PEN_FLOOR) * Math.pow(force, PEN_GAMMA));
}

/**
 * 反解：想让笔迹粗到设定线宽的 `factor` 倍，压感通道该存什么值。
 *
 * 鼠标、手指和不带压感的笔都没有压感读数，它们的粗细是另外算出来的（走得快就细），
 * 但文件里每个点只有一个压感字段，所以得把想要的粗细折回压感值再存。这样
 * `strokeRadius` 始终只是「压感 → 粗细」一个函数，导出 PDF 和重新打开文件都对得上。
 */
export function pressureForFactor(factor) {
  const f = clamp(factor, PEN_FLOOR, 1);
  const force = Math.pow((f - PEN_FLOOR) / (1 - PEN_FLOOR), 1 / PEN_GAMMA);
  return clamp((force * PEN_KNEE) / (1 + PEN_KNEE - force), 0, 1);
}

/**
 * 交给 perfect-freehand 的「压感」。
 *
 * 它的半径公式是 ``size * easing(0.5 - thinning * (0.5 - p))``。取 ``thinning = 1``
 * 之后这个式子塌成 ``size * p``，所以只要把 ``size`` 设成笔宽、``p`` 设成
 * 「半径 ÷ 笔宽」，算出来的半径就正好是 strokeRadius 给的那个数。
 *
 * 这样做是为了把两件事分开：粗细还是我们自己那条按 iPad 实际量程定的曲线
 * （见 strokeRadius），轮廓的形状交给 perfect-freehand。它自带的 thinning 是按
 * 「设备把 0～1 用满」写的，这台设备只报 0.003～0.13，直接用等于没有压感。
 */
function outlinePressure(stroke, pressure) {
  return strokeRadius(stroke.tool, stroke.w, pressure) / Math.max(stroke.w, 1e-6);
}

/**
 * 笔画的轮廓点列，由 perfect-freehand 生成，结果缓存在 ``stroke._pts`` 上。
 *
 * 为什么用它而不是自己写：这套东西的值不是推出来的，是有人拿真笔反复调出来的
 * ——急转弯处补的圆帽、末端那一圈半的收尾、按 ``smoothing`` 抽掉挨得太近的轮廓点，
 * 每一条都有它的道理，自己从头写只会把这些坑再踩一遍。
 *
 * ``cut`` 的两位是像素橡皮切出来的平口，对应它的 ``start.cap`` / ``end.cap``。
 * ``simulatePressure`` 永远关掉：鼠标和手指没有压感读数，但我们在输入层已经把
 * 速度折算进压感通道了（见 input.js 的 pressureFor），不需要它再猜一遍。
 */
export function strokeOutline(stroke) {
  if (stroke._pts) return stroke._pts;
  const flat = stroke.p;
  const input = [];
  for (let i = 0; i + 2 < flat.length; i += 3) {
    input.push([
      flat[i] * INK_SCALE,
      flat[i + 1] * INK_SCALE,
      outlinePressure(stroke, flat[i + 2]),
    ]);
  }
  const cut = stroke.cut | 0;
  const size = Math.max(stroke.w, 0.6);
  // 分两步调，不用 getStroke 那个一把梭的入口：它把同一个 size 同时当成「起笔处
  // 先丢掉多长一段」和「笔有多粗」，而这两件事没关系。起笔那一段是用来挡落笔
  // 抖动的，按笔宽算的话，13 宽的笔要走满 13 个单位才开始出墨——写小字时每个
  // 笔画的头都被吞掉一截，手感上就是「笔动了墨没跟上」。
  const points = getStrokePoints(input, {
    size: START_NOISE * INK_SCALE,
    streamline: OUTLINE_STREAMLINE,
    last: true,
  });
  const out = getStrokeOutlinePoints(points, {
    size: size * INK_SCALE,
    thinning: 1,
    smoothing: OUTLINE_SMOOTHING,
    simulatePressure: false,
    last: true,
    start: { cap: !(cut & 1) },
    end: { cap: !(cut & 2) },
  });
  for (const q of out) {
    q[0] /= INK_SCALE;
    q[1] /= INK_SCALE;
  }
  stroke._pts = out;
  return out;
}

// perfect-freehand 的两个默认值，原样用。smoothing 决定轮廓点之间的最小间距
// （``(size * smoothing)²``），streamline 是对输入位置的平滑，转弯处会把路径
// 往内侧拉一点。
const OUTLINE_SMOOTHING = 0.5;
const OUTLINE_STREAMLINE = 0.5;
// 起笔处先丢掉多长一段（世界单位）。原版拿笔宽当这个值，对笔来说太大了——
// 落笔抖动的幅度是一两个像素，不是一个笔宽。
const START_NOISE = 1;
// 送进 perfect-freehand 之前先把坐标放大这么多倍，出来再缩回去。
//
// 它里面有一个写死的绝对常数 END_NOISE_THRESHOLD = 3：末端 3 个单位之内的轮廓点
// 一律跳过，用来掐掉抬笔前的噪声。在它自己的坐标系里 3 很小，在我们的世界坐标里
// 3 差不多是 13 宽的笔的半个半径——笔画末尾那一小段就没有轮廓点了，二次贝塞尔
// 直接从更靠前的地方拐进末端的圆帽，于是先细一下再鼓个球。真机录像里量到末尾
// 五个点的墨迹宽度从应有的 96% 一路掉到 66%。
//
// 放大坐标之后那个常数在我们这边相当于 0.3 个世界单位，影响就看不出来了。其他
// 参数（size、压感折算出来的半径、smoothing 决定的最小间距）都是长度，跟着一起
// 放大，形状不变。
const INK_SCALE = 10;

/**
 * 把轮廓点列画成闭合路径：二次贝塞尔穿过相邻两点的中点，顶点当控制点。
 *
 * 这就是 perfect-freehand README 里 getSvgPathFromStroke 那条 ``Q`` 加一串 ``T``
 * 展开之后的样子——``T`` 的控制点是上一个控制点关于端点的反射，推一遍就会发现
 * 第 i 段的控制点正好是第 i 个轮廓点。这里直接调 quadraticCurveTo，不拼字符串。
 */
function quadPath(path, pts) {
  const len = pts.length;
  if (len < 4) return;
  path.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < len - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    path.quadraticCurveTo(a[0], a[1], (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
  }
  path.closePath();
}

/** 清掉一条笔画的几何缓存。点列一变就得整条清掉，三个缓存是一套。 */
export function clearStrokeCache(stroke) {
  stroke._pts = null;
  stroke._path = null;
  stroke._bbox = null;
}

/**
 * 笔画的可填充路径。
 *
 * 轮廓点列由 perfect-freehand 生成（见 strokeOutline），这里把它画成一条闭合的
 * 二次贝塞尔回路。整条笔画是**一次 fill**，不是若干段叠加——荧光笔是半透明的，
 * 分段画会在重叠处出现更深的色块。
 *
 * 之前自己写过两版：先是两侧各算一条斜接偏移线接成闭合回路，采样比笔粗密的时候
 * 内侧偏移点会折回去自交，nonzero 下自交出来的小环算 0，笔画里破白洞；后来改成
 * 「相邻两点画两圆凸包、逐段求并」，白洞没有了，但中心线还是折线，笔走得快时
 * 一眼能看出是直线拼出来的。换成 perfect-freehand 之后这两件事都不用自己管。
 */
export function buildPath(stroke) {
  if (stroke._path) return stroke._path;
  const path = new Path2D();
  stroke._path = path;
  quadPath(path, strokeOutline(stroke));
  return path;
}

export function strokeBBox(stroke) {
  if (stroke._bbox) return stroke._bbox;
  // 直接用轮廓点算：二次贝塞尔回路落在轮廓点的凸包里，所以轮廓点的包围盒一定
  // 框得住墨迹。以前是「中心线包围盒外扩一个最大半径」，那是个偏大的估计。
  const pts = strokeOutline(stroke);
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [x, y] of pts) {
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
  }
  if (!pts.length) {
    const flat = stroke.p;
    x0 = x1 = flat[0] || 0;
    y0 = y1 = flat[1] || 0;
  }
  // r 是「墨迹最远伸出中心线多少」，命中判定拿它当作用范围
  let maxR = 0;
  const flat = stroke.p;
  for (let i = 2; i < flat.length; i += 3) {
    const r = strokeRadius(stroke.tool, stroke.w, flat[i]);
    if (r > maxR) maxR = r;
  }
  const bbox = { x0, y0, x1, y1, r: maxR };
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
// 像素橡皮只记遮罩（见 eraseKind）。遮罩的段数要有上限：一条笔画的遮罩超过
// MASK_LIMIT 时先抽稀，抽完还超才落实成切分（app-eraser.js 的 bakeMask）。

/**
 * 一条笔画最多挂多少段胶囊。超了就把遮罩落实成切分，见 app-eraser.js 的 bakeMask。
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
 * 橡皮的粗细在落笔时定下，同一次拖动里各段的半径本来相等；这点余量只用来容忍
 * 浮点误差，不靠严格相等判断。链上记的半径不跟着动，所以链里每一段和它本来的
 * 半径最多差这么多，也不会随着链变长一路漂上去。
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


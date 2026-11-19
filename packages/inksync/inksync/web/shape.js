// 形状识别（第一版只认直线）：一笔写完、笔尖停住约半秒，这一笔变成起点到终点的直线。
//
// 这里只有纯计算，不碰事件和画布，node 里能直接测（tests/js/shape.test.mjs）。
// 什么时候调用、变直之后怎么显示和同步，见 input.js 的 notePause / endDraw。
//
// 手感对齐 iPad 自带的「画完停住」：停顿是笔还按在屏幕上时判定的，停够了当场变直，
// 之后不抬笔接着移动，直线的终点跟着笔走；抬笔时才提交。

// 停多久算「停住」。iPad 备忘录里大约也是半秒；再短，写字中途稍一迟疑就会误触发。
export const PAUSE_MS = 500;
// 停住时笔尖允许晃动的范围，屏幕像素。真机上 Pencil 按着不动，坐标在相邻整数之间
// 跳（见 input.js 的 MIN_STEP_PX），手本身再抖一两个像素；6 像素以内都算没动。
// 按屏幕算而不是按世界坐标算：手抖多大和画布缩放到多少无关。
export const PAUSE_RADIUS_PX = 6;
// 起点到终点至少这么长（屏幕像素）才认成直线：点一下、短短一顿不该变成线段。
export const LINE_MIN_PX = 24;
// 笔迹离起止连线最远能偏多少，按连线长度的比例。手画的「直线」一般偏 2%～5%，
// 稍有弧度的线也在 10% 以内；字母、弧线、勾都远远超过。
export const LINE_TOLERANCE = 0.1;
// 短线按比例算出来的余量太小，再给几个屏幕像素的绝对余量，手抖不至于让短线认不出来。
export const LINE_SLACK_PX = 4;
// 笔迹总长最多是起止距离的多少倍。挡掉沿一条线来回涂的那种：每一点都离连线很近，
// 但不是一条线。手画直线的抖动只多出几个百分点。
export const LINE_DETOUR = 1.25;

/**
 * 跟踪笔尖是不是停住了。
 *
 * 记一个「锚点」：笔走到这里的位置和时刻。之后的采样只要没离开锚点 ``radius``
 * 就不换锚点，离开了就以那个采样为新锚点。所以「停了多久」就是当前时刻减去锚点
 * 的时刻——不管停的这段时间里来了多少个采样，也不管一个都没来（鼠标停住就不再
 * 发事件）。
 *
 * 时间一律用调用方给的时间戳（指针事件的 timeStamp），同一笔之内比较，不和
 * performance.now() 混用：外壳送来的采样时间在另一条时间轴上。
 */
export class PauseTracker {
  constructor(radius = PAUSE_RADIUS_PX, ms = PAUSE_MS) {
    this.radius = radius;
    this.ms = ms;
    this.x = 0;
    this.y = 0;
    this.t = 0;
  }

  /** 落笔：锚点放在落笔处。 */
  reset(x, y, t) {
    this.x = x;
    this.y = y;
    this.t = t;
  }

  /**
   * 送进一个采样（屏幕坐标、时间戳）。返回在这个采样**之前**笔是不是已经停够了
   * ——停够之后才走开，停顿照样算数，所以要先判断、再挪锚点。
   */
  feed(x, y, t) {
    const paused = this.held(t);
    const dx = x - this.x;
    const dy = y - this.y;
    if (dx * dx + dy * dy > this.radius * this.radius) this.reset(x, y, t);
    return paused;
  }

  /** 到时刻 ``t`` 为止，笔在锚点附近停了够不够久。 */
  held(t) {
    return t - this.t >= this.ms;
  }
}

/**
 * 这串点（``[x, y, p, ...]``，世界坐标）像不像一条直线。
 *
 * ``scale`` 是视口缩放（屏幕像素 / 世界单位），用来把以屏幕像素定的门槛换成世界单位。
 */
export function isLine(points, scale = 1) {
  const count = (points.length / 3) | 0;
  if (count < 2) return false;
  const x0 = points[0];
  const y0 = points[1];
  const x1 = points[(count - 1) * 3];
  const y1 = points[(count - 1) * 3 + 1];
  const dx = x1 - x0;
  const dy = y1 - y0;
  const chord = Math.hypot(dx, dy);
  if (chord * scale < LINE_MIN_PX) return false;
  const limit = Math.max(chord * LINE_TOLERANCE, LINE_SLACK_PX / scale);
  let path = 0;
  for (let i = 0; i < count; i++) {
    const x = points[i * 3];
    const y = points[i * 3 + 1];
    // 到连线（线段所在直线）的垂直距离；投影落在线段外的部分由总长那一条挡住
    if (Math.abs((x - x0) * dy - (y - y0) * dx) / chord > limit) return false;
    if (i) path += Math.hypot(x - points[i * 3 - 3], y - points[i * 3 - 2]);
  }
  return path <= chord * LINE_DETOUR;
}

/**
 * 把一笔拉直：起点、终点不动，中间每个点按它在原笔迹上走过的路程比例摆到连线上，
 * 压感原样保留——拉直的是形状，下笔轻重、起笔收笔的粗细变化都还在。
 *
 * 返回新数组，不改原来的点。
 */
export function straighten(points) {
  const count = (points.length / 3) | 0;
  const out = points.slice(0, count * 3);
  if (count < 2) return out;
  let total = 0;
  for (let i = 1; i < count; i++) {
    total += Math.hypot(points[i * 3] - points[i * 3 - 3], points[i * 3 + 1] - points[i * 3 - 2]);
  }
  if (!(total > 0)) return out;
  const x0 = points[0];
  const y0 = points[1];
  const dx = points[(count - 1) * 3] - x0;
  const dy = points[(count - 1) * 3 + 1] - y0;
  let walked = 0;
  for (let i = 1; i < count - 1; i++) {
    walked += Math.hypot(points[i * 3] - points[i * 3 - 3], points[i * 3 + 1] - points[i * 3 - 2]);
    const f = walked / total;
    out[i * 3] = x0 + dx * f;
    out[i * 3 + 1] = y0 + dy * f;
  }
  return out;
}

/**
 * 均匀挑出最多 ``max`` 个点（首尾一定在），给实时笔迹用。
 *
 * 变直之后笔还按着、终点跟着笔走，每一帧都要把整条线重发给对端；直线用不着几百个点，
 * 留几十个足够把粗细变化画出来，提交时发的仍是完整的点列。
 */
export function thinPoints(points, max = 48) {
  const count = (points.length / 3) | 0;
  if (count <= max) return points.slice(0, count * 3);
  const out = [];
  for (let k = 0; k < max; k++) {
    const i = Math.round((k * (count - 1)) / (max - 1));
    out.push(points[i * 3], points[i * 3 + 1], points[i * 3 + 2]);
  }
  return out;
}

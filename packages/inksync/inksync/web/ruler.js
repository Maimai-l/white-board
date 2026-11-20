// 尺子：照 iPad 自带的那把做。只在本机，不同步；沿尺边画出来的笔画照常同步。
//
// 尺子固定在屏幕上，不跟着画布平移缩放走——画布挪了，尺子还在手底下那个位置。
// 所以它的位置和角度都用**画布区域的屏幕坐标**（和 InputController.toScreen 同一套）
// 记，判定「笔落在尺边附近」的距离也按屏幕像素算，缩放多少都是同一个手感。
// 画出来的点要存成白板坐标：落笔那一刻把尺边换算成白板坐标里的一条直线，之后每个
// 采样点都投影到这条线上（视口只有平移和缩放、没有旋转，所以方向不变）。
//
// 这里只有几何和状态，没有界面。外观在白板应用的 ruler-view.js 和 app.css 里，
// 输入怎么分给尺子还是画布在 input-ruler.js 里。
//
// 本地坐标：u 沿着尺子，v 垂直于尺子。角度 0 时 u 朝右、v 朝下；尺子的两条长边在
// v = ±width / 2，side = -1 是 v 为负的那一条（角度 0 时的上边），+1 是另一条。

/** 尺身的厚度，屏幕像素。也是「手指落在尺子上」的判定范围。 */
export const RULER_WIDTH = 88;
/**
 * 笔落在尺身外面、离尺边这么多屏幕像素以内，这一笔就贴着尺边画。
 *
 * 落在尺身上面（两条边之间）一律贴到近的那条边。24 像素约是 Pencil 笔尖宽度的
 * 十倍：笔尖贴着尺边起笔时不会因为落点偏了一两毫米就画成歪线，离尺子稍远一点
 * 写字又不会被吸过去。
 */
export const SNAP_DISTANCE = 24;
/** 转到 0°、45°、90° …… 附近这么多度以内就吸到整角度，和 iPad 一样容易摆正。 */
export const ANGLE_SNAP_DEG = 2;

const TAU = Math.PI * 2;

/** 角度规整到 (-π, π]。 */
export function normalizeAngle(angle) {
  let a = angle % TAU;
  if (a <= -Math.PI) a += TAU;
  if (a > Math.PI) a -= TAU;
  return a;
}

/** 屏幕坐标 → 尺子的本地坐标 {u, v}。 */
export function toLocal(ruler, x, y) {
  const dx = x - ruler.cx;
  const dy = y - ruler.cy;
  const c = Math.cos(ruler.angle);
  const s = Math.sin(ruler.angle);
  return { u: dx * c + dy * s, v: -dx * s + dy * c };
}

/** 尺子的本地坐标 → 屏幕坐标。 */
export function fromLocal(ruler, u, v) {
  const c = Math.cos(ruler.angle);
  const s = Math.sin(ruler.angle);
  return [ruler.cx + u * c - v * s, ruler.cy + u * s + v * c];
}

/** 这个屏幕点落在尺身上没有。尺子在长度方向上不设边：画出来比屏幕还长。 */
export function rulerContains(ruler, x, y) {
  return Math.abs(toLocal(ruler, x, y).v) <= ruler.width / 2;
}

/** 某一条长边所在的直线：线上一点 (x, y) 和单位方向 (ux, uy)。 */
export function edgeLine(ruler, side) {
  const [x, y] = fromLocal(ruler, 0, (side < 0 ? -1 : 1) * (ruler.width / 2));
  return { x, y, ux: Math.cos(ruler.angle), uy: Math.sin(ruler.angle) };
}

/**
 * 从这个屏幕点起笔，该贴哪条尺边：返回那条边的直线（见 edgeLine），不贴返回 null。
 *
 * 落在尺身上贴近的那条边；落在外面，离边不超过 ``distance`` 屏幕像素才贴。
 */
export function snapEdge(ruler, x, y, distance = SNAP_DISTANCE) {
  const { v } = toLocal(ruler, x, y);
  const outside = Math.abs(v) - ruler.width / 2;
  if (outside > distance) return null;
  return edgeLine(ruler, v < 0 ? -1 : 1);
}

/** 点投影到直线上（直线的方向必须是单位向量）。 */
export function projectOntoLine(line, x, y) {
  const t = (x - line.x) * line.ux + (y - line.y) * line.uy;
  return [line.x + t * line.ux, line.y + t * line.uy];
}

/** 点到直线的距离。 */
export function distanceToLine(line, x, y) {
  return Math.abs((x - line.x) * line.uy - (y - line.y) * line.ux);
}

/** 离 45° 的整数倍不到 ``ANGLE_SNAP_DEG`` 度就吸过去。 */
export function snapAngle(angle, toleranceDeg = ANGLE_SNAP_DEG) {
  const step = Math.PI / 4;
  const k = Math.round(angle / step);
  const tolerance = (toleranceDeg * Math.PI) / 180;
  return Math.abs(angle - k * step) <= tolerance ? normalizeAngle(k * step) : normalizeAngle(angle);
}

/**
 * 显示出来的角度：0–90 的整数度数，和 iPad 的尺子一样。
 *
 * 尺子没有头尾，转 180° 是同一把尺子；再把 90° 以上的折回来，显示的是尺边和水平线
 * 之间那个不大于 90° 的夹角——往左斜 30° 和往右斜 30° 都显示 30°。
 */
export function displayAngle(angle) {
  const deg = (angle * 180) / Math.PI;
  const half = ((deg % 180) + 180) % 180;
  const folded = half > 90 ? 180 - half : half;
  return Math.round(folded);
}

/**
 * 手指拖着尺子走：尺子像是粘在手指上。
 *
 * ``start`` 是手指落下（或上一次换手）时尺子的状态 {cx, cy, angle}；``from`` 是那一刻
 * 各个手指的位置，``to`` 是现在的位置，都是 [x, y]，一一对应。一根手指只平移；两根
 * 手指时尺子跟着两指连线转，并且两指中点相对尺子的位置不变（角度吸附之后也一样）。
 * 第三根及以后的手指不参与。``bounds`` {w, h} 给了的话，尺子中心不出这个范围，
 * 免得整把尺子被拖出屏幕找不回来。
 */
export function moveRuler(start, from, to, bounds = null) {
  let cx = start.cx;
  let cy = start.cy;
  let angle = start.angle;
  if (from.length >= 2 && to.length >= 2) {
    const a0 = Math.atan2(from[1][1] - from[0][1], from[1][0] - from[0][0]);
    const a1 = Math.atan2(to[1][1] - to[0][1], to[1][0] - to[0][0]);
    angle = snapAngle(start.angle + normalizeAngle(a1 - a0));
    const turn = angle - start.angle;
    const m0 = [(from[0][0] + from[1][0]) / 2, (from[0][1] + from[1][1]) / 2];
    const m1 = [(to[0][0] + to[1][0]) / 2, (to[0][1] + to[1][1]) / 2];
    const ox = start.cx - m0[0];
    const oy = start.cy - m0[1];
    const c = Math.cos(turn);
    const s = Math.sin(turn);
    cx = m1[0] + ox * c - oy * s;
    cy = m1[1] + ox * s + oy * c;
  } else if (from.length >= 1 && to.length >= 1) {
    cx += to[0][0] - from[0][0];
    cy += to[0][1] - from[0][1];
  }
  if (bounds && bounds.w > 0 && bounds.h > 0) {
    cx = Math.min(Math.max(cx, 0), bounds.w);
    cy = Math.min(Math.max(cy, 0), bounds.h);
  }
  return { cx, cy, angle };
}

/**
 * 一把尺子的状态。``visible`` 为假时它不存在：不画、不吃手指、不吸笔。
 *
 * 位置只在本机内存里，重新载入页面就回到默认（收起）。界面通过 onChange 跟着画。
 */
export class Ruler {
  constructor({ width = RULER_WIDTH } = {}) {
    this.visible = false;
    this.cx = 0;
    this.cy = 0;
    this.angle = 0;
    this.width = width;
    this.bounds = { w: 0, h: 0 };
    this.placed = false;
    this.listeners = new Set();
  }

  /** 画布区域的大小变了。第一次知道大小时把尺子摆到正中，水平放。 */
  setBounds(w, h) {
    this.bounds = { w, h };
    if (!this.placed && w > 0 && h > 0) {
      this.cx = w / 2;
      this.cy = h / 2;
      this.placed = true;
    }
    this.set(moveRuler(this, [], [], this.bounds));
  }

  show(visible) {
    this.visible = !!visible;
    this.emit();
  }

  set({ cx, cy, angle }) {
    this.cx = cx;
    this.cy = cy;
    this.angle = normalizeAngle(angle);
    this.emit();
  }

  /** 现在的状态，拷一份（拖动时作为起点）。 */
  state() {
    return { cx: this.cx, cy: this.cy, angle: this.angle };
  }

  onChange(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit() {
    for (const listener of this.listeners) listener(this);
  }
}

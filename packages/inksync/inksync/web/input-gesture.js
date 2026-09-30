// 平移、缩放、惯性滑动：手指、触控板、滚轮、WebKit 的 gesture 事件。
//
// 这些方法装到 InputController.prototype 上（见 input.js 末尾），里面的 this
// 就是那个 InputController。从 input.js 原样搬过来，只是换了个文件放。

import { clamp } from "./util.js";

// 惯性滑动：速度取最近这段时间的平均，之后按指数衰减。
const FLING_WINDOW = 120;
const FLING_MIN = 0.35; // px/ms（约 350 px/s），低于这个速度算拖动而不是甩
const FLING_MAX = 4;
const FLING_TAU = 220; // 衰减时间常数，越大滑得越远
const FLING_STOP = 0.015;
const WHEEL_SETTLE = 220;
// ctrl + 滚轮缩放时，单个事件的 deltaY 上限（鼠标滚轮一格是 100，触控板只有几像素）
const WHEEL_ZOOM_MAX = 25;

export function installGesture(proto) {
  Object.assign(proto, methods);
}

const methods = {
  startGesture(event) {
    const [x, y] = this.toScreen(event);
    if (!this.gesture) {
      this.gesture = {
        points: new Map(),
        mid: null,
        dist: 0,
        dx: 0,
        dy: 0,
        zoomed: false,
        samples: [],
      };
    }
    this.gesture.points.set(event.pointerId, { x, y });
    this.updateGestureRef();
  },

  updateGestureRef() {
    const gesture = this.gesture;
    if (!gesture) return;
    const points = [...gesture.points.values()];
    let sx = 0;
    let sy = 0;
    for (const point of points) {
      sx += point.x;
      sy += point.y;
    }
    gesture.mid = { x: sx / points.length, y: sy / points.length };
    gesture.dist =
      points.length >= 2 ? Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y) : 0;
  },

  moveGesture(event) {
    const gesture = this.gesture;
    if (!gesture || !gesture.points.has(event.pointerId)) return;
    const [x, y] = this.toScreen(event);
    gesture.points.set(event.pointerId, { x, y });
    const prevMid = gesture.mid;
    const prevDist = gesture.dist;
    this.updateGestureRef();
    if (!prevMid) return;
    const dx = gesture.mid.x - prevMid.x;
    const dy = gesture.mid.y - prevMid.y;
    this.viewport.panBy(dx, dy);
    gesture.dx += dx;
    gesture.dy += dy;
    const now = performance.now();
    gesture.samples.push([now, dx, dy]);
    while (gesture.samples.length && now - gesture.samples[0][0] > FLING_WINDOW) {
      gesture.samples.shift();
    }
    if (prevDist > 0 && gesture.dist > 0) {
      this.viewport.zoomAt(gesture.dist / prevDist, gesture.mid.x, gesture.mid.y);
      gesture.zoomed = true;
    }
    this.hooks.onViewChange();
  },

  endGesturePointer(pointerId) {
    if (!this.gesture) return;
    this.gesture.points.delete(pointerId);
    if (this.gesture.points.size) {
      this.updateGestureRef();
      return;
    }
    const gesture = this.gesture;
    this.gesture = null;
    // 松手之后：先让界面决定要不要吸附，没吸附就按甩出去的速度继续滑。
    if (this.hooks.onGestureEnd?.()) return;
    this.startMomentum(gesture);
  },

  /** 惯性滑动：按最近一段时间的平均速度继续走，指数衰减到停。 */
  startMomentum(gesture) {
    const now = performance.now();
    const samples = gesture.samples.filter((sample) => now - sample[0] <= FLING_WINDOW);
    if (samples.length < 2) return;
    const span = now - samples[0][0];
    if (span <= 0) return;
    let vx = samples.reduce((sum, sample) => sum + sample[1], 0) / span;
    let vy = samples.reduce((sum, sample) => sum + sample[2], 0) / span;
    const speed = Math.hypot(vx, vy);
    if (speed < FLING_MIN) return;
    if (speed > FLING_MAX) {
      vx = (vx / speed) * FLING_MAX;
      vy = (vy / speed) * FLING_MAX;
    }

    let last = now;
    const step = (time) => {
      const dt = Math.min(32, time - last);
      last = time;
      const before = [this.viewport.x, this.viewport.y];
      this.viewport.panBy(vx * dt, vy * dt);
      this.hooks.onViewChange();
      const decay = Math.exp(-dt / FLING_TAU);
      vx *= decay;
      vy *= decay;
      const moved =
        Math.abs(this.viewport.x - before[0]) > 0.01 ||
        Math.abs(this.viewport.y - before[1]) > 0.01;
      if (!moved || Math.hypot(vx, vy) < FLING_STOP) {
        this.momentum = 0;
        return;
      }
      this.momentum = requestAnimationFrame(step);
    };
    this.momentum = requestAnimationFrame(step);
  },

  stopMomentum() {
    if (this.momentum) {
      cancelAnimationFrame(this.momentum);
      this.momentum = 0;
    }
  },

  endGesture(revert = false) {
    const gesture = this.gesture;
    if (revert && gesture && !gesture.zoomed && (gesture.dx || gesture.dy)) {
      this.viewport.panBy(-gesture.dx, -gesture.dy);
      this.hooks.onViewChange();
    }
    this.gesture = null;
    for (const [id, entry] of this.pointers) {
      if (entry.role === "gesture") this.pointers.delete(id);
    }
  },

  onWheel(event) {
    event.preventDefault();
    this.stopMomentum();
    // 滚轮 / 触控板停下来之后，和松手一样给界面一次吸附的机会。
    clearTimeout(this._wheelTimer);
    this._wheelTimer = setTimeout(() => this.hooks.onGestureEnd?.(), WHEEL_SETTLE);
    const [x, y] = this.toScreen(event);
    const factor = event.deltaMode === 1 ? 16 : 1;
    if (event.ctrlKey || event.metaKey) {
      // 触控板捏合一次只来几个像素，鼠标滚轮一格就是 100：不夹住的话，
      // 滚轮一格直接缩掉三分之二。夹到一格约等于按一次缩放按钮。
      const step = clamp(event.deltaY * factor, -WHEEL_ZOOM_MAX, WHEEL_ZOOM_MAX);
      this.viewport.zoomAt(Math.exp(-step * 0.01), x, y);
    } else {
      this.viewport.panBy(-event.deltaX * factor, -event.deltaY * factor);
    }
    this.hooks.onViewChange();
  },

  /* ---------------- 触控板捏合（WebKit 的 gesture 事件） ---------------- */

  onPinchStart(event) {
    event.preventDefault(); // 不让浏览器去缩放网页本身
    this._pinch = null;
    // iPad 上的双指缩放是自己用 pointer 事件做的，WebKit 同时还会发这套 gesture
    // 事件，不挡住就会缩两次。手上有指针就说明是屏幕上的手势，这里不接。
    if (this.gesture || this.pointers.size) return;
    // 指针停在工具栏、面板上时只拦默认行为，不动画布
    if (event.target && event.target.closest && event.target.closest("#ui")) return;
    this.stopMomentum();
    clearTimeout(this._wheelTimer);
    this._wheelTimer = 0;
    // event.scale 是从手势开始算起的累计倍数，这里记着上一次的值算增量
    this._pinch = { scale: event.scale || 1 };
  },

  onPinch(event) {
    event.preventDefault();
    if (!this._pinch) return;
    const scale = event.scale || 1;
    const ratio = this._pinch.scale > 0 ? scale / this._pinch.scale : 1;
    this._pinch.scale = scale;
    if (!Number.isFinite(ratio) || ratio <= 0) return;
    const [x, y] = this.toScreen(event);
    this.viewport.zoomAt(ratio, x, y);
    this.hooks.onViewChange();
  },

  onPinchEnd(event) {
    event.preventDefault();
    if (!this._pinch) return;
    this._pinch = null;
    this.hooks.onGestureEnd?.();  // 和松手一样，给一次吸附的机会
  },
};

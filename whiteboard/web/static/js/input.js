// 指针输入：书写、擦除、平移与缩放。
//
// 规则（对齐 iPad 原生体感）：
//   * 默认只有 Apple Pencil 能画线，手指一律是平移 / 缩放，手掌搭上去也不会留痕；
//     没有 Pencil 的人可以在工具栏上打开「手指书写」，此时单指画线、双指手势，
//     第二根手指落下会撤掉刚起笔的那一下；
//   * 鼠标左键书写，中键 / 右键 / 空格拖动画布，滚轮平移，⌘ / Ctrl + 滚轮缩放。
//
// 所有笔迹先落在本地画布上，再通过网络发出去，本地书写不等待任何回包。

import { TOOLS } from "./stroke.js";
import { clamp } from "./util.js";

const FINGER_FLAG = "whiteboard.fingerDraw";
// Pencil 落笔期间以及抬笔后的这段时间里，手指 / 手掌一律不参与任何操作。
const PALM_GRACE = 500;
const SMOOTH_PEN = 0.45;
const SMOOTH_MOUSE = 0.6;
const PRESSURE_SMOOTH = 0.25;

export function loadFingerDraw() {
  try {
    return localStorage.getItem(FINGER_FLAG) === "1";
  } catch (err) {
    return false;
  }
}

export class InputController {
  constructor(options) {
    this.stage = options.stage;
    this.viewport = options.viewport;
    this.renderer = options.renderer;
    this.getTool = options.getTool;
    this.hooks = options.hooks;
    this.strokePrefix = options.strokePrefix;
    this.device = options.device;

    this.counter = 0;
    this.pointers = new Map();
    this.draw = null;
    this.erase = null;
    this.gesture = null;
    this.liveRef = null;
    this.spaceHeld = false;
    this.fingerDraw = loadFingerDraw();
    this.pendingLive = [];
    this.lastPenAt = -Infinity;
    this.lastInputAt = -Infinity;
    this.sampleCount = 0;
    // 诊断用：区分「主线程被卡住」和「系统把事件抢走了」两种卡顿。
    this.stats = { down: 0, move: 0, up: 0, cancel: 0, maxGap: 0, coalesced: 0 };
    this.canceled = null;
    this._rect = null;

    const stage = this.stage;
    stage.addEventListener("pointerdown", (e) => this.onDown(e));
    stage.addEventListener("pointermove", (e) => this.onMove(e));
    stage.addEventListener("pointerup", (e) => this.onUp(e));
    stage.addEventListener("pointercancel", (e) => this.onUp(e, true));
    stage.addEventListener("pointerleave", (e) => this.onLeave(e));
    stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    stage.addEventListener("contextmenu", (e) => e.preventDefault());

    // iPadOS 上光有 touch-action: none 还不够：书写快一点，Safari 的选择 / 查词
    // 手势就会抢走这一笔，表现为卡一下并弹出选择气泡。把 touch 事件的默认行为
    // 一并挡掉，pointer 事件不受影响（它们是独立产生的）。
    for (const name of ["touchstart", "touchmove", "touchend", "touchcancel"]) {
      stage.addEventListener(name, (e) => e.preventDefault(), { passive: false });
    }
    // Safari 的双指缩放手势会顶掉 pointer 事件，这里屏蔽掉。
    for (const name of ["gesturestart", "gesturechange", "gestureend"]) {
      stage.addEventListener(name, (e) => e.preventDefault());
    }
    for (const name of ["selectstart", "dragstart"]) {
      stage.addEventListener(name, (e) => e.preventDefault());
    }

    // 兜底：触摸没落在 #stage 上时（界面控件除外）同样掐掉默认行为。
    // 注意不能对控件调 preventDefault，否则 iOS 不会再合成 click，按钮就点不动了。
    const ui = document.getElementById("ui");
    for (const name of ["touchstart", "touchmove"]) {
      document.addEventListener(
        name,
        (e) => {
          if (ui && ui.contains(e.target)) return;
          e.preventDefault();
        },
        { passive: false, capture: true }
      );
    }

    // 画布铺满窗口，位置只会在窗口变化时改变，没必要每个采样点都去量一次。
    const invalidate = () => {
      this._rect = null;
    };
    addEventListener("resize", invalidate);
    addEventListener("orientationchange", invalidate);
    addEventListener("scroll", invalidate, true);
    if (window.visualViewport) visualViewport.addEventListener("resize", invalidate);
    addEventListener("keydown", (e) => {
      if (e.code === "Space") this.spaceHeld = true;
    });
    addEventListener("keyup", (e) => {
      if (e.code === "Space") this.spaceHeld = false;
    });
  }

  /** 指针捕获：合成事件或指针已经消失时浏览器会抛错，这里忽略掉。 */
  capture(pointerId, on) {
    try {
      if (on) this.stage.setPointerCapture(pointerId);
      else this.stage.releasePointerCapture(pointerId);
    } catch (err) {
      /* 捕获失败不影响绘制 */
    }
  }

  // ----------------------------------------------------------------- 坐标

  /** 缓存画布位置：Pencil 一帧能给出二十几个合并采样点，每个都量一次会强制重排。 */
  rect() {
    if (this._rect === null) this._rect = this.stage.getBoundingClientRect();
    return this._rect;
  }

  toWorld(event) {
    const rect = this.rect();
    return this.viewport.toWorld(event.clientX - rect.left, event.clientY - rect.top);
  }

  toScreen(event) {
    const rect = this.rect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  setFingerDraw(enabled) {
    this.fingerDraw = !!enabled;
    try {
      localStorage.setItem(FINGER_FLAG, this.fingerDraw ? "1" : "0");
    } catch (err) {
      /* 记不住就只在本次会话内生效 */
    }
  }

  // --------------------------------------------------------------- 分派

  /** Pencil 正在写，或者刚抬起不久。 */
  penActive() {
    if (this.draw && this.draw.type === "pen") return true;
    return performance.now() - this.lastPenAt < PALM_GRACE;
  }

  classify(event) {
    if (event.pointerType === "pen") return "draw";
    if (event.pointerType === "mouse") {
      if (event.button !== 0 || this.spaceHeld) return "gesture";
      return "draw";
    }
    // 手掌屏蔽：用 Pencil 写字时，搭在屏幕上的手既不画线也不会把画布拖走
    if (this.penActive()) return "ignore";
    // 触摸：默认只平移 / 缩放，打开「手指书写」后才画线
    if (!this.fingerDraw) return "gesture";
    if (this.draw || this.erase || this.gesture) return "gesture";
    return "draw";
  }

  onDown(event) {
    event.preventDefault();
    // 有选区在就先清掉：放大镜是跟着选区走的。
    const selection = getSelection && getSelection();
    if (selection && !selection.isCollapsed) selection.removeAllRanges();
    this.stats.down += 1;
    this.stats.maxGap = 0;
    this.lastInputAt = performance.now();
    this._rect = null;
    if (event.pointerType === "pen") {
      this.lastPenAt = performance.now();
      // 手掌通常比笔尖先碰到屏幕：把它刚拖出来的那一点位移撤回去，画面不会跳。
      this.endGesture(true);
    }

    const role = this.classify(event);
    if (role === "ignore") return;

    this.capture(event.pointerId, true);
    this.pointers.set(event.pointerId, { type: event.pointerType, role });

    if (role === "draw") {
      const tool = this.getTool();
      if (tool.tool === "eraser") this.startErase(event);
      else this.startDraw(event, tool);
      return;
    }

    // 手势：手指落下时若正在用手指书写，撤掉那一笔
    if (event.pointerType === "touch" && this.draw && this.draw.type === "touch") {
      this.cancelDraw();
    }
    this.startGesture(event);
  }

  onMove(event) {
    const now = performance.now();
    if (this.draw && this.lastInputAt > 0) {
      const gap = now - this.lastInputAt;
      if (gap > this.stats.maxGap) this.stats.maxGap = gap;
    }
    this.stats.move += 1;
    this.lastInputAt = now;
    if (event.pointerType === "pen") this.lastPenAt = performance.now();
    const entry = this.pointers.get(event.pointerId);
    if (!entry) {
      // 系统有时会在书写途中发 pointercancel（手势识别、通知横幅之类），
      // 但笔还按在屏幕上。这时把后面这一段接着画出来，不要整截丢掉。
      if (this.canResume(event)) {
        this.onDown(event);
        return;
      }
      if (event.pointerType === "mouse") this.updateCursor(event);
      return;
    }
    event.preventDefault();
    if (entry.role === "draw") {
      if (this.erase) this.moveErase(event);
      else if (this.draw) this.moveDraw(event);
      return;
    }
    this.moveGesture(event);
  }

  /** 这个还在按着的指针是不是被系统中途取消掉了。 */
  canResume(event) {
    if (this.draw || this.erase || this.gesture) return false;
    const canceled = this.canceled;
    // 只接被系统中途取消掉的那一个指针，而且只在刚取消不久时接。
    if (!canceled || canceled.id !== event.pointerId) return false;
    if (performance.now() - canceled.at > 3000) return false;
    const pressed = (event.buttons & 1) === 1 || event.pressure > 0;
    if (!pressed) return false;
    if (event.pointerType === "pen") return true;
    if (event.pointerType === "touch") return this.fingerDraw;
    return false;
  }

  onUp(event, canceled = false) {
    if (canceled) {
      this.stats.cancel += 1;
      this.canceled = { id: event.pointerId, at: performance.now() };
    } else {
      this.stats.up += 1;
      this.canceled = null;
    }
    this.lastInputAt = performance.now();
    if (event.pointerType === "pen") this.lastPenAt = performance.now();
    const entry = this.pointers.get(event.pointerId);
    this.pointers.delete(event.pointerId);
    this.capture(event.pointerId, false);
    if (!entry) return;
    if (entry.role === "draw") {
      if (this.erase) this.endErase();
      else if (this.draw) this.endDraw(event, canceled);
      return;
    }
    this.endGesturePointer(event.pointerId);
  }

  onLeave(event) {
    if (event.pointerType === "mouse" && !this.draw && !this.erase) {
      this.renderer.cursor = null;
    }
  }

  // --------------------------------------------------------------- 书写

  newStrokeId() {
    this.counter += 1;
    return `${this.strokePrefix}-${this.counter.toString(36)}`;
  }

  pressureFor(event, sample) {
    if (event.pointerType === "pen") {
      let pressure = event.pressure > 0 ? event.pressure : 0.5;
      if (typeof event.altitudeAngle === "number" && event.altitudeAngle > 0) {
        // 笔身放平时笔迹变宽，模拟侧锋
        const tilt = 1 - clamp(event.altitudeAngle / (Math.PI / 2), 0, 1);
        pressure = clamp(pressure * (1 + tilt * 0.45), 0, 1);
      }
      return pressure;
    }
    // 鼠标 / 手指没有压感，用速度反推：走得快笔迹细。
    return clamp(1 - sample.speed / 3.2, 0.38, 1);
  }

  startDraw(event, tool) {
    const [wx, wy] = this.toWorld(event);
    const scale = (TOOLS[tool.tool] || TOOLS.pen).scale;
    const stroke = {
      id: this.newStrokeId(),
      tool: tool.tool,
      color: tool.color,
      w: tool.width * scale,
      p: [],
      dev: this.device,
    };
    this.draw = {
      pointerId: event.pointerId,
      type: event.pointerType,
      stroke,
      sx: wx,
      sy: wy,
      sp: event.pointerType === "pen" && event.pressure > 0 ? event.pressure : 0.5,
      lastTime: event.timeStamp,
      lastScreen: this.toScreen(event),
    };
    this.liveRef = stroke;
    this.addSample(event, wx, wy, true);
    this.renderer.setLive("local", stroke);
    this.renderer.cursor = null;
    this.hooks.onStrokeStart(stroke);
  }

  addSample(event, wx, wy, first = false) {
    const draw = this.draw;
    if (!draw) return;
    const alpha = draw.type === "pen" ? SMOOTH_PEN : SMOOTH_MOUSE;
    if (first) {
      draw.sx = wx;
      draw.sy = wy;
    } else {
      draw.sx += (wx - draw.sx) * alpha;
      draw.sy += (wy - draw.sy) * alpha;
    }

    const [screenX, screenY] = this.toScreen(event);
    const dt = Math.max(1, event.timeStamp - draw.lastTime);
    const speed = Math.hypot(screenX - draw.lastScreen[0], screenY - draw.lastScreen[1]) / dt;
    draw.lastTime = event.timeStamp;
    draw.lastScreen = [screenX, screenY];

    const target = this.pressureFor(event, { speed });
    draw.sp = first ? target : draw.sp + (target - draw.sp) * PRESSURE_SMOOTH;

    const points = draw.stroke.p;
    if (!first) {
      const dx = draw.sx - points[points.length - 3];
      const dy = draw.sy - points[points.length - 2];
      const minDist = 0.65 / this.viewport.scale;
      if (dx * dx + dy * dy < minDist * minDist) return;
    }
    points.push(draw.sx, draw.sy, draw.sp);
    this.sampleCount += 1;
    this.pendingLive.push(draw.sx, draw.sy, draw.sp);
    draw.stroke._path = null;
    draw.stroke._bbox = null;
  }

  moveDraw(event) {
    const draw = this.draw;
    if (!draw || draw.pointerId !== event.pointerId) return;
    const events = event.getCoalescedEvents ? event.getCoalescedEvents() : null;
    if (events && events.length) {
      this.stats.coalesced = events.length;
      for (const sample of events) {
        const [wx, wy] = this.toWorld(sample);
        this.addSample(sample, wx, wy);
      }
    } else {
      const [wx, wy] = this.toWorld(event);
      this.addSample(event, wx, wy);
    }
  }

  endDraw(event, canceled) {
    const draw = this.draw;
    if (!draw) return;
    if (!canceled) {
      const [wx, wy] = this.toWorld(event);
      draw.stroke.p.push(wx, wy, draw.sp);
      this.pendingLive.push(wx, wy, draw.sp);
      draw.stroke._path = null;
      draw.stroke._bbox = null;
    }
    this.draw = null;
    this.renderer.setLive("local", null);
    this.flushLive();
    this.liveRef = null;
    this.hooks.onStrokeEnd(draw.stroke);
  }

  cancelDraw() {
    if (!this.draw) return;
    const stroke = this.draw.stroke;
    this.draw = null;
    this.pendingLive = [];
    this.liveRef = null;
    this.renderer.setLive("local", null);
    this.hooks.onStrokeCancel(stroke);
  }

  takeSampleCount() {
    const count = this.sampleCount;
    this.sampleCount = 0;
    return count;
  }

  /** 每帧把新采样点推给对端；掉线时直接丢，笔画结束的正式操作会补齐。 */
  flushLive() {
    if (!this.pendingLive.length || !this.liveRef) return;
    const points = this.pendingLive;
    this.pendingLive = [];
    this.hooks.onLivePoints(this.liveRef, points);
  }

  // --------------------------------------------------------------- 擦除

  startErase(event) {
    const tool = this.getTool();
    this.erase = { pointerId: event.pointerId, ids: [], radius: tool.eraserSize / 2 };
    this.moveErase(event);
  }

  moveErase(event) {
    const erase = this.erase;
    if (!erase || erase.pointerId !== event.pointerId) return;
    const [wx, wy] = this.toWorld(event);
    this.renderer.cursor = { x: wx, y: wy, r: erase.radius };
    const hit = this.hooks.onErase(wx, wy, erase.radius);
    if (hit && hit.length) erase.ids.push(...hit);
  }

  endErase() {
    const erase = this.erase;
    this.erase = null;
    this.renderer.cursor = null;
    if (erase && erase.ids.length) this.hooks.onEraseEnd(erase.ids);
  }

  updateCursor(event) {
    const tool = this.getTool();
    if (tool.tool !== "eraser") {
      if (this.renderer.cursor) this.renderer.cursor = null;
      return;
    }
    const [wx, wy] = this.toWorld(event);
    this.renderer.cursor = { x: wx, y: wy, r: tool.eraserSize / 2 };
  }

  // --------------------------------------------------------- 平移 / 缩放

  startGesture(event) {
    const [x, y] = this.toScreen(event);
    if (!this.gesture) this.gesture = { points: new Map(), mid: null, dist: 0, dx: 0, dy: 0, zoomed: false };
    this.gesture.points.set(event.pointerId, { x, y });
    this.updateGestureRef();
  }

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
  }

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
    if (prevDist > 0 && gesture.dist > 0) {
      this.viewport.zoomAt(gesture.dist / prevDist, gesture.mid.x, gesture.mid.y);
      gesture.zoomed = true;
    }
    this.hooks.onViewChange();
  }

  endGesturePointer(pointerId) {
    if (!this.gesture) return;
    this.gesture.points.delete(pointerId);
    if (!this.gesture.points.size) this.gesture = null;
    else this.updateGestureRef();
  }

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
  }

  onWheel(event) {
    event.preventDefault();
    const [x, y] = this.toScreen(event);
    if (event.ctrlKey || event.metaKey) {
      this.viewport.zoomAt(Math.exp(-event.deltaY * 0.01), x, y);
    } else {
      const factor = event.deltaMode === 1 ? 16 : 1;
      this.viewport.panBy(-event.deltaX * factor, -event.deltaY * factor);
    }
    this.hooks.onViewChange();
  }
}

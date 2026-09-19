// 指针输入：书写、擦除、平移与缩放。
//
// 规则（对齐 iPad 原生体感）：
//   * 一旦检测到 Apple Pencil，手指就只负责平移 / 缩放，不再画线（即手掌误触屏蔽）；
//   * 没有 Pencil 时单指书写、双指平移缩放，第二根手指落下会撤掉刚起笔的那一下；
//   * 鼠标左键书写，中键 / 右键 / 空格拖动画布，滚轮平移，⌘ / Ctrl + 滚轮缩放。
//
// 所有笔迹先落在本地画布上，再通过网络发出去，本地书写不等待任何回包。

import { TOOLS } from "./stroke.js";
import { clamp } from "./util.js";

const PENCIL_FLAG = "whiteboard.pencil";
const PENCIL_TTL = 12 * 3600 * 1000;
const SMOOTH_PEN = 0.45;
const SMOOTH_MOUSE = 0.6;
const PRESSURE_SMOOTH = 0.25;

function loadPencilSeen() {
  try {
    const raw = Number(localStorage.getItem(PENCIL_FLAG) || 0);
    return raw > 0 && Date.now() - raw < PENCIL_TTL;
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
    this.pencilSeen = loadPencilSeen();
    this.pendingLive = [];

    const stage = this.stage;
    stage.addEventListener("pointerdown", (e) => this.onDown(e));
    stage.addEventListener("pointermove", (e) => this.onMove(e));
    stage.addEventListener("pointerup", (e) => this.onUp(e));
    stage.addEventListener("pointercancel", (e) => this.onUp(e, true));
    stage.addEventListener("pointerleave", (e) => this.onLeave(e));
    stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    stage.addEventListener("contextmenu", (e) => e.preventDefault());
    // Safari 的双指缩放手势会顶掉 pointer 事件，这里屏蔽掉。
    for (const name of ["gesturestart", "gesturechange", "gestureend"]) {
      stage.addEventListener(name, (e) => e.preventDefault());
    }
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

  toWorld(event) {
    const rect = this.stage.getBoundingClientRect();
    return this.viewport.toWorld(event.clientX - rect.left, event.clientY - rect.top);
  }

  toScreen(event) {
    const rect = this.stage.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  // --------------------------------------------------------------- 分派

  classify(event) {
    if (event.pointerType === "pen") return "draw";
    if (event.pointerType === "mouse") {
      if (event.button !== 0 || this.spaceHeld) return "gesture";
      return "draw";
    }
    // 触摸
    if (this.pencilSeen) return "gesture";
    if (this.draw || this.erase || this.gesture) return "gesture";
    return "draw";
  }

  onDown(event) {
    event.preventDefault();
    if (event.pointerType === "pen" && !this.pencilSeen) {
      this.pencilSeen = true;
      try {
        localStorage.setItem(PENCIL_FLAG, String(Date.now()));
      } catch (err) {
        /* 无痕模式下记不住也无妨，本次会话内仍然生效 */
      }
      this.hooks.onPencilDetected?.();
    }
    if (event.pointerType === "pen") {
      // Pencil 落笔时把手指造成的手势取消掉
      this.endGesture();
    }

    this.capture(event.pointerId, true);
    const role = this.classify(event);
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
    const entry = this.pointers.get(event.pointerId);
    if (!entry) {
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

  onUp(event, canceled = false) {
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
    this.pendingLive.push(draw.sx, draw.sy, draw.sp);
    draw.stroke._path = null;
    draw.stroke._bbox = null;
  }

  moveDraw(event) {
    const draw = this.draw;
    if (!draw || draw.pointerId !== event.pointerId) return;
    const events = event.getCoalescedEvents ? event.getCoalescedEvents() : null;
    if (events && events.length) {
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
    if (!this.gesture) this.gesture = { points: new Map(), mid: null, dist: 0 };
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
    this.viewport.panBy(gesture.mid.x - prevMid.x, gesture.mid.y - prevMid.y);
    if (prevDist > 0 && gesture.dist > 0) {
      this.viewport.zoomAt(gesture.dist / prevDist, gesture.mid.x, gesture.mid.y);
    }
    this.hooks.onViewChange();
  }

  endGesturePointer(pointerId) {
    if (!this.gesture) return;
    this.gesture.points.delete(pointerId);
    if (!this.gesture.points.size) this.gesture = null;
    else this.updateGestureRef();
  }

  endGesture() {
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

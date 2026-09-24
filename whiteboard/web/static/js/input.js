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
// 惯性滑动：速度取最近这段时间的平均，之后按指数衰减。
const FLING_WINDOW = 120;
const FLING_MIN = 0.35; // px/ms（约 350 px/s），低于这个速度算拖动而不是甩
const FLING_MAX = 4;
const FLING_TAU = 220; // 衰减时间常数，越大滑得越远
const FLING_STOP = 0.015;
const WHEEL_SETTLE = 220;
// ctrl + 滚轮缩放时，单个事件的 deltaY 上限（鼠标滚轮一格是 100，触控板只有几像素）
const WHEEL_ZOOM_MAX = 25;

// 橡皮的直径，单位是**屏幕像素**。橡皮是工具不是墨水，尺寸恒定在屏幕上，
// 放大就等于擦得更细。这一条有实测依据：同一个倾角在 zoom 1 和 zoom 2.02 下，
// 印记在 drawing 坐标里差一倍，乘回缩放之后对得上（见 docs/eraser.md）。
const ERASER_TIP = 6;

/**
 * 像素橡皮的直径随笔身与屏幕的夹角变化，实测自 iPad 原生 PencilKit。
 *
 * 采集方式：在一大片实心墨迹上点一排孤立的像素橡皮点，每个点固定一个笔身角度，
 * 再把 PKStroke.mask 里对应的那个洞的面积换算成等效直径。二十一个点全部对上，
 * 中心偏差都在 3 pt 以内。
 *
 * 结论有三条，都和我原来拍脑袋定的不一样：
 *
 * * 变粗从 80° 就开始，25° 左右饱和——不是 20° 以上一律笔尖。常握笔大约 50°，
 *   那里原生已经是 17 了，而原来的实现还停在 6，细得没法用橡皮写字。
 * * 最粗约 81，不是 45。
 * * 力度不参与：同一倾角段里力度从 0.15 到 0.51，直径不跟着动。
 *
 * 孤立点只采到 80° / 50° / 35°～27° / ≤25° 这几档，中间两段一开始是线性插值填的，
 * 后来拿拖动那一批做逐像素对照发现两处都不对：68° 处原生已经是 16.5 而插值只给
 * 11.4（窄 45%），44° 处原生还是 17～19 而插值给到 23（宽 30%）。实际是
 * **68° 到 42° 之间基本平在 17**，40° 以下才陡升。这两个拐点就是这么来的。
 */
const ERASER_CURVE = [
  [90, 6],
  [80, 7.5],
  [68, 16.5],
  [45, 18],
  [40, 24],
  [35, 35],
  [32, 52],
  [28, 75],
  [25, 81],
  [0, 81],
];
const ERASER_SMOOTH = 0.35; // 宽度跟着走的快慢，倾斜读数本来就抖，直接跳会很难看

/** 查 ERASER_CURVE，按角度线性插值出直径。 */
function eraserDiameter(deg) {
  const curve = ERASER_CURVE;
  if (deg >= curve[0][0]) return curve[0][1];
  for (let i = 0; i + 1 < curve.length; i++) {
    const [aHigh, dHigh] = curve[i];
    const [aLow, dLow] = curve[i + 1];
    if (deg >= aLow) {
      const t = (aHigh - deg) / (aHigh - aLow);
      return dHigh + (dLow - dHigh) * t;
    }
  }
  return curve[curve.length - 1][1];
}

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

/**
 * 笔身与屏幕的夹角，弧度。0 是笔贴在屏幕上，π/2 是笔竖直。
 *
 * 同一件事有两套 API，必须都认：
 *
 * * ``tiltX`` / ``tiltY`` 是 Pointer Events Level 2 的，单位是度，从**竖直**方向
 *   算起。Apple Pencil 在 Safari 上报的一直是这一对，各家浏览器也都支持。
 * * ``altitudeAngle`` 是 Level 3 后加的，从**屏幕平面**算起。问题在于规范规定
 *   「设备报不出倾斜时返回 π/2」，也就是竖直——只看它的话，凡是不支持这个属性
 *   的浏览器都会被当成笔一直立着，倾斜永远读不出来。
 *
 * 所以以 tiltX / tiltY 为准，两个都是 0 时才去看 altitudeAngle。另外
 * ``altitudeAngle === 0`` 是「笔平贴在屏幕上」这个合法读数，不能当成缺数据。
 */
export function penAltitude(event) {
  const tiltX = typeof event.tiltX === "number" ? event.tiltX : 0;
  const tiltY = typeof event.tiltY === "number" ? event.tiltY : 0;
  if (tiltX || tiltY) {
    // 规范附录里的换算：altitude = atan(1 / hypot(tan tiltX, tan tiltY))
    const tx = Math.tan((clamp(tiltX, -89.9, 89.9) * Math.PI) / 180);
    const ty = Math.tan((clamp(tiltY, -89.9, 89.9) * Math.PI) / 180);
    return Math.atan(1 / Math.hypot(tx, ty));
  }
  const altitude = event.altitudeAngle;
  if (typeof altitude === "number" && Number.isFinite(altitude)) {
    return clamp(altitude, 0, Math.PI / 2);
  }
  return Math.PI / 2; // 什么都报不出来，按竖直算
}

export class InputController {
  constructor(options) {
    this.stage = options.stage;
    this.viewport = options.viewport;
    this.renderer = options.renderer;
    this.getTool = options.getTool;
    this.getLimits = options.getLimits || (() => null);
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
    this.stats = {
      down: 0, move: 0, up: 0, cancel: 0, maxGap: 0, coalesced: 0,
      touch: 0, uncancelable: 0, penCancel: 0,
      // 诊断面板用：真机上没法接开发者工具，笔的倾斜到底报不报、报的是哪一套，
      // 只能在屏幕上看
      tiltX: 0, tiltY: 0, altRaw: null, tiltDeg: 90,
    };
    this.canceled = null;
    this.momentum = 0;
    this._wheelTimer = 0;
    this._pinch = null;
    this._rect = null;

    const stage = this.stage;
    stage.addEventListener("pointerdown", (e) => this.onDown(e));
    stage.addEventListener("pointermove", (e) => this.onMove(e));
    stage.addEventListener("pointerup", (e) => this.onUp(e));
    stage.addEventListener("pointercancel", (e) => this.onUp(e, true));
    stage.addEventListener("pointerleave", (e) => this.onLeave(e));
    stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    // 触控板捏合：Chrome / Firefox 发的是 ctrl + wheel（在 onWheel 里），
    // WebKit（Safari 和 Mac 窗口用的 WKWebView）发的是这套非标准的 gesture 事件，
    // 两条路都得接，不然 Mac 应用里捏合是没反应的。挂在 window 上：应用窗口里
    // 任何地方都不该缩放网页本身，哪怕指针停在工具栏上。
    window.addEventListener("gesturestart", (e) => this.onPinchStart(e), { passive: false });
    window.addEventListener("gesturechange", (e) => this.onPinch(e), { passive: false });
    window.addEventListener("gestureend", (e) => this.onPinchEnd(e), { passive: false });
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

    // 文档级、捕获阶段、第一时间 preventDefault。
    //
    // iPadOS 把「点一下、再点住拖动」识别成选择文字，于是冒出放大镜并吃掉这一笔；
    // 断笔重新落笔正好构成这个双击模式。Excalidraw 修同类问题也是把 preventDefault
    // 提到 touchstart 处理函数的第一行（excalidraw#4705）。
    // 界面控件要放过，否则 iOS 不再合成 click，按钮就点不动了。
    const ui = document.getElementById("ui");
    const guard = (e) => {
      if (ui && ui.contains(e.target)) return;
      this.stats.touch += 1;
      if (!e.cancelable) {
        // 不可取消说明系统已经接管了这次手势，我们拦不住（多半是随手写 Scribble）。
        this.stats.uncancelable += 1;
        return;
      }
      e.preventDefault();
    };
    for (const name of ["touchstart", "touchmove"]) {
      document.addEventListener(name, guard, { passive: false, capture: true });
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

  withinLimits(event) {
    const limits = this.getLimits();
    if (!limits) return true;
    const [wx, wy] = this.toWorld(event);
    if (wx < limits.x0 || wx > limits.x1 || wy < limits.y0) return false;
    return limits.y1 === undefined || wy <= limits.y1;
  }

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
    this.stopMomentum();
    this.hooks.onInteractionStart?.();
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

    let role = this.classify(event);
    if (role === "ignore") return;
    // 笔记模式：纸张之外不落笔，改成拖动画布。
    if (role === "draw" && !this.withinLimits(event)) role = "gesture";

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
    if (event.pointerType === "pen") {
      this.lastPenAt = performance.now();
      this.noteTilt(event);
    }
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
    const entry = this.pointers.get(event.pointerId);
    this.pointers.delete(event.pointerId);
    this.capture(event.pointerId, false);
    this.lastInputAt = performance.now();
    if (event.pointerType === "pen") this.lastPenAt = performance.now();

    if (canceled) {
      this.stats.cancel += 1;
      this.canceled = { id: event.pointerId, at: performance.now() };
      // Pencil 写着写着被系统打断，多半是随手写（Scribble）在抢输入。
      if (event.pointerType === "pen" && entry && entry.role === "draw") {
        this.stats.penCancel += 1;
        this.hooks.onPenInterrupted?.(this.stats.penCancel);
      }
    } else {
      this.stats.up += 1;
      this.canceled = null;
    }

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

  /** 把这一笔的倾斜读数记下来，诊断面板上能直接看到（连点状态圆点三下打开）。 */
  noteTilt(event) {
    this.stats.tiltX = Math.round(event.tiltX || 0);
    this.stats.tiltY = Math.round(event.tiltY || 0);
    this.stats.altRaw =
      typeof event.altitudeAngle === "number"
        ? Math.round((event.altitudeAngle * 180) / Math.PI)
        : null;
    this.stats.tiltDeg = Math.round((penAltitude(event) * 180) / Math.PI);
  }

  pressureFor(event, sample) {
    if (event.pointerType === "pen") {
      let pressure = event.pressure > 0 ? event.pressure : 0.5;
      // 笔身放平时笔迹变宽，模拟侧锋
      const tilt = 1 - penAltitude(event) / (Math.PI / 2);
      pressure = clamp(pressure * (1 + tilt * 0.45), 0, 1);
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
      limits: this.getLimits(),
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

    if (draw.limits) {
      draw.sx = clamp(draw.sx, draw.limits.x0, draw.limits.x1);
      if (draw.sy < draw.limits.y0) draw.sy = draw.limits.y0;
      if (draw.limits.y1 !== undefined && draw.sy > draw.limits.y1) draw.sy = draw.limits.y1;
    }

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
      let [wx, wy] = this.toWorld(event);
      if (draw.limits) {
        wx = clamp(wx, draw.limits.x0, draw.limits.x1);
        if (wy < draw.limits.y0) wy = draw.limits.y0;
        if (draw.limits.y1 !== undefined && wy > draw.limits.y1) wy = draw.limits.y1;
      }
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

  /**
   * 橡皮此刻的半径。
   *
   * **对象橡皮擦**碰到哪一笔就整笔删掉，作用点永远是笔尖那么大，不变。
   *
   * **像素橡皮擦**是把笔画切开，擦多宽就是切口多宽，所以跟着笔身与屏幕的夹角走：
   * 立着还是笔尖，压下去才变宽，到 ``ERASER_FULL_DEG`` 就已经最粗。两种都不用
   * 手动调尺寸——想擦大片就把笔压下去，想抠一笔就立起来。
   *
   * 鼠标、手指，以及报不出倾斜的笔，像素模式按中间那一档给：没有倾斜可依据时
   * 一直是笔尖等于没法用，一直最粗又太凶。
   */
  eraserRadius(event) {
    if (this.getTool().eraserMode !== "pixel") return ERASER_TIP / 2;
    // 鼠标和手指没有倾斜可依据，按常握笔的角度给一档，不然一直是笔尖等于没法用
    if (!event || event.pointerType !== "pen") return eraserDiameter(50) / 2;
    return eraserDiameter((penAltitude(event) * 180) / Math.PI) / 2;
  }

  startErase(event) {
    this.erase = {
      pointerId: event.pointerId,
      ids: [],
      radius: this.eraserRadius(event),
      last: null,
    };
    this.moveErase(event);
  }

  /** 屏幕半径换算成世界半径：判定和光标都在世界坐标里做。 */
  worldRadius(screenRadius) {
    return screenRadius / this.viewport.scale;
  }

  moveErase(event) {
    const erase = this.erase;
    if (!erase || erase.pointerId !== event.pointerId) return;
    // 平滑在屏幕尺度上做：倾斜读数给的本来就是屏幕上该有多粗
    erase.radius += (this.eraserRadius(event) - erase.radius) * ERASER_SMOOTH;
    const [wx, wy] = this.toWorld(event);
    const radius = this.worldRadius(erase.radius);
    this.renderer.cursor = { x: wx, y: wy, r: radius };
    // 擦得快的时候两次事件之间能隔开一大段，判定要按扫过的这条线段来，
    // 只看当前这个点会留下一串没擦到的缝
    const hit = this.hooks.onErase(wx, wy, radius, erase.last);
    erase.last = [wx, wy];
    if (hit && hit.length) erase.ids.push(...hit);
  }

  endErase() {
    const erase = this.erase;
    this.erase = null;
    this.renderer.cursor = null;
    // 一律通知抬笔：啃边不删任何笔画，ids 是空的，但撤销记录要在这里收口
    if (erase) this.hooks.onEraseEnd(erase.ids);
  }

  updateCursor(event) {
    const tool = this.getTool();
    if (tool.tool !== "eraser") {
      if (this.renderer.cursor) this.renderer.cursor = null;
      return;
    }
    const [wx, wy] = this.toWorld(event);
    this.renderer.cursor = { x: wx, y: wy, r: this.worldRadius(this.eraserRadius(event)) };
  }

  // --------------------------------------------------------- 平移 / 缩放

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
  }

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
  }

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
  }

  stopMomentum() {
    if (this.momentum) {
      cancelAnimationFrame(this.momentum);
      this.momentum = 0;
    }
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
  }

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
  }

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
  }

  onPinchEnd(event) {
    event.preventDefault();
    if (!this._pinch) return;
    this._pinch = null;
    this.hooks.onGestureEnd?.();  // 和松手一样，给一次吸附的机会
  }
}

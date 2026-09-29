// 指针输入：书写、擦除、平移与缩放。
//
// 规则（对齐 iPad 原生体感）：
//   * 默认只有 Apple Pencil 能画线，手指一律是平移 / 缩放，手掌搭上去也不会留痕；
//     没有 Pencil 的人可以在工具栏上打开「手指书写」，此时单指画线、双指手势，
//     第二根手指落下会撤掉刚起笔的那一下；
//   * 鼠标左键书写，中键 / 右键 / 空格拖动画布，滚轮平移，⌘ / Ctrl + 滚轮缩放。
//
// 所有笔迹先落在本地画布上，再通过网络发出去，本地书写不等待任何回包。

import { clearStrokeCache, TOOLS, pressureForFactor } from "./stroke.js";
import { clamp, isTextField } from "./util.js";
import { installShellFallback } from "./shell-fallback.js";
import { installErase } from "./input-erase.js";
import { installGesture } from "./input-gesture.js";

const FINGER_FLAG = "whiteboard.fingerDraw";
// Pencil 落笔期间以及抬笔后的这段时间里，手指 / 手掌一律不参与任何操作。
const PALM_GRACE = 500;

const SMOOTH_MOUSE = 0.6;
// 两个采样点至少要隔开多少个**屏幕**像素才算动过。
//
// iPad 报的 clientX/clientY 是整数（八份真机录像里 100%），所以位移小于一个像素
// 的那些采样点不带位置信息，只有量化噪声。笔停在原地、或者正在抬起来的时候，
// 坐标就在相邻整数之间跳，方向每次翻 90°——真机录像里一笔的末尾有连着七个采样点
// 都是这样。轮廓那边会把这些当成真的急转弯：偏移方向按 lrp(下一段, 这一段, 点积)
// 取，转角一大长度就缩，笔画在那里被掐细到应有宽度的 62%，紧接着末端又扣一个
// 整圆的帽子——看上去就是「细一下再鼓个球」。
//
// 门槛原来是 0.65 个屏幕像素，一个像素的抖动照样过得去。1.2 挡得住，同时慢慢写
// 的时候也不丢细节：真正在动的笔累计走满 1.2 像素就会留下一个点。
const MIN_STEP_PX = 1.2;
// 也不能小于笔半宽的这个比例。笔停下来之前的那几个采样点位移只有零点几个世界
// 单位，而笔本身有六七个单位宽——这种位移改变不了形状，只会给轮廓送去乱跳的方向。
const MIN_STEP_RATIO = 0.12;
// 抬笔前「已经停住」的判定半径，按笔半宽算。见 trimSettledTail。
const TAIL_SETTLE = 0.3;
const PRESSURE_SMOOTH = 0.25;

// 没有压感读数的指针（鼠标、手指、不带压感的笔）该画多粗。粗细本身是按速度算的，
// 存进文件之前折回压感值，见 stroke.js 的 pressureForFactor。
// 慢下来就是设定的线宽，快到 1.98 px/ms 收到七成——和以前那条
// `clamp(1 - speed / 3.2, 0.38, 1)` 配旧曲线画出来的粗细全程差不到 1%。
const SPEED_THIN = 0.157;
const SPEED_FLOOR = 0.69;
// 这支笔一次都没报过压感：按设定线宽的七成半画，也是以前的粗细。
const NO_PRESSURE_FACTOR = 0.75;

// iPad 外壳（docs/ipad-shell.md）。外壳把 UIKit 的 Pencil 采样转给网页，网页声明
// 自己认得的接口版本范围；外壳的版本落在范围里才会开始发。提高上限时保留对上一个
// 版本的支持，直到下一个版本发布——Mac 端先更新、外壳还没更新时照样能用。
export const SHELL_BRIDGE = [1, 1];
// 外壳的触摸编号从 1 开始，和浏览器的 pointerId 放进同一张表里会撞，挪开一段。
const SHELL_POINTER_BASE = 1e6;
// 坐标偏差：两边的时间戳换算到同一条时间轴之后，只在前后这么多毫秒之内找对应的
// 外壳采样。Safari 的事件时间可能是派发时刻而不是触摸时刻，窗口要容得下这点延迟，
// 又不能大到让一笔里走回头的另一段混进来。
const SHELL_MATCH_MS = 25;
// 同一次落笔里最多留这么多个采样用来比对坐标，够一笔两三秒。
const SHELL_TRACE = 512;

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

/** 压感读数加上倾斜：笔身放平时笔迹变宽，模拟侧锋。 */
function tiltedPressure(pressure, altitude) {
  const tilt = 1 - altitude / (Math.PI / 2);
  return clamp(pressure * (1 + tilt * 0.45), 0, 1);
}

/**
 * 外壳的一个采样换成 InputController 认得的样子。
 *
 * 字段和浏览器的指针事件一一对应，之后的判定、平滑、提交都不区分来源：
 *
 * * 压力取 ``f / fmax``。Safari 报的压力和它在同一量级，按文档 7.2 节推测就是
 *   同一个量，所以不做换算；
 * * 倾斜只给 ``altitudeAngle``，``tiltX`` / ``tiltY`` 置 0——``penAltitude`` 看到
 *   这两个都是 0 才会去读 altitude；
 * * 时间换成毫秒、保留小数。只在同一次落笔之内求时间差，不和 ``performance.now()``
 *   比较（文档 5.3 节）。
 */
export function shellEvent(sample, id, phase) {
  const fmax = sample.fmax > 0 ? sample.fmax : 0;
  return {
    clientX: sample.x,
    clientY: sample.y,
    pressure: fmax ? clamp(sample.f / fmax, 0, 1) : 0,
    tiltX: 0,
    tiltY: 0,
    twist: 0,
    altitudeAngle: typeof sample.alt === "number" ? sample.alt : Math.PI / 2,
    azimuthAngle: sample.az,
    timeStamp: sample.t * 1000,
    pointerId: SHELL_POINTER_BASE + id,
    pointerType: "pen",
    isPrimary: true,
    button: 0,
    buttons: phase === "up" || phase === "cancel" ? 0 : 1,
    fromShell: true,
    // 外壳断流时网页用 Safari 事件补的采样，不算进外壳的采样率
    fallback: sample.k === "safari",
    preventDefault() {},
    stopPropagation() {},
  };
}

/**
 * 砍掉笔停下来之后那一小撮采样点。
 *
 * 抬笔之前笔通常已经停住了，但事件还在来：坐标在相邻整数之间游走，真机录像里
 * 一笔末尾常有四五个点挤在一两个世界单位之内，而且相对笔画的走向偏出去一点。
 * 轮廓那边会在最后一个点上扣一个整圆的笔帽，于是那一撮点把笔帽顶到笔画的轴线
 * 外面——看上去就是笔画的头上鼓一个球。
 *
 * 所以从末尾往回走，凡是离终点不到 ``TAIL_SETTLE`` 倍笔半宽的点都砍掉，让笔画
 * 停在最后一个「还在动」的位置上。至少留两个点，点一下画个点的情形不受影响。
 */
function trimSettledTail(stroke) {
  const p = stroke.p;
  const count = (p.length / 3) | 0;
  if (count < 3) return;
  const limit = (stroke.w / 2) * TAIL_SETTLE;
  const ex = p[(count - 1) * 3];
  const ey = p[(count - 1) * 3 + 1];
  let keep = count - 1;
  while (keep > 1) {
    const dx = p[(keep - 1) * 3] - ex;
    const dy = p[(keep - 1) * 3 + 1] - ey;
    if (dx * dx + dy * dy > limit * limit) break;
    keep -= 1;
  }
  if (keep < count - 1) p.length = (keep + 1) * 3;
}

/** 指针事件里补点要用的字段，复制出来：事件对象过后不一定还能读。 */
function snapPen(event) {
  return {
    clientX: event.clientX,
    clientY: event.clientY,
    pressure: event.pressure,
    tiltX: event.tiltX,
    tiltY: event.tiltY,
    altitudeAngle: event.altitudeAngle,
    azimuthAngle: event.azimuthAngle,
  };
}

/** 丢掉一秒之前的记录，返回剩下的个数。 */
function countTicks(ticks, now) {
  while (ticks.length && now - ticks[0][0] > 1000) ticks.shift();
  return ticks.length;
}

function countMoved(ticks) {
  let moved = 0;
  for (const [, isNew] of ticks) moved += isNew;
  return moved;
}

export class InputController {
  constructor(options) {
    this.stage = options.stage;
    this.viewport = options.viewport;
    this.renderer = options.renderer;
    this.getTool = options.getTool;
    this.getLimits = options.getLimits || (() => null);
    // 只读：白板文件没能完整读出来（服务端锁住了它）。落笔一律改成拖动画布，
    // 否则写上去的笔画服务端不收，重新打开就没了。
    this.readOnly = false;
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
      // 笔每秒送来多少个事件、其中多少个是新位置。这两个数差一倍以上就说明
      // 系统在重复投递同一个位置，笔迹的上限就卡在「新位置」那一个数上。
      penHz: 0, penMoveHz: 0,
      // 外壳（docs/ipad-shell.md 7.4 节）：输入来源、外壳版本，外壳来源的采样率，
      // 以及外壳采样和 Safari 自己的 pen 事件在同一时刻差了多少 CSS 像素
      source: "browser", shellVersion: "", shellBridge: 0,
      shellHz: 0, shellMoveHz: 0,
      shellDev: null, shellDevN: 0,
      // 估计属性更新：修正到笔画上的，和笔画提交之后才到、只能丢掉的
      shellUpd: 0, shellUpdLate: 0,
      // 外壳没送 up、由网页替它收尾的笔画数；外壳断流时用 Safari 事件补上的采样数
      shellOrphan: 0, shellFallback: 0,
    };
    // [时间, 是不是新位置]，只留最近一秒
    this.penTicks = [];
    this.lastPenPos = null;
    // 外壳输入来源。active 时 #stage 上的 pen 指针事件一律不参与书写，改用外壳的采样。
    this.shell = {
      active: false,
      ignored: new Set(), // 落在界面控件上的那几次落笔，整次都不管
      ticks: [],
      lastPos: null,
      est: new Map(), // estimationUpdateIndex → 这个采样落进了哪一笔的第几个点
      // 坐标偏差比对：同一次落笔里两边的采样，以及两边落笔那一刻的时间
      trace: { shell: [], safari: [], shellDown: null, safariDown: null },
      // 最近一个外壳采样（原始数据）和收到它的时刻，替外壳收尾时用
      lastSample: null,
      lastAt: -Infinity,
      // 最近一次收到外壳真实采样的时刻（不算网页自己补的）
      realAt: -Infinity,
      watchdog: 0,
      // Safari 最近一次 pen 落笔（外壳一直没送这一笔时，由它起笔），以及网页自己
      // 起的笔用的编号（负数，和外壳的编号不会重）
      safariDown: null,
      fallbackSeq: 0,
      fallbackDown: null,
    };
    // 录像从这里收外壳送来的每一批原样数据
    this.shellTap = null;
    // 回放时为真：补点和收尾都在录像里，不再按时间重新判断一遍
    this.replaying = false;
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
      // 在输入框里打的空格是字，不是「按住空格拖动画布」
      if (e.code === "Space" && !isTextField(e.target)) this.spaceHeld = true;
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

  /** 缓存画布位置：每个采样点都量一次会强制重排，而一帧里可能有好几个。 */
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
    if (this.readOnly) return false;
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

  /** 外壳接管 Pencil 时，Safari 自己发的 pen 事件：不参与书写，只拿来比对坐标。 */
  shadowedPen(event) {
    return this.shell.active && event.pointerType === "pen" && !event.fromShell;
  }

  onDown(event) {
    event.preventDefault();
    if (this.shadowedPen(event)) {
      // 手掌屏蔽照样要算：Safari 的这一下常常比外壳那一批先到
      this.lastPenAt = performance.now();
      this.traceSafari(event, true);
      this.shell.safariDown = { event: snapPen(event), at: performance.now() };
      return;
    }
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
    if (this.shadowedPen(event)) {
      event.preventDefault();
      this.lastPenAt = now;
      this.notePenRate(now, event);
      this.traceSafari(event, false);
      if (this.shellMissing()) this.shellFallback(this.shell.safariDown.event, "down");
      if (this.shellStalled()) this.shellFallback(event, "move");
      return;
    }
    if (this.draw && this.lastInputAt > 0) {
      const gap = now - this.lastInputAt;
      if (gap > this.stats.maxGap) this.stats.maxGap = gap;
    }
    this.stats.move += 1;
    this.lastInputAt = now;
    if (event.pointerType === "pen") {
      this.lastPenAt = performance.now();
      this.noteTilt(event);
      this.notePenRate(now, event);
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
    if (this.shadowedPen(event)) {
      this.lastPenAt = performance.now();
      this.traceSafari(event, false);
      this.measureShellOffset();
      if (this.shellMissing()) this.shellFallback(this.shell.safariDown.event, "down");
      if (this.shellStalled()) this.shellFallback(event, "up");
      else if (this.openShellId() !== null) this.watchShellStroke();
      else this.watchShellTap(snapPen(event));
      this.shell.safariDown = null;
      return;
    }
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
      // 取消不补最后一段：那一下不是用户抬的笔，位置不代表他想擦到哪
      if (this.erase) this.endErase(canceled ? null : event);
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

  // ------------------------------------------------------------ 外壳输入

  /**
   * 外壳握手的结果。``active`` 为真时 Pencil 的输入改由外壳提供，``#stage`` 上
   * Safari 自己的 pen 事件只用来比对坐标；手指和鼠标照旧走指针事件。
   */
  setShell(state) {
    const shell = this.shell;
    shell.active = !!(state && state.active);
    shell.ignored.clear();
    shell.est.clear();
    this.stats.source = shell.active ? "shell" : "browser";
    this.stats.shellVersion = (state && state.version) || "";
    this.stats.shellBridge = (state && state.bridge) || 0;
  }

  /**
   * 外壳送来的一批采样（格式见 docs/ipad-shell.md 5.1 节）。
   *
   * 每个采样换成和指针事件同样的对象，交给 onDown / onMove / onUp，之后的判定、
   * 平滑、提交和 Safari 来源完全是同一条路。估计属性更新修正还没提交的那一笔；
   * 预测采样只画在实时层上，不进笔画。
   */
  receiveShell(batch) {
    if (!batch || !this.shell.active) return;
    if (this.shellTap) this.shellTap(batch);
    const samples = Array.isArray(batch.samples) ? batch.samples : [];
    if (samples.length && !batch.fallback && !batch.orphan) this.shell.realAt = performance.now();
    for (const sample of samples) this.shellSample(sample);
    // 网页替外壳收尾的那一笔：外壳之后要是又送来这一笔的采样，一律不管
    if (batch.orphan) for (const sample of samples) this.shell.ignored.add(sample.id);
    if (Array.isArray(batch.updates)) {
      for (const update of batch.updates) this.shellUpdate(update);
    }
    this.shellPredict(batch.pred || null);
  }

  shellSample(sample) {
    const shell = this.shell;
    shell.lastSample = sample;
    shell.lastAt = performance.now();
    const id = sample.id;
    const phase = sample.ph;
    const event = shellEvent(sample, id, phase);
    if (phase === "down") {
      // 网页已经替这一笔用 Safari 的事件起了笔，外壳的落笔才到：不再开第二笔
      if (!sample.k || sample.k === "real") {
        const open = this.openShellId();
        // 同一个触摸：外壳和 Safari 报的落点只差取整，1.5 像素以内
        const late = shell.fallbackDown;
        const same =
          late && performance.now() - late.at < 2000 &&
          Math.abs(late.x - sample.x) <= 1.5 && Math.abs(late.y - sample.y) <= 1.5;
        if ((open !== null && open < 0) || same) {
          shell.ignored.add(id);
          return;
        }
      } else if (sample.k === "safari") {
        shell.fallbackDown = { x: sample.x, y: sample.y, at: performance.now() };
      }
      // 落在界面控件上的这一整次落笔都不管，交给控件自己的点击事件——Pencil 照样
      // 能点工具栏。判断的是「落点在不在画布里」：Safari 的 pen 事件本来也只有落在
      // 画布上的才会走到这里，两边的行为因此一致。
      if (!this.onStage(sample.x, sample.y)) {
        shell.ignored.add(id);
        return;
      }
      shell.ignored.delete(id);
      if (shell.est.size > 4096) shell.est.clear();
      this.traceShell(sample, true);
      this.onDown(event);
      this.noteEstimate(sample, event, 0);
      return;
    }
    if (shell.ignored.has(id)) {
      if (phase === "up" || phase === "cancel") shell.ignored.delete(id);
      return;
    }
    this.traceShell(sample, false);
    if (phase === "move") {
      const before = this.draw ? this.draw.stroke.p.length : 0;
      this.onMove(event);
      this.noteEstimate(sample, event, before);
      return;
    }
    this.onUp(event, phase === "cancel");
    this.measureShellOffset();
  }

  /** 外壳这一笔现在是不是开着：书写或擦除都算。返回它的触摸编号。 */
  openShellId() {
    if (this.draw && this.draw.shell) return this.draw.pointerId - SHELL_POINTER_BASE;
    if (this.erase && this.erase.pointerId >= SHELL_POINTER_BASE) {
      return this.erase.pointerId - SHELL_POINTER_BASE;
    }
    return null;
  }

  /** 这个视口坐标上是不是画布（而不是工具栏、面板之类的界面控件）。 */
  onStage(x, y) {
    const target = document.elementFromPoint(x, y);
    return !!target && this.stage.contains(target);
  }

  /** 这个采样等着更新力度，而且真的落成了笔画上的一个点：记下是哪一个点。 */
  noteEstimate(sample, event, before) {
    const draw = this.draw;
    if (!draw || draw.pointerId !== event.pointerId) return;
    if (sample.ui === null || sample.ui === undefined) return;
    if (!Array.isArray(sample.est) || !sample.est.length) return;
    const points = draw.stroke.p;
    if (points.length <= before) return; // 位移太小没有落点，修不修都一样
    this.shell.est.set(sample.ui, { stroke: draw.stroke, index: points.length / 3 - 1, sample });
  }

  /**
   * 估计属性更新（通常是力度）。第一阶段只修还没提交的那一笔；笔画提交之后才到的
   * 只能丢掉，记个数。按 InkProbe 四份会话，更新在采样之后约 25 ms 到达，每一笔
   * 最后约 6 个采样的更新会落在抬笔之后（见 docs/ipad-shell.md 7.2 节）。
   */
  shellUpdate(update) {
    const shell = this.shell;
    const entry = shell.est.get(update.ui);
    if (!entry) return;
    shell.est.delete(update.ui);
    const draw = this.draw;
    if (!draw || draw.stroke !== entry.stroke) {
      this.stats.shellUpdLate += 1;
      return;
    }
    const points = draw.stroke.p;
    const i = entry.index;
    if (i * 3 + 2 >= points.length) return;
    const sample = entry.sample;
    const force = typeof update.f === "number" ? update.f : sample.f;
    const altitude = typeof update.alt === "number" ? update.alt : sample.alt;
    if (!(sample.fmax > 0) || !(force > 0)) return;
    const target = tiltedPressure(
      clamp(force / sample.fmax, 0, 1),
      clamp(typeof altitude === "number" ? altitude : Math.PI / 2, 0, Math.PI / 2),
    );
    // 和 addSample 里一样的平滑：从前一个点的压感往目标走一步
    points[i * 3 + 2] = i === 0 ? target : points[i * 3 - 1] + (target - points[i * 3 - 1]) * PRESSURE_SMOOTH;
    clearStrokeCache(draw.stroke);
    this.stats.shellUpd += 1;
  }

  /**
   * 预测采样：只画在实时层上。每一批的预测整体替换上一批的，所以每次都从这一笔
   * 当前的点列复制一份再接上去，笔画本身和发给对端的实时点都不含预测。
   */
  shellPredict(pred) {
    const draw = this.draw;
    if (!draw || !draw.shell) return;
    const samples = pred && Array.isArray(pred.samples) ? pred.samples : [];
    if (!samples.length || SHELL_POINTER_BASE + pred.id !== draw.pointerId) {
      this.renderer.setLive("local", draw.stroke);
      return;
    }
    const points = draw.stroke.p.slice();
    let sp = draw.sp;
    for (const sample of samples) {
      const event = shellEvent(sample, pred.id, "move");
      let [wx, wy] = this.toWorld(event);
      if (draw.limits) {
        wx = clamp(wx, draw.limits.x0, draw.limits.x1);
        if (wy < draw.limits.y0) wy = draw.limits.y0;
        if (draw.limits.y1 !== undefined && wy > draw.limits.y1) wy = draw.limits.y1;
      }
      const raw =
        event.pressure > 0
          ? event.pressure
          : draw.lastPressure > 0
            ? draw.lastPressure
            : pressureForFactor(NO_PRESSURE_FACTOR);
      sp += (tiltedPressure(raw, penAltitude(event)) - sp) * PRESSURE_SMOOTH;
      points.push(wx, wy, sp);
    }
    this.renderer.setLive("local", { ...draw.stroke, p: points, _pts: null, _path: null, _bbox: null });
  }

  /**
   * 坐标偏差（诊断面板，验收标准 A3）：同一次落笔里，外壳的采样和 Safari 自己的
   * pen 事件在同一时刻差了多少 CSS 像素。
   *
   * 两边的时间戳不在同一条时间轴上，用落笔那一下对齐：两边的第一个采样是同一个
   * UITouch，时间差就是两条时间轴的偏移。Safari 的每个采样都是 UIKit 的某一个
   * 采样取整之后的样子，所以在换算后前后 ``SHELL_MATCH_MS`` 之内找离它最近的
   * 外壳采样，两者之差就是偏差：取整本身最多 0.5 像素，再多就是坐标系没对齐。
   */
  traceShell(sample, down) {
    const trace = this.shell.trace;
    if (down) {
      trace.shell = [];
      trace.shellDown = sample.t * 1000;
    }
    if (trace.shell.length < SHELL_TRACE) trace.shell.push([sample.t * 1000, sample.x, sample.y]);
  }

  traceSafari(event, down) {
    const trace = this.shell.trace;
    if (down) {
      trace.safari = [];
      trace.safariDown = event.timeStamp;
    }
    if (trace.safari.length < SHELL_TRACE) {
      trace.safari.push([event.timeStamp, event.clientX, event.clientY]);
    }
  }

  measureShellOffset() {
    const trace = this.shell.trace;
    if (trace.shellDown === null || trace.safariDown === null) return;
    if (!trace.shell.length || !trace.safari.length) return;
    const offset = trace.safariDown - trace.shellDown;
    const shell = trace.shell;
    let worst = 0;
    let matched = 0;
    let start = 0;
    for (const [time, x, y] of trace.safari) {
      const want = time - offset;
      while (start < shell.length && shell[start][0] < want - SHELL_MATCH_MS) start += 1;
      let best = null;
      let bestDist = Infinity;
      for (let j = start; j < shell.length && shell[j][0] <= want + SHELL_MATCH_MS; j++) {
        const dist = Math.hypot(shell[j][1] - x, shell[j][2] - y);
        if (dist < bestDist) {
          bestDist = dist;
          best = shell[j];
        }
      }
      if (!best) continue;
      worst = Math.max(worst, Math.abs(best[1] - x), Math.abs(best[2] - y));
      matched += 1;
    }
    if (matched) {
      this.stats.shellDev = worst;
      this.stats.shellDevN = matched;
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

  /**
   * 笔的采样率：每秒来多少个事件，其中多少个带来了新位置。
   *
   * 这两个数要分开看。iPad 上事件来了 120～125/s，其中只有约 64 条带来新坐标；
   * 另外那一半和前一条完全一样——坐标、压感、倾角全同（八份真机录像里这个比例是
   * 80%～100%），不带任何信息。笔迹的上限由「新位置」那个数决定，再好的平滑也
   * 补不回没采到的那一段。真机上接不了开发者工具，只能显示在屏幕上。
   */
  notePenRate(now, event) {
    const pos = `${event.clientX},${event.clientY}`;
    if (event.fromShell) {
      if (event.fallback) return;
      // 外壳的采样同样按「收到的时刻」计数，算法和 Safari 那两个数相同，才能直接比。
      // 一批里的几个采样是同一时刻收到的，照样各算一个。
      const shell = this.shell;
      shell.ticks.push([now, pos !== shell.lastPos ? 1 : 0]);
      shell.lastPos = pos;
      this.stats.shellHz = countTicks(shell.ticks, now);
      this.stats.shellMoveHz = countMoved(shell.ticks);
      return;
    }
    this.penTicks.push([now, pos !== this.lastPenPos ? 1 : 0]);
    this.lastPenPos = pos;
    this.stats.penHz = countTicks(this.penTicks, now);
    this.stats.penMoveHz = countMoved(this.penTicks);
  }

  /**
   * 这一个采样点该用多大的压感。
   *
   * ``pressure === 0`` 有两种含义，必须分开：设备根本报不了压感（那就得给个
   * 默认值），和笔快离开屏幕时压感掉到 0（那是真读数，末尾该收细）。原来一律
   * 当成前者顶成 0.5，于是每一笔的收尾都不是收细而是鼓一个包——真机录像里
   * 抬笔前那两下压感就是 0.005、0.005、0，顶成 0.5 之后末端反而粗了两成半。
   *
   * 所以只要这一笔里报过一次正压感，后面的 0 就按「掉读数」处理，沿用上一次的
   * 值；一次都没报过才算这支笔没有压感。
   *
   * 返回的是压感通道的值，不是粗细。压感读数原样返回（粗细曲线在 stroke.js 里
   * 按 iPad 实际的量程展开），没有读数的时候返回「想要的粗细」折回来的那个值。
   */
  pressureFor(event, sample) {
    if (event.pointerType === "pen") {
      const draw = this.draw;
      const raw = event.pressure;
      let pressure;
      if (raw > 0) {
        pressure = raw;
        if (draw) draw.lastPressure = raw;
      } else if (draw && draw.lastPressure > 0) {
        pressure = draw.lastPressure;
      } else {
        pressure = pressureForFactor(NO_PRESSURE_FACTOR);
      }
      return tiltedPressure(pressure, penAltitude(event));
    }
    // 鼠标 / 手指没有压感，用速度反推：走得快笔迹细。
    const factor = clamp(1 - sample.speed * SPEED_THIN, SPEED_FLOOR, 1);
    return pressureForFactor(factor);
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
      sp:
        event.pointerType === "pen" && event.pressure > 0
          ? event.pressure
          : pressureForFactor(NO_PRESSURE_FACTOR),
      // 这一笔里报过正压感没有：报过的话，后面的 0 就是掉读数而不是「没有压感」
      lastPressure: event.pointerType === "pen" && event.pressure > 0 ? event.pressure : 0,
      lastTime: event.timeStamp,
      lastScreen: this.toScreen(event),
      shell: !!event.fromShell,
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
    // 笔的位置原样存，不预平滑：轮廓那边 perfect-freehand 的 streamline 已经在做
    // 同一件事，两层叠起来只是多一份延迟、把转角多削一道。鼠标和手指照旧要平滑
    // ——它们的坐标抖动来源不一样（轨迹球、手指接触面积），而且没有压感可参照。
    if (first || draw.type === "pen") {
      draw.sx = wx;
      draw.sy = wy;
    } else {
      draw.sx += (wx - draw.sx) * SMOOTH_MOUSE;
      draw.sy += (wy - draw.sy) * SMOOTH_MOUSE;
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
      const minDist = Math.max(
        MIN_STEP_PX / this.viewport.scale,
        (draw.stroke.w / 2) * MIN_STEP_RATIO,
      );
      if (dx * dx + dy * dy < minDist * minDist) return;
    }
    points.push(draw.sx, draw.sy, draw.sp);
    this.sampleCount += 1;
    this.pendingLive.push(draw.sx, draw.sy, draw.sp);
    clearStrokeCache(draw.stroke);
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
      trimSettledTail(draw.stroke);
      clearStrokeCache(draw.stroke);
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

}

// 0.9.43 外壳断流时的兜底（watchShellStroke / shellMissing / watchShellTap /
// shellStalled / shellFallback）单独放在 shell-fallback.js 里，见那边的说明。
installShellFallback(InputController.prototype, { SHELL_BRIDGE, penAltitude });

// 擦除、平移缩放两部分各放一个文件，方法装回 InputController 上。
installErase(InputController.prototype, { penAltitude });
installGesture(InputController.prototype);

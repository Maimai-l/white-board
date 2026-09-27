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
 * 11.4（窄 45%），44° 处原生还是 17～19 而插值给到 23（宽 30%）。
 *
 * 平台段的下端后来又往下挪过一次。会话 a 有五条拖动落在 37°～43°，原生垂直宽度
 * 全在 15.5～16.0，而当时的曲线在这一段给到 20～28（宽 25%～70%）。加上 44°～68°
 * 那一批，**37° 到 68° 之间是平的，约 16.5**，37° 以下才陡升。
 *
 * 37° 和 35° 之间这个台阶很陡：16.5 跳到 35.2。两侧是两种量法——35° 及以下来自
 * 孤立点的**洞面积换算的等效直径**，37° 及以上来自沿拖动路径量的**垂直宽度**，
 * 只有落笔形状是圆的时候两者才是同一个数。按 35° 处的两个值反推，那里的落笔更像
 * 一个 16.5 宽、75 长的椭圆。
 *
 * 曾经因此把 37° 以下改成「两个锚点之间连直线」，想把台阶抹平。逐像素对照否了这个
 * 改动：抹平之后 test1 的 IoU 从 0.931 掉到 0.862、bench1 从 0.871 掉到 0.830，
 * 而孤立点那几档原样留着时两边都不掉。也就是说，这几档虽然量的是等效直径，拿来
 * 当曲线却更贴近原生擦掉的实际范围，所以留着不动。
 *
 * 台阶带来的手感问题不在曲线上，在「整笔粗细跟不跟着倾斜走」——见 startErase。
 */
const ERASER_CURVE = [
  [90, 6],
  [80, 7.5],
  [68, 16.5],
  [37, 16.5],
  [35, 35],
  [32, 52],
  [28, 75],
  [25, 81],
  [0, 81],
];

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
// Safari 的 pen 已经抬起，外壳那一笔过了这么久还没有 up：由网页替它收尾。
// 不收尾的话这一笔一直开着，手掌屏蔽就一直生效，手指的平移缩放全部失灵——
// 0.9.43 的录像 20260927-211825 就是这样。
const SHELL_ORPHAN_MS = 150;
// 外壳这一笔超过这么久没有新采样、Safari 的 pen 事件却还在来：外壳断流了，
// 用 Safari 的事件把这一笔接着画完。外壳每帧至少送一批，正常间隔不到 20 ms。
const SHELL_STALL_MS = 50;

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
      if (this.shellStalled()) this.shellFallback(event, "up");
      else this.watchShellStroke();
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

  /**
   * Safari 报了抬笔：等一会儿，外壳要是一直没再送采样，就替它送一个 up。
   *
   * 替它送的这一批照样经过 receiveShell，所以录像里有它，回放时结果相同。
   * 这一笔之后如果外壳又送来采样，按 ignored 丢掉。
   */
  watchShellStroke() {
    const shell = this.shell;
    if (this.replaying || this.openShellId() === null) return;
    const liftedAt = performance.now();
    clearTimeout(shell.watchdog);
    shell.watchdog = setTimeout(() => {
      shell.watchdog = 0;
      if (shell.lastAt > liftedAt) return; // 外壳还在送，它自己会收尾
      const id = this.openShellId();
      const last = shell.lastSample;
      if (id === null || !last || last.id !== id) return;
      this.stats.shellOrphan += 1;
      this.receiveShell({
        bridge: SHELL_BRIDGE[1],
        samples: [{ ...last, ph: "up", est: [], ui: null }],
        pred: null,
        updates: [],
        orphan: true,
      });
    }, SHELL_ORPHAN_MS);
  }

  /** 外壳这一笔开着，却已经有一阵没送真实采样了。 */
  shellStalled() {
    if (this.replaying || this.openShellId() === null) return false;
    return performance.now() - this.shell.realAt > SHELL_STALL_MS;
  }

  /**
   * 外壳断流时，用 Safari 自己的 pen 事件把这一笔接着画完。
   *
   * 0.9.43 的外壳在网页 preventDefault 之后就收不到这一笔的触摸了（见
   * docs/ipad-shell.md 12 节 Q2），每一笔只有开头约 30 ms。外壳要重新安装才能
   * 修好，网页这边先兜住：精度退回 Safari 的水平，但笔画是完整的。
   *
   * 补的采样也走 receiveShell，录像里有它，回放时不再重新判断。
   */
  shellFallback(event, phase) {
    const shell = this.shell;
    const id = this.openShellId();
    const last = shell.lastSample;
    const t = last ? last.t + (performance.now() - shell.lastAt) / 1000 : 0;
    this.stats.shellFallback += 1;
    if (phase === "up") this.stats.shellOrphan += 1;
    this.receiveShell({
      bridge: SHELL_BRIDGE[1],
      samples: [{
        id,
        ph: phase,
        k: "safari",
        t,
        x: event.clientX,
        y: event.clientY,
        f: event.pressure || 0,
        fmax: 1,
        alt: penAltitude(event),
        az: typeof event.azimuthAngle === "number" ? event.azimuthAngle : 0,
        est: [],
        ui: null,
      }],
      pred: null,
      updates: [],
      fallback: true,
      orphan: phase === "up",
    });
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

  /** 抬笔的位置和最后一个采样点是不是同一处，是的话就不用再扫一段。 */
  sameSpot(last, event) {
    if (!last) return false;
    const [wx, wy] = this.toWorld(event);
    return Math.abs(last[0] - wx) < 1e-6 && Math.abs(last[1] - wy) < 1e-6;
  }

  /**
   * 橡皮的粗细在落笔那一刻定下来，整笔不再跟着倾斜走。
   *
   * 原生就是这样：92 条原生橡皮笔画里，65% 整笔只报一个倾角读数，九成笔内极差
   * 在 2.36° 以内，中位数是 0。而我们原来每个采样点都重新平滑一次
   * （``ERASER_SMOOTH``），iPad 报的 ``tiltX`` / ``tiltY`` 又是整度的，写字时笔身
   * 本来就在晃，于是同一笔里直径能差 2.7 倍——擦痕一节粗一节细，像一串香肠。
   *
   * 悬停时的光标圈照旧跟着倾斜走（``updateCursor``），那是预览，本来就该跟手。
   */
  startErase(event) {
    this.erase = {
      pointerId: event.pointerId,
      ids: [],
      radius: this.eraserRadius(event),
      last: null,
    };
    this.moveErase(event);
  }

  /**
   * 屏幕半径换算成世界半径：判定和光标都在世界坐标里做。
   *
   * 橡皮在屏幕上恒定大小，两个方向都成立，所以直接除以缩放。放大方向早就量过
   * （同一倾角在 zoom 1 和 zoom 2.02 下，印记在 drawing 坐标里差一倍）；缩小方向
   * 由会话 c 定下来：zoom 0.25、倾角 10.4°，原生擦出来的垂直宽度是 322 个 drawing
   * 单位，乘回缩放是 80.5 个屏幕单位，曲线在那个倾角给的是 81。
   *
   * 这里曾经有过一个 `Math.max(scale, 1)` 的下限，依据是 bench2：zoom 0.25 下
   * 2361 个橡皮采样、其中 200 个直接压在可见墨迹上，二十条笔画却一个遮罩都没有。
   * 会话 c 在同样的缩放下擦得很彻底，所以那不是缩放规则，bench2 那次是整条橡皮
   * 没有被 PencilKit 记录下来。详见 docs/eraser.md。
   */
  worldRadius(screenRadius) {
    return screenRadius / this.viewport.scale;
  }

  /**
   * 橡皮走到一个采样点：扫过上一点到这一点之间那一段。
   *
   * 粗细在落笔时定下，整笔不再变（见 startErase）。
   *
   * 擦得快的时候两次采样之间能隔开一大段，判定要按扫过的这条线段来，
   * 只看当前这个点会留下一串没擦到的缝。
   */
  eraseAt(erase, event) {
    const [wx, wy] = this.toWorld(event);
    const radius = this.worldRadius(erase.radius);
    this.renderer.cursor = { x: wx, y: wy, r: radius };
    const hit = this.hooks.onErase(wx, wy, radius, erase.last);
    erase.last = [wx, wy];
    if (hit && hit.length) erase.ids.push(...hit);
  }

  /**
   * 橡皮和画线一样要吃掉一帧里的全部合并采样点。
   *
   * iPad 上笔是 120Hz 而 pointermove 一帧才来一次，中间那些点都塞在
   * getCoalescedEvents 里。只取最后一个，等于把一帧里的一段曲线压成一条直线，
   * 擦得越快压得越狠，擦痕边上就出现一节一节的直棱。画线那边一直是取全部的，
   * 橡皮这边漏了。
   */
  moveErase(event) {
    const erase = this.erase;
    if (!erase || erase.pointerId !== event.pointerId) return;
    const events = event.getCoalescedEvents ? event.getCoalescedEvents() : null;
    if (events && events.length > 1) {
      for (const sample of events) this.eraseAt(erase, sample);
    } else {
      this.eraseAt(erase, event);
    }
  }

  endErase(event) {
    const erase = this.erase;
    // 抬笔那一下的位置也要擦掉。最后一个 pointermove 停在上一帧，笔离开屏幕
    // 之前还走了一段，这一段只有 pointerup 里有。不补的话擦痕停在上一帧的
    // 位置，末端留下一道正好是橡皮直径宽的硬边——手感上就是「一松手就多出个
    // 断面」，而且笔在那一段上真正压过的地方还留着墨。
    if (erase && event && !this.sameSpot(erase.last, event)) {
      this.eraseAt(erase, event);
    }
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

// 把 vendor/pencilkit-picker.js 里的那条工具盘接到白板上（触摸设备上的默认工具栏）。
//
// 那份实现是「PencilKit 工具栏 + 画布」的整套复刻。我们只要工具栏：画布、直尺、
// 套索、历史记录都归白板自己管，所以这里用继承的方式把画布那一半摘掉，
// 原文件一个字节都没改（见 vendor/README.md）。
//
// 接进来的东西：选工具、每支笔各自的颜色与粗细、点选中的笔弹出粗细面板、
// 完整的取色器（色板 / 色轮 / RGB 滑块）、拖动换边、丢进角落缩成一个圆、
// 撤销按钮、「更多」里的自动最小化与手指绘图开关。
//
// 拖动与松手按 iPadOS 的手感重写过：圆进入边的触发区要停留一会儿才变成长条，
// 长条一出触发区立即变回圆；松手时按惯性推算停点，决定去哪个角（圆）或哪条边（长条）。
// 松手分「甩」和「慢放」两种：甩的时候先飞到位再展开、带回弹；慢放时飞行、转笔、展开
// 几乎同时进行、不回弹。动画进行中可以随时拿起。手感参数都在下面。
//
// 没接的：不透明度（笔迹格式里还没有这个字段）、重做（白板没有重做）、
// 直尺和套索（白板没有这两个功能，对应的按钮已经从布局里去掉）。

import { clamp } from "./util.js";

// 去掉套索和直尺之后的布局，其余数值原样取自 vendor 里的 PK_LAYOUT。
const LAYOUT = {
  h: {
    W: 544,
    H: 105,
    items: {
      more: { x: 490, y: 37, w: 31, h: 31, r: 0 },
      eraser: { x: 274, y: 0, w: 30, h: 105, r: 0 },
      pencil: { x: 220, y: 0, w: 30, h: 105, r: 0 },
      marker: { x: 168, y: 0, w: 30, h: 105, r: 0 },
      pen: { x: 116, y: 0, w: 30, h: 105, r: 0 },
      redo: { x: 64, y: 37, w: 31, h: 31, r: 0 },
      undo: { x: 22, y: 37, w: 31, h: 31, r: 0 },
      grip: { x: 254, y: 5, w: 36, h: 5, r: 0 },
    },
    swatches: [
      { x: 437, y: 57, w: 30, h: 30, r: 0, c: "custom" },
      { x: 396, y: 57, w: 30, h: 30, r: 0, c: "#fc3142" },
      { x: 354, y: 57, w: 30, h: 30, r: 0, c: "#fed031" },
      { x: 437, y: 16, w: 30, h: 30, r: 0, c: "#53d669" },
      { x: 396, y: 16, w: 30, h: 30, r: 0, c: "#157efa" },
      { x: 354, y: 16, w: 30, h: 30, r: 0, c: "#000000" },
    ],
  },
  vl: {
    W: 105,
    H: 499,
    items: {
      more: { x: 36, y: 443, w: 31, h: 31, r: 0 },
      eraser: { x: 38, y: 212, w: 30, h: 105, r: 90 },
      pencil: { x: 38, y: 160, w: 30, h: 105, r: 90 },
      marker: { x: 38, y: 107, w: 30, h: 105, r: 90 },
      pen: { x: 38, y: 54, w: 30, h: 105, r: 90 },
      grip: { x: 80, y: 247, w: 36, h: 5, r: 90 },
      redo: { x: 58, y: 41, w: 31, h: 31, r: 0 },
      undo: { x: 16, y: 41, w: 31, h: 31, r: 0 },
    },
    swatches: [
      { x: 58, y: 386, w: 30, h: 30, r: 0, c: "#53d669" },
      { x: 18, y: 386, w: 30, h: 30, r: 0, c: "custom" },
      { x: 58, y: 346, w: 30, h: 30, r: 0, c: "#157efa" },
      { x: 18, y: 346, w: 30, h: 30, r: 0, c: "#fc3142" },
      { x: 58, y: 305, w: 30, h: 30, r: 0, c: "#000000" },
      { x: 18, y: 305, w: 30, h: 30, r: 0, c: "#fed031" },
    ],
  },
  vr: {
    W: 105,
    H: 499,
    items: {
      more: { x: 36, y: 443, w: 31, h: 31, r: 0 },
      eraser: { x: 38, y: 212, w: 30, h: 105, r: 270 },
      pencil: { x: 38, y: 160, w: 30, h: 105, r: 270 },
      marker: { x: 38, y: 107, w: 30, h: 105, r: 270 },
      pen: { x: 38, y: 54, w: 30, h: 105, r: 270 },
      grip: { x: -10, y: 247, w: 36, h: 5, r: 90 },
      redo: { x: 58, y: 41, w: 31, h: 31, r: 0 },
      undo: { x: 16, y: 41, w: 31, h: 31, r: 0 },
    },
    swatches: [
      { x: 58, y: 386, w: 30, h: 30, r: 0, c: "#53d669" },
      { x: 18, y: 386, w: 30, h: 30, r: 0, c: "custom" },
      { x: 58, y: 346, w: 30, h: 30, r: 0, c: "#157efa" },
      { x: 18, y: 346, w: 30, h: 30, r: 0, c: "#fc3142" },
      { x: 58, y: 305, w: 30, h: 30, r: 0, c: "#000000" },
      { x: 18, y: 305, w: 30, h: 30, r: 0, c: "#fed031" },
    ],
  },
};

// 我们的工具 → 那份实现里的工具造型
export const TOOL_ART = { pen: "pen", marker: "pencil", highlighter: "marker", eraser: "eraser" };
const ART_TOOL = Object.fromEntries(Object.entries(TOOL_ART).map(([k, v]) => [v, k]));
const TITLES = { pen: "钢笔", marker: "马克笔", highlighter: "荧光笔", eraser: "橡皮擦" };

/* ============================ 手感参数：都在这里调 ============================ */
// 所有手感参数集中在 TUNING 里，改这里的数值即可。
export const TUNING = {
  // ---- 触发区：贴着边、沿边方向居中的长方形 ----
  // 宽和高都以触发区所贴的那条边为参考：宽 = 沿着这条边的长度，高 = 从这条边伸进屏幕的深度。
  // 上下两条边：宽占屏幕宽的比例；高（像素）
  ZONE_TB_WIDTH_RATIO: 0.47,
  ZONE_TB_HEIGHT_PX: 200,
  // 左右两条边：宽占屏幕高的比例；高（像素）
  ZONE_LR_WIDTH_RATIO: 0.35,
  ZONE_LR_HEIGHT_PX: 180,
  // 相邻两个触发区之间至少留出的间隔：上下触发区的两端至少让出「左右触发区的高 + 这个值」，
  // 上下触发区的宽设得太大时会被自动缩短，保证与左右触发区互不接触
  ZONE_GAP_PX: 24,

  // ---- 拖动中 ----
  // 圆进入某条边的触发区后，停留多少毫秒才变成这条边的长条（出触发区则立即变回圆）
  DOCK_DWELL_MS: 250,
  // 手底下圆与长条互变：形状以手指为中心向四周伸缩，这里是时长和曲线
  MORPH_MS: 460,
  MORPH_EASE: "cubic-bezier(.28, 1.1, .4, 1)",

  // ---- 松手 ----
  // 用松手前多少毫秒内的移动计算速度；松手前停住超过这个时间，就当作没有速度
  VELOCITY_WINDOW_MS: 100,
  // 松手速度达到多少（像素/秒）算「甩」，低于它算「慢放」（点击展开、悬停展开也算慢放）。
  // 只有「甩」才按惯性推算停点；「慢放」就以松手点为停点
  FLING_SPEED: 1200,
  // 甩的时候：推算停点 = 松手点 + 速度（像素/秒）× 这个秒数，越大飞得越远
  FLING_PROJECTION_S: 0.61,
  // 甩的时候：推算停点离松手点最远多少像素（惯性滑行距离的上限）
  FLING_MAX_PX: 600,

  // ---- 圆的飞行：展开前的飞行、收成圆、飞到角落都用这一组，不回弹 ----
  TRAVEL_MS: 660,
  TRAVEL_EASE: "cubic-bezier(.25, .8, .25, 1)",
  // 圆里的笔转向（去左边朝右、去右边朝左、去上下竖直；方向相同就不转）
  TURN_MS: 300,
  TURN_EASE: "cubic-bezier(.4, 0, .2, 1)",

  // ---- 甩：长条先收成圆 → 转笔 → 飞到位 → 展开（带回弹） ----
  // 起飞后多少毫秒开始转笔，留时间让长条先收成圆；FAST_TURN_AT_MS + TURN_MS 应不大于 TRAVEL_MS
  FAST_TURN_AT_MS: 0,
  // 飞行结束后停多少毫秒再展开；负数表示在飞行结束前提前展开。
  // 飞行曲线前快后慢，圆看起来到位时离飞行结束还有一段时间，所以通常要设成负数才没有停顿感
  FAST_EXPAND_PAUSE_MS: -350,
  // 展开的时长与曲线，末尾略微超出再回弹；上下两边之间直接滑过去的长条也用这一组
  BOUNCE_MS: 400,
  BOUNCE_EASE: "cubic-bezier(.32, 1.28, .5, 1)",

  // ---- 甩：在上下两边之间，长条直接滑过去（单独调） ----
  // 滑动的最短时长
  SLIDE_MS: 400,
  // 滑动速度上限（像素/秒）：距离远时自动拉长时长，时长 = 距离 ÷ 这个速度，但不短于 SLIDE_MS
  SLIDE_MAX_SPEED: 1000,
  // 滑动曲线（带回弹）
  SLIDE_EASE: "cubic-bezier(.32, 1.28, .5, 1)",

  // ---- 慢放：飞行、转笔同时开始，很快就开始展开（平滑，不回弹） ----
  // 起飞后多少毫秒开始展开
  SLOW_EXPAND_AT_MS: 50,
  // 展开的时长与曲线；拖动中已在手底下展开的长条贴到边上也用这一组
  SMOOTH_MS: 420,
  SMOOTH_EASE: "cubic-bezier(.25, .8, .25, 1)",
};

/* ========================================================================== */

// 各条边上笔的方向：左边的长条里笔朝右，右边朝左，上下竖直
const EDGE_DEG = { left: 90, right: -90, top: 0, bottom: 0 };
// vendor 里拖动中圆的直径（MOVE_D）
const MOVE_D = 105;

/** 把 "cubic-bezier(a, b, c, d)" 变成一个函数：输入时间进度 0–1，返回动画进度。其他写法按匀速处理。 */
function easeFn(css) {
  const m = /cubic-bezier\(([^)]+)\)/.exec(css || "");
  const p = m ? m[1].split(",").map(Number) : [];
  if (p.length !== 4 || p.some((n) => !Number.isFinite(n))) return (t) => t;
  const [x1, y1, x2, y2] = p;
  const curve = (t, a, b) => 3 * (1 - t) * (1 - t) * t * a + 3 * (1 - t) * t * t * b + t * t * t;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let lo = 0;
    let hi = 1;
    let t = x;
    for (let i = 0; i < 40; i++) {
      const v = curve(t, x1, x2);
      if (Math.abs(v - x) < 1e-5) break;
      if (v < x) lo = t;
      else hi = t;
      t = (lo + hi) / 2;
    }
    return curve(t, y1, y2);
  };
}

let loading = null;

/** 按需加载：这份 vendor 文件有 400 多 KB，用不上它的设备就不要去解析。 */
export async function loadPicker() {
  if (!loading) {
    loading = import("../vendor/pencilkit-picker.js").then(() => window.PencilBoard);
  }
  const Base = await loading;
  if (!Base) throw new Error("笔具盘没能载入");
  return makeClass(Base);
}

let Picker = null;

function makeClass(Base) {
  if (Picker) return Picker;

  Picker = class PencilKitPicker extends Base {
    /* ---------------- 把画布那一半摘掉 ---------------- */

    _build() {
      super._build();
      // 画布、直尺、套索都由白板自己负责，这里只留工具栏
      for (const node of [this.ink, this.live, this.rulerEl]) node.remove();
      this.ink = this.live = document.createElement("canvas");
      this.inkCtx = this.liveCtx = this.ink.getContext("2d");
      // 套索和直尺白板没有：从 DOM 里摘掉就行，对象要留着——
      // 它的 _renderUI 会遍历整份工具清单，删了会空指针
      for (const key of ["lasso", "ruler"]) {
        if (this.el[key]) this.el[key].remove();
      }
      // 笔迹还没有不透明度这个字段，统一按不透明处理（工具上那个「80」就不显示了）
      for (const cfg of Object.values(this.tools)) {
        if (cfg && cfg.opacity !== undefined) cfg.opacity = 1;
      }
      for (const [art, tool] of Object.entries(ART_TOOL)) {
        const button = this.el[art];
        if (!button) continue;
        button.title = TITLES[tool];
        button.setAttribute("aria-label", TITLES[tool]);
      }
      // 动画的时长和曲线交给 css/pk-host.css 使用，数值只在本文件顶部的 TUNING 里维护
      this._applyTuning();
    }

    /** 把 TUNING 里的动画时长和曲线写进 css/pk-host.css 使用的 --pkm-* 变量；改了 TUNING 后再调一次。 */
    _applyTuning() {
      const style = this.picker.style;
      style.setProperty("--pkm-travel", `${TUNING.TRAVEL_MS}ms ${TUNING.TRAVEL_EASE}`);
      style.setProperty("--pkm-grow", `${TUNING.BOUNCE_MS}ms ${TUNING.BOUNCE_EASE}`);
      style.setProperty("--pkm-smooth", `${TUNING.SMOOTH_MS}ms ${TUNING.SMOOTH_EASE}`);
      style.setProperty("--pkm-turn", `${TUNING.TURN_MS}ms ${TUNING.TURN_EASE}`);
    }

    _bind() {
      super._bind();
      // 摘掉两处：一是它绑的 ⌘Z / Esc / Delete，会和白板自己的快捷键打架；
      // 二是它的拖动过程，我们要在拖到边上时就直接展开，而不是松手才贴边。
      const drop = new Set(["keydown", "pointermove", "pointerup", "pointercancel"]);
      this._subs = (this._subs || []).filter(([target, type, fn, opts]) => {
        if (target === window && drop.has(type)) {
          window.removeEventListener(type, fn, opts);
          return false;
        }
        return true;
      });
      this._on(window, "pointermove", (event) => this._dragMove(event));
      this._on(window, "pointerup", (event) => this._dragFinish(event));
      this._on(window, "pointercancel", (event) => this._dragFinish(event));
      // vendor 在圆飞行途中（moving）不接受按下；这里补上，动画进行中也能直接拿起
      this._on(this.picker, "pointerdown", (event) => {
        this._eatClick = false; // 这是一次新的按下，上一次拖动留下的标记作废
        if (this.state !== "moving" || this._pd || event.button > 0) return;
        this._pd = { id: event.pointerId, x: event.clientX, y: event.clientY, drag: false };
      });
      // 见 _dragFinish：只吞掉松手当下派生的那一次点击
      this._on(
        this.picker,
        "click",
        (event) => {
          if (!this._eatClick) return;
          this._eatClick = false;
          event.stopPropagation();
          event.preventDefault();
        },
        true
      );
      // 收起来之后，笔 / 光标靠近就提前展开，不用非得点中那个圆
      this._on(window, "pointermove", (event) => this._hoverExpand(event));
      // 「更多」里的开关不发事件，点完之后自己对一次；另外接住我们加的两行
      this._on(this.pop, "click", (event) => {
        const row = event.target.closest("[data-wb]");
        if (!row) {
          this._sync();
          return;
        }
        const what = row.dataset.wb;
        this._closePop();
        if (what === "clear" && this.onClear) this.onClear();
        if (what === "leave" && this.onLeave) this.onLeave();
      });
    }

    /* ---------------- 拖动 ---------------- */

    /**
     * 开始拖动（手指移动超过 10px 时）。动画进行中拿起时，工具盘从画面上当前的位置、
     * 大小和笔的角度接着变化，不会跳。长条被拿起时保持长条，并记住手指相对长条中心的
     * 位置；圆被拿起时变成拖动中的大圆，圆里的笔保持原来的方向。
     */
    _dragStart() {
      clearTimeout(this._settleT);
      clearTimeout(this._turnT);
      this._settleT = this._turnT = null;
      this._closePop();
      this._samples = [];
      this._formWant = undefined;
      this._grab = { x: 0, y: 0 };
      // 这次拖动是不是从长条开始的（从哪条边）；甩的时候即使中途已变成圆，也按长条处理
      this._dragFrom = this.state === "docked" ? this.dock : null;
      if (this.state === "docked") {
        const bar = this.picker.getBoundingClientRect();
        this._grab = {
          x: this._pd.x - (bar.left + bar.width / 2),
          y: this._pd.y - (bar.top + bar.height / 2),
        };
      } else {
        if (this.state === "minimized") this._setArt(0, MOVE_D, false);
        this._setState("moving");
      }
      this.picker.dataset.motion = "follow";
      // 从当前画面上的大小开始，变到当前形态应有的大小
      const now = this.picker.getBoundingClientRect();
      this._size = { w: now.width, h: now.height };
      this._morphTo(this._formSize());
      this._lastPt = this._local({ clientX: this._pd.x, clientY: this._pd.y });
      cancelAnimationFrame(this._dragRaf);
      this._dragRaf = requestAnimationFrame(() => this._frame());
    }

    /**
     * 拖动中：指针所在的触发区（_dockZone）决定「想要」的形态，不在触发区就是圆。
     * 出了触发区立即变圆；进入某条边的触发区则开始计时，停留够 DOCK_DWELL_MS 才变成
     * 这条边的长条，中途换边重新计时。计时期间保持当前形态跟手。
     */
    _dragMove(event) {
      const drag = this._pd;
      if (!drag || drag.id !== event.pointerId) return;
      if (!drag.drag) {
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 10) return;
        drag.drag = true;
        this._dragStart();
      }
      const pt = this._local(event);
      this._lastPt = pt;
      this._track(pt);

      const want = this._dockZone(pt);
      const have = this.state === "docked" ? this.dock : null;
      if (want === have) {
        clearTimeout(this._formT);
        this._formT = null;
        this._formWant = undefined;
      } else if (!want) {
        clearTimeout(this._formT);
        this._formT = null;
        this._formWant = undefined;
        this._switchForm(null);
        return;
      } else if (want !== this._formWant) {
        clearTimeout(this._formT);
        this._formWant = want;
        this._formT = setTimeout(() => this._switchForm(want), TUNING.DOCK_DWELL_MS);
      }
      this._follow(pt);
    }

    /**
     * 在手底下切换形态：want 是某条边时变成那条边的长条；want 为 null 时变回圆，
     * 圆里的笔先摆成原来长条里的方向，再转回竖直（从左右两边拖出来时会看到转正）。
     */
    _switchForm(want) {
      this._formT = null;
      this._formWant = undefined;
      if (!this._pd || !this._pd.drag) return;
      if (want) {
        this.dock = want;
        this.minCorner = null;
        this._layoutBar();
        this._setState("docked");
      } else {
        this._setArt(EDGE_DEG[this.dock] || 0, MOVE_D, true);
        this._setArt(0, MOVE_D, false);
        this._setState("moving");
      }
      // 手指相对形状中心的偏移随形变一起缩到 0：形状从原来的中心平滑移到手指下，不会一下跳过去
      this._morphTo(this._formSize(), { x: 0, y: 0 });
    }

    /** 记下指针位置；位置和形状由 _frame 每一帧统一计算。 */
    _follow(pt) {
      this._lastPt = pt;
    }

    /** 当前形态应有的大小：圆为拖动中的大圆，长条按当前这条边的布局。 */
    _formSize() {
      if (this.state !== "docked") return { w: MOVE_D, h: MOVE_D };
      const L = LAYOUT[this.mode];
      return { w: L.W * this._scale, h: L.H * this._scale };
    }

    /**
     * 从现在的大小开始，按 MORPH_MS / MORPH_EASE 变到 to；手指相对形状中心的偏移
     * 同时从现在的值变到 grabTo（不传则保持不变）。
     */
    _morphTo(to, grabTo) {
      const now = performance.now();
      const cur = this._stateNow(now);
      this._morph = {
        from: cur.size,
        to,
        grabFrom: cur.grab,
        grabTo: grabTo || cur.grab,
        t0: now,
        dur: TUNING.MORPH_MS,
        ease: easeFn(TUNING.MORPH_EASE),
      };
    }

    /** 某一时刻形状的大小，以及手指相对形状中心的偏移。 */
    _stateNow(now) {
      const m = this._morph;
      if (!m) return { size: this._size || { w: MOVE_D, h: MOVE_D }, grab: this._grab || { x: 0, y: 0 } };
      const t = m.dur > 0 ? Math.min(1, (now - m.t0) / m.dur) : 1;
      const e = m.ease(t);
      const mix = (a, b) => a + (b - a) * e;
      return {
        size: { w: mix(m.from.w, m.to.w), h: mix(m.from.h, m.to.h) },
        grab: { x: mix(m.grabFrom.x, m.grabTo.x), y: mix(m.grabFrom.y, m.grabTo.y) },
      };
    }

    /**
     * 拖动中每一帧：先算出形状此刻的大小和手指偏移，再以「手指减去偏移」为中心摆放，
     * 所以圆与长条互变时，形状从中心向两端伸缩，不会先偏到一边。长条不超出屏幕。
     */
    _frame() {
      this._dragRaf = 0;
      if (!this._pd || !this._pd.drag) return;
      const { size, grab } = this._stateNow(performance.now());
      const { w, h } = size;
      this._size = size;
      this._grab = grab;
      const pt = this._lastPt;
      let cx = pt.x - grab.x;
      let cy = pt.y - grab.y;
      if (this.state === "docked") {
        cx = clamp(cx, 8 + w / 2, Math.max(8 + w / 2, this.W - w / 2 - 8));
        cy = clamp(cy, 8 + h / 2, Math.max(8 + h / 2, this.H - h / 2 - 8));
      }
      this._apply({ x: cx - w / 2, y: cy - h / 2, w, h, r: Math.min(w, h) / 2 });
      this._dragRaf = requestAnimationFrame(() => this._frame());
    }

    /** 记录拖动轨迹，只保留最近 VELOCITY_WINDOW_MS 内的点（至少两个），用来算松手速度。 */
    _track(pt) {
      const now = performance.now();
      const list = this._samples || (this._samples = []);
      list.push({ x: pt.x, y: pt.y, t: now });
      while (list.length > 2 && now - list[0].t > TUNING.VELOCITY_WINDOW_MS) list.shift();
    }

    /** 松手时的速度（像素/秒）。最后一次移动已经是很久以前，说明手停住了，速度为 0。 */
    _velocity() {
      const list = this._samples || [];
      if (list.length < 2) return { x: 0, y: 0 };
      const first = list[0];
      const last = list[list.length - 1];
      const dt = last.t - first.t;
      if (dt <= 0 || performance.now() - last.t > TUNING.VELOCITY_WINDOW_MS) return { x: 0, y: 0 };
      return { x: ((last.x - first.x) / dt) * 1000, y: ((last.y - first.y) / dt) * 1000 };
    }

    /**
     * 松手：未到时间的形态切换一律作废。速度达到 FLING_SPEED 算「甩」，否则算「慢放」。
     * 甩的时候按速度推算停点（滑行距离有上限，并限制在屏幕内），慢放以松手点为停点；
     * 停点落在某条边的触发区里就展开到那条边；
     * 否则交给 _dragDrop 按角落 / 最近的边处理。
     */
    _dragFinish(event) {
      const drag = this._pd;
      if (!drag || drag.id !== event.pointerId) return;
      this._pd = null;
      if (!drag.drag) return;
      cancelAnimationFrame(this._dragRaf);
      this._dragRaf = 0;
      this._morph = null;
      clearTimeout(this._formT);
      this._formT = null;
      this._formWant = undefined;

      const pt = this._local(event);
      const v = this._velocity();
      const speed = Math.hypot(v.x, v.y);
      const fast = speed >= TUNING.FLING_SPEED;
      // 只有甩才有惯性滑行，滑行距离不超过 FLING_MAX_PX
      const glide = fast ? Math.min(speed * TUNING.FLING_PROJECTION_S, TUNING.FLING_MAX_PX) : 0;
      const target = {
        x: clamp(pt.x + (speed ? (v.x / speed) * glide : 0), 0, this.W),
        y: clamp(pt.y + (speed ? (v.y / speed) * glide : 0), 0, this.H),
      };
      // vendor 的防误触是「松手后 400ms 内的点击一律吞掉」（pencilkit-picker.js:589），
      // 代价是拖完要等 0.4 秒，第一下点击才生效。真正需要挡的只有松手当下派生的那一次：
      // 长条是以手指为中心跟手的，手指松开时正压在橡皮那一格上，不挡就会误选橡皮；
      // 圆同理，轻轻一蹭就会当成点一下、直接展开。所以把 vendor 那个时间窗关掉
      // （_dragEnd 置 0），改成用 _eatClick 精确吞掉一次，没有任何死时间。
      this._dragEnd = 0;
      this._eatClick = true;
      // 松手时指针就在圆旁边，要等它先离开一次，悬停展开才重新生效
      this._hoverArmed = false;
      // 甩的时候，只要这次拖动是从长条开始的，就按「从那条边的长条出发」处理
      const fromEdge = this.state === "docked" ? this.dock : fast ? this._dragFrom : null;
      const zone = this._dockZone(target);
      if (zone) this._openAt(zone, fast, fromEdge);
      else this._dragDrop(target, fast, fromEdge);
    }

    /**
     * 指针落在哪条边的触发区里，不在任何触发区返回 null。
     * 每个触发区是贴着边、沿边方向居中的长方形；上下两块的两端至少让出左右两块的高
     * 再加 ZONE_GAP_PX，所以上下两块与左右两块在水平方向上错开，相邻触发区不会接触。
     */
    _dockZone(pt) {
      const W = this.W;
      const H = this.H;
      const tbDepth = Math.min(TUNING.ZONE_TB_HEIGHT_PX, H / 2 - TUNING.ZONE_GAP_PX);
      const lrDepth = Math.min(TUNING.ZONE_LR_HEIGHT_PX, W / 2 - TUNING.ZONE_GAP_PX);
      const tbMargin = Math.max((W - W * TUNING.ZONE_TB_WIDTH_RATIO) / 2, lrDepth + TUNING.ZONE_GAP_PX);
      const lrMargin = (H - H * TUNING.ZONE_LR_WIDTH_RATIO) / 2;
      const inTB = pt.x >= tbMargin && pt.x <= W - tbMargin;
      const inLR = pt.y >= lrMargin && pt.y <= H - lrMargin;
      if (inTB && pt.y <= tbDepth) return "top";
      if (inTB && pt.y >= H - tbDepth) return "bottom";
      if (inLR && pt.x <= lrDepth) return "left";
      if (inLR && pt.x >= W - lrDepth) return "right";
      return null;
    }

    /** 悬停到收起来的那个圆附近就展开（手指没有悬停，只能点）。 */
    _hoverExpand(event) {
      if (this.state !== "minimized" || event.buttons) return;
      if (event.pointerType === "touch") return;
      const rect = this.picker.getBoundingClientRect();
      const pad = 56;
      const inX = event.clientX >= rect.left - pad && event.clientX <= rect.right + pad;
      const inY = event.clientY >= rect.top - pad && event.clientY <= rect.bottom + pad;
      if (!inX || !inY) {
        this._hoverArmed = true;
        return;
      }
      if (!this._hoverArmed) return;
      this._expand();
    }

    // 画布事件归白板，这里一概不接
    _down() {}
    _move() {}
    _up() {}

    _resize() {
      const rect = this.root.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      this.W = rect.width;
      this.H = rect.height;
      this._closePop();
      this._layoutBar();
      if (this.state === "moving") this._setState("docked");
      this._apply(this._geom(), false);
    }

    /* ---------------- 停靠：多一个顶部 ---------------- */

    get mode() {
      // 顶部用的就是底部那套横排布局，只是摆的位置和面板方向不一样
      return this.dock === "top" ? "h" : super.mode;
    }

    /** 停点不在任何触发区里：落在角落就收成圆停在那个角，否则展开到最近的边。 */
    _dragDrop(pt, fast, fromEdge) {
      const left = pt.x;
      const right = this.W - pt.x;
      const top = pt.y;
      const bottom = this.H - pt.y;
      const corner = Math.min(160, this.W / 4, this.H / 4);
      if (Math.min(left, right) < corner && Math.min(top, bottom) < corner) {
        this._settleCorner((top < bottom ? "t" : "b") + (left < right ? "l" : "r"));
        return;
      }
      const nearest = Math.min(left, right, top, bottom);
      this._openAt(
        nearest === bottom ? "bottom" : nearest === top ? "top" : nearest === left ? "left" : "right",
        fast,
        fromEdge
      );
    }

    /* ---------------- 展开与收起的动画 ---------------- */

    /**
     * 展开到 edge 这条边。fast 为 true 表示「甩」，否则为「慢放」。fromEdge 是出发时长条
     * 所在的边，从圆出发时为空。
     * 长条直接滑过去、不经过圆的两种情况：已经是这条边的长条；从上下两边之一甩到另一边
     * （笔的方向不变；拖动中已经变成圆的，直接变回长条滑过去）。
     * 其余情况经过圆：长条先收成圆，圆里的笔摆成原来长条的方向，飞向目标并转到目标方向。
     *   甩：FAST_TURN_AT_MS 开始转笔，飞到位后再展开，带回弹。
     *   慢放：转笔与飞行同时开始，SLOW_EXPAND_AT_MS 就开始展开，平滑不回弹。
     * 点击圆、悬停展开都按慢放处理。
     */
    _openAt(edge, fast, fromEdge) {
      clearTimeout(this._settleT);
      clearTimeout(this._turnT);
      this._settleT = this._turnT = null;
      const fromBar = this.state === "docked";
      const from = this.dock;
      this.dock = edge;
      this.minCorner = null;
      this._layoutBar();
      const bar = this._geom("docked");
      this._emit("dock", edge);

      const flat = (e) => e === "top" || e === "bottom";
      const sameBar = fromBar && from === edge;
      const flatJump = fast && flat(fromEdge) && flat(edge);
      if (flatJump && !sameBar) {
        // 上下两边之间直接滑过去：距离越远时长越长，速度不超过 SLIDE_MAX_SPEED
        const now = this.picker.getBoundingClientRect();
        const root = this.root.getBoundingClientRect();
        const dist = Math.abs(now.top + now.height / 2 - root.top - (bar.y + bar.h / 2));
        const ms = Math.max(TUNING.SLIDE_MS, (dist / Math.max(1, TUNING.SLIDE_MAX_SPEED)) * 1000);
        this.picker.style.setProperty("--pkm-slide", `${Math.round(ms)}ms ${TUNING.SLIDE_EASE}`);
        this.picker.dataset.motion = "slide";
        this._setState("docked");
        this._apply(bar);
        return;
      }
      if (sameBar) {
        this.picker.dataset.motion = fast ? "grow" : "smooth";
        this._setState("docked");
        this._apply(bar);
        return;
      }

      const d = Math.min(bar.w, bar.h);
      if (fromBar) this._setArt(EDGE_DEG[from] || 0, d, true);
      else if (this.state === "minimized") this._setArt(0, d, false);
      this._setState("moving");
      this.picker.dataset.motion = "travel";
      this._apply({
        x: bar.x + bar.w / 2 - d / 2,
        y: bar.y + bar.h / 2 - d / 2,
        w: d,
        h: d,
        r: d / 2,
      });

      const deg = EDGE_DEG[edge];
      if (fast) {
        this._turnT = setTimeout(() => {
          this._turnT = null;
          this._setArt(deg, d, false);
        }, TUNING.FAST_TURN_AT_MS);
      } else {
        this._setArt(deg, d, false);
      }
      this._settleT = setTimeout(() => {
        this._settleT = null;
        this.picker.dataset.motion = fast ? "grow" : "smooth";
        this._setState("docked");
        this._apply(this._geom());
      }, fast ? Math.max(0, TUNING.TRAVEL_MS + TUNING.FAST_EXPAND_PAUSE_MS) : TUNING.SLOW_EXPAND_AT_MS);
    }

    /** 收成圆，停在 corner（"tl" / "tr" / "bl" / "br"）这个角；笔从原来的方向转回竖直。 */
    _settleCorner(corner) {
      this._toCircle();
      this.minCorner = corner;
      this._setState("minimized");
      this.picker.dataset.motion = "travel";
      this._apply(this._geom());
      this._emit("dock", "corner");
    }

    /** 自动最小化：收成圆，停在当前这条边上。 */
    _minimize() {
      this._toCircle();
      this._closePop();
      this.minCorner = null;
      this._setState("minimized");
      this.picker.dataset.motion = "travel";
      this._apply(this._geom());
    }

    /** 收成最小化的圆之前的准备：停掉进行中的展开；从长条收起时，笔先摆成长条里的方向。 */
    _toCircle() {
      clearTimeout(this._settleT);
      clearTimeout(this._turnT);
      this._settleT = this._turnT = null;
      if (this.state === "docked") this._setArt(EDGE_DEG[this.dock] || 0, MOVE_D, true);
      this.bubbleArt.style.transform = "";
      this._artDeg = 0;
    }

    /**
     * 设置圆里的笔的角度。笔的图形高 105px、以工具盘顶边为基准摆放；这里把它的中心移到
     * 高度为 height 的圆的圆心，并绕圆心转。instant 为 true 时不带动画，直接摆到这个角度。
     * 始终写成同一种变换形式，角度变化时才会绕圆心平滑转动。
     */
    _setArt(deg, height, instant) {
      const art = this.bubbleArt;
      if (instant) art.style.transition = "none";
      art.style.transform = `translateY(${height / 2}px) rotate(${deg}deg) translateY(-52.5px)`;
      if (instant) {
        void art.offsetWidth;
        art.style.transition = "";
      }
      this._artDeg = deg;
    }

    /**
     * 点击圆展开（悬停展开也走这里），按慢放处理。上一次停靠的是左边或右边时，
     * 展开到离圆较近的左边或右边；其他情况展开到离圆较近的顶部或底部。
     */
    _expand() {
      const root = this.root.getBoundingClientRect();
      const circle = this.picker.getBoundingClientRect();
      const cx = circle.left + circle.width / 2 - root.left;
      const cy = circle.top + circle.height / 2 - root.top;
      const side = this.dock === "left" || this.dock === "right";
      const edge = side ? (cx < this.W / 2 ? "left" : "right") : cy < this.H / 2 ? "top" : "bottom";
      this._openAt(edge, false);
    }

    /** 顶部停靠时面板往下开；其余方向沿用原实现。 */
    _placePop() {
      if (this.dock !== "top") return super._placePop();
      const pop = this.pop;
      pop.style.setProperty("--pk-pop-scale", "1");
      const anchor = this._popAnchor.getBoundingClientRect();
      const root = this.root.getBoundingClientRect();
      const bar = this.picker.getBoundingClientRect();
      const gap = 16;
      const width = pop.offsetWidth;
      const height = pop.offsetHeight;
      const scale = Math.min(
        1,
        (this.H - (bar.bottom - root.top) - gap - 8) / height,
        (this.W - 16) / width
      );
      pop.style.setProperty("--pk-pop-scale", String(scale));
      const shown = { w: width * scale, h: height * scale };
      const cx = anchor.left - root.left + anchor.width / 2;
      const x = clamp(cx - shown.w / 2, 8, this.W - shown.w - 8);
      const y = bar.bottom - root.top + gap;
      const arrow = clamp((cx - x) / scale, 24, width - 24);
      pop.style.setProperty("--ax", arrow + "px");
      pop.dataset.side = "bottom";
      // 缩放以左上角为基准，横向位置要按原始尺寸折算回去
      pop.style.left = x - arrow * (1 - scale) + "px";
      pop.style.top = y + "px";
    }

    /* ---------------- 换成去掉套索直尺的布局 ---------------- */

    _layoutBar() {
      const L = LAYOUT[this.mode];
      this.picker.dataset.dock = this.dock;
      const bar = this.bar;
      bar.style.width = L.W + "px";
      bar.style.height = L.H + "px";
      for (const key in L.items) {
        const node = this.el[key];
        if (!node) continue;
        const it = L.items[key];
        node.style.width = it.w + "px";
        node.style.height = it.h + "px";
        node.style.transform =
          `translate(${it.x}px,${it.y}px)` + (it.r ? ` rotate(${it.r}deg)` : "");
      }
      L.swatches.forEach((it, index) => {
        const node = this.swatchEls[index];
        node.style.width = it.w + "px";
        node.style.height = it.h + "px";
        node.style.transform = `translate(${it.x}px,${it.y}px)`;
        if (node.dataset.color !== it.c) {
          node.dataset.color = it.c;
          node.classList.toggle("pk-rainbow", it.c === "custom");
          node.innerHTML = "";
          if (it.c === "custom") node.setAttribute("aria-label", "更多颜色");
          else {
            node.style.setProperty("--c", it.c);
            node.setAttribute("aria-label", "颜色 " + it.c);
          }
        }
      });
      const horiz = this.mode === "h";
      const avail = (horiz ? this.W : this.H) - 40;
      this._scale = Math.min(1, avail / (horiz ? L.W : L.H));
      bar.style.transform = this._scale < 1 ? `scale(${this._scale})` : "none";
      this._renderUI();
    }

    _geom(state, pt) {
      const st = state || this.state;
      if (st !== "docked") return super._geom(state, pt);
      const L = LAYOUT[this.mode];
      const s = this._scale;
      const w = L.W * s;
      const h = L.H * s;
      const r = 52.5 * s;
      if (this.dock === "left") return { x: 20, y: (this.H - h) / 2, w, h, r };
      if (this.dock === "right") return { x: this.W - w - 20, y: (this.H - h) / 2, w, h, r };
      if (this.dock === "top") return { x: (this.W - w) / 2, y: 20, w, h, r };
      return { x: (this.W - w) / 2, y: this.H - h - 20, w, h, r };
    }

    /** 「更多」菜单里补上白板自己的两项。 */
    _popHTML(kind) {
      const html = super._popHTML(kind);
      if (kind !== "more") return html;
      // 清空白板是一项单独的权限，白板那边没给就别把这一行画出来
      const rows =
        (this.allowClear === false
          ? ""
          : `<button type="button" class="pk-row" data-wb="clear"><span>清空白板</span></button>`) +
        `<button type="button" class="pk-row" data-wb="leave"><span>换回普通工具栏</span></button>`;
      return html.replace(/<\/div>$/, rows + "</div>");
    }

    /* ---------------- 和白板对接 ---------------- */

    /** 工具、颜色、粗细任何一项变了都会走到 _renderUI，就在这里同步出去。 */
    _renderUI(force) {
      // 撤销 / 重做按钮的亮灭由白板的历史栈决定：它自己那套判定看的是
      // history 和 hIndex，这里按白板的状态摆一摆，图标就跟着对了。
      const back = this._canUndo ? 1 : 0;
      const forward = this._canRedo ? 1 : 0;
      this.hIndex = back;
      this.history = new Array(back + forward + 1).fill(null).map(() => []);
      super._renderUI(force);
      this._sync();
    }

    setHistory(canUndo, canRedo) {
      if (this._canUndo === !!canUndo && this._canRedo === !!canRedo) return;
      this._canUndo = !!canUndo;
      this._canRedo = !!canRedo;
      this._renderUI();
    }

    _sync() {
      if (!this.onChange) return;
      const tool = ART_TOOL[this.current] || "pen";
      const cfg = this.tools[this.current] || {};
      const next = {
        tool,
        color: cfg.color || "#000000",
        sizeIndex: typeof cfg.size === "number" ? cfg.size : 1,
        fingerDraws: !!this.fingerDraws,
        // 橡皮那个面板里的「对象 / 像素」二选一，白板两种都做了，见 app.js 的 erasePixels
        eraserMode: (this.tools.eraser && this.tools.eraser.mode) === "pixel" ? "pixel" : "object",
      };
      const key = JSON.stringify(next);
      if (key === this._lastSync) return;
      this._lastSync = key;
      this.onChange(next);
    }

    /** 撤销 / 重做都交给白板的历史栈。 */
    undo() {
      if (this.onUndo) this.onUndo();
    }

    redo() {
      if (this.onRedo) this.onRedo();
    }

    /** 白板落笔时调用：开了「自动最小化」就把工具盘收起来。 */
    strokeStarted() {
      if (this.autoMin && this.state === "docked") this._minimize();
    }

    /** 白板那边改了橡皮模式（比如在普通工具栏里改的），推回工具盘。 */
    setEraserMode(mode) {
      if (!this.tools.eraser) return;
      const next = mode === "pixel" ? "pixel" : "object";
      if (this.tools.eraser.mode === next) return;
      this.tools.eraser.mode = next;
      this._refreshPop();
      this._sync();
    }

    /** 白板那边改了工具 / 颜色（比如从缓存恢复），推回工具盘。 */
    applyState({ tool, color, sizeIndex, fingerDraws, eraserMode }) {
      const art = TOOL_ART[tool] || "pen";
      this.current = art;
      const cfg = this.tools[art];
      if (cfg) {
        if (color && cfg.color !== undefined) cfg.color = color;
        if (typeof sizeIndex === "number" && cfg.sizes) {
          cfg.size = Math.max(0, Math.min(cfg.sizes.length - 1, sizeIndex));
        }
      }
      if (fingerDraws !== undefined) this.fingerDraws = !!fingerDraws;
      if (eraserMode !== undefined && this.tools.eraser) {
        this.tools.eraser.mode = eraserMode === "pixel" ? "pixel" : "object";
      }
      this._renderUI(true);
    }

    /** 卸载：先停掉本文件的计时器，再走 vendor 原有的清理。 */
    destroy() {
      cancelAnimationFrame(this._dragRaf);
      clearTimeout(this._formT);
      clearTimeout(this._settleT);
      clearTimeout(this._turnT);
      super.destroy();
    }
  };
  return Picker;
}

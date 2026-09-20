// 把 vendor/pencilkit-picker.js 里的那条工具盘接到白板上（beta，只在 iPad 端用）。
//
// 那份实现是「PencilKit 工具栏 + 画布」的整套复刻。我们只要工具栏：画布、直尺、
// 套索、历史记录都归白板自己管，所以这里用继承的方式把画布那一半摘掉，
// 原文件一个字节都没改（见 vendor/README.md）。
//
// 接进来的东西：选工具、每支笔各自的颜色与粗细、点选中的笔弹出粗细面板、
// 完整的取色器（色板 / 色轮 / RGB 滑块）、拖动换边、丢进角落缩成一个圆、
// 撤销按钮、「更多」里的自动最小化与手指绘图开关。
//
// 没接的：不透明度（笔迹格式里还没有这个字段）、重做（白板没有重做）、
// 直尺和套索（白板没有这两个功能，对应的按钮已经从布局里去掉）。

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
    H: 448,
    items: {
      more: { x: 36, y: 392, w: 31, h: 31, r: 0 },
      eraser: { x: 38, y: 212, w: 30, h: 105, r: 90 },
      pencil: { x: 38, y: 160, w: 30, h: 105, r: 90 },
      marker: { x: 38, y: 107, w: 30, h: 105, r: 90 },
      pen: { x: 38, y: 54, w: 30, h: 105, r: 90 },
      grip: { x: 80, y: 222, w: 36, h: 5, r: 90 },
      redo: { x: 58, y: 41, w: 31, h: 31, r: 0 },
      undo: { x: 16, y: 41, w: 31, h: 31, r: 0 },
    },
    swatches: [
      { x: 58, y: 335, w: 30, h: 30, r: 0, c: "#53d669" },
      { x: 18, y: 335, w: 30, h: 30, r: 0, c: "custom" },
      { x: 58, y: 295, w: 30, h: 30, r: 0, c: "#157efa" },
      { x: 18, y: 295, w: 30, h: 30, r: 0, c: "#fc3142" },
      { x: 58, y: 254, w: 30, h: 30, r: 0, c: "#000000" },
      { x: 18, y: 254, w: 30, h: 30, r: 0, c: "#fed031" },
    ],
  },
  vr: {
    W: 105,
    H: 448,
    items: {
      more: { x: 36, y: 392, w: 31, h: 31, r: 0 },
      eraser: { x: 38, y: 212, w: 30, h: 105, r: 270 },
      pencil: { x: 38, y: 160, w: 30, h: 105, r: 270 },
      marker: { x: 38, y: 107, w: 30, h: 105, r: 270 },
      pen: { x: 38, y: 54, w: 30, h: 105, r: 270 },
      grip: { x: -10, y: 222, w: 36, h: 5, r: 90 },
      redo: { x: 58, y: 41, w: 31, h: 31, r: 0 },
      undo: { x: 16, y: 41, w: 31, h: 31, r: 0 },
    },
    swatches: [
      { x: 58, y: 335, w: 30, h: 30, r: 0, c: "#53d669" },
      { x: 18, y: 335, w: 30, h: 30, r: 0, c: "custom" },
      { x: 58, y: 295, w: 30, h: 30, r: 0, c: "#157efa" },
      { x: 18, y: 295, w: 30, h: 30, r: 0, c: "#fc3142" },
      { x: 58, y: 254, w: 30, h: 30, r: 0, c: "#000000" },
      { x: 18, y: 254, w: 30, h: 30, r: 0, c: "#fed031" },
    ],
  },
};

// 我们的工具 → 那份实现里的工具造型
export const TOOL_ART = { pen: "pen", marker: "pencil", highlighter: "marker", eraser: "eraser" };
const ART_TOOL = Object.fromEntries(Object.entries(TOOL_ART).map(([k, v]) => [v, k]));
const TITLES = { pen: "钢笔", marker: "马克笔", highlighter: "荧光笔", eraser: "橡皮擦" };

let loading = null;

/** 按需加载：这份 vendor 文件有 400 多 KB，不开 beta 就不要去解析它。 */
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
    }

    _bind() {
      super._bind();
      // 它也绑了 ⌘Z / Esc / Delete，会和白板自己的快捷键打架，摘掉这一条
      this._subs = (this._subs || []).filter(([target, type, fn, opts]) => {
        if (target === window && type === "keydown") {
          window.removeEventListener(type, fn, opts);
          return false;
        }
        return true;
      });
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
      return { x: (this.W - w) / 2, y: this.H - h - 20, w, h, r };
    }

    /** 「更多」菜单里补上白板自己的两项。 */
    _popHTML(kind) {
      const html = super._popHTML(kind);
      if (kind !== "more") return html;
      const rows =
        `<button type="button" class="pk-row" data-wb="clear"><span>清屏</span></button>` +
        `<button type="button" class="pk-row" data-wb="leave"><span>换回普通工具栏</span></button>`;
      return html.replace(/<\/div>$/, rows + "</div>");
    }

    /* ---------------- 和白板对接 ---------------- */

    /** 工具、颜色、粗细任何一项变了都会走到 _renderUI，就在这里同步出去。 */
    _renderUI(force) {
      // 撤销按钮的亮灭由白板的撤销栈决定：借它自己那套历史判定，图标才会跟着变
      this.hIndex = this._canUndo ? 1 : 0;
      this.history = this._canUndo ? [[], []] : [[]];
      super._renderUI(force);
      if (this.el.redo) this.el.redo.disabled = true; // 白板没有重做
      this._sync();
    }

    setUndoEnabled(on) {
      if (this._canUndo === !!on) return;
      this._canUndo = !!on;
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
      };
      const key = JSON.stringify(next);
      if (key === this._lastSync) return;
      this._lastSync = key;
      this.onChange(next);
    }

    /** 撤销交给白板；重做白板没有。 */
    undo() {
      if (this.onUndo) this.onUndo();
    }

    redo() {}

    /** 白板落笔时调用：开了「自动最小化」就把工具盘收起来。 */
    strokeStarted() {
      if (this.autoMin && this.state === "docked") this._minimize();
    }

    /** 白板那边改了工具 / 颜色（比如从缓存恢复），推回工具盘。 */
    applyState({ tool, color, sizeIndex, fingerDraws }) {
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
      this._renderUI(true);
    }
  };
  return Picker;
}

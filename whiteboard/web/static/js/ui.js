// 界面：Material 3 风格，只用图标，不放文案。
//
// iPad 端只有一条工具栏；Mac 端另外多出白板列表、白板设置（背景 / 存储目录 /
// 描述文件）、导出与缩放。
//
// 工具栏有两套：默认那条是一排图标，位置（上 / 下）由 toolpicker.js 的 ToolDock 管；
// 触摸设备上走 pkpicker.js 接进来的那条 PencilKit 工具盘，普通那条收起来。
// 触摸设备上没有换回普通工具栏的入口，颜色面板里那个「笔具盘」开关是留给
// 早先关掉过笔具盘的机器回来用的（选择记在本机）。

import { icon } from "./icons.js";
import { loadFingerDraw } from "./input.js";
import { loadPicker } from "./pkpicker.js";
import { inShell } from "./shell.js";
import { ToolDock } from "./toolpicker.js";
import { boardLabel, iconButton } from "./ui-common.js";
import { installBoards } from "./ui-boards.js";
import { installDialogs } from "./ui-dialogs.js";
import { installSettings } from "./ui-settings.js";
import { el, clamp } from "./util.js";

export const COLORS = [
  "#1b1b1f", "#5f6368", "#e53935", "#fb8c00", "#fdd835",
  "#43a047", "#00acc1", "#1e88e5", "#8e24aa", "#6d4c41",
];
export const WIDTHS = [1.5, 3, 5, 8, 13];
// 两种橡皮。对象：碰到哪一笔就整笔删掉。像素：把笔画从扫过的地方切开，留下两头。
// 笔迹始终是矢量的，「像素」指的是擦起来的手感，不是真去抹位图。
export const ERASER_MODES = [
  { key: "object", title: "对象橡皮擦" },
  { key: "pixel", title: "像素橡皮擦" },
];
// 橡皮的粗细不用手动调：对象橡皮擦一直是笔尖，像素橡皮擦跟着笔身角度走，
// 数值都在 input.js 的 ERASER_* 里。

const TOOL_KEY = "whiteboard.tool";

export const INK_TOOLS = ["pen", "marker", "highlighter"];
export const ALL_TOOLS = [...INK_TOOLS, "eraser"];
const TOOL_TITLES = { pen: "钢笔", marker: "马克笔", highlighter: "荧光笔", eraser: "橡皮擦" };

/** 每件工具各记一套颜色和粗细，换笔不会把上一支的设置带过去。 */
function defaultTool() {
  return {
    tool: "pen",
    pen: { color: COLORS[0], widthIndex: 1 },
    marker: { color: COLORS[2], widthIndex: 2 },
    highlighter: { color: COLORS[4], widthIndex: 3 },
    eraser: { color: COLORS[0], widthIndex: 0 },
    eraserMode: "object", // object：碰到哪一笔删哪一笔；pixel：把笔画切开
  };
}

const HEX = /^#[0-9a-fA-F]{6}$/;

function sanitizeEntry(raw, fallback) {
  const entry = { ...fallback };
  // 笔具盘的取色器能调出任意颜色，不限于面板上那十个
  if (raw && typeof raw.color === "string" && HEX.test(raw.color)) entry.color = raw.color;
  if (raw && raw.widthIndex >= 0 && raw.widthIndex < WIDTHS.length) {
    entry.widthIndex = raw.widthIndex;
  }
  return entry;
}

function loadTool() {
  const fallback = defaultTool();
  try {
    const saved = JSON.parse(localStorage.getItem(TOOL_KEY) || "null");
    if (!saved) return fallback;
    const state = { ...fallback };
    state.tool = ALL_TOOLS.includes(saved.tool) ? saved.tool : "pen";
    state.eraserMode = ERASER_MODES.some((m) => m.key === saved.eraserMode)
      ? saved.eraserMode
      : fallback.eraserMode;
    // 老版本只存了一套颜色 / 粗细，摊给每件工具，升级上来不会突然变样
    const flat = saved.color !== undefined || saved.widthIndex !== undefined ? saved : null;
    for (const key of ALL_TOOLS) state[key] = sanitizeEntry(saved[key] || flat, fallback[key]);
    return state;
  } catch (err) {
    return fallback;
  }
}

/**
 * 是不是 iPad。笔具盘（PencilKit 那条工具盘，见 pkpicker.js）只给 iPad：它照着
 * iPadOS 做、配 Apple Pencil 用。别的设备——Mac 窗口、电脑上的浏览器、安卓平板、
 * 带触屏的电脑——一律是普通工具栏。
 *
 * iPadOS 13 起 Safari 默认用桌面版 UA，报的是 Macintosh；Mac 本身没有触摸点，
 * 所以「Macintosh 且有多个触摸点」就是 iPad。外壳只装在 iPad 上。
 */
function isIPad() {
  if (inShell()) return true;
  const ua = navigator.userAgent || "";
  return /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

export class UI {
  constructor({ role, native, perms, actions }) {
    this.role = role;
    this.native = native;
    this.perms = perms || new Set();
    this.actions = actions;
    this.root = document.getElementById("ui");
    this.tool = loadTool();
    this.meta = null;
    this.boards = [];
    this.folders = [];
    this.openFolder = ""; // 空串表示停在最外面那一层
    this.boardQuery = "";
    this.info = null;
    this.popover = null;
    this.sheet = null;
    // 默认只认 Apple Pencil，手指负责平移缩放；没有 Pencil 的人在设置里打开手指书写。
    this.touchDevice = this.role === "ipad" || navigator.maxTouchPoints > 1;
    this.picker = isIPad();
    this.undoEnabled = false;
    this.redoEnabled = false;
    this.build();
  }

  // ------------------------------------------------------------- 构建

  build() {
    this.status = el("div", {
      id: "status",
      class: "offline",
      title: "连接状态（连点三下显示诊断）",
      onclick: () => this.countStatusTaps(),
    });
    this.root.append(this.status);

    this.fingerDraw = loadFingerDraw();

    this.toolbar = el("div", { id: "toolbar", class: "pill" });
    this.root.append(this.toolbar);
    this.dock = new ToolDock({ bar: this.toolbar });
    this.fillToolbar();

    this.buildCorner();
    this.watchOverlays();
  }

  /**
   * 白板选择界面或者设置抽屉开着的时候，把笔具盘藏起来。
   *
   * 笔具盘自带 z-index 并且每帧都在画自己那层，盖在别的界面上就是一直闪。层级
   * 已经排过（见 app.css），这里再断一刀：这两个界面开着的时候本来也不写字。
   * 对话框不算——它是半透明的一小块，底下该看见什么就看见什么。
   */
  watchOverlays() {
    const sync = () => {
      const open = !!this.root.querySelector(".gallery, .sheet");
      this.root.dataset.overlay = open ? "1" : "";
    };
    new MutationObserver(sync).observe(this.root, { childList: true, subtree: true });
    sync();
  }

  /**
   * 右上角那一组：管理白板、白板设置、导出。
   *
   * 两端都有，样式不同。Mac 上还多「连接 iPad」和「关于」两项——它们要选文件、
   * 看版本、查更新，只有本机进程里才有意义，所以跟着 native 走，不进权限体系。
   * 其余每一项都对应一项权限：本机全有，别的设备看 Mac 上放开了哪几项。
   *
   * iPad 上这一组是必须的：开了笔具盘之后普通工具栏整条藏起来，这三项就没有
   * 别的入口了（清空在笔具盘的「更多」里，连接状态点在左上角，都不受影响）。
   */
  buildCorner() {
    const buttons = [];
    if (this.may("manage")) buttons.push(iconButton("boards", "白板", () => this.openBoards()));
    // 外壳里这一项还管着「换一台 Mac」，所以没有设置权限也要给
    if (this.may("settings") || this.native || inShell()) {
      buttons.push(iconButton("settings", "白板设置", () => this.openSettings()));
    }
    if (this.may("export")) {
      buttons.push((this.exportButton = iconButton("image", "导出 PNG", () => this.actions.onExport())));
    }
    if (this.native) {
      buttons.push(iconButton("tablet", "连接 iPad", () => this.openConnect()));
      buttons.push(iconButton("info", "关于", () => this.openAbout()));
    }
    if (buttons.length) {
      // 触摸设备上做成 iOS 那种蓝色图标控件：同一条磨砂底，图标用强调色
      const cls = this.touchDevice ? "pill tinted" : "pill";
      this.corner = el("div", { id: "topright", class: cls }, buttons);
      this.root.append(this.corner);
    }
    if (this.role !== "mac") return;
    this.root.append(
      el("div", { id: "zoombar", class: "pill" }, [
        iconButton("zoomIn", "放大", () => this.actions.onZoom(1.25)),
        iconButton("fit", "回到内容", () => this.actions.onFit()),
        iconButton("zoomOut", "缩小", () => this.actions.onZoom(0.8)),
      ]),
    );
  }

  may(permission) {
    return this.perms.has(permission);
  }

  /** 填工具栏。开了笔具盘就把这条藏起来，交给 pkpicker.js 那条。 */
  fillToolbar() {
    this.closePopover();
    const bar = this.toolbar;
    bar.innerHTML = "";
    this.toolButtons = {};
    this.colorButton = null;
    this.colorDot = null;
    this.fillClassicBar(bar);
    this.dock.apply(this.dock.dock, false);
    this.syncDockButton();
    this.setUndoEnabled(this.undoEnabled);
    this.setRedoEnabled(this.redoEnabled);
    this.selectTool(this.tool.tool);
    if (this.picker) this.mountPicker();
  }

  /** 原来那条：一排图标 + 单独的颜色按钮。 */
  fillClassicBar(bar) {
    for (const key of ALL_TOOLS) {
      const button = iconButton(key, TOOL_TITLES[key], () => this.selectTool(key));
      this.toolButtons[key] = button;
      bar.append(button);
    }
    bar.append(el("div", { class: "sep" }));
    this.appendCommonButtons(bar);

    this.colorButton = el("button", {
      class: "icon-btn",
      title: "颜色与粗细",
      onclick: (event) => this.togglePalette(event.currentTarget),
    });
    this.colorDot = el("i", { class: "color-dot", style: { background: this.ink.color } });
    this.colorButton.append(this.colorDot);
    bar.append(this.colorButton, el("div", { class: "sep" }));
    this.appendEditButtons(bar);
  }

  /** 两条都有的：手指书写开关和位置按钮。 */
  appendCommonButtons(bar) {
    if (this.touchDevice) {
      this.fingerButton = iconButton("hand", "手指书写", () => this.toggleFingerDraw());
      this.fingerButton.classList.toggle("active", this.fingerDraw);
      bar.append(this.fingerButton);
    }
    // 手拿着 iPad 写字时底部这条够不着，让它能挪到上边去。
    this.dockButton = iconButton("dockTop", "工具栏换个位置", () => this.toggleDock());
    bar.append(this.dockButton, el("div", { class: "sep" }));
  }

  appendEditButtons(bar) {
    this.undoButton = iconButton("undo", "撤销", () => this.actions.onUndo());
    this.redoButton = iconButton("redo", "重做", () => this.actions.onRedo());
    bar.append(this.undoButton, this.redoButton);
    if (this.may("clear")) {
      bar.append(iconButton("trash", "清空白板", () => this.confirmClear(), "danger"));
    }
  }

  /* ---------------------------------------------------- 笔具盘 */

  /** 挂上 PencilKit 那条工具盘：原来那条藏起来，输入照旧走白板的画布。 */
  async mountPicker() {
    if (this.pk || this.pkLoading) return;
    this.pkLoading = true;
    try {
      const Picker = await loadPicker();
      if (!this.picker) return; // 还没装好就被关掉了
      this.pkHost = el("div", { id: "pk-host" });
      this.root.append(this.pkHost);
      const pk = new Picker(this.pkHost, { theme: "auto" });
      pk.onChange = (state) => this.onPickerChange(state);
      pk.onUndo = () => this.actions.onUndo();
      pk.onRedo = () => this.actions.onRedo();
      pk.allowClear = this.may("clear");
      pk.onClear = () => this.confirmClear();
      pk.applyState({
        tool: this.tool.tool,
        color: this.ink.color,
        sizeIndex: this.ink.widthIndex,
        fingerDraws: this.fingerDraw,
        eraserMode: this.tool.eraserMode,
      });
      this.pk = pk;
      pk.setHistory(this.undoEnabled, this.redoEnabled);
      this.toolbar.classList.add("hidden");
      this.watchPickerClash();
    } catch (err) {
      this.picker = false;
      this.message("笔具盘没能载入", "close", 5000);
    } finally {
      this.pkLoading = false;
    }
  }

  /**
   * 笔具盘挪到右上角那一组底下时，让那一组先躲开。
   *
   * 两条都是浮在画布上的控件，叠在一起既对不齐也不像一套东西。笔具盘的位置由
   * 用户拖着定，能停在四条边和四个角，所以只能按它当下的位置决定躲不躲：真的
   * 压上来才淡出，挪开就回来。
   *
   * 位置是写在 .pk-picker 的行内 style 上的（left / top / width / height），
   * 动画交给 CSS 过渡，所以：改 style 时量一次（拖动过程中每帧都改），过渡结束
   * 再量一次（飞过去、展开这些是松手之后才走完的），窗口尺寸变了也量一次。
   */
  watchPickerClash() {
    if (!this.corner || !this.pkHost || this.clashStop) return;
    const picker = this.pkHost.querySelector(".pk-picker");
    if (!picker) return;
    const check = () => this.syncPickerClash(picker);
    const observer = new MutationObserver(check);
    observer.observe(picker, { attributes: true, attributeFilter: ["style", "data-state"] });
    picker.addEventListener("transitionend", check);
    addEventListener("resize", check);
    this.clashStop = () => {
      observer.disconnect();
      picker.removeEventListener("transitionend", check);
      removeEventListener("resize", check);
      this.corner.classList.remove("shy");
      this.clashStop = null;
    };
    check();
  }

  /** 两个矩形挨上了就把右上角那一组让出去。留一点余量，贴着边也算挨上。 */
  syncPickerClash(picker) {
    if (!this.corner) return;
    const gap = 8;
    const a = picker.getBoundingClientRect();
    const b = this.corner.getBoundingClientRect();
    const hit =
      a.left < b.right + gap && a.right > b.left - gap && a.top < b.bottom + gap && a.bottom > b.top - gap;
    this.corner.classList.toggle("shy", hit);
  }

  /** 工具盘那边改了工具 / 颜色 / 粗细 / 手指书写，同步到白板。 */
  onPickerChange(state) {
    if (state.fingerDraws !== this.fingerDraw) {
      this.fingerDraw = state.fingerDraws;
      this.actions.onFingerDraw(this.fingerDraw);
    }
    const entry = this.tool[state.tool];
    if (entry) {
      entry.color = state.color;
      entry.widthIndex = Math.max(0, Math.min(WIDTHS.length - 1, state.sizeIndex));
    }
    if (state.eraserMode && state.eraserMode !== this.tool.eraserMode) {
      this.tool.eraserMode = state.eraserMode;
      this.rememberTool();
    }
    this.selectTool(state.tool);
  }

  /** 落笔时通知工具盘（开了「自动最小化」就收起来）。 */
  strokeStarted() {
    if (this.pk) this.pk.strokeStarted();
  }

  syncDockButton() {
    if (!this.dockButton) return;
    this.dockButton.innerHTML = icon(this.dock.dock === "top" ? "dockBottom" : "dockTop");
  }

  toggleDock() {
    this.closePopover();
    this.dock.toggle();
    this.syncDockButton();
  }

  /** 连点三下状态圆点：在真机上打开 / 关掉诊断面板。 */
  countStatusTaps() {
    const now = Date.now();
    if (now - (this._tapAt || 0) > 1200) this._taps = 0;
    this._tapAt = now;
    this._taps = (this._taps || 0) + 1;
    if (this._taps >= 3) {
      this._taps = 0;
      this.actions.onToggleDebug();
    }
  }

  // ------------------------------------------------------------- 工具

  /** 当前这支笔自己的颜色与粗细。 */
  get ink() {
    return this.tool[this.tool.tool] || this.tool.pen;
  }

  selectTool(tool) {
    this.tool.tool = ALL_TOOLS.includes(tool) ? tool : "pen";
    this.rememberTool();
    for (const [key, button] of Object.entries(this.toolButtons)) {
      const on = key === this.tool.tool;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", String(on));
    }
    if (this.colorDot) this.colorDot.style.background = this.ink.color;
    this.actions.onToolChange(this.toolState());
  }

  /**
   * 改颜色 / 粗细。笔具盘模式下只改当前这支（和 iPad 上一样，每支笔各记各的），
   * 普通工具栏仍然是一改全改，升级上来的人手感不变。
   */
  setInk(patch) {
    const targets = this.picker ? [this.tool.tool] : ALL_TOOLS;
    for (const key of targets) Object.assign(this.tool[key], patch);
    this.rememberTool();
    if (this.colorDot) this.colorDot.style.background = this.ink.color;
    this.actions.onToolChange(this.toolState());
  }

  toggleFingerDraw() {
    this.fingerDraw = !this.fingerDraw;
    this.fingerButton.classList.toggle("active", this.fingerDraw);
    this.actions.onFingerDraw(this.fingerDraw);
    this.toast(this.fingerDraw ? "hand" : "pen");
  }

  rememberTool() {
    try {
      localStorage.setItem(TOOL_KEY, JSON.stringify(this.tool));
    } catch (err) {
      /* 存不下就每次从默认值开始 */
    }
  }

  toolState() {
    const ink = this.ink;
    return {
      tool: this.tool.tool,
      color: ink.color,
      width: WIDTHS[ink.widthIndex],
      eraserMode: this.tool.eraserMode,
    };
  }

  setUndoEnabled(enabled) {
    this.undoEnabled = !!enabled;
    if (this.undoButton) this.undoButton.toggleAttribute("disabled", !enabled);
    if (this.pk) this.pk.setHistory(this.undoEnabled, this.redoEnabled);
  }

  setRedoEnabled(enabled) {
    this.redoEnabled = !!enabled;
    if (this.redoButton) this.redoButton.toggleAttribute("disabled", !enabled);
    if (this.pk) this.pk.setHistory(this.undoEnabled, this.redoEnabled);
  }

  // ------------------------------------------------------------- 弹层

  closePopover() {
    if (this.popover) {
      this.popover.remove();
      this.popover = null;
      removeEventListener("pointerdown", this._popoverCloser, true);
    }
  }

  showPopover(anchor, content) {
    this.closePopover();
    const popover = el("div", { class: "popover" }, content);
    this.root.append(popover);
    const rect = anchor.getBoundingClientRect();
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    popover.style.left = `${clamp(rect.left + rect.width / 2 - width / 2, 12, innerWidth - width - 12)}px`;
    // 工具栏在上边时锚点上方没地方，翻到下面开
    const above = rect.top - height - 12;
    const below = rect.bottom + 12;
    popover.style.top =
      above >= 12 ? `${above}px` : `${clamp(below, 12, Math.max(12, innerHeight - height - 12))}px`;
    this.popover = popover;
    this._popoverCloser = (event) => {
      if (!popover.contains(event.target) && !anchor.contains(event.target)) this.closePopover();
    };
    setTimeout(() => addEventListener("pointerdown", this._popoverCloser, true), 0);
    return popover;
  }

  /** 颜色格子；橡皮擦没有颜色，调用方自己决定放不放。 */
  colorSwatches() {
    const swatches = el("div", { class: "swatches" });
    for (const color of COLORS) {
      const button = el("button", {
        class: `swatch${color === this.ink.color ? " active" : ""}`,
        style: { background: color },
        title: color,
        onclick: () => {
          this.setInk({ color });
          for (const node of swatches.children) node.classList.remove("active");
          button.classList.add("active");
        },
      });
      swatches.append(button);
    }
    return swatches;
  }

  /** 粗细。橡皮没有这一档：它的粗细不用手动调，见 input.js 的 eraserRadius。 */
  widthOptions() {
    const widths = el("div", { class: "widths" });
    WIDTHS.forEach((width, index) => {
      const size = 4 + index * 4;
      const button = el(
        "button",
        {
          class: `width-opt${index === this.ink.widthIndex ? " active" : ""}`,
          title: `${width}`,
          onclick: () => {
            this.setInk({ widthIndex: index });
            for (const node of widths.children) node.classList.remove("active");
            button.classList.add("active");
          },
        },
        [el("i", { style: { width: `${size}px`, height: `${size}px` } })]
      );
      widths.append(button);
    });
    return widths;
  }

  /** 橡皮的两种模式，只在选中橡皮时出现在同一个面板里。 */
  eraserModes() {
    const row = el("div", { class: "seg", role: "radiogroup", "aria-label": "橡皮擦类型" });
    for (const mode of ERASER_MODES) {
      const button = el("button", {
        class: this.tool.eraserMode === mode.key ? "is-on" : "",
        role: "radio",
        "aria-checked": String(this.tool.eraserMode === mode.key),
        text: mode.title,
        onclick: () => {
          this.tool.eraserMode = mode.key;
          this.rememberTool();
          for (const node of row.children) {
            node.classList.toggle("is-on", node === button);
            node.setAttribute("aria-checked", String(node === button));
          }
          this.actions.onToolChange(this.toolState());
          if (this.pk) this.pk.setEraserMode(mode.key);
        },
      });
      row.append(button);
    }
    return row;
  }

  togglePalette(anchor) {
    if (this.popover) {
      this.closePopover();
      return;
    }
    const parts = [];
    if (this.tool.tool === "eraser") {
      parts.push(this.eraserModes());
    } else {
      parts.push(this.colorSwatches(), this.widthOptions());
    }
    this.showPopover(anchor, parts);
  }

  toast(iconName) {
    const node = el("div", { class: "toast", html: icon(iconName) });
    this.root.append(node);
    setTimeout(() => node.remove(), 1600);
  }

  /** 一条可点掉的提示，用于页面无能为力、只能让用户去改系统设置的情况。 */
  /** 普通消息条：和 showNotice 不同，每次都会显示。 */
  message(text, iconName = "info", ms = 4000) {
    const node = el("div", { class: "notice", html: icon(iconName) });
    node.append(el("span", { text }));
    node.addEventListener("click", () => node.remove());
    this.root.append(node);
    const timer = setTimeout(() => node.remove(), ms);
    return () => {
      clearTimeout(timer);
      node.remove();
    };
  }

  showNotice(key, text, iconName = "pen") {
    const stamp = `whiteboard.notice.${key}`;
    try {
      if (Date.now() - Number(localStorage.getItem(stamp) || 0) < 24 * 3600 * 1000) return;
      localStorage.setItem(stamp, String(Date.now()));
    } catch (err) {
      /* 记不住就每次都提示 */
    }
    const node = el("div", { class: "notice", html: icon(iconName) });
    node.append(el("span", { text }));
    node.addEventListener("click", () => node.remove());
    this.root.append(node);
    setTimeout(() => node.remove(), 15000);
  }

  // ------------------------------------------------------------- 侧板

  closeSheet() {
    if (this.sheet) {
      this.sheet.remove();
      this.sheet = null;
    }
  }

  openSheet(groups) {
    this.closeSheet();
    const scrim = el("div", { class: "scrim", onclick: () => this.closeSheet() });
    const sheet = el("div", { class: "sheet" }, [
      el("div", { class: "sheet-head" }, [iconButton("close", "关闭", () => this.closeSheet())]),
      ...groups,
    ]);
    sheet.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(sheet);
    this.root.append(scrim);
    this.sheet = scrim;
    return sheet;
  }


  // ------------------------------------------------------------- 状态

  setStatus(status) {
    this.status.className = status;
  }

  setMeta(meta) {
    this.meta = meta;
    if (this.exportButton) {
      const isDoc = meta && meta.kind === "doc";
      this.exportButton.title = isDoc ? "导出（笔迹合进原件）" : "导出 PNG";
      this.exportButton.innerHTML = icon(isDoc ? "download" : "image");
    }
    if (this.sheet && this.sheet.dataset.kind === "settings") this.openSettings();
  }

  /**
   * 合并而不是整个换掉。
   *
   * 这份信息有两个来源：服务端在 init / sync / switch 里给的（主机名、端口、
   * 地址、存储目录、版本号），以及 pywebview 那条只有 Mac 窗口才有的本地接口
   * （是不是打包版、日志路径、别的设备的权限）。整个换掉的话，切一次白板就会
   * 把本地接口那几项冲掉，「关于」里的版本号也就没了。
   */
  setInfo(info) {
    this.info = { ...(this.info || {}), ...(info || {}) };
  }
}

// 白板选择界面、对话框、设置抽屉各放一个文件，方法装回 UI 上。
installBoards(UI.prototype);
installDialogs(UI.prototype);
installSettings(UI.prototype);

export { boardLabel };

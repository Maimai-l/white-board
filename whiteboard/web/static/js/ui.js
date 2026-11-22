// 界面：照 macOS / iPadOS 的系统控件来——磨砂玻璃、系统色、细分隔线。
//
// 两套外框，按有没有触摸屏分：
//
// - Mac（以及电脑上的浏览器）：顶上一条磨砂标题栏，左边是白板库和白板名字，右边
//   是连接 iPad、导出、白板设置；底部正中一条工具栏（笔、颜色、撤销、更多），
//   右下角是缩放。
// - iPad（以及别的触摸设备）：左上角一个返回白板库的按钮，右上角一组连接状态、
//   导出、白板设置；工具栏是 pkpicker.js 接进来的那条 PencilKit 笔具盘（只给 iPad，
//   见 isIPad），别的触摸设备用普通工具栏。
//
// 白板库、对话框、设置面板各放一个文件，方法装回 UI 上（见文件末尾）。

import { icon } from "./icons.js";
import { loadFingerDraw, loadShapeLines } from "./boards.js";
import { loadPicker } from "./pkpicker.js";
import { inShell } from "/inksync/shell.js";
import { boardLabel, iconButton } from "./ui-common.js";
import { installBoards } from "./ui-boards.js";
import { installDialogs } from "./ui-dialogs.js";
import { installSettings } from "./ui-settings.js";
import { closeMenu, dropChromeFocus, showMenu } from "./ui-menu.js";
import { leave } from "./motion.js";
import { el, clamp } from "/inksync/util.js";
import { reportError } from "/inksync/report.js";

// 系统色：黑、灰、蓝、红、绿、橙、黄、紫。笔具盘的取色器能调出任意颜色，存下来的不限于这几个。
export const COLORS = [
  "#1c1c1e", "#8e8e93", "#007aff", "#ff3b30", "#34c759", "#ff9500", "#ffcc00", "#af52de",
];
// 五档粗细，和笔具盘的五档一一对应（每支笔记的是档位，见 sanitizeEntry）
export const WIDTHS = [1.5, 3, 5, 8, 13];
const WIDTH_TITLES = ["极细", "细", "中", "粗", "极粗"];
// 两种橡皮。对象：碰到哪一笔就整笔删掉。像素：把笔画从扫过的地方切开，留下两头。
// 笔迹始终是矢量的，「像素」指的是擦起来的手感，不是真去抹位图。
export const ERASER_MODES = [
  { key: "object", title: "对象橡皮擦" },
  { key: "pixel", title: "像素橡皮擦" },
];
// 橡皮的粗细不用手动调：对象橡皮擦一直是笔尖，像素橡皮擦跟着笔身角度走，
// 数值都在 input-erase.js 的 ERASER_* 里。

const TOOL_KEY = "whiteboard.tool";

export const INK_TOOLS = ["pen", "marker", "highlighter"];
export const ALL_TOOLS = [...INK_TOOLS, "eraser"];
const TOOL_TITLES = { pen: "钢笔", marker: "马克笔", highlighter: "荧光笔", eraser: "橡皮擦" };

/** 每件工具各记一套颜色和粗细，换笔不会把上一支的设置带过去。 */
function defaultTool() {
  return {
    tool: "pen",
    pen: { color: COLORS[0], widthIndex: 1 },
    marker: { color: COLORS[3], widthIndex: 2 },
    highlighter: { color: COLORS[6], widthIndex: 3 },
    eraser: { color: COLORS[0], widthIndex: 0 },
    eraserMode: "object", // object：碰到哪一笔删哪一笔；pixel：把笔画切开
  };
}

const HEX = /^#[0-9a-fA-F]{6}$/;

function sanitizeEntry(raw, fallback) {
  const entry = { ...fallback };
  // 笔具盘的取色器能调出任意颜色，不限于面板上那几个
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
    // 忽略：读不了 localStorage 或存的东西坏了，就从默认设置开始
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

/** 消息条里的文字：一句是一个 span，两句时每句一行。 */
function noticeText(text) {
  if (!Array.isArray(text)) return el("span", { text });
  return el("span", { class: "notice-lines" }, text.map((line) => el("span", { text: line })));
}


/** 连点三下：相邻两下之间最多隔这么久（毫秒）。 */
const TRIPLE_TAP_MS = 300;
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
    this.boardQuery = "";
    this.info = null;
    this.popover = null;
    this.sheet = null;
    this.panel = null;
    this.devices = [];
    // 默认只认 Apple Pencil，手指负责平移缩放；没有 Pencil 的人在设置里打开手指书写。
    this.touchDevice = this.role === "ipad" || navigator.maxTouchPoints > 1;
    this.picker = isIPad();
    this.undoEnabled = false;
    this.redoEnabled = false;
    this.netStatus = "offline";
    this.build();
  }

  // ------------------------------------------------------------- 构建

  build() {
    document.documentElement.dataset.chrome = this.touchDevice ? "touch" : "bar";
    // Mac 窗口没有系统标题栏，红绿灯在顶栏左边（app.py 在地址里带上 window=inset，
    // whiteboard/titlebar.py）。顶栏的空白处能拖动窗口，双击和系统标题栏一样放大
    if (new URLSearchParams(location.search).get("window") === "inset" && !this.touchDevice) {
      document.documentElement.dataset.window = "inset";
      document.addEventListener("dblclick", (event) => {
        if (event.target instanceof Element && event.target.matches(".pywebview-drag-region")) {
          this.actions.onZoomWindow();
        }
      });
    }
    this.fingerDraw = loadFingerDraw();
    this.shapeLines = loadShapeLines();

    this.toolbar = el("div", { id: "toolbar", class: "pill", role: "toolbar", "aria-label": "绘图工具" });
    this.root.append(this.toolbar);
    // 工具栏固定在底部（Mac 自带应用都是这样）。老版本存过的位置不再用，清掉
    try {
      localStorage.removeItem("whiteboard.toolbar");
    } catch (err) {
      console.warn("ui.js build：清不掉老的工具栏位置，不影响使用", err);
    }
    this.fillToolbar();

    this.buildChrome();
    this.watchOverlays();
  }

  /**
   * 白板库或者设置面板开着的时候，把笔具盘藏起来。
   *
   * 笔具盘自带 z-index 并且每帧都在画自己那层，盖在别的界面上就是一直闪。层级
   * 已经排过（见 app.css），这里再断一刀：这两个界面开着的时候本来也不写字。
   * 对话框不算——它是半透明的一小块，底下该看见什么就看见什么。
   */
  watchOverlays() {
    const sync = () => {
      // 正在淡出的白板库不算：笔具盘在它底下先露出来，跟着它一起淡入
      const open = !!this.root.querySelector(".gallery:not(.leaving), .sheet:not(.leaving)");
      this.root.dataset.overlay = open ? "1" : "";
    };
    new MutationObserver(sync).observe(this.root, { childList: true, subtree: true, attributeFilter: ["class"] });
    sync();
  }

  /**
   * 工具栏以外的外框：Mac 上是顶栏和缩放，iPad 上是左右上角两组按钮。
   *
   * 每个入口对应一项权限：本机全有，别的设备看 Mac 上放开了哪几项。「连接 iPad」
   * 要选文件、看本机地址，只有本机进程里才有意义，所以跟着 native 走，不进权限体系。
   * 外壳里设置还管着「换一台 Mac」，所以没有设置权限也要给。
   */
  buildChrome() {
    for (const node of this.root.querySelectorAll("#topbar, #topleft, #topright, #zoombar, #status")) node.remove();
    this.corner = null;
    if (this.touchDevice) this.buildPills();
    else this.buildBar();
    this.syncTitle();
    this.setStatus(this.netStatus);
    this.syncDevices();
  }

  /** Mac：顶上一条磨砂标题栏，右下角缩放。 */
  buildBar() {
    const bar = el("header", { id: "topbar", class: "pywebview-drag-region" });
    if (this.may("manage")) bar.append(iconButton("boards", "白板库", () => this.openBoards(), "bar-lib"));
    this.titleName = el("span", { class: "topbar-name" });
    this.titleFolder = el("span", { class: "topbar-folder" });
    bar.append(el("div", { class: "topbar-title" }, [this.titleName, this.titleFolder]));
    bar.append(el("div", { class: "topbar-spacer" }));

    const buttons = [];
    if (this.native) {
      // 连接 iPad：图标是蓝色说明有 iPad 连着；连点三下打开诊断面板
      this.status = iconButton("connect", "连接 iPad", (event) => {
        this.countStatusTaps();
        this.togglePanel("connect", event.currentTarget);
      });
      this.status.id = "status";
      buttons.push(this.status);
    } else {
      this.status = el("span", { id: "status", title: "连接状态", onclick: () => this.countStatusTaps() });
      bar.querySelector(".topbar-title").append(this.status);
    }
    if (this.may("export")) {
      buttons.push((this.exportButton = iconButton("download", "导出", () => this.actions.onExport())));
    }
    if (this.may("settings") || this.native) {
      buttons.push(iconButton("settings", "白板设置", (event) => this.togglePanel("settings", event.currentTarget)));
    }
    this.corner = el("div", { id: "topright" }, buttons);
    bar.append(this.corner);
    this.root.append(bar);

    this.zoomLabel = el("button", {
      class: "zoom-level",
      title: "实际大小",
      "aria-label": "实际大小",
      text: "100%",
      onclick: () => this.actions.onZoomReset(),
    });
    this.root.append(
      el("div", { id: "zoombar", class: "pill", role: "group", "aria-label": "缩放" }, [
        iconButton("minus", "缩小", () => this.actions.onZoom(0.8)),
        this.zoomLabel,
        iconButton("add", "放大", () => this.actions.onZoom(1.25)),
        el("div", { class: "sep" }),
        iconButton("fit", "回到内容", () => this.actions.onFit()),
      ])
    );
  }

  /** iPad：左上角回白板库，右上角连接状态、导出、设置。 */
  buildPills() {
    if (this.may("manage")) {
      this.root.append(
        el("div", { id: "topleft", class: "pill tinted" }, [iconButton("back", "白板库", () => this.openBoards())])
      );
    }
    this.status = iconButton("connect", "连接状态", (event) => {
      this.countStatusTaps();
      this.togglePanel("status", event.currentTarget);
    });
    this.status.id = "status";
    const buttons = [this.status];
    if (this.may("export")) {
      buttons.push((this.exportButton = iconButton("download", "导出", () => this.actions.onExport())));
    }
    if (this.may("settings")) {
      buttons.push(iconButton("settings", "白板设置", (event) => this.togglePanel("settings", event.currentTarget)));
    }
    this.corner = el("div", { id: "topright", class: "pill tinted" }, buttons);
    this.root.append(this.corner);
    if (this.pkHost) {
      if (this.clashStop) this.clashStop();
      this.watchPickerClash();
    }
  }

  /** 顶栏里的白板名字和所在的文件夹。 */
  syncTitle() {
    if (!this.titleName) return;
    const meta = this.meta;
    this.titleName.textContent = meta ? boardLabel(meta) : "";
    this.titleFolder.textContent = (meta && meta.folder) || "";
    this.titleFolder.hidden = !(meta && meta.folder);
  }

  /** 缩放比例：缩放按钮中间那个数。 */
  setZoom(scale) {
    if (!this.zoomLabel) return;
    const text = `${Math.round(scale * 100)}%`;
    if (this.zoomLabel.textContent !== text) this.zoomLabel.textContent = text;
  }

  may(permission) {
    return this.perms.has(permission);
  }

  /**
   * Mac 上改了这台设备的权限：外框、更多菜单和笔具盘里的清空跟着变，失去权限时
   * 关掉对应的界面。真正的拦截在服务端，这里只是不再显示入口。
   */
  setPerms(perms) {
    this.perms = perms;
    this.closePanel();
    closeMenu();
    this.buildChrome();
    if (this.pk) this.pk.allowClear = this.may("clear");
    if (!this.may("manage")) this.closeGallery();
    if (!this.may("export") && this.exportDialog) this.exportDialog.close();
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
    this.setUndoEnabled(this.undoEnabled);
    this.setRedoEnabled(this.redoEnabled);
    this.selectTool(this.tool.tool);
    if (this.picker) this.mountPicker();
  }

  /** 普通工具栏：四件工具 | 颜色与粗细 | 撤销、重做 | 更多。 */
  fillClassicBar(bar) {
    for (const key of ALL_TOOLS) {
      const button = iconButton(key, TOOL_TITLES[key], () => this.selectTool(key));
      this.toolButtons[key] = button;
      bar.append(button);
    }
    bar.append(el("div", { class: "sep" }));
    this.colorButton = el("button", {
      class: "icon-btn",
      title: "颜色与粗细",
      "aria-label": "颜色与粗细",
      onclick: (event) => this.togglePalette(event.currentTarget),
    });
    this.colorDot = el("i", { class: "color-dot", style: { background: this.ink.color } });
    this.colorButton.append(this.colorDot);
    bar.append(this.colorButton, el("div", { class: "sep" }));
    this.undoButton = iconButton("undo", "撤销", () => this.actions.onUndo());
    this.redoButton = iconButton("redo", "重做", () => this.actions.onRedo());
    bar.append(this.undoButton, this.redoButton, el("div", { class: "sep" }));
    this.moreButton = iconButton("more", "更多", (event) => this.moreMenu(event.currentTarget));
    bar.append(this.moreButton);
  }

  /**
   * 工具栏上的更多：回到内容、清空；触摸设备上多手指书写和形状识别两个开关。
   * 关于白板和检查更新在 Mac 菜单栏的应用菜单里（appmenu.py），不放在这里。
   */
  moreMenu(anchor) {
    const items = [];
    if (this.touchDevice) {
      items.push(
        { label: "手指书写", checked: this.fingerDraw, onSelect: () => this.toggleFingerDraw() },
        { label: "形状识别", checked: this.shapeLines, onSelect: () => this.toggleShapeLines() },
        "-"
      );
    }
    items.push({ label: "回到内容", onSelect: () => this.actions.onFit() });
    if (this.may("clear")) items.push("-", { label: "清空白板…", danger: true, onSelect: () => this.confirmClear() });
    this.closePopover();
    showMenu(items, anchor, { root: this.root, label: "更多", className: "from-toolbar" });
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
      // 尺子只在笔具盘里有，也就是只在 iPad 上有（见 isIPad）
      pk.onRuler = (shown) => this.actions.onRuler(shown);
      pk.applyState({
        tool: this.tool.tool,
        color: this.ink.color,
        sizeIndex: this.ink.widthIndex,
        fingerDraws: this.fingerDraw,
        shapeLines: this.shapeLines,
        eraserMode: this.tool.eraserMode,
      });
      this.pk = pk;
      pk.setHistory(this.undoEnabled, this.redoEnabled);
      this.toolbar.classList.add("hidden");
      this.watchPickerClash();
    } catch (err) {
      reportError("ui.mountPicker 笔具盘没能载入", { message: String(err) });
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

  /** 工具盘那边改了工具 / 颜色 / 粗细 / 手指书写 / 形状识别，同步到白板。 */
  onPickerChange(state) {
    if (state.fingerDraws !== this.fingerDraw) {
      this.fingerDraw = state.fingerDraws;
      this.actions.onFingerDraw(this.fingerDraw);
    }
    if (state.shapeLines !== this.shapeLines) {
      this.shapeLines = state.shapeLines;
      this.actions.onShapeLines(this.shapeLines);
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


  /**
   * 连点三下连接按钮：在真机上打开 / 关掉诊断面板。和系统的三连击一样，每两下之间
   * 不超过 TRIPLE_TAP_MS；以前放宽到 1.2 秒，开一下、关一下、再开一下就误弹出来。
   */
  countStatusTaps() {
    const now = Date.now();
    if (now - (this._tapAt || 0) > TRIPLE_TAP_MS) this._taps = 0;
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
    this.actions.onFingerDraw(this.fingerDraw);
    this.toast(this.fingerDraw ? "hand" : "pen");
  }

  /** 普通工具栏更多菜单里的形状识别开关。 */
  toggleShapeLines() {
    this.shapeLines = !this.shapeLines;
    this.actions.onShapeLines(this.shapeLines);
  }

  rememberTool() {
    try {
      localStorage.setItem(TOOL_KEY, JSON.stringify(this.tool));
    } catch (err) {
      /* 忽略：存不下就每次从默认值开始 */
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
      leave(this.popover);
      this.popover = null;
      removeEventListener("pointerdown", this._popoverCloser, true);
      removeEventListener("keydown", this._popoverKeys, true);
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
    // 锚点上方没地方，翻到下面开
    const above = rect.top - height - 12;
    const below = rect.bottom + 12;
    popover.style.top =
      above >= 12 ? `${above}px` : `${clamp(below, 12, Math.max(12, innerHeight - height - 12))}px`;
    // 进场动画从按钮那头长出来：开在按钮下面时从上沿
    if (above < 12) popover.style.transformOrigin = "top center";
    this.popover = popover;
    this._popoverCloser = (event) => {
      if (!popover.contains(event.target) && !anchor.contains(event.target)) this.closePopover();
    };
    // Esc 也关上，和别的面板一样
    this._popoverKeys = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      this.closePopover();
      dropChromeFocus();
    };
    // 在按钮的 click 里打开，按下已经过去了：马上开始听（见 ui-menu.js）
    addEventListener("pointerdown", this._popoverCloser, true);
    addEventListener("keydown", this._popoverKeys, true);
    return popover;
  }

  /** 颜色格子；橡皮擦没有颜色，调用方自己决定放不放。 */
  colorSwatches() {
    const swatches = el("div", { class: "swatches" });
    for (const color of COLORS) {
      const button = el("button", {
        class: `swatch${color === this.ink.color.toLowerCase() ? " active" : ""}`,
        style: { background: color },
        title: color,
        "aria-label": color,
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

  /** 粗细。橡皮没有这一档：它的粗细不用手动调，见 input-erase.js 的 eraserRadius。 */
  widthOptions() {
    const widths = el("div", { class: "widths" }, [el("span", { class: "widths-label", text: "粗细" })]);
    WIDTHS.forEach((width, index) => {
      // 一道横线，越粗越厚：和设计稿一样画成笔画的样子，不画圆点
      const size = [2, 3, 4, 6, 8][index];
      const button = el(
        "button",
        {
          class: `width-opt${index === this.ink.widthIndex ? " active" : ""}`,
          title: WIDTH_TITLES[index],
          "aria-label": WIDTH_TITLES[index],
          onclick: () => {
            this.setInk({ widthIndex: index });
            for (const node of widths.querySelectorAll(".width-opt")) node.classList.remove("active");
            button.classList.add("active");
          },
        },
        [el("i", { style: { height: `${size}px`, background: this.ink.color } })]
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
    setTimeout(() => leave(node), 1600);
  }

  /**
   * 普通消息条：和 showNotice 不同，每次都会显示。``text`` 可以是一句，也可以是
   * 两句的数组（各占一行，docs/ui-text.md）。
   */
  message(text, iconName = "info", ms = 4000) {
    const node = el("div", { class: "notice", html: icon(iconName) });
    node.append(noticeText(text));
    node.addEventListener("click", () => leave(node));
    this.noticeStack().append(node);
    let timer = setTimeout(() => leave(node), ms);
    const close = () => {
      clearTimeout(timer);
      leave(node);
    };
    /** 原地换成另一句（进行中的消息变成结果），重新计时；不另起一条。 */
    close.update = (next, nextIcon = iconName, nextMs = ms) => {
      if (!node.isConnected || node.classList.contains("leaving")) return this.message(next, nextIcon, nextMs);
      clearTimeout(timer);
      node.replaceChildren();
      node.insertAdjacentHTML("afterbegin", icon(nextIcon));
      node.append(noticeText(next));
      timer = setTimeout(() => leave(node), nextMs);
      return close;
    };
    return close;
  }

  /**
   * 消息条都放进同一个竖排的容器：同时有几条就往下排开，不互相盖住（Mac 上在顶栏下面靠右，
   * iPad 上居中）。
   */
  noticeStack() {
    if (!this._notices || !this._notices.isConnected) {
      this._notices = el("div", { id: "notices", role: "status", "aria-live": "polite" });
      this.root.append(this._notices);
    }
    return this._notices;
  }

  /** 一条可点掉的提示，用于页面无能为力、只能让用户去改系统设置的情况。一天只提示一次。 */
  showNotice(key, text, iconName = "pen") {
    const stamp = `whiteboard.notice.${key}`;
    try {
      if (Date.now() - Number(localStorage.getItem(stamp) || 0) < 24 * 3600 * 1000) return;
      localStorage.setItem(stamp, String(Date.now()));
    } catch (err) {
      /* 忽略：记不住就每次都提示 */
    }
    const node = el("div", { class: "notice", html: icon(iconName) });
    node.append(noticeText(text));
    node.addEventListener("click", () => leave(node));
    this.noticeStack().append(node);
    setTimeout(() => leave(node), 15000);
  }

  // ------------------------------------------------------------- 侧板

  closeSheet() {
    if (this.sheet) {
      leave(this.sheet);
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

  /** 和 Mac 的连接状态（online / connecting / offline），写在 #status 上给样式用。 */
  setStatus(status) {
    this.netStatus = status;
    if (!this.status) return;
    this.status.dataset.status = status;
    if (this.touchDevice) {
      const host = (this.info && this.info.hostname) || "Mac";
      const label = status === "online" ? `已连接到 ${host}` : status === "connecting" ? "正在连接…" : "未连接";
      this.status.title = label;
      this.status.setAttribute("aria-label", label);
    }
  }

  /** 连着这台 Mac 的别的设备（服务端的 devices 消息）。图标亮起来说明有 iPad 连着。 */
  setDevices(devices) {
    const before = new Set(this.devices.map((device) => device.id));
    const after = new Set((devices || []).map((device) => device.id));
    const joined = (devices || []).filter((device) => !before.has(device.id));
    const left = this.devices.filter((device) => !after.has(device.id));
    this.devices = devices || [];
    this.syncDevices();
    // 第一次拿到名单不算有人连上来
    if (this.devicesSeen) {
      for (const device of joined) this.message(`${device.label} 已连接`, "connect", 3000);
      for (const device of left) this.message(`${device.label} 已断开`, "connect", 3000);
    }
    this.devicesSeen = true;
    if (this.panel && this.panel.kind === "connect") this.refreshPanel();
  }

  syncDevices() {
    if (!this.status || this.touchDevice || !this.native) return;
    const count = this.devices.length;
    this.status.dataset.peers = count ? "1" : "";
    const label = count ? `${this.devices[0].label} 已连接` : "连接 iPad";
    this.status.title = label;
    this.status.setAttribute("aria-label", label);
  }

  setMeta(meta) {
    this.meta = meta;
    this.syncTitle();
    if (this.panel && this.panel.kind === "settings") this.refreshPanel();
  }

  /**
   * 合并而不是整个换掉。
   *
   * 这份信息有两个来源：服务端在 init / sync / switch 里给的（主机名、端口、
   * 地址、存储目录、版本号），以及 pywebview 那条只有 Mac 窗口才有的本地接口
   * （是不是打包版、日志路径、别的设备的权限）。整个换掉的话，切一次白板就会
   * 把本地接口那几项冲掉。
   */
  setInfo(info) {
    this.info = { ...(this.info || {}), ...(info || {}) };
    if (this.touchDevice) this.setStatus(this.netStatus);
  }
}

// 白板选择界面、对话框、设置抽屉各放一个文件，方法装回 UI 上。
installBoards(UI.prototype);
installDialogs(UI.prototype);
installSettings(UI.prototype);

export { boardLabel };

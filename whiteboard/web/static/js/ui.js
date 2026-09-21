// 界面：Material 3 风格，只用图标，不放文案。
//
// iPad 端只有一条工具栏；Mac 端另外多出白板列表、白板设置（背景 / 存储目录 /
// 描述文件）、导出与缩放。
//
// 工具栏有两套：默认那条是一排图标，位置（上 / 下）由 toolpicker.js 的 ToolDock 管；
// 勾上「笔具盘 beta」之后这条收起来，换成 pkpicker.js 接进来的 PencilKit 工具盘。

import { icon } from "./icons.js";
import { loadFingerDraw } from "./input.js";
import { renderNotes } from "./notes.js";
import { loadPicker } from "./pkpicker.js";
import { ToolDock } from "./toolpicker.js";
import { el, clamp } from "./util.js";

export const COLORS = [
  "#1b1b1f", "#5f6368", "#e53935", "#fb8c00", "#fdd835",
  "#43a047", "#00acc1", "#1e88e5", "#8e24aa", "#6d4c41",
];
export const WIDTHS = [1.5, 3, 5, 8, 13];
export const ERASER_SIZES = [16, 28, 44, 66, 96];

const TOOL_KEY = "whiteboard.tool";
const PICKER_KEY = "whiteboard.picker";

export const INK_TOOLS = ["pen", "marker", "highlighter"];
export const ALL_TOOLS = [...INK_TOOLS, "eraser"];
const TOOL_TITLES = { pen: "钢笔", marker: "马克笔", highlighter: "荧光笔", eraser: "橡皮擦" };
const KIND_NAMES = { board: "白板", note: "笔记", doc: "文档" };

// 可以放开给别的设备的权限，顺序就是设置面板里的顺序；键与 config.REMOTE_PERMISSIONS 一致。
const PERMISSIONS = [
  { key: "manage", title: "管理白板", note: "切换、新建或删除白板" },
  { key: "settings", title: "设置白板", note: "背景纹理" },
  { key: "export", title: "导出白板", note: "" }, // 没有必要写note, 而且UI上的文字需要仔细推敲的, 不是让你在上面写random prose的, 比如至少短语结构应该统一
];

/** 卡片上显示的名字。没起名就按延伸方式给个默认，文档板退回原件的文件名。 */
export function boardLabel(board) {
  if (board.name) return board.name;
  const doc = board.kind === "doc" ? board.doc : null;
  if (doc && doc.name) {
    const dot = doc.name.lastIndexOf(".");
    return dot > 0 ? doc.name.slice(0, dot) : doc.name;
  }
  return KIND_NAMES[board.kind] || KIND_NAMES.board;
}

/** 卡片下方那行时间：今天只给时刻，今年不给年份，其余给全。 */
function boardDate(seconds) {
  const when = new Date(seconds * 1000);
  const now = new Date();
  if (when.toDateString() === now.toDateString()) {
    return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = when.getFullYear() === now.getFullYear();
  return when.toLocaleDateString(
    [],
    sameYear ? { month: "numeric", day: "numeric" } : { year: "numeric", month: "numeric", day: "numeric" }
  );
}

/** 搜索匹配的范围：显示出来的名字，加上文档板的原件文件名。 */
function boardHaystack(board) {
  const doc = board.kind === "doc" && board.doc ? board.doc.name || "" : "";
  return `${boardLabel(board)} ${board.name || ""} ${doc}`.toLowerCase();
}

/** 每件工具各记一套颜色和粗细，换笔不会把上一支的设置带过去。 */
function defaultTool() {
  return {
    tool: "pen",
    pen: { color: COLORS[0], widthIndex: 1 },
    marker: { color: COLORS[2], widthIndex: 2 },
    highlighter: { color: COLORS[4], widthIndex: 3 },
    eraser: { color: COLORS[0], widthIndex: 1 },
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
    // 老版本只存了一套颜色 / 粗细，摊给每件工具，升级上来不会突然变样
    const flat = saved.color !== undefined || saved.widthIndex !== undefined ? saved : null;
    for (const key of ALL_TOOLS) state[key] = sanitizeEntry(saved[key] || flat, fallback[key]);
    return state;
  } catch (err) {
    return fallback;
  }
}

/** 笔具盘（beta）：iPad 上换成 PencilKit 那条工具盘，见 pkpicker.js。 */
function loadPickerFlag() {
  try {
    return localStorage.getItem(PICKER_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function iconButton(name, title, onClick, extraClass = "") {
  return el("button", {
    class: `icon-btn ${extraClass}`.trim(),
    title,
    "aria-label": title,
    html: icon(name),
    onclick: onClick,
  });
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
    this.boardQuery = "";
    this.info = null;
    this.popover = null;
    this.sheet = null;
    this.picker = loadPickerFlag();
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

    // 默认只认 Apple Pencil，手指负责平移缩放；没有 Pencil 的人在这里打开手指书写。
    this.touchDevice = this.role === "ipad" || navigator.maxTouchPoints > 1;
    this.fingerDraw = loadFingerDraw();

    this.toolbar = el("div", { id: "toolbar", class: "pill" });
    this.root.append(this.toolbar);
    this.dock = new ToolDock({ bar: this.toolbar });
    this.fillToolbar();

    if (this.role === "mac") {
      // 每个入口都对应一项权限：本机全有，别的设备看 Mac 上放开了哪几项。
      // 「连接 iPad」和「关于」只有本机进程里才有意义（选文件、看版本、查更新），
      // 所以跟着 native 走，不进权限体系。
      const buttons = [];
      if (this.may("manage")) buttons.push(iconButton("boards", "白板", () => this.openBoards()));
      if (this.may("settings") || this.native) {
        buttons.push(iconButton("settings", "白板设置", () => this.openSettings()));
      }
      if (this.may("export")) {
        buttons.push((this.exportButton = iconButton("image", "导出 PNG", () => this.actions.onExport())));
      }
      if (this.native) {
        buttons.push(iconButton("tablet", "连接 iPad", () => this.openConnect()));
        buttons.push(iconButton("info", "关于", () => this.openAbout()));
      }
      const topright = el("div", { id: "topright", class: "pill" }, buttons);
      const zoombar = el("div", { id: "zoombar", class: "pill" }, [
        iconButton("zoomIn", "放大", () => this.actions.onZoom(1.25)),
        iconButton("fit", "回到内容", () => this.actions.onFit()),
        iconButton("zoomOut", "缩小", () => this.actions.onZoom(0.8)),
      ]);
      if (buttons.length) this.root.append(topright);
      this.root.append(zoombar);
    }
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
    bar.append(iconButton("trash", "清屏", () => this.confirmClear(), "danger"));
  }

  /* ------------------------------------------------ 笔具盘（beta） */

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
      pk.onClear = () => this.confirmClear();
      pk.onLeave = () => this.setPicker(false);
      pk.applyState({
        tool: this.tool.tool,
        color: this.ink.color,
        sizeIndex: this.ink.widthIndex,
        fingerDraws: this.fingerDraw,
      });
      this.pk = pk;
      pk.setHistory(this.undoEnabled, this.redoEnabled);
      this.toolbar.classList.add("hidden");
    } catch (err) {
      this.picker = false;
      this.message("笔具盘没能载入", "close", 5000);
    } finally {
      this.pkLoading = false;
    }
  }

  unmountPicker() {
    if (!this.pk) return;
    this.pk.destroy();
    this.pk = null;
    if (this.pkHost) this.pkHost.remove();
    this.pkHost = null;
    this.toolbar.classList.remove("hidden");
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
    this.selectTool(state.tool);
  }

  /** 落笔时通知工具盘（开了「自动最小化」就收起来）。 */
  strokeStarted() {
    if (this.pk) this.pk.strokeStarted();
  }

  /** 笔具盘开关。 */
  setPicker(on) {
    this.picker = !!on;
    try {
      localStorage.setItem(PICKER_KEY, this.picker ? "1" : "0");
    } catch (err) {
      /* 记不住就下次回到普通工具栏 */
    }
    this.closePopover();
    if (this.picker) this.mountPicker();
    else this.unmountPicker();
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

  /** 关于：图标、名字、版本，以及更新相关的入口都收在这里。 */
  /** 关于：图标、名字、一句说明、几行信息，链接在下面，检查更新在最底下。 */
  openAbout() {
    this.closeSheet();
    const info = this.info || {};
    const rows = [["版本", info.version || "—"]];
    if (info.hostname) rows.push(["地址", `${info.hostname}:${info.port || ""}`]);

    const link = (text, onclick) => el("button", { class: "link", text, onclick });
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog about" }, [
      el("img", { class: "about-icon", src: "/icon.png", alt: "" }),
      el("div", { class: "about-name", text: "白板" }),
      el(
        "div",
        { class: "about-rows" },
        rows.flatMap(([key, value]) => [
          el("span", { class: "about-key", text: key }),
          el("span", { class: "about-val", text: String(value) }),
        ])
      ),
      el("div", { class: "about-links" }, [
        link("更新日志", () => this.actions.onOpenReleases()),
        link("日志文件", () => this.actions.onOpenLog()),
      ]),
      this.updateRow(),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
    return scrim;
  }

  updateRow() {
    const note = el("span", { class: "check-note" });
    const button = el("button", { class: "btn", text: "检查更新" });
    button.addEventListener("click", async () => {
      button.setAttribute("disabled", "");
      note.textContent = "检查中…";
      let message = "";
      try {
        message = await this.actions.onCheckUpdate();
      } catch (err) {
        message = "检查失败";
      }
      note.textContent = message || "";
      button.removeAttribute("disabled");
    });
    // 结果放在按钮上方，并且始终占着一行高度：按钮不会因为出结果而被顶着移动。
    return el("div", { class: "row about-update" }, [note, button]);
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
      eraserSize: ERASER_SIZES[this.tool.eraser.widthIndex],
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

  /** 粗细；橡皮擦用的是自己那套尺寸，所以点大小按工具走。 */
  widthOptions() {
    const eraser = this.tool.tool === "eraser";
    const widths = el("div", { class: "widths" });
    WIDTHS.forEach((width, index) => {
      const size = eraser ? 6 + index * 3.6 : 4 + index * 4;
      const button = el(
        "button",
        {
          class: `width-opt${index === this.ink.widthIndex ? " active" : ""}`,
          title: `${eraser ? ERASER_SIZES[index] : width}`,
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

  /** 笔具盘的开关，放在颜色面板里；只有触摸设备才给（这条是给 iPad 的）。 */
  pickerRow() {
    if (!this.touchDevice) return null;
    const input = el("input", { type: "checkbox" });
    input.checked = this.picker;
    input.addEventListener("change", () => this.setPicker(input.checked));
    return el("label", { class: "beta-row" }, [
      input,
      el("span", { text: "笔具盘 beta" }),
    ]);
  }

  togglePalette(anchor) {
    if (this.popover) {
      this.closePopover();
      return;
    }
    const parts = [];
    if (this.tool.tool !== "eraser") parts.push(this.colorSwatches());
    parts.push(this.widthOptions());
    const row = this.pickerRow();
    if (row) parts.push(row);
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

  /**
   * 更新对话框：图标、标题、更新说明、自动下载开关、三个按钮。
   *
   * ``state`` 来自本地进程（update_state），``actions`` 里是四个回调：
   * skip / installNow / installOnQuit / setAuto，另外 poll 用来刷新下载进度。
   */
  showUpdateDialog(state, actions) {
    this.closeUpdateDialog();
    const info = state.info || {};
    const version = info.version || "";

    const title = el("h2", { text: `新版本的白板可以安装了` });
    const subtitle = el("p");
    const notes = el("div", { class: "update-notes", html: renderNotes(info.notes) });
    notes.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link) return;
      event.preventDefault();
      this.actions.onOpenUrl(link.getAttribute("href"));
    });

    const bar = el("i");
    const progress = el("div", { class: "update-progress" }, [bar]);

    const checkbox = el("input", { type: "checkbox" });
    checkbox.checked = !!state.auto;
    checkbox.addEventListener("change", () => actions.setAuto(checkbox.checked));
    const auto = el("label", { class: "update-auto" }, [
      checkbox,
      el("span", { text: "以后自动下载更新" }),
    ]);

    const skip = el("button", { class: "btn", text: "跳过这个版本" });
    const later = el("button", { class: "btn", text: "退出应用时安装" });
    const now = el("button", { class: "btn primary", text: "安装并重启应用" });

    const render = (current) => {
      const staged = !!current.staged;
      this.updateStaged = staged;
      const downloading = !!current.downloading;
      subtitle.textContent = staged
        ? `白板 ${version} 已下载完毕并可以使用。要立刻安装并重启白板吗？`
        : downloading
          ? `正在下载白板 ${version}…`
          : `白板 ${version} 可以下载安装，当前版本 ${current.current || ""}。`;
      progress.style.display = downloading || (current.progress > 0 && !staged) ? "" : "none";
      bar.style.width = `${Math.round((current.progress || 0) * 100)}%`;
      now.textContent = staged ? "安装并重启应用" : "下载并安装";
    };
    render(state);

    /** 下载 / 校验失败时把服务端给的具体原因显示出来，而不是一句「失败」。 */
    const failure = async (fallback) => {
      if (!actions.poll) return fallback;
      const current = await actions.poll();
      const reason = current && current.error;
      return reason ? `${reason}，这次先不更新。` : fallback;
    };

    skip.addEventListener("click", async () => {
      await actions.skip();
      this.closeUpdateDialog();
    });
    later.addEventListener("click", async () => {
      later.setAttribute("disabled", "");
      now.setAttribute("disabled", "");
      if (!this.updateStaged) {
        progress.style.display = "";
        if (!(await actions.download())) {
          subtitle.textContent = await failure("下载失败了，稍后再试。");
          later.removeAttribute("disabled");
          now.removeAttribute("disabled");
          return;
        }
      }
      const ok = await actions.installOnQuit();
      this.closeUpdateDialog();
      this.toast(ok ? "check" : "close");
    });
    now.addEventListener("click", async () => {
      now.setAttribute("disabled", "");
      later.setAttribute("disabled", "");
      // 先下载再安装：两步分开，进度条才有东西可显示
      if (!this.updateStaged) {
        subtitle.textContent = `正在下载白板 ${version}…`;
        progress.style.display = "";
        const downloaded = await actions.download();
        if (!downloaded) {
          subtitle.textContent = await failure("下载失败了，稍后再试。");
          now.removeAttribute("disabled");
          later.removeAttribute("disabled");
          return;
        }
      }
      subtitle.textContent = "正在安装，应用会重新打开…";
      const ok = await actions.installNow();
      if (!ok) {
        subtitle.textContent = "安装失败了，稍后再试。";
        now.removeAttribute("disabled");
        later.removeAttribute("disabled");
      }
    });

    const dialog = el("div", { class: "update-dialog" }, [
      el("div", { class: "update-head" }, [
        el("img", { src: "/icon.png", alt: "" }),
        el("div", {}, [title, subtitle]),
      ]),
      notes,
      progress,
      auto,
      el("div", { class: "update-actions" }, [
        skip,
        el("span", { class: "spacer" }),
        later,
        now,
      ]),
    ]);
    const scrim = el("div", { class: "scrim" });
    scrim.append(dialog);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(scrim);
    this.updateDialog = { scrim, render };

    // 下载中就跟着刷新进度
    if (actions.poll) {
      this._updateTimer = setInterval(async () => {
        if (!this.updateDialog) return;
        const current = await actions.poll();
        if (current) render(current);
      }, 400);
    }
    return this.updateDialog;
  }

  closeUpdateDialog() {
    clearInterval(this._updateTimer);
    this._updateTimer = 0;
    if (this.updateDialog) {
      this.updateDialog.scrim.remove();
      this.updateDialog = null;
    }
  }

  confirm(iconName, onYes) {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog" }, [
      el("div", { html: icon(iconName), style: { color: "var(--error)" } }),
      iconButton("close", "取消", () => scrim.remove()),
      iconButton("check", "确定", () => {
        scrim.remove();
        onYes();
      }),
    ]);
    scrim.append(dialog);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(scrim);
  }

  confirmClear() {
    this.confirm("trash", () => this.actions.onClear());
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

  setBoards(boards, currentId) {
    this.boards = boards;
    this.currentBoardId = currentId;
    if (this.gallery) this.renderBoards();
  }

  closeGallery() {
    if (this.gallery) {
      this.gallery.remove();
      this.gallery = null;
    }
  }

  /** 打开选择界面前先把当前白板的缩略图刷新一遍，免得看到的是旧图。 */
  async openBoards() {
    this.boardQuery = "";
    if (this.actions.onBoardsOpen) await this.actions.onBoardsOpen();
    this.renderBoards();
  }

  /** Mac 端专门的白板选择界面：满屏缩略图，左上角标出延伸类型，下面是名字和日期。 */
  renderBoards() {
    // 列表随时可能被广播刷新（别处改了名、新建、删除），重建之前记住焦点落在哪，
    // 建完再放回去，否则正在输入的搜索框或改名框会被抽走。
    const active = document.activeElement;
    const inside = active && this.gallery && this.gallery.contains(active);
    const focusKey = inside ? active.dataset.focusKey : null;
    const caret = focusKey && active.setSelectionRange ? [active.selectionStart, active.selectionEnd] : null;

    this.closeGallery();
    this.closeSheet();

    const search = el("input", {
      class: "board-search",
      type: "text",
      placeholder: "搜索白板",
      value: this.boardQuery,
      spellcheck: "false",
      "data-focus-key": "search",
      oninput: () => {
        this.boardQuery = search.value;
        this.fillBoardGrid();
      },
      onkeydown: (event) => {
        if (event.key !== "Escape" || !search.value) return;
        event.stopPropagation(); // 别让 Esc 顺手把整个界面关掉
        search.value = this.boardQuery = "";
        this.fillBoardGrid();
      },
    });

    this.boardGrid = el("div", { class: "gallery-grid" });
    const gallery = el("div", { class: "gallery" }, [
      el("div", { class: "gallery-head" }, [
        el("label", { class: "search-box" }, [
          el("span", { class: "search-icon", html: icon("search", 18) }),
          search,
        ]),
        iconButton("close", "关闭", () => this.closeGallery()),
      ]),
      this.boardGrid,
    ]);
    this.root.append(gallery);
    this.gallery = gallery;
    this.fillBoardGrid();

    if (!focusKey) return;
    const back = gallery.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
    if (!back) return;
    back.focus();
    if (caret && back.setSelectionRange) back.setSelectionRange(caret[0], caret[1]);
  }

  /** 只重铺格子。搜索时不碰上面那条，输入框和输入法状态才不会被打断。 */
  fillBoardGrid() {
    const grid = this.boardGrid;
    if (!grid) return;
    grid.textContent = "";
    const query = this.boardQuery.trim().toLowerCase();
    const matched = query ? this.boards.filter((b) => boardHaystack(b).includes(query)) : this.boards;

    for (const board of matched) grid.append(this.boardItem(board));

    if (query) {
      if (!matched.length) {
        grid.append(el("p", { class: "gallery-empty", text: "没有名字对得上的白板" }));
      }
      return; // 搜索结果里不放「新建」，免得点错
    }
    grid.append(
      el("div", { class: "board-item" }, [
        el("button", {
          class: "board-card add",
          html: icon("add", 32),
          title: "新建白板",
          onclick: () => this.chooseKind(),
        }),
      ])
    );
  }

  /** 一块白板：缩略图 + 可以直接改的名字 + 最后一次写的时间。 */
  boardItem(board) {
    const card = el(
      "div",
      {
        class: `board-card${board.id === this.currentBoardId ? " active" : ""}`,
        style: {
          backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated)})`,
        },
        onclick: () => {
          this.closeGallery();
          this.actions.onSelectBoard(board.id);
        },
      },
      [
        el("span", {
          class: "kind",
          html: icon(["note", "doc"].includes(board.kind) ? board.kind : "board", 20),
        }),
      ]
    );
    if (this.boards.length > 1) {
      card.append(
        el("button", {
          class: "del",
          html: icon("close", 18),
          title: "删除白板",
          onclick: (event) => {
            event.stopPropagation();
            this.confirm("trash", () => this.actions.onDeleteBoard(board.id));
          },
        })
      );
    }

    // 名字就是一个长得像文字的输入框：点一下直接改，清空就回到默认名。
    const name = el("input", {
      class: "board-name",
      type: "text",
      value: board.name || "",
      placeholder: boardLabel(board),
      title: "点一下改名",
      spellcheck: "false",
      maxlength: "64",
      "data-focus-key": `name:${board.id}`,
      onkeydown: (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          name.blur();
        } else if (event.key === "Escape") {
          event.stopPropagation();
          name.value = board.name || "";
          name.blur();
        }
      },
      // change 只在值真的变了又失去焦点（或按了回车）时才发，正好是我们要的时机
      onchange: () => {
        const next = name.value.trim();
        name.value = next;
        if (next === (board.name || "")) return;
        this.actions.onRenameBoard(board.id, next);
      },
    });

    return el("div", { class: "board-item" }, [
      card,
      el("div", { class: "board-meta" }, [
        name,
        el("span", {
          class: "board-date",
          text: boardDate(board.updated),
          title: new Date(board.updated * 1000).toLocaleString(),
        }),
      ]),
    ]);
  }

  /** 弹一个文件选择框，选中的 PDF / 图片交给上层去建板。 */
  pickDoc() {
    const input = el("input", {
      type: "file",
      accept: ".pdf,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff,application/pdf,image/*",
      style: { display: "none" },
    });
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      input.remove();
      if (file) this.actions.onNewDoc(file);
    });
    this.root.append(input);
    input.click();
  }

  /** 新建白板时选延伸方式：大白板、笔记，或者直接拿一份 PDF / 图片当底。 */
  chooseKind() {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const pick = (kind) => {
      scrim.remove();
      this.closeGallery();
      this.actions.onNewBoard(kind);
    };
    const dialog = el("div", { class: "dialog kinds" }, [
      el("button", {
        class: "kind-tile",
        html: icon("board", 48),
        title: "大白板：四个方向都无限延伸",
        onclick: () => pick("board"),
      }),
      el("button", {
        class: "kind-tile",
        html: icon("note", 48),
        title: "笔记：宽度固定，只向下延伸",
        onclick: () => pick("note"),
      }),
      el("button", {
        class: "kind-tile",
        html: icon("doc", 48),
        title: "打开 PDF / 图片，直接在上面写（也可以把文件拖进窗口）",
        onclick: () => {
          scrim.remove();
          this.closeGallery();
          this.pickDoc();
        },
      }),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
  }

  backgroundOptions() {
    const kinds = [
      ["blank", ""],
      ["grid", `<path d="M0 14h56M0 30h56M14 0v44M28 0v44M42 0v44" stroke="currentColor" stroke-width="1.5"/>`],
      ["lines", `<path d="M0 14h56M0 24h56M0 34h56" stroke="currentColor" stroke-width="1.5"/>`],
      ["dots", (() => {
        let dots = "";
        for (let x = 8; x < 56; x += 12) {
          for (let y = 8; y < 44; y += 12) dots += `<circle cx="${x}" cy="${y}" r="1.6" fill="currentColor"/>`;
        }
        return dots;
      })()],
    ];
    const row = el("div", { class: "bg-opts" });
    for (const [kind, markup] of kinds) {
      row.append(
        el("button", {
          class: `bg-opt${this.meta && this.meta.background === kind ? " active" : ""}`,
          html: `<svg viewBox="0 0 56 44">${markup}</svg>`,
          title: kind,
          onclick: () => this.actions.onMeta({ background: kind }),
        })
      );
    }
    return row;
  }

  openSettings() {
    const groups = [];
    // 文档板的底是原件本身，背景纹理没有意义。
    const isDoc = this.meta && this.meta.kind === "doc";
    if (!isDoc && this.may("settings")) {
      groups.push(this.settingsGroup("背景", [this.backgroundOptions()]));
    }
    // 选目录要开本地文件对话框，只有 pywebview 窗口里才有；别的设备看不到这两段。
    if (this.native) {
      groups.push(this.settingsGroup("存储目录", [this.dataDirCard()]));
      groups.push(
        this.settingsGroup("其他设备权限", [
          el("div", { class: "perm-list" }, PERMISSIONS.map((item) => this.permissionRow(item))),
        ])
      );
    }
    const sheet = this.openSheet(groups);
    sheet.parentElement.dataset.kind = "settings";
  }

  /** 设置面板里的一段：一个小标题加内容。 */
  settingsGroup(title, children) {
    return el("section", { class: "group" }, [
      el("h2", { class: "group-title", text: title }),
      ...children,
    ]);
  }

  dataDirCard() {
    const path = (this.info && this.info.data_dir) || "";
    return el("div", { class: "card" }, [
      el("div", { class: "addr", text: path }),
      // 合着的文件夹是「换一个」，开着的是「打开看看」，和原来那一行一致
      iconButton("folder", "换一个目录", async () => {
        const dir = await this.actions.onChooseDir();
        if (!dir) return;
        if (this.info) this.info.data_dir = dir;
        this.toast("check");
        this.openSettings();
      }),
      iconButton("folderOpen", "在访达里打开", () => this.actions.onOpenDir()),
    ]);
  }

  /**
   * 一项权限一行：名字、一句话说明、一个开关。真正的拦截在服务端，
   * 这里改的是本地进程里的配置。
   */
  permissionRow({ key, title, note }) {
    const granted = (this.info && this.info.remote_permissions) || {};
    const input = el("input", { type: "checkbox" });
    input.checked = !!granted[key];
    input.addEventListener("change", async () => {
      const next = await this.actions.onRemotePermission(key, input.checked);
      if (!next) return;
      input.checked = !!next[key];
      if (this.info) this.info.remote_permissions = next;
    });
    return el("label", { class: "perm-row" }, [
      el("span", { class: "perm-text" }, [
        el("span", { class: "perm-title", text: title }),
        el("span", { class: "perm-note", text: note }),
      ]),
      input,
    ]);
  }

  connectCard() {
    const url = this.info && this.info.urls && this.info.urls.length ? this.info.urls[0] : "";
    return el("div", { class: "card" }, [
      el("div", { html: icon("tablet"), style: { color: "var(--primary)" } }),
      el("div", { class: "addr", text: url }),
      iconButton("download", "下载 iPad 描述文件", () => this.actions.onProfile()),
      iconButton("link", "在浏览器打开", () => this.actions.onOpenUrl(url)),
    ]);
  }

  openConnect() {
    const sheet = this.openSheet([el("div", { class: "group" }, [this.connectCard()])]);
    sheet.parentElement.dataset.kind = "connect";
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

  setInfo(info) {
    this.info = info;
  }
}

// 界面：Material 3 风格，只用图标，不放文案。
//
// iPad 端只有一条工具栏；Mac 端另外多出白板列表、白板设置（背景 / 存储目录 /
// 描述文件）、导出与缩放。
//
// 工具栏有两套：默认那条是一排图标；勾上「笔具盘 beta」之后换成 toolart.js 画的
// 一支支笔（参考 iPad 的 PencilKit 工具面板，形状和配色按 Material 3 重做），
// 位置由 toolpicker.js 的 ToolDock 管，可以拖到任意一边或者收进角落。

import { icon } from "./icons.js";
import { loadFingerDraw } from "./input.js";
import { renderNotes } from "./notes.js";
import { toolArt } from "./toolart.js";
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

function sanitizeEntry(raw, fallback) {
  const entry = { ...fallback };
  if (raw && COLORS.includes(raw.color)) entry.color = raw.color;
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

/** 笔具盘（beta）：工具画成一支支笔，选中的抬起来，再点一下开设置。 */
function loadPicker() {
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
  constructor({ role, native, actions }) {
    this.role = role;
    this.native = native;
    this.actions = actions;
    this.root = document.getElementById("ui");
    this.tool = loadTool();
    this.meta = null;
    this.boards = [];
    this.info = null;
    this.popover = null;
    this.sheet = null;
    this.picker = loadPicker();
    this.undoEnabled = false;
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
    this.dock = new ToolDock({
      root: this.root,
      bar: this.toolbar,
      renderBubble: () => this.bubbleArt(),
    });
    this.fillToolbar();

    if (this.role === "mac") {
      const topright = el("div", { id: "topright", class: "pill" }, [
        iconButton("boards", "白板", () => this.openBoards()),
        iconButton("settings", "白板设置", () => this.openSettings()),
        (this.exportButton = iconButton("image", "导出 PNG", () => this.actions.onExport())),
        iconButton("tablet", "连接 iPad", () => this.openConnect()),
        iconButton("info", "关于", () => this.openAbout()),
      ]);
      const zoombar = el("div", { id: "zoombar", class: "pill" }, [
        iconButton("zoomIn", "放大", () => this.actions.onZoom(1.25)),
        iconButton("fit", "回到内容", () => this.actions.onFit()),
        iconButton("zoomOut", "缩小", () => this.actions.onZoom(0.8)),
      ]);
      this.root.append(topright, zoombar);
    }
  }

  /** 按当前模式重新填一遍工具栏（换模式时整条重建）。 */
  fillToolbar() {
    this.closePopover();
    const bar = this.toolbar;
    bar.innerHTML = "";
    bar.classList.toggle("picker", this.picker);
    this.toolButtons = {};
    this.colorButton = null;
    this.colorDot = null;
    if (this.picker) this.fillPickerBar(bar);
    else this.fillClassicBar(bar);
    this.dock.enableDrag(this.picker);
    this.dock.apply({}, false);
    this.syncDockButton();
    this.setUndoEnabled(this.undoEnabled);
    this.selectTool(this.tool.tool);
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

  /**
   * 笔具盘（beta）：每件工具画成一支立着的笔，笔尖就是它自己的墨色，选中的那支
   * 抬起来。点已经选中的那支会开设置，颜色和粗细都在里面——不再需要单独的颜色按钮。
   */
  fillPickerBar(bar) {
    // 一条明显的握把：告诉人这条栏可以拖，也给手指一个不会误触按钮的着力点
    bar.append(el("div", { class: "grip", title: "拖动换位置" }));
    for (const key of ALL_TOOLS) {
      const button = el("button", {
        class: "tool-slot",
        title: TOOL_TITLES[key],
        "aria-label": TOOL_TITLES[key],
        onclick: (event) => this.onPickerTool(key, event.currentTarget),
      });
      this.toolButtons[key] = button;
      bar.append(button);
    }
    bar.append(el("div", { class: "sep" }));
    this.appendCommonButtons(bar);
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
    bar.append(this.undoButton);
    bar.append(iconButton("trash", "清屏", () => this.confirmClear(), "danger"));
  }

  /** 点工具：换一支就是换一支，点的是正在用的那支就开设置。 */
  onPickerTool(key, anchor) {
    if (this.tool.tool === key) this.openInspector(anchor);
    else this.selectTool(key);
  }

  /** 把每支笔按自己的墨色和粗细重画一遍。 */
  renderToolArt() {
    if (this.picker) {
      for (const key of ALL_TOOLS) {
        const button = this.toolButtons[key];
        if (!button) continue;
        button.innerHTML = toolArt(key, {
          color: this.tool[key].color,
          widthIndex: this.tool[key].widthIndex,
          widthCount: WIDTHS.length,
        });
      }
    }
    if (this.dock) this.dock.refreshBubble();
  }

  bubbleArt() {
    const ink = this.ink;
    return toolArt(this.tool.tool, {
      color: ink.color,
      widthIndex: ink.widthIndex,
      widthCount: WIDTHS.length,
      size: 40,
    });
  }

  /** 笔具盘开关：换模式就整条重建。 */
  setPicker(on) {
    this.picker = !!on;
    try {
      localStorage.setItem(PICKER_KEY, this.picker ? "1" : "0");
    } catch (err) {
      /* 记不住就下次回到普通工具栏 */
    }
    if (!this.picker) this.dock.apply({ minimized: false });
    this.fillToolbar();
  }

  syncDockButton() {
    if (!this.dockButton) return;
    const onTop = this.dock.dock === "top" && !this.dock.minimized;
    this.dockButton.innerHTML = icon(onTop ? "dockBottom" : "dockTop");
  }

  toggleDock() {
    this.closePopover();
    this.dock.toggle();
    this.syncDockButton();
  }

  /** 关于：图标、名字、版本，以及更新相关的入口都收在这里。 */
  openAbout() {
    this.closeSheet();
    const info = this.info || {};
    const version = info.version ? `版本 ${info.version}` : "";
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog about" }, [
      el("img", { class: "about-icon", src: "/icon.png", alt: "" }),
      el("div", { class: "about-name", text: "白板" }),
      el("div", { class: "about-meta", text: version }),
      el("div", { class: "about-meta", text: "局域网共享白板" }),
      this.updateRow(),
      el("div", { class: "row about-links" }, [
        el("button", {
          class: "btn",
          text: "更新日志",
          onclick: () => this.actions.onOpenReleases(),
        }),
        el("button", {
          class: "btn",
          text: "日志文件",
          onclick: () => this.actions.onOpenLog(),
        }),
      ]),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
    return scrim;
  }

  /** 检查更新按钮 + 结果文字。 */
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
    this.renderToolArt();
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
    this.renderToolArt();
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
    const side = this.dock && !this.dock.minimized ? this.dock.dock : "bottom";
    if (side === "left" || side === "right") {
      // 工具栏立在侧边：弹层开到它旁边，别压在工具上
      const beside = side === "left" ? rect.right + 12 : rect.left - width - 12;
      popover.style.left = `${clamp(beside, 12, Math.max(12, innerWidth - width - 12))}px`;
      popover.style.top = `${clamp(rect.top + rect.height / 2 - height / 2, 12, Math.max(12, innerHeight - height - 12))}px`;
    } else {
      popover.style.left = `${clamp(rect.left + rect.width / 2 - width / 2, 12, innerWidth - width - 12)}px`;
      // 工具栏在上边时锚点上方没地方，翻到下面开
      const above = rect.top - height - 12;
      const below = rect.bottom + 12;
      popover.style.top =
        above >= 12 ? `${above}px` : `${clamp(below, 12, Math.max(12, innerHeight - height - 12))}px`;
    }
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

  /** 笔具盘的开关，放在工具设置面板里，两种模式都能切回去。 */
  pickerRow() {
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
    parts.push(this.widthOptions(), this.pickerRow());
    this.showPopover(anchor, parts);
  }

  /** 笔具盘：点正在用的那支笔弹出来的设置。 */
  openInspector(anchor) {
    if (this.popover) {
      this.closePopover();
      return;
    }
    const parts = [el("div", { class: "pop-title", text: TOOL_TITLES[this.tool.tool] })];
    parts.push(this.widthOptions());
    if (this.tool.tool !== "eraser") parts.push(this.colorSwatches());
    parts.push(this.pickerRow());
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
    if (this.actions.onBoardsOpen) await this.actions.onBoardsOpen();
    this.renderBoards();
  }

  /** Mac 端专门的白板选择界面：满屏缩略图，左上角标出延伸类型。 */
  renderBoards() {
    this.closeGallery();
    this.closeSheet();
    const grid = el("div", { class: "gallery-grid" });

    for (const board of this.boards) {
      const card = el(
        "div",
        {
          class: `board-card${board.id === this.currentBoardId ? " active" : ""}`,
          style: {
            backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated)})`,
          },
          title: new Date(board.updated * 1000).toLocaleString(),
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
      grid.append(card);
    }

    grid.append(
      el("button", {
        class: "board-card add",
        html: icon("add", 32),
        title: "新建白板",
        onclick: () => this.chooseKind(),
      })
    );

    const gallery = el("div", { class: "gallery" }, [
      el("div", { class: "gallery-head" }, [
        iconButton("close", "关闭", () => this.closeGallery()),
      ]),
      grid,
    ]);
    this.root.append(gallery);
    this.gallery = gallery;
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
    // 文档板的底是原件本身，背景纹理没有意义。
    const isDoc = this.meta && this.meta.kind === "doc";
    const groups = isDoc ? [] : [el("div", { class: "group" }, [this.backgroundOptions()])];
    // 选择 / 打开存储目录要调用本地文件对话框，只有 pywebview 窗口里才有。
    if (this.actions.isNative()) {
      groups.push(
        el("div", { class: "group" }, [
          el("div", { class: "row" }, [
            el("span", { class: "row-label", text: "存储目录" }),
            iconButton("folder", "选择存储目录", async () => {
              const dir = await this.actions.onChooseDir();
              if (dir) this.toast("check");
            }),
            iconButton("folderOpen", "打开存储目录", () => this.actions.onOpenDir()),
          ]),
        ])
      );
    }
    groups.push(el("div", { class: "group" }, [this.connectCard()]));
    const sheet = this.openSheet(groups);
    sheet.parentElement.dataset.kind = "settings";
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

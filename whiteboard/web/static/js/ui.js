// 界面：Material 3 风格，只用图标，不放文案。
//
// iPad 端只有底部一条工具栏；Mac 端另外多出白板列表、白板设置（尺寸 / 背景 /
// 存储目录 / 描述文件）、导出与缩放。

import { icon } from "./icons.js";
import { loadFingerDraw } from "./input.js";
import { el, clamp } from "./util.js";

export const COLORS = [
  "#1b1b1f", "#5f6368", "#e53935", "#fb8c00", "#fdd835",
  "#43a047", "#00acc1", "#1e88e5", "#8e24aa", "#6d4c41",
];
export const WIDTHS = [1.5, 3, 5, 8, 13];
export const ERASER_SIZES = [16, 28, 44, 66, 96];

const TOOL_KEY = "whiteboard.tool";

function loadTool() {
  const fallback = { tool: "pen", color: COLORS[0], widthIndex: 1 };
  try {
    const saved = JSON.parse(localStorage.getItem(TOOL_KEY) || "null");
    if (!saved) return fallback;
    return {
      tool: ["pen", "marker", "highlighter", "eraser"].includes(saved.tool) ? saved.tool : "pen",
      color: COLORS.includes(saved.color) ? saved.color : COLORS[0],
      widthIndex: saved.widthIndex >= 0 && saved.widthIndex < WIDTHS.length ? saved.widthIndex : 1,
    };
  } catch (err) {
    return fallback;
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

    this.toolButtons = {};
    const tools = [
      ["pen", "pen", "钢笔"],
      ["marker", "marker", "马克笔"],
      ["highlighter", "highlighter", "荧光笔"],
      ["eraser", "eraser", "橡皮擦"],
    ];
    const toolbar = el("div", { id: "toolbar", class: "pill" });
    for (const [key, iconName, title] of tools) {
      const button = iconButton(iconName, title, () => this.selectTool(key));
      this.toolButtons[key] = button;
      toolbar.append(button);
    }
    toolbar.append(el("div", { class: "sep" }));

    // 默认只认 Apple Pencil，手指负责平移缩放；没有 Pencil 的人在这里打开手指书写。
    this.touchDevice = this.role === "ipad" || navigator.maxTouchPoints > 1;
    if (this.touchDevice) {
      this.fingerDraw = loadFingerDraw();
      this.fingerButton = iconButton("hand", "手指书写", () => this.toggleFingerDraw());
      this.fingerButton.classList.toggle("active", this.fingerDraw);
      toolbar.append(this.fingerButton, el("div", { class: "sep" }));
    }

    this.colorButton = el("button", {
      class: "icon-btn",
      title: "颜色与粗细",
      onclick: (e) => this.togglePalette(e.currentTarget),
    });
    this.colorDot = el("i", { class: "color-dot", style: { background: this.tool.color } });
    this.colorButton.append(this.colorDot);
    toolbar.append(this.colorButton, el("div", { class: "sep" }));

    this.undoButton = iconButton("undo", "撤销", () => this.actions.onUndo());
    toolbar.append(this.undoButton);
    toolbar.append(iconButton("trash", "清屏", () => this.confirmClear(), "danger"));
    this.root.append(toolbar);
    this.setUndoEnabled(false);

    if (this.role === "mac") {
      const topright = el("div", { id: "topright", class: "pill" }, [
        iconButton("boards", "白板", () => this.openBoards()),
        iconButton("settings", "白板设置", () => this.openSettings()),
        iconButton("image", "导出 PNG", () => this.actions.onExport()),
        iconButton("tablet", "连接 iPad", () => this.openConnect()),
      ]);
      const zoombar = el("div", { id: "zoombar", class: "pill" }, [
        iconButton("zoomIn", "放大", () => this.actions.onZoom(1.25)),
        iconButton("fit", "回到内容", () => this.actions.onFit()),
        iconButton("zoomOut", "缩小", () => this.actions.onZoom(0.8)),
      ]);
      this.root.append(topright, zoombar);
    }

    this.colorDot.style.background = this.tool.color;
    this.selectTool(this.tool.tool);
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

  selectTool(tool) {
    this.tool.tool = tool;
    this.rememberTool();
    for (const [key, button] of Object.entries(this.toolButtons)) {
      button.classList.toggle("active", key === tool);
    }
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
    return {
      tool: this.tool.tool,
      color: this.tool.color,
      width: WIDTHS[this.tool.widthIndex],
      eraserSize: ERASER_SIZES[this.tool.widthIndex],
    };
  }

  setUndoEnabled(enabled) {
    this.undoButton.toggleAttribute("disabled", !enabled);
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
    popover.style.left = `${clamp(rect.left + rect.width / 2 - width / 2, 12, innerWidth - width - 12)}px`;
    popover.style.top = `${Math.max(12, rect.top - popover.offsetHeight - 12)}px`;
    this.popover = popover;
    this._popoverCloser = (event) => {
      if (!popover.contains(event.target) && !anchor.contains(event.target)) this.closePopover();
    };
    setTimeout(() => addEventListener("pointerdown", this._popoverCloser, true), 0);
    return popover;
  }

  togglePalette(anchor) {
    if (this.popover) {
      this.closePopover();
      return;
    }
    const swatches = el("div", { class: "swatches" });
    for (const color of COLORS) {
      const button = el("button", {
        class: `swatch${color === this.tool.color ? " active" : ""}`,
        style: { background: color },
        title: color,
        onclick: () => {
          this.tool.color = color;
          this.rememberTool();
          this.colorDot.style.background = color;
          for (const node of swatches.children) node.classList.remove("active");
          button.classList.add("active");
          this.actions.onToolChange(this.toolState());
        },
      });
      swatches.append(button);
    }

    const widths = el("div", { class: "widths" });
    WIDTHS.forEach((width, index) => {
      const size = 4 + index * 4;
      const button = el(
        "button",
        {
          class: `width-opt${index === this.tool.widthIndex ? " active" : ""}`,
          title: `${width}`,
          onclick: () => {
            this.tool.widthIndex = index;
            this.rememberTool();
            for (const node of widths.children) node.classList.remove("active");
            button.classList.add("active");
            this.actions.onToolChange(this.toolState());
          },
        },
        [el("i", { style: { width: `${size}px`, height: `${size}px` } })]
      );
      widths.append(button);
    });

    this.showPopover(anchor, [swatches, widths]);
  }

  toast(iconName) {
    const node = el("div", { class: "toast", html: icon(iconName) });
    this.root.append(node);
    setTimeout(() => node.remove(), 1600);
  }

  /** 一条可点掉的提示，用于页面无能为力、只能让用户去改系统设置的情况。 */
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
    if (this.sheet && this.sheet.dataset.kind === "boards") this.openBoards();
  }

  openBoards() {
    const tiles = el("div", { class: "boards" });
    for (const board of this.boards) {
      const tile = el("div", {
        class: `board-tile${board.id === this.currentBoardId ? " active" : ""}`,
        style: { backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated)})` },
        title: new Date(board.updated * 1000).toLocaleString(),
        onclick: () => {
          this.closeSheet();
          this.actions.onSelectBoard(board.id);
        },
      });
      if (this.boards.length > 1) {
        tile.append(
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
      tiles.append(tile);
    }
    tiles.append(
      el("div", {
        class: "board-tile add",
        html: icon("add", 28),
        title: "新建白板",
        onclick: () => {
          this.closeSheet();
          this.actions.onNewBoard();
        },
      })
    );
    const sheet = this.openSheet([el("div", { class: "group" }, [tiles])]);
    sheet.parentElement.dataset.kind = "boards";
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
    const groups = [el("div", { class: "group" }, [this.backgroundOptions()])];
    // 选择 / 打开存储目录要调用本地文件对话框，只有 pywebview 窗口里才有。
    if (this.actions.isNative()) {
      groups.push(
        el("div", { class: "group" }, [
          el("div", { class: "row" }, [
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
    if (this.sheet && this.sheet.dataset.kind === "settings") this.openSettings();
  }

  setInfo(info) {
    this.info = info;
  }
}

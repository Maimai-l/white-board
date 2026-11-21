// 导出对话框和调整页框的视图（docs/backlog/001-export-redesign.md、010-export-v2.md）。
//
// 分页和出文件都在服务端（POST /api/export/{board}，见 docs/protocol.md「Paged export」）；
// 这里只管：取自动页框、在本机画缩略图、选页（缩略图和页码输入框）、选格式（PDF、
// PNG、白板文件）、范围（分页或整块）和清晰度、调整页框，最后把页框和选中的页交给服务端。
//
// ``host`` 是白板应用（app.js 的 App）：用它的 state / viewport 画缩略图和页框，
// 用它的 ui 弹提示，用 host.native() 判断是不是 Mac 窗口（那边走原生的保存对话框）。

import { docOf } from "./boards.js";
import { icon } from "./icons.js";
import {
  PAPERS,
  currentPage,
  drawnFrame,
  exportRequest,
  filenameOf,
  frameAt,
  pixelSize,
  placement,
  rawName,
  resizeFrame,
} from "./export-pages.js";
import { formatPages, parsePages } from "./page-range.js";
import { downloadURL } from "/inksync/exporter.js";
import { Renderer } from "/inksync/renderer.js";
import { reportError } from "/inksync/report.js";
import { el } from "/inksync/util.js";
import { leave } from "./motion.js";

const THUMB_WIDTH = 200;
// 文档板页多：缩略图小一些，排两列
const GRID_THUMB_WIDTH = 98;
// 整块白板的预览最大多大（CSS 像素）
const WHOLE_BOX = [200, 330];
const DPI = [
  ["标准", 150],
  ["高", 300],
  ["最高", 600],
];
const PAPER_NAMES = { content: "跟随内容", a4: "A4", letter: "Letter" };
// 文档板的页图按这一档取，和白板上显示的档位共用缓存（layers.js 的 BUCKETS）
const DOC_THUMB_BUCKET = 640;
const FILE_ICON = `<svg width="72" height="88" viewBox="0 0 72 88" aria-hidden="true"><path d="M8 4h38l18 18v58a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4z" fill="#ffffff" stroke="rgba(60,60,67,.3)" stroke-width="1.5"/><path d="M46 4v14a4 4 0 0 0 4 4h14" fill="none" stroke="rgba(60,60,67,.3)" stroke-width="1.5"/><path d="M16 46c6-8 10 6 16-2s10-6 14 0 6 4 10-2" fill="none" stroke="#1c1c1e" stroke-width="2.2" stroke-linecap="round"/><path d="M16 58c8-4 14 4 22 0s10-2 16 2" fill="none" stroke="#1c1c1e" stroke-width="2.2" stroke-linecap="round"/></svg>`;

let nextKey = 1;

function withKey(page) {
  return { ...page, key: nextKey++ };
}

export class ExportDialog {
  constructor(host) {
    this.host = host;
    this.ui = host.ui;
    this.boardId = host.state.id;
    this.isDoc = !!docOf(host.state.meta);
    this.format = "pdf";
    // 范围：分页或整块（文档板只有分页）
    this.range = "pages";
    this.dpi = 300;
    // 大白板和笔记默认纸张跟随内容；文档板用原件的页，纸张不用选
    this.paper = this.isDoc ? "a4" : "content";
    this.pages = [];
    this.whole = null;
    this.selected = new Set();
    // 「页面」那一栏：全部，或者输入框里写的页码（和缩略图的选中双向同步）
    this.pageMode = "all";
    this.pageText = "";
    this.pageError = null;
    this.adjusted = false;
    this.images = new Map();
    this.build();
  }

  // ---------------------------------------------------------- 结构

  build() {
    this.count = el("span", { class: "export-count" });
    // 全选和取消全选是同一个链接：全选上了就变成取消全选
    this.allLink = el("button", { class: "text-btn", type: "button", onclick: () => this.toggleAll() });
    // 当前页：视口中间那一页，导出正在看的那一页最快
    const current = el("button", { class: "text-btn", type: "button", text: "当前页", onclick: () => this.selectCurrent() });
    const head = el("div", { class: "export-pages-head" }, [this.count, el("span", { class: "spacer" }), this.allLink, current]);
    this.cards = el("div", { class: `export-cards${this.isDoc ? " is-grid" : ""}` });
    this.pagesPanel = el("div", { class: "export-pages" }, [head, this.cards]);

    this.wholeCanvas = el("canvas", { class: "export-canvas" });
    this.wholeThumb = el("div", { class: "export-whole-thumb" }, [this.wholeCanvas]);
    this.wholePanel = el("div", { class: "export-panel export-whole" }, [
      this.wholeThumb,
      el("span", { class: "export-whole-caption", text: "整张长图" }),
    ]);

    const meta = this.host.state.meta;
    this.filePanel = el("div", { class: "export-panel export-file" }, [
      el("span", { class: "export-file-icon", html: FILE_ICON }),
      el("span", { class: "export-file-name", text: rawName(meta && meta.name) }),
    ]);

    this.formatSeg = segmented(
      [["PDF", "pdf"], ["PNG", "png"], ["WBZ", "wbz"]],
      () => this.format,
      (value) => {
        this.format = value;
        this.refresh();
      },
      "格式",
    );
    this.rangeSeg = segmented(
      [["按页", "pages"], ["整张长图", "whole"]],
      () => this.range,
      (value) => {
        this.range = value;
        this.refresh();
      },
      "范围",
    );
    this.dpiSeg = segmented(
      DPI,
      () => this.dpi,
      (value) => {
        this.dpi = value;
        this.refresh();
      },
      "清晰度",
    );
    this.pixels = el("div", { class: "export-hint" });

    const group = `export-pages-${nextKey++}`;
    this.allRadio = el("input", { type: "radio", name: group, value: "all" });
    this.customRadio = el("input", { type: "radio", name: group, value: "custom" });
    this.pickedRadio = el("input", { type: "radio", name: group, value: "picked" });
    this.allRadio.addEventListener("change", () => this.setPageMode("all"));
    this.customRadio.addEventListener("change", () => this.setPageMode("custom"));
    this.pickedRadio.addEventListener("change", () => this.setPageMode("picked"));
    this.allText = el("span");
    this.pickedText = el("span");
    this.pageField = el("input", {
      type: "text",
      class: "export-range-field",
      "aria-label": "页码",
      placeholder: this.isDoc ? "例如 1-5, 8, 11-13" : "例如 1-3, 5",
      autocomplete: "off",
      spellcheck: "false",
    });
    this.pageField.addEventListener("input", () => this.typePages(this.pageField.value));
    this.pageErrorLine = el("div", { class: "export-range-error", role: "alert" });
    const pageControl = el("div", { class: "export-range", role: "radiogroup", "aria-label": "页面" }, [
      el("label", { class: "export-radio" }, [this.allRadio, this.allText]),
      el("label", { class: "export-radio" }, [this.customRadio, el("span", { text: "范围" }), this.pageField]),
      this.pageErrorLine,
      el("label", { class: "export-radio top" }, [
        this.pickedRadio,
        el("span", { class: "export-radio-text" }, [
          this.pickedText,
          el("span", { class: "export-radio-note", text: "从边栏选择页面" }),
        ]),
      ]),
    ]);

    this.paperLabel = el("span", { text: PAPER_NAMES[this.paper] });
    const select = el("select", { "aria-label": "纸张" }, Object.keys(PAPER_NAMES).map(
      (key) => el("option", { value: key, text: PAPER_NAMES[key] }),
    ));
    select.value = this.paper;
    select.addEventListener("change", () => {
      this.paper = select.value;
      this.paperLabel.textContent = PAPER_NAMES[this.paper];
      this.loadAuto();
    });
    const paperButton = el("div", { class: "export-popup" }, [
      this.paperLabel,
      el("span", { class: "chevron", html: chevron() }),
      select,
    ]);

    this.pagingText = el("span");
    const paging = el("div", { class: "export-paging" }, [
      this.pagingText,
      el("button", { class: "lib-btn tinted small", type: "button", text: "调整…", onclick: () => this.adjust() }),
    ]);

    // 每一行是 [标签, 控件]，只建一次，按选择显示或隐藏：输入框打字时不能被摘下来
    // 重新插进去（那样会丢焦点）
    const label = (text) => el("span", { class: "export-label", text });
    const note = el("div", { class: "export-note", text: "可以在白板应用中重新打开" });
    this.rows = {
      format: [label("格式"), this.formatSeg.node],
      note: [el("span"), note],
      range: this.isDoc ? null : [label("范围"), this.rangeSeg.node],
      pages: [label("页面"), pageControl],
      dpi: [label("清晰度"), el("div", { class: "export-stack" }, [this.dpiSeg.node, this.pixels])],
      paper: this.isDoc ? null : [label("纸张"), paperButton],
      paging: this.isDoc ? null : [label("分页"), paging],
    };
    this.options = el("div", { class: "export-options" }, Object.values(this.rows).filter(Boolean).flat());

    this.primary = el("button", { class: "lib-btn primary", type: "button", onclick: () => this.export() });
    const footer = el("div", { class: "export-footer" }, [
      el("button", { class: "lib-btn", type: "button", text: "取消", onclick: () => this.close() }),
      this.primary,
    ]);

    // 左边一栏是要导出的东西（页、整张长图或者白板文件），右边是选项，按钮在右下
    const name = (meta && meta.name) || "";
    this.dialog = el("div", { class: "export-dialog", role: "dialog", "aria-label": "导出" }, [
      el("div", { class: "export-side" }, [this.pagesPanel, this.wholePanel, this.filePanel]),
      el("div", { class: "export-main" }, [
        el("h2", { class: "export-title" }, [
          el("span", { text: "导出" }),
          name ? el("span", { class: "export-board-name", text: name }) : null,
        ]),
        this.options,
      ]),
      footer,
    ]);
    this.dialog.addEventListener("click", (event) => event.stopPropagation());
    this.scrim = el("div", { class: "scrim export-scrim" }, [this.dialog]);
    this.scrim.addEventListener("click", () => this.close());
    // Esc 等于取消，和 macOS 的表单一样。调整分页时对话框藏着，Esc 归调整那一层管
    this.keys = (event) => {
      if (event.key !== "Escape" || this.scrim.hidden || this.closed) return;
      event.stopPropagation();
      event.preventDefault();
      this.close();
    };
  }

  async open() {
    this.ui.root.append(this.scrim);
    addEventListener("keydown", this.keys, true);
    this.refresh();
    await this.loadAuto();
  }

  close() {
    this.closed = true;
    if (this.adjustView) this.adjustView.destroy();
    removeEventListener("keydown", this.keys, true);
    leave(this.scrim);
    if (this.ui.exportDialog === this) this.ui.exportDialog = null;
  }

  // ---------------------------------------------------------- 页

  async loadAuto() {
    const paper = this.paper;
    let payload;
    try {
      const response = await fetch(`/api/export/${encodeURIComponent(this.boardId)}/pages?paper=${paper}`);
      if (!response.ok) throw new Error((await response.text()) || String(response.status));
      payload = await response.json();
    } catch (err) {
      reportError("取导出页框失败", { message: String(err) });
      this.ui.message("导出失败", "close", 6000);
      return;
    }
    if (this.closed || paper !== this.paper) return;
    this.whole = payload.whole || null;
    this.wholeDrawn = null;
    this.setPages(payload.pages, false);
  }

  /** 换了一组页。``fromAdjust``：从调整视图回来，页框还是原来那些（按 key 认）。 */
  setPages(pages, adjusted, fromAdjust = false) {
    const before = new Map(this.pages.map((page) => [page.key, this.selected.has(page.key)]));
    this.pages = pages.map((page) => (page.key ? page : withKey(page)));
    this.adjusted = adjusted;
    if (this.pageMode === "all") {
      this.selected = new Set(this.pages.map((page) => page.key));
    } else if (fromAdjust) {
      // 原来就有的页保留选中与否，新出现的页默认选中；输入框跟着改写
      this.selected = new Set(
        this.pages.filter((page) => before.get(page.key) !== false).map((page) => page.key),
      );
      this.writePageText();
    } else {
      // 换了纸张，页全换了：按输入框里的页码重新选
      this.applyPageText();
    }
    this.renderCards();
    this.refresh();
  }

  selectAll() {
    this.selected = new Set(this.pages.map((page) => page.key));
    this.pageMode = "all";
    this.syncCards();
    this.refresh();
  }

  /** 边栏上那个链接：全选上了就全部取消，否则全选。取消之后停在所选的页。 */
  toggleAll() {
    if (this.selected.size === this.pages.length) {
      this.picked = new Set();
      this.selected = new Set();
      this.pageMode = "picked";
      this.syncCards();
      this.refresh();
      return;
    }
    this.selectAll();
  }

  selectCurrent() {
    const [cx, cy] = this.host.viewCenter();
    const index = currentPage(this.pages, cx, cy);
    this.selected = new Set(index >= 0 ? [this.pages[index].key] : []);
    this.picked = new Set(this.selected);
    this.pageMode = "picked";
    this.writePageText();
    this.syncCards();
    this.refresh();
  }

  /** 点了边栏上的一页：切到所选的页，在当前的选择上加上或去掉这一页。 */
  toggle(key) {
    if (this.selected.has(key)) this.selected.delete(key);
    else this.selected.add(key);
    this.picked = new Set(this.selected);
    this.pageMode = "picked";
    this.writePageText();
    this.syncCards();
    this.refresh();
  }

  /** 输入框里敲了字：立刻按页码选，单选按钮换到自定义。 */
  typePages(text) {
    this.pageText = text;
    this.pageMode = "custom";
    this.applyPageText();
    this.syncCards();
    this.refresh();
  }

  setPageMode(mode) {
    this.pageMode = mode;
    if (mode === "all") {
      this.selected = new Set(this.pages.map((page) => page.key));
    } else if (mode === "picked") {
      const keys = new Set(this.pages.map((page) => page.key));
      this.selected = new Set([...(this.picked || this.selected)].filter((key) => keys.has(key)));
    } else if (this.pageText.trim()) {
      this.applyPageText();
    } else {
      this.writePageText();
    }
    this.syncCards();
    this.refresh();
  }

  /**
   * 按输入框里的页码选页。写得不对时输入框下面说哪里不对、导出按钮变灰，缩略图留着
   * 上一次写对时的选择（边打字边看，「1-」这样打到一半时缩略图不会全灭再亮）。
   */
  applyPageText() {
    const { pages, error } = parsePages(this.pageText, this.pages.length);
    this.pageError = error;
    if (error) {
      const keys = new Set(this.pages.map((page) => page.key));
      this.selected = new Set([...this.selected].filter((key) => keys.has(key)));
      return;
    }
    this.selected = new Set(pages.map((index) => this.pages[index].key));
  }

  /** 把选中的页写回输入框（紧凑的写法，例如 1-3、8）。 */
  writePageText() {
    this.pageText = formatPages(this.selectedIndices());
    this.pageError = null;
  }

  selectedIndices() {
    return this.pages.flatMap((page, index) => (this.selected.has(page.key) ? [index] : []));
  }

  renderCards() {
    this.cards.textContent = "";
    this.cardNodes = new Map();
    const width = this.isDoc ? GRID_THUMB_WIDTH : THUMB_WIDTH;
    this.pages.forEach((page, index) => {
      const [pw, ph] = page.paper;
      const height = Math.round((width * ph) / pw);
      const canvas = el("canvas", { class: "export-canvas" });
      const thumb = el("div", { class: "export-thumb", style: { width: `${width}px`, height: `${height}px` } }, [
        canvas,
      ]);
      // 页的下沿压着一颗勾选胶囊：圆勾加页码
      const check = el("span", { class: "export-check" }, [
        el("span", { class: "export-check-box", html: icon("check", 10) }),
        el("span", { class: "export-caption", text: `${index + 1} / ${this.pages.length}` }),
      ]);
      const card = el(
        "button",
        {
          class: "export-card",
          type: "button",
          "data-page": String(index + 1),
          "aria-label": `第 ${index + 1} 页`,
          onclick: () => this.toggle(page.key),
        },
        [el("div", { class: "export-thumb-wrap" }, [thumb, check])],
      );
      this.cards.append(card);
      this.cardNodes.set(page.key, { card, canvas, page, width, height });
    });
    this.syncCards();
    this.drawThumbs();
  }

  syncCards() {
    for (const [key, { card }] of this.cardNodes || []) {
      const on = this.selected.has(key);
      card.classList.toggle("is-selected", on);
      card.setAttribute("aria-pressed", String(on));
    }
  }

  /** 缩略图在本机画：一帧画一张，页多的白板也不会卡住对话框。 */
  drawThumbs() {
    const token = (this.thumbToken = (this.thumbToken || 0) + 1);
    const queue = [...this.cardNodes.values()];
    const step = async () => {
      if (token !== this.thumbToken || this.closed) return;
      const item = queue.shift();
      if (!item) return;
      try {
        await this.drawThumb(item);
      } catch (err) {
        reportError("导出缩略图画不出来", { message: String(err) });
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async drawThumb({ canvas, page, width: cssWidth, height }) {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.round(cssWidth * ratio);
    canvas.width = width;
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const k = width / page.paper[0];
    const { scale, left, top } = placement(page);
    const images = this.isDoc ? await this.docImages(page) : null;
    const area = { x0: page.x, y0: page.y, x1: page.x + page.w, y1: page.y + page.h };
    const content = Renderer.renderToCanvas(this.host.state, { scale: scale * k, bounds: area, images });
    ctx.drawImage(content, left * k, top * k, page.w * scale * k, page.h * scale * k);
  }

  /** 整块白板的预览：按纸张的比例缩进 WHOLE_BOX，和页的缩略图同一个画法。 */
  drawWhole() {
    const page = this.whole;
    if (!page || this.wholeDrawn === page) return;
    this.wholeDrawn = page;
    const [pw, ph] = page.paper;
    const k = Math.min(WHOLE_BOX[0] / pw, WHOLE_BOX[1] / ph);
    const width = Math.max(1, Math.round(pw * k));
    const height = Math.max(1, Math.round(ph * k));
    Object.assign(this.wholeThumb.style, { width: `${width}px`, height: `${height}px` });
    this.drawThumb({ canvas: this.wholeCanvas, page, width, height }).catch((err) => {
      reportError("整块白板的预览画不出来", { message: String(err) });
    });
  }

  /** 文档板：只把落在这一页里的页图载进来，其余位置留 null（renderToCanvas 跳过）。 */
  async docImages(page) {
    const layers = this.host.state.layers || [];
    return Promise.all(
      layers.map((layer) => {
        const hit = layer.sheet && Math.abs(layer.x - page.x) < 0.5 && Math.abs(layer.y - page.y) < 0.5;
        if (!hit) return null;
        const src = layer.src.split("{w}").join(String(DOC_THUMB_BUCKET));
        if (!this.images.has(src)) {
          this.images.set(
            src,
            new Promise((resolve) => {
              const img = new Image();
              img.onload = () => resolve(img);
              img.onerror = () => {
                reportError("导出缩略图的页图载入失败", { src });
                resolve(null);
              };
              img.src = src;
            }),
          );
        }
        return this.images.get(src);
      }),
    );
  }

  // ---------------------------------------------------------- 右边那一栏

  /** 现在导出的是什么：白板文件（file）、整块一页（whole），还是选中的页（pages）。 */
  mode() {
    if (this.format === "wbz") return "file";
    if (!this.isDoc && this.range === "whole") return "whole";
    return "pages";
  }

  refresh() {
    const mode = this.mode();
    const paged = mode === "pages";
    const count = this.selected.size;
    const total = this.pages.length;
    this.count.textContent = `已选 ${count} 页`;
    this.allLink.textContent = count === total && total ? "取消全选" : "全选";
    this.allText.textContent = `全部 ${total} 页`;
    this.pickedText.textContent = this.pageMode === "picked" ? `所选的 ${count} 页` : "所选的页";
    this.formatSeg.sync();
    this.rangeSeg.sync();
    this.dpiSeg.sync();

    this.pagesPanel.hidden = !paged;
    this.wholePanel.hidden = mode !== "whole";
    this.filePanel.hidden = mode !== "file";
    if (mode === "whole") this.drawWhole();

    const shown = {
      note: mode === "file",
      range: mode !== "file",
      pages: paged,
      dpi: mode !== "file" && this.format === "png",
      paper: paged,
      paging: paged,
    };
    for (const [name, on] of Object.entries(shown)) {
      for (const node of this.rows[name] || []) node.hidden = !on;
    }

    this.allRadio.checked = this.pageMode === "all";
    this.customRadio.checked = this.pageMode === "custom";
    this.pickedRadio.checked = this.pageMode === "picked";
    if (this.pageField.value !== this.pageText) this.pageField.value = this.pageText;
    const error = this.pageMode === "custom" ? this.pageError : null;
    this.pageErrorLine.textContent = error || "";
    this.pageErrorLine.hidden = !error;
    this.pageField.toggleAttribute("aria-invalid", !!error);

    this.pixels.textContent = this.format === "png" ? this.pixelHint(mode) : "";
    if (!this.isDoc) {
      this.pagingText.textContent = this.adjusted
        ? `手动分成 ${this.pages.length} 页`
        : `自动分成 ${this.pages.length} 页`;
    }

    this.primary.textContent = { pdf: "导出 PDF", png: "导出 PNG", wbz: "导出 WBZ" }[this.format];
    const blocked = paged && (count === 0 || !!error);
    this.primary.toggleAttribute("disabled", blocked || !!this.busy);
  }

  /** PNG 的像素尺寸，和服务端同一个算法（含像素上限）。纸张跟随内容时各页不一样大，说最大的一页。 */
  pixelHint(mode) {
    if (mode === "whole") {
      if (!this.whole) return "";
      const [w, h] = pixelSize(this.whole, this.dpi);
      return `${w} × ${h} 像素`;
    }
    const sizes = this.pages.filter((page) => this.selected.has(page.key)).map((page) => pixelSize(page, this.dpi));
    if (!sizes.length) return "";
    const [w, h] = sizes.reduce((big, size) => (size[0] * size[1] > big[0] * big[1] ? size : big));
    const same = sizes.every(([sw, sh]) => sw === sizes[0][0] && sh === sizes[0][1]);
    return same ? `每页 ${w} × ${h} 像素` : `最大一页 ${w} × ${h} 像素`;
  }

  // ---------------------------------------------------------- 调整页框

  adjust() {
    this.scrim.hidden = true;
    this.adjustView = new AdjustView(this.host, this.pages.map((page) => ({ ...page })), {
      paper: this.paper === "content" ? null : PAPERS[this.paper],
      auto: async () => {
        const response = await fetch(
          `/api/export/${encodeURIComponent(this.boardId)}/pages?paper=${this.paper}`,
        );
        if (!response.ok) throw new Error((await response.text()) || String(response.status));
        return (await response.json()).pages.map(withKey);
      },
      done: (frames, { changed, reset }) => {
        this.adjustView = null;
        this.scrim.hidden = false;
        // 在调整视图里点过「自动分页」就从自动的结果算起，之后再动过才算手动调整
        this.setPages(frames, changed || (!reset && this.adjusted), true);
      },
    });
  }

  // ---------------------------------------------------------- 导出

  async export() {
    if (this.busy || this.primary.disabled) return;
    const mode = this.mode();
    let frames = this.pages;
    if (mode === "whole") frames = "whole";
    else if (this.isDoc) frames = null;
    const body = exportRequest({
      format: this.format,
      frames,
      selected: this.selectedIndices(),
      paper: this.paper,
      dpi: this.dpi,
    });
    this.busy = true;
    this.refresh();
    const done = this.ui.message("正在导出…", "download", 120000);
    try {
      const api = this.host.native();
      if (api && api.export_doc) {
        const path = await api.export_doc(this.boardId, body);
        done();
        if (path) {
          this.close();
          this.ui.toast("check");
        } else if (path === false) {
          this.ui.message("导出失败", "close", 6000);
        }
        return;
      }
      const response = await fetch(`/api/export/${encodeURIComponent(this.boardId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error((await response.text()) || String(response.status));
      const name = filenameOf(response.headers.get("Content-Disposition"), `whiteboard.${body.format}`);
      const url = URL.createObjectURL(await response.blob());
      downloadURL(url, name);
      // 立刻撤销的话有的浏览器还没来得及读，留一分钟再收
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      done();
      this.close();
      this.ui.toast("check");
    } catch (err) {
      done();
      reportError("导出失败", { message: String(err) });
      this.ui.message("导出失败", "close", 6000);
    } finally {
      this.busy = false;
      if (!this.closed) this.refresh();
    }
  }
}

/** 分段控件：``items`` 是 [文字, 值]，``current()`` 取当前值，``pick(值)`` 改值。 */
function segmented(items, current, pick, label) {
  const node = el("div", { class: "seg export-seg", role: "radiogroup", "aria-label": label });
  const buttons = items.map(([label, value]) =>
    el("button", { role: "radio", text: label, "data-value": String(value), onclick: () => pick(value) }),
  );
  node.append(...buttons);
  const sync = () => {
    buttons.forEach((button, index) => {
      const on = items[index][1] === current();
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-checked", String(on));
    });
  };
  sync();
  return { node, sync };
}

function chevron() {
  return `<svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 4.5 6 2l2.5 2.5M3.5 7.5 6 10l2.5-2.5"/></svg>`;
}

// ============================================================ 调整页框

const CORNERS = ["nw", "ne", "sw", "se"];
// 在白板上拖出页框：拖动不到这么远（屏幕像素）当成点了一下
const DRAW_MIN = 8;

/**
 * 调整页框：对话框藏起来，白板上画出每一页的框。点一下选中，拖框内移动，拖四个角
 * 缩放（选了纸张时保持比例），右上角的按钮删掉这一页。在白板空白处单指拖动拖出一个
 * 新页框（见 drawHandler）；两指、滚轮和触控板照常平移缩放。这期间不落笔（见 app.js
 * 的 setExportAdjusting / updateReadOnly）。
 *
 * ``paper`` 是选中的纸张 [宽, 高]（pt），纸张跟随内容时为 null。
 */
class AdjustView {
  constructor(host, frames, { paper, auto, done }) {
    this.host = host;
    this.frames = frames;
    this.paper = paper;
    this.auto = auto;
    this.done = done;
    this.changed = false;
    this.reset = false;
    this.selectedKey = null;
    // 页框可能在视口外面（或者比视口大）：先让全部页框都看得见，结束时回到原来的视图。
    // 要在画页框之前调好视口，否则页框先按旧视口画出来，下一帧才跳到位
    if (frames.length) {
      const bounds = frames.reduce(
        (box, f) => ({
          x0: Math.min(box.x0, f.x),
          y0: Math.min(box.y0, f.y),
          x1: Math.max(box.x1, f.x + f.w),
          y1: Math.max(box.y1, f.y + f.h),
        }),
        { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity },
      );
      this.savedView = host.showExportArea(bounds);
    }
    // 正在拖出的页框：{start: 白板坐标, from / last: 屏幕坐标, active: 拖得够远了}
    this.draft = null;
    this.build();
    host.setExportAdjusting(true, this.drawHandler());
    this.tick = this.tick.bind(this);
    this.raf = requestAnimationFrame(this.tick);
  }

  build() {
    // 白板上除了页框以外的地方蒙一层：用遮罩挖掉页框，页框叠在一起的地方也是挖空的
    // （even-odd 的路径在两个框重叠处会被算成「在外面」又蒙上）
    const svg = (tag, attrs = {}) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      return node;
    };
    const maskId = `export-veil-${nextKey++}`;
    this.veilHoles = svg("g");
    this.veil = svg("svg", { class: "export-veil" });
    this.veil.append(
      svg("defs"),
      svg("rect", { class: "export-veil-fill", x: "0", y: "0", width: "100%", height: "100%", mask: `url(#${maskId})` }),
    );
    const mask = svg("mask", { id: maskId });
    mask.append(svg("rect", { x: "0", y: "0", width: "100%", height: "100%", fill: "#fff" }), this.veilHoles);
    this.veil.firstChild.append(mask);
    this.svg = svg;
    this.frameLayer = el("div", { class: "export-frames" });
    this.bar = el("div", { class: "pill export-bar" }, [
      el("button", { class: "bar-btn", text: "清空", onclick: () => this.clear() }),
      el("button", { class: "bar-btn", text: "自动分页", onclick: () => this.reauto() }),
      el("button", { class: "bar-btn", text: "添加一页", onclick: () => this.addPage() }),
      el("div", { class: "sep" }),
      el("button", { class: "bar-btn primary", text: "完成", onclick: () => this.finish() }),
    ]);
    this.hint = el("div", { class: "export-adjust-hint", text: "在白板上拖出页框" });
    this.layer = el("div", { class: "export-adjust" }, [this.veil, this.frameLayer, this.bar, this.hint]);
    this.host.ui.root.append(this.layer);
    this.renderFrames();
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.layer.remove();
    this.host.setExportAdjusting(false);
    if (this.savedView) this.host.restoreExportView(this.savedView);
  }

  finish() {
    this.destroy();
    this.done(this.frames, { changed: this.changed, reset: this.reset });
  }

  async reauto() {
    try {
      this.frames = await this.auto();
    } catch (err) {
      reportError("取导出页框失败", { message: String(err) });
      this.host.ui.message("导出失败", "close", 6000);
      return;
    }
    this.changed = false;
    this.reset = true;
    this.selectedKey = null;
    this.renderFrames();
  }

  clear() {
    this.frames = [];
    this.selectedKey = null;
    this.changed = true;
    this.renderFrames();
  }

  /**
   * 交给白板输入（InputController.setClaim）的处理器：白板空白处的单指拖动从按下的点
   * 拖出一个页框，松开就加上；拖得太短当成点了一下，取消选中。页框和按钮条自己接
   * 指针事件，按在它们上面不会走到这里。
   */
  drawHandler() {
    const screen = (event) => [event.clientX, event.clientY];
    return {
      down: (event) => {
        this.draft = { start: this.host.toWorld(event.clientX, event.clientY), from: screen(event), last: screen(event), active: false };
        return true;
      },
      move: (event) => {
        const draft = this.draft;
        if (!draft) return;
        draft.last = screen(event);
        if (!draft.active && Math.hypot(draft.last[0] - draft.from[0], draft.last[1] - draft.from[1]) >= DRAW_MIN) {
          draft.active = true;
          this.selectedKey = null;
          this.renderFrames();
          return;
        }
        if (draft.active) this.layout();
      },
      end: (event) => {
        const draft = this.draft;
        this.draft = null;
        if (!draft || this.destroyed) return;
        if (event) draft.last = screen(event);
        if (draft.active) {
          const frame = withKey(this.draftFrame(draft));
          this.frames.push(frame);
          this.selectedKey = frame.key;
          this.changed = true;
        } else {
          this.selectedKey = null;
        }
        this.renderFrames();
      },
      cancel: () => {
        const active = this.draft && this.draft.active;
        this.draft = null;
        if (active && !this.destroyed) this.renderFrames();
      },
    };
  }

  /** 正在拖出的页框（白板坐标）：起点固定在白板上，终点按现在的视口换算。 */
  draftFrame(draft) {
    const [x, y] = this.host.toWorld(draft.last[0], draft.last[1]);
    return drawnFrame(draft.start[0], draft.start[1], x, y, this.paper);
  }

  addPage() {
    const [cx, cy] = this.host.viewCenter();
    const template = this.frames.find((frame) => frame.key === this.selectedKey) || this.frames[0];
    const frame = withKey(frameAt(cx, cy, template, this.paper));
    this.frames.push(frame);
    this.selectedKey = frame.key;
    this.changed = true;
    this.renderFrames();
  }

  remove(key) {
    this.frames = this.frames.filter((frame) => frame.key !== key);
    if (this.selectedKey === key) this.selectedKey = null;
    this.changed = true;
    this.renderFrames();
  }

  renderFrames() {
    this.frameLayer.textContent = "";
    this.nodes = new Map();
    this.frames.forEach((frame, index) => {
      const selected = frame.key === this.selectedKey;
      const node = el("div", { class: `export-frame${selected ? " is-selected" : ""}`, "data-page": String(index + 1) }, [
        el("span", { class: "export-badge", text: String(index + 1) }),
      ]);
      node.addEventListener("pointerdown", (event) => this.startDrag(event, frame, "move"));
      if (selected) {
        for (const corner of CORNERS) {
          const handle = el("span", { class: `export-handle ${corner}`, "data-corner": corner });
          handle.addEventListener("pointerdown", (event) => this.startDrag(event, frame, corner));
          node.append(handle);
        }
        const remove = el("button", { class: "export-delete", title: "删除", "aria-label": "删除", html: icon("trash", 16) });
        remove.addEventListener("pointerdown", (event) => event.stopPropagation());
        remove.addEventListener("click", () => this.remove(frame.key));
        node.append(remove);
      }
      this.frameLayer.append(node);
      this.nodes.set(frame.key, node);
    });
    this.draftNode = null;
    if (this.draft && this.draft.active) {
      this.draftNode = el("div", { class: "export-frame is-drawing", "aria-label": "正在拖出的页框" }, [
        el("span", { class: "export-badge", text: String(this.frames.length + 1) }),
      ]);
      this.frameLayer.append(this.draftNode);
    }
    // 拖框的时候也收起：提示正好在页框页码的位置上
    this.hint.hidden = this.frames.length > 0 || Boolean(this.draftNode);
    this.lastView = null;
    this.layout();
  }

  /** 视口一动就重新摆页框：每帧比一下视口，没变就什么都不做。 */
  tick() {
    const { scale, x, y } = this.host.viewport;
    const [w, h] = this.host.viewSize();
    const key = `${scale} ${x} ${y} ${w} ${h}`;
    if (key !== this.lastView) this.layout();
    this.raf = requestAnimationFrame(this.tick);
  }

  layout() {
    const viewport = this.host.viewport;
    const [w, h] = this.host.viewSize();
    this.lastView = `${viewport.scale} ${viewport.x} ${viewport.y} ${w} ${h}`;
    const holes = [];
    const placed = this.frames.map((frame) => [frame, this.nodes.get(frame.key)]);
    if (this.draftNode && this.draft) placed.push([this.draftFrame(this.draft), this.draftNode]);
    for (const [frame, node] of placed) {
      const [sx, sy] = viewport.toScreen(frame.x, frame.y);
      const sw = frame.w * viewport.scale;
      const sh = frame.h * viewport.scale;
      if (node) {
        Object.assign(node.style, { left: `${sx}px`, top: `${sy}px`, width: `${sw}px`, height: `${sh}px` });
      }
      holes.push(this.svg("rect", { x: sx, y: sy, width: sw, height: sh, rx: 4, fill: "#000" }));
    }
    this.veil.setAttribute("width", String(w));
    this.veil.setAttribute("height", String(h));
    this.veilHoles.replaceChildren(...holes);
  }

  startDrag(event, frame, mode) {
    event.preventDefault();
    event.stopPropagation();
    if (this.selectedKey !== frame.key) {
      this.selectedKey = frame.key;
      this.renderFrames();
    }
    const node = this.nodes.get(frame.key);
    const target = event.currentTarget.isConnected ? event.currentTarget : node;
    try {
      target.setPointerCapture(event.pointerId);
    } catch (err) {
      // 刚重建的节点可能还没接上指针，捕获失败时靠 window 上的监听收尾
      reportError("页框拿不到指针捕获", { message: String(err) });
    }
    const start = { ...frame };
    const [x0, y0] = this.host.toWorld(event.clientX, event.clientY);
    const move = (moveEvent) => {
      if (moveEvent.pointerId !== event.pointerId) return;
      // 在窗口外松开时页面收不到 pointerup，回到窗口的第一下移动按键已经松了：就此结束
      if (moveEvent.buttons === 0) {
        end();
        return;
      }
      const [wx, wy] = this.host.toWorld(moveEvent.clientX, moveEvent.clientY);
      let next;
      if (mode === "move") next = { ...start, x: start.x + wx - x0, y: start.y + wy - y0 };
      else next = resizeFrame(start, mode, wx, wy, this.paper);
      const index = this.frames.findIndex((item) => item.key === frame.key);
      if (index < 0) return;
      this.frames[index] = { ...next, key: frame.key };
      this.changed = true;
      this.layout();
    };
    const end = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      target.removeEventListener("lostpointercapture", up);
    };
    const up = (upEvent) => {
      if (upEvent.pointerId === event.pointerId) end();
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
    target.addEventListener("lostpointercapture", up);
  }
}

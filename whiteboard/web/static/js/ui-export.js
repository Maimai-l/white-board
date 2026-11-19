// 导出对话框和调整页框的视图（docs/backlog/001-export-redesign.md）。
//
// 分页和出文件都在服务端（POST /api/export/{board}，见 docs/protocol.md「分页导出」）；
// 这里只管：取自动页框、在本机画缩略图、选页、选格式和清晰度、调整页框，最后把
// 页框和选中的页交给服务端。
//
// ``host`` 是白板应用（app.js 的 App）：用它的 state / viewport 画缩略图和页框，
// 用它的 ui 弹提示，用 host.native() 判断是不是 Mac 窗口（那边走原生的保存对话框）。

import { docOf } from "./boards.js";
import { icon } from "./icons.js";
import {
  PAPERS,
  currentPage,
  exportRequest,
  filenameOf,
  frameAt,
  pixelSize,
  placement,
  resizeFrame,
} from "./export-pages.js";
import { downloadURL } from "/inksync/exporter.js";
import { Renderer } from "/inksync/renderer.js";
import { reportError } from "/inksync/report.js";
import { el } from "/inksync/util.js";

const THUMB_WIDTH = 200;
const DPI = [
  ["标准", 150],
  ["高", 300],
  ["最高", 600],
];
const PAPER_LABELS = { a4: "A4，自动横竖", letter: "Letter，自动横竖" };
const PAPER_NAMES = { a4: "A4", letter: "Letter" };
// 文档板的页图按这一档取，和白板上显示的档位共用缓存（layers.js 的 BUCKETS）
const DOC_THUMB_BUCKET = 640;

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
    this.dpi = 300;
    this.paper = "a4";
    this.pages = [];
    this.selected = new Set();
    this.adjusted = false;
    this.images = new Map();
    this.build();
  }

  // ---------------------------------------------------------- 结构

  build() {
    this.count = el("span", { class: "export-count" });
    const head = el("div", { class: "export-pages-head" }, [
      this.count,
      el("span", { class: "spacer" }),
      el("button", { class: "text-btn", text: "全选", onclick: () => this.selectAll() }),
      el("button", { class: "text-btn", text: "当前页", onclick: () => this.selectCurrent() }),
    ]);
    this.cards = el("div", { class: "export-cards" });
    const pagesColumn = el("div", { class: "export-pages" }, [head, this.cards]);

    this.formatSeg = segmented(
      [["PDF", "pdf"], ["PNG", "png"]],
      () => this.format,
      (value) => {
        this.format = value;
        this.refresh();
      },
    );
    this.dpiSeg = segmented(
      DPI,
      () => this.dpi,
      (value) => {
        this.dpi = value;
        this.refresh();
      },
    );
    this.pixels = el("div", { class: "export-hint" });
    this.dpiRow = [
      el("span", { class: "export-label", text: "清晰度" }),
      el("div", { class: "export-stack" }, [this.dpiSeg.node, this.pixels]),
    ];

    this.paperLabel = el("span", { text: PAPER_LABELS[this.paper] });
    const select = el("select", { "aria-label": "纸张" }, Object.keys(PAPERS).map(
      (key) => el("option", { value: key, text: PAPER_NAMES[key] }),
    ));
    select.value = this.paper;
    select.addEventListener("change", () => {
      this.paper = select.value;
      this.paperLabel.textContent = PAPER_LABELS[this.paper];
      this.loadAuto();
    });
    const paperButton = el("div", { class: "export-popup" }, [
      this.paperLabel,
      el("span", { class: "chevron", html: chevron() }),
      select,
    ]);
    this.paperRow = [el("span", { class: "export-label", text: "纸张" }), paperButton];

    this.pagingText = el("span");
    this.pagingRow = [
      el("span", { class: "export-label", text: "分页" }),
      el("div", { class: "export-paging" }, [
        this.pagingText,
        el("button", { class: "pill-btn", text: "调整", onclick: () => this.adjust() }),
      ]),
    ];

    this.options = el("div", { class: "export-options" });

    this.primary = el("button", { class: "pill-btn primary", onclick: () => this.export() });
    const footer = el("div", { class: "export-footer" }, [
      el("button", { class: "pill-btn", text: "取消", onclick: () => this.close() }),
      this.primary,
    ]);

    this.dialog = el("div", { class: "export-dialog", role: "dialog", "aria-label": "导出" }, [
      el("h2", { class: "export-title", text: "导出" }),
      el("div", { class: "export-body" }, [pagesColumn, this.options]),
      footer,
    ]);
    this.dialog.addEventListener("click", (event) => event.stopPropagation());
    this.scrim = el("div", { class: "scrim export-scrim" }, [this.dialog]);
    this.scrim.addEventListener("click", () => this.close());
  }

  async open() {
    this.ui.root.append(this.scrim);
    this.refresh();
    await this.loadAuto();
  }

  close() {
    this.closed = true;
    if (this.adjustView) this.adjustView.destroy();
    this.scrim.remove();
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
      this.ui.message(`导出失败：${String(err.message || err).slice(0, 80)}`, "close", 6000);
      return;
    }
    if (this.closed || paper !== this.paper) return;
    this.setPages(payload.pages, false);
  }

  setPages(pages, adjusted) {
    const before = new Map(this.pages.map((page) => [page.key, this.selected.has(page.key)]));
    this.pages = pages.map((page) => (page.key ? page : withKey(page)));
    // 原来就有的页保留选中与否，新出现的页默认选中
    this.selected = new Set(
      this.pages.filter((page) => before.get(page.key) !== false).map((page) => page.key),
    );
    this.adjusted = adjusted;
    this.renderCards();
    this.refresh();
  }

  selectAll() {
    this.selected = new Set(this.pages.map((page) => page.key));
    this.syncCards();
    this.refresh();
  }

  selectCurrent() {
    const [cx, cy] = this.host.viewCenter();
    const index = currentPage(this.pages, cx, cy);
    this.selected = new Set(index >= 0 ? [this.pages[index].key] : []);
    this.syncCards();
    this.refresh();
  }

  toggle(key) {
    if (this.selected.has(key)) this.selected.delete(key);
    else this.selected.add(key);
    this.syncCards();
    this.refresh();
  }

  selectedIndices() {
    return this.pages.flatMap((page, index) => (this.selected.has(page.key) ? [index] : []));
  }

  renderCards() {
    this.cards.textContent = "";
    this.cardNodes = new Map();
    this.pages.forEach((page, index) => {
      const [pw, ph] = page.paper;
      const height = Math.round((THUMB_WIDTH * ph) / pw);
      const canvas = el("canvas", { class: "export-canvas" });
      const thumb = el("div", { class: "export-thumb", style: { width: `${THUMB_WIDTH}px`, height: `${height}px` } }, [
        canvas,
      ]);
      const check = el("span", { class: "export-check", html: icon("check", 14) });
      const card = el(
        "button",
        { class: "export-card", "data-page": String(index + 1), onclick: () => this.toggle(page.key) },
        [el("div", { class: "export-thumb-wrap" }, [thumb, check]), el("span", { class: "export-caption", text: `第 ${index + 1} 页` })],
      );
      this.cards.append(card);
      this.cardNodes.set(page.key, { card, canvas, page, height });
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

  async drawThumb({ canvas, page, height }) {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.round(THUMB_WIDTH * ratio);
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

  refresh() {
    const count = this.selected.size;
    this.count.textContent = `已选 ${count} 页`;
    this.formatSeg.sync();
    this.dpiSeg.sync();
    const rows = [[el("span", { class: "export-label", text: "格式" }), this.formatSeg.node]];
    if (this.format === "png") {
      rows.push(this.dpiRow);
      const first = this.pages.find((page) => this.selected.has(page.key));
      if (first) {
        const [w, h] = pixelSize(first, this.dpi);
        this.pixels.textContent = `每页 ${w} × ${h} 像素`;
      } else {
        this.pixels.textContent = "";
      }
    }
    if (!this.isDoc) {
      rows.push(this.paperRow);
      this.pagingText.textContent = this.adjusted
        ? `手动调整过，${this.pages.length} 页`
        : `自动分成 ${this.pages.length} 页`;
      rows.push(this.pagingRow);
    }
    this.options.replaceChildren(...rows.flat());
    this.primary.textContent = this.format === "pdf" ? "导出 PDF" : "导出图片";
    this.primary.toggleAttribute("disabled", count === 0 || !!this.busy);
  }

  // ---------------------------------------------------------- 调整页框

  adjust() {
    this.scrim.hidden = true;
    this.adjustView = new AdjustView(this.host, this.pages.map((page) => ({ ...page })), {
      paper: PAPERS[this.paper],
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
        this.setPages(frames, changed || (!reset && this.adjusted));
      },
    });
  }

  // ---------------------------------------------------------- 导出

  async export() {
    const select = this.selectedIndices();
    if (!select.length || this.busy) return;
    const body = exportRequest({
      format: this.format,
      frames: this.isDoc ? null : this.pages,
      selected: select,
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
          this.ui.message("导出失败，日志里有详细原因", "close", 6000);
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
      this.ui.message(`导出失败：${String(err.message || err).slice(0, 80)}`, "close", 6000);
    } finally {
      this.busy = false;
      if (!this.closed) this.refresh();
    }
  }
}

/** 分段控件：``items`` 是 [文字, 值]，``current()`` 取当前值，``pick(值)`` 改值。 */
function segmented(items, current, pick) {
  const node = el("div", { class: "seg export-seg", role: "radiogroup" });
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

/**
 * 调整页框：对话框藏起来，白板上画出每一页的框。点一下选中，拖框内移动，拖四个角
 * 等比缩放，右上角的按钮删掉这一页。白板照常平移缩放（这期间不落笔，见 app.js 的
 * updateReadOnly）。
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
    this.build();
    host.setExportAdjusting(true);
    // 页框可能在视口外面（或者比视口大）：先让全部页框都看得见，结束时回到原来的视图
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
      el("button", { class: "bar-btn", text: "自动分页", onclick: () => this.reauto() }),
      el("button", { class: "bar-btn", text: "添加一页", onclick: () => this.addPage() }),
      el("div", { class: "sep" }),
      el("button", { class: "bar-btn primary", text: "完成", onclick: () => this.finish() }),
    ]);
    this.layer = el("div", { class: "export-adjust" }, [this.veil, this.frameLayer, this.bar]);
    this.host.ui.root.append(this.layer);
    this.renderFrames();
  }

  destroy() {
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
      this.host.ui.message(`导出失败：${String(err.message || err).slice(0, 80)}`, "close", 6000);
      return;
    }
    this.changed = false;
    this.reset = true;
    this.selectedKey = null;
    this.renderFrames();
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
    for (const frame of this.frames) {
      const [sx, sy] = viewport.toScreen(frame.x, frame.y);
      const sw = frame.w * viewport.scale;
      const sh = frame.h * viewport.scale;
      const node = this.nodes.get(frame.key);
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
      const [wx, wy] = this.host.toWorld(moveEvent.clientX, moveEvent.clientY);
      let next;
      if (mode === "move") next = { ...start, x: start.x + wx - x0, y: start.y + wy - y0 };
      else next = resizeFrame(start, mode, wx, wy);
      const index = this.frames.findIndex((item) => item.key === frame.key);
      if (index < 0) return;
      this.frames[index] = { ...this.frames[index], x: next.x, y: next.y, w: next.w, h: next.h };
      this.changed = true;
      this.layout();
    };
    const up = (upEvent) => {
      if (upEvent.pointerId !== event.pointerId) return;
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }
}

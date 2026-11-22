// 导入 PDF 时选页（docs/backlog/014-import-pages.md）。
//
// PDF 多于一页时，原件先在服务端暂存（/api/doc/stage），这里弹出一个和导出对话框一个样子的
// 对话框：左边一栏是原件每一页的缩略图，点一下选上或取消；右边选全部、范围（3-15）或边栏里
// 选的页，可以每页后面加一页空白留给批注。点导入才建白板；取消就把暂存的原件删掉。
//
// open() 返回一个 Promise：导入时是 ``{ pages: [原件页码…], blanks }``（页码从 0 起），取消是 null。

import { icon } from "./icons.js";
import { formatPages, parsePages } from "./page-range.js";
import { el } from "/inksync/util.js";
import { leave } from "./motion.js";

// 缩略图宽度（像素，两列）和从服务端要的图宽（高分屏上也清楚）
const THUMB_WIDTH = 98;
const THUMB_FETCH = 240;
// 一块白板最多多少页，和服务端的 models.MAX_DOC_PAGES 一样
const MAX_BOARD_PAGES = 400;

let nextGroup = 0;

export class ImportDialog {
  /** ``staged``：/api/doc/stage 的回复（stage、name、total、pages）。 */
  constructor(ui, staged) {
    this.ui = ui;
    this.staged = staged;
    this.total = staged.pages.length;
    this.selected = new Set(staged.pages.map((_page, index) => index));
    this.mode = "all";
    this.blanks = false;
    this.pageError = "";
    this.build();
  }

  build() {
    this.count = el("span", { class: "export-count" });
    this.allLink = el("button", { class: "text-btn", type: "button", onclick: () => this.toggleAll() });
    const head = el("div", { class: "export-pages-head" }, [this.count, el("span", { class: "spacer" }), this.allLink]);
    this.cards = el("div", { class: "export-cards is-grid" });
    this.renderCards();

    const group = `import-pages-${nextGroup++}`;
    this.allRadio = el("input", { type: "radio", name: group, value: "all" });
    this.customRadio = el("input", { type: "radio", name: group, value: "custom" });
    this.pickedRadio = el("input", { type: "radio", name: group, value: "picked" });
    this.allRadio.addEventListener("change", () => this.setMode("all"));
    this.customRadio.addEventListener("change", () => this.setMode("custom"));
    this.pickedRadio.addEventListener("change", () => this.setMode("picked"));
    this.pageField = el("input", {
      type: "text",
      class: "export-range-field",
      "aria-label": "页码",
      placeholder: "例如 3-15",
      autocomplete: "off",
      spellcheck: "false",
    });
    this.pageField.addEventListener("input", () => this.typePages(this.pageField.value));
    this.pageField.addEventListener("focus", () => {
      if (this.mode !== "custom") this.setMode("custom");
    });
    this.pageErrorLine = el("div", { class: "export-range-error", role: "alert" });
    const pages = el("div", { class: "export-range", role: "radiogroup", "aria-label": "页面" }, [
      el("label", { class: "export-radio" }, [this.allRadio, el("span", { text: `全部 ${this.total} 页` })]),
      el("label", { class: "export-radio" }, [this.customRadio, el("span", { text: "范围" }), this.pageField]),
      this.pageErrorLine,
      el("label", { class: "export-radio top" }, [
        this.pickedRadio,
        el("span", { class: "export-radio-text" }, [
          el("span", { text: "所选的页" }),
          el("span", { class: "export-radio-note", text: "从边栏选择页面" }),
        ]),
      ]),
    ]);
    this.blankBox = el("input", { type: "checkbox" });
    this.blankBox.addEventListener("change", () => {
      this.blanks = this.blankBox.checked;
      this.refresh();
    });
    const blank = el("label", { class: "export-radio" }, [this.blankBox, el("span", { text: "每页后面加一页空白" })]);

    const label = (text) => el("span", { class: "export-label", text });
    const options = el("div", { class: "export-options" }, [label("页面"), pages, label("空白页"), blank]);
    this.primary = el("button", { class: "lib-btn primary", type: "button", onclick: () => this.finish(true) });
    const footer = el("div", { class: "export-footer" }, [
      el("button", { class: "lib-btn", type: "button", text: "取消", onclick: () => this.finish(false) }),
      this.primary,
    ]);
    this.dialog = el("div", { class: "export-dialog import-dialog", role: "dialog", "aria-label": "导入" }, [
      el("div", { class: "export-side" }, [el("div", { class: "export-pages" }, [head, this.cards])]),
      el("div", { class: "export-main" }, [
        el("h2", { class: "export-title" }, [
          el("span", { text: "导入" }),
          el("span", { class: "export-board-name", text: this.staged.name }),
        ]),
        options,
      ]),
      footer,
    ]);
    this.dialog.addEventListener("click", (event) => event.stopPropagation());
    this.scrim = el("div", { class: "scrim export-scrim" }, [this.dialog]);
    this.scrim.addEventListener("click", () => this.finish(false));
    this.keys = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault();
        this.finish(false);
      } else if (event.key === "Enter" && !this.primary.disabled && event.target !== this.pageField) {
        event.preventDefault();
        this.finish(true);
      }
    };
  }

  /** 两列缩略图：图按需加载（浏览器自己的懒加载），页下沿是勾选胶囊和页码。 */
  renderCards() {
    this.cardNodes = this.staged.pages.map(([width, height], index) => {
      const thumbHeight = Math.round((THUMB_WIDTH * height) / width);
      const image = el("img", {
        class: "export-thumb-image",
        alt: "",
        loading: "lazy",
        decoding: "async",
        width: String(THUMB_WIDTH),
        height: String(thumbHeight),
        src: `/api/doc/stage/${encodeURIComponent(this.staged.stage)}/${index}?w=${THUMB_FETCH}`,
      });
      const thumb = el("div", { class: "export-thumb", style: { width: `${THUMB_WIDTH}px`, height: `${thumbHeight}px` } }, [image]);
      const check = el("span", { class: "export-check" }, [
        el("span", { class: "export-check-box", html: icon("check", 10) }),
        el("span", { class: "export-caption", text: String(index + 1) }),
      ]);
      const card = el(
        "button",
        { class: "export-card", type: "button", "data-page": String(index + 1), "aria-label": `第 ${index + 1} 页`, onclick: () => this.toggle(index) },
        [el("div", { class: "export-thumb-wrap" }, [thumb, check])]
      );
      this.cards.append(card);
      return card;
    });
  }

  setMode(mode) {
    this.mode = mode;
    if (mode === "all") {
      this.selected = new Set(this.staged.pages.map((_page, index) => index));
      this.pageError = "";
    } else if (mode === "custom") {
      this.typePages(this.pageField.value);
      return;
    } else {
      this.pageError = "";
    }
    this.refresh();
  }

  typePages(text) {
    this.mode = "custom";
    const { pages, error } = parsePages(text, this.total);
    this.pageError = text.trim() ? error : "";
    if (!error) this.selected = new Set(pages);
    this.refresh();
  }

  /** 点缩略图：切到所选的页，选上或取消这一页。 */
  toggle(index) {
    if (this.selected.has(index)) this.selected.delete(index);
    else this.selected.add(index);
    this.mode = this.selected.size === this.total ? "all" : "picked";
    this.pageError = "";
    this.refresh();
  }

  toggleAll() {
    if (this.selected.size === this.total) {
      this.selected = new Set();
      this.mode = "picked";
    } else {
      this.selected = new Set(this.staged.pages.map((_page, index) => index));
      this.mode = "all";
    }
    this.pageError = "";
    this.refresh();
  }

  refresh() {
    const count = this.selected.size;
    this.allRadio.checked = this.mode === "all";
    this.customRadio.checked = this.mode === "custom";
    this.pickedRadio.checked = this.mode === "picked";
    if (this.mode !== "custom" && document.activeElement !== this.pageField) {
      this.pageField.value = this.mode === "all" || !count ? "" : formatPages([...this.selected].sort((a, b) => a - b));
    }
    this.cardNodes.forEach((card, index) => {
      const on = this.selected.has(index);
      card.classList.toggle("is-selected", on);
      card.setAttribute("aria-pressed", String(on));
    });
    this.count.textContent = `已选 ${count} 页`;
    this.allLink.textContent = count === this.total ? "取消全选" : "全选";
    const boardPages = count * (this.blanks ? 2 : 1);
    const tooMany = boardPages > MAX_BOARD_PAGES;
    this.pageErrorLine.textContent = this.pageError || (tooMany ? `一块白板最多 ${MAX_BOARD_PAGES} 页` : "");
    this.pageErrorLine.hidden = !this.pageErrorLine.textContent;
    this.primary.textContent = `导入 ${count} 页`;
    this.primary.disabled = !count || !!this.pageError || tooMany;
  }

  open() {
    this.ui.root.append(this.scrim);
    addEventListener("keydown", this.keys, true);
    this.refresh();
    this.primary.focus();
    return new Promise((resolve) => {
      this.resolve = resolve;
    });
  }

  finish(ok) {
    if (!this.resolve) return;
    removeEventListener("keydown", this.keys, true);
    leave(this.scrim);
    const resolve = this.resolve;
    this.resolve = null;
    resolve(ok ? { pages: [...this.selected].sort((a, b) => a - b), blanks: this.blanks } : null);
  }
}

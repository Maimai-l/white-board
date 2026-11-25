// 文档白板右边的页码滑条（docs/backlog/015-page-scrubber.md）。
//
// 几百页的 PDF 要翻到后面某一页，一路滚太慢。屏幕右边一条滑条，和 iPad 上「图书」、PDF
// 阅读器的一样：
//
// - 平时看不到；视野一动（滚动、缩放）出现一根细滑块，停下 FADE_MS 后淡出。
// - 指针移到右边（Mac）或按住滑块：出现轨道，滑块变粗；上下拖，页面跟着走，旁边显示
//   「12 / 300」；松手停在那一页的顶上。点轨道直接跳到那一页。
// - 看不到的时候不接任何输入：贴着右边写字不会被它截走。滑块在的时候（刚滚过、指针在
//   右边），手指和 Pencil 都能拖它，拖的时候不写字（它在 #ui 里，不在 #stage 上）。
// - 拖的时候一路经过几十页，不给每一页都去要图（ImageLayers.hold），停下再取。
//
// 滑块的位置就是视野中心在全部页面里的比例：拖到哪儿，视野中心就在哪儿，两者一直对得上。

import { el } from "/inksync/util.js";

// 视野停下多久之后细滑块淡出（和 iOS 的滚动条差不多）
const FADE_MS = 1500;
// 指针离右边多近算「移到右边」（Mac）
const EDGE_PX = 28;
// 指针离开右边之后，轨道再留多久
const LEAVE_MS = 600;
// 松手停在那一页顶上时，页上沿离屏幕上沿留多少（屏幕像素）
const TOP_GAP = 16;

export class PageScrubber {
  /** ``app``：白板应用（InkPad 的子类），滑条读它的视野和页面，改它的视野。 */
  constructor(app) {
    this.app = app;
    this.state = "hidden"; // hidden | scroll（细滑块）| active（轨道 + 粗滑块）
    this.sheets = [];
    this.dragging = null;
    this.fadeTimer = 0;
    this.leaveTimer = 0;
    this.build();
  }

  build() {
    this.thumb = el("div", { class: "scrubber-thumb" });
    this.hit = el(
      "div",
      { class: "scrubber-hit", role: "slider", "aria-label": "页码", "aria-valuemin": "1", tabindex: "-1" },
      [this.thumb]
    );
    this.track = el("div", { class: "scrubber-track" });
    this.count = el("span", { class: "scrubber-page" });
    this.total = el("span", { class: "scrubber-total" });
    this.bubble = el("div", { class: "scrubber-bubble", "aria-hidden": "true" }, [this.count, this.total]);
    this.node = el("div", { class: "scrubber", "data-state": "hidden", hidden: true }, [this.track, this.hit, this.bubble]);
    this.app.ui.root.append(this.node);

    this.hit.addEventListener("pointerdown", (event) => this.grab(event, false));
    this.track.addEventListener("pointerdown", (event) => this.grab(event, true));
    addEventListener("pointermove", (event) => this.hover(event), { passive: true });
  }

  // ------------------------------------------------------------------ 页面

  /** 换了白板或页变了：只有多于一页的文档板才有滑条。 */
  setBoard() {
    const app = this.app;
    const meta = app.state.meta;
    const isDoc = !!(meta && meta.data && meta.data.doc);
    this.sheets = isDoc ? app.state.layers.filter((layer) => layer.sheet) : [];
    const on = this.sheets.length > 1;
    this.node.hidden = !on;
    if (!on) {
      this.endDrag();
      this.setState("hidden");
      return;
    }
    this.hit.setAttribute("aria-valuemax", String(this.sheets.length));
    this.total.textContent = `/ ${this.sheets.length}`;
    this.place();
  }

  /** 全部页面在世界坐标里的上沿和下沿。 */
  span() {
    const first = this.sheets[0];
    const last = this.sheets[this.sheets.length - 1];
    return { top: first.y, bottom: last.y + (last.height || last.width) };
  }

  /** 视野中心在全部页面里的比例（0 是第一页顶上，1 是最后一页底下）。 */
  fraction() {
    const { top, bottom } = this.span();
    const [, cy] = this.app.viewCenter();
    return Math.min(1, Math.max(0, (cy - top) / Math.max(1, bottom - top)));
  }

  /** 世界坐标 ``y`` 所在（或最近）的那一页，从 0 起。 */
  pageAt(y) {
    let best = 0;
    let bestDistance = Infinity;
    this.sheets.forEach((layer, index) => {
      const top = layer.y;
      const bottom = layer.y + (layer.height || layer.width);
      const distance = y < top ? top - y : y > bottom ? y - bottom : 0;
      if (distance < bestDistance) {
        best = index;
        bestDistance = distance;
      }
    });
    return best;
  }

  // ------------------------------------------------------------------ 显示

  /** 视野变了（app 的 view 事件）：滑块跟着走，没在拖也没指着的话亮一下再淡出。 */
  onView() {
    if (this.node.hidden) return;
    this.place();
    if (this.dragging || this.state === "active") return;
    this.setState("scroll");
    clearTimeout(this.fadeTimer);
    this.fadeTimer = setTimeout(() => {
      if (!this.dragging && this.state === "scroll") this.setState("hidden");
    }, FADE_MS);
  }

  setState(state) {
    this.state = state;
    this.node.dataset.state = state;
  }

  /** 按视野摆滑块，更新页码。 */
  place() {
    if (!this.sheets.length) return;
    const trackHeight = this.node.clientHeight;
    const thumbHeight = this.hit.offsetHeight;
    const fraction = this.dragging ? this.dragging.fraction : this.fraction();
    const top = Math.round(fraction * Math.max(0, trackHeight - thumbHeight));
    this.hit.style.transform = `translateY(${top}px)`;
    this.bubble.style.transform = `translateY(${top + (thumbHeight - this.bubble.offsetHeight) / 2}px)`;
    const { top: first, bottom } = this.span();
    const page = this.pageAt(first + fraction * (bottom - first)) + 1;
    this.count.textContent = String(page);
    this.hit.setAttribute("aria-valuenow", String(page));
  }

  /** Mac：指针移到右边，出现轨道；离开一会儿再收起。 */
  hover(event) {
    if (event.pointerType !== "mouse" || this.node.hidden || this.dragging) return;
    const box = this.node.getBoundingClientRect();
    const near = event.clientX >= innerWidth - EDGE_PX && event.clientY >= box.top && event.clientY <= box.bottom;
    if (near) {
      clearTimeout(this.leaveTimer);
      clearTimeout(this.fadeTimer);
      if (this.state !== "active") {
        this.setState("active");
        this.place();
      }
    } else if (this.state === "active" && !this.leaveTimer) {
      this.leaveTimer = setTimeout(() => {
        this.leaveTimer = 0;
        if (!this.dragging && this.state === "active") this.setState("hidden");
      }, LEAVE_MS);
    }
  }

  // ------------------------------------------------------------------ 拖

  /** 按住滑块（或点轨道）：开始拖。``jump`` 时先跳到按下的位置。 */
  grab(event, jump) {
    if (this.node.hidden || this.state === "hidden" || event.button > 0) return;
    event.preventDefault();
    event.stopPropagation();
    clearTimeout(this.fadeTimer);
    clearTimeout(this.leaveTimer);
    this.leaveTimer = 0;
    const box = this.node.getBoundingClientRect();
    const thumbBox = this.hit.getBoundingClientRect();
    // 按在滑块上：手指相对滑块的位置不变，滑块不会一下跳到手指中间
    const offset = jump ? this.hit.offsetHeight / 2 : event.clientY - thumbBox.top;
    this.dragging = { pointerId: event.pointerId, offset, box, fraction: this.fraction() };
    this.app.renderer.images.hold(true);
    this.setState("active");
    this.node.classList.add("dragging");
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch (err) {
      // 忽略：拿不到捕获也能拖（下面挂在 window 上），只是指针移出窗口后收不到
    }
    const move = (moved) => {
      if (moved.pointerId === this.dragging?.pointerId) this.drag(moved.clientY);
    };
    const up = (lifted) => {
      if (lifted.pointerId !== this.dragging?.pointerId) return;
      removeEventListener("pointermove", move, true);
      removeEventListener("pointerup", up, true);
      removeEventListener("pointercancel", up, true);
      this.drop();
    };
    addEventListener("pointermove", move, true);
    addEventListener("pointerup", up, true);
    addEventListener("pointercancel", up, true);
    this.drag(event.clientY);
  }

  /** 拖到 ``clientY``：视野中心跟到对应的位置，页面跟着走。 */
  drag(clientY) {
    const drag = this.dragging;
    if (!drag) return;
    const room = Math.max(1, drag.box.height - this.hit.offsetHeight);
    const fraction = Math.min(1, Math.max(0, (clientY - drag.offset - drag.box.top) / room));
    drag.fraction = fraction;
    const { top, bottom } = this.span();
    this.centerOn(top + fraction * (bottom - top));
    this.place();
  }

  /** 松手：停在那一页的顶上，恢复取图。 */
  drop() {
    const drag = this.dragging;
    if (!drag) return;
    const { top, bottom } = this.span();
    const page = this.sheets[this.pageAt(top + drag.fraction * (bottom - top))];
    this.endDrag();
    this.showTop(page);
    this.setState("scroll");
    this.onView();
  }

  endDrag() {
    if (!this.dragging) return;
    this.dragging = null;
    this.node.classList.remove("dragging");
    this.app.renderer.images.hold(false);
  }

  // ------------------------------------------------------------------ 视野

  centerOn(worldY) {
    const app = this.app;
    const v = app.viewport;
    v.y = app.renderer.viewH / 2 - worldY * v.scale;
    this.applyView();
  }

  /** 这一页的上沿放到屏幕上沿往下一点。 */
  showTop(layer) {
    const app = this.app;
    const v = app.viewport;
    v.y = TOP_GAP - layer.y * v.scale;
    this.applyView();
  }

  applyView() {
    const app = this.app;
    app.stopViewAnimation();
    app.clampView();
    app.renderer.requestFull();
    app.saveView();
  }
}

// 笔具盘（beta）的位置控制。
//
// 照着 iPad PencilKit 那个工具面板的用法：整条栏可以拖，松手贴到最近的一边；
// 丢到屏幕角落就缩成一个圆，圆上显示当前用的那支笔，点一下再展开。写字时把它
// 推到角落，整块画布就空出来了。
//
// 只管位置，不管里面放什么——工具按钮由 ui.js 填。

import { el } from "./util.js";

export const DOCKS = ["bottom", "top", "left", "right"];
export const CORNERS = ["tl", "tr", "bl", "br"];

const DOCK_KEY = "whiteboard.toolbar";
const DRAG_SLOP = 10; // 手指抖一下不算拖
const CORNER_ZONE = 150; // 离两边都这么近就算丢进角落了
const EDGE = 14;

/** 读上次停的位置；老版本存的是 "top" / "bottom" 这样的字符串。 */
export function loadDock() {
  const fallback = { dock: "bottom", minimized: false, corner: "br" };
  try {
    const raw = localStorage.getItem(DOCK_KEY);
    if (!raw) return fallback;
    if (raw === "top" || raw === "bottom") return { ...fallback, dock: raw };
    const saved = JSON.parse(raw);
    return {
      dock: DOCKS.includes(saved.dock) ? saved.dock : "bottom",
      minimized: !!saved.minimized,
      corner: CORNERS.includes(saved.corner) ? saved.corner : "br",
    };
  } catch (err) {
    return fallback;
  }
}

function saveDock(state) {
  try {
    localStorage.setItem(DOCK_KEY, JSON.stringify(state));
  } catch (err) {
    /* 记不住就下次回到默认位置 */
  }
}

export class ToolDock {
  constructor({ root, bar, renderBubble }) {
    this.root = root;
    this.bar = bar;
    this.renderBubble = renderBubble || (() => "");
    const saved = loadDock();
    this.dock = saved.dock;
    this.minimized = saved.minimized;
    this.corner = saved.corner;
    this.dragging = null;
    this.bubble = el("button", {
      id: "toolbubble",
      class: "icon-btn",
      title: "展开工具栏",
      "aria-label": "展开工具栏",
      onclick: () => this.expand(),
    });
    root.append(this.bubble);
    this._onMove = (event) => this.onMove(event);
    this._onUp = (event) => this.onUp(event);
  }

  get state() {
    return { dock: this.dock, minimized: this.minimized, corner: this.corner };
  }

  /** 把当前状态写到 <html> 上，CSS 据此摆位置。 */
  apply(patch = {}, remember = true) {
    if (patch.dock && DOCKS.includes(patch.dock)) this.dock = patch.dock;
    if (patch.corner && CORNERS.includes(patch.corner)) this.corner = patch.corner;
    if (patch.minimized !== undefined) this.minimized = !!patch.minimized;
    const html = document.documentElement;
    html.dataset.toolbar = this.minimized ? "min" : this.dock;
    html.dataset.corner = this.corner;
    this.bar.style.left = "";
    this.bar.style.top = "";
    this.bar.style.transform = "";
    this.bar.classList.remove("dragging");
    this.refreshBubble();
    if (remember) saveDock(this.state);
  }

  refreshBubble() {
    this.bubble.innerHTML = this.renderBubble();
  }

  expand() {
    this.apply({ minimized: false });
  }

  /** 在上下之间换一下；从左右或者收起来的状态点，先回到底部。 */
  toggle() {
    if (this.minimized || this.dock === "left" || this.dock === "right") {
      this.apply({ dock: "bottom", minimized: false });
      return;
    }
    this.apply({ dock: this.dock === "top" ? "bottom" : "top" });
  }

  /** 只有笔具盘模式才允许拖动。 */
  enableDrag(on) {
    this.bar.classList.toggle("draggable", !!on);
    if (on && !this._down) {
      this._down = (event) => this.onDown(event);
      this.bar.addEventListener("pointerdown", this._down);
    } else if (!on && this._down) {
      this.bar.removeEventListener("pointerdown", this._down);
      this._down = null;
    }
  }

  onDown(event) {
    // 按在按钮上是点按钮，按在空白处才是拖整条
    if (event.button > 0 || event.target.closest("button")) return;
    const rect = this.bar.getBoundingClientRect();
    this.dragging = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      // 抓住的是栏上的哪一点，拖的时候保持这个相对位置
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
      moved: false,
    };
    addEventListener("pointermove", this._onMove);
    addEventListener("pointerup", this._onUp);
    addEventListener("pointercancel", this._onUp);
  }

  onMove(event) {
    const drag = this.dragging;
    if (!drag || drag.id !== event.pointerId) return;
    if (!drag.moved) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_SLOP) return;
      drag.moved = true;
      this.bar.classList.add("dragging");
    }
    event.preventDefault();
    this.bar.style.transform = "none";
    this.bar.style.left = `${event.clientX - drag.offsetX}px`;
    this.bar.style.top = `${event.clientY - drag.offsetY}px`;
  }

  onUp(event) {
    const drag = this.dragging;
    if (!drag || drag.id !== event.pointerId) return;
    this.dragging = null;
    removeEventListener("pointermove", this._onMove);
    removeEventListener("pointerup", this._onUp);
    removeEventListener("pointercancel", this._onUp);
    if (!drag.moved) return;
    this.drop(event.clientX, event.clientY);
  }

  /** 松手：离哪边近就贴哪边；两边都近（角落）就收成一个圆。 */
  drop(x, y) {
    const width = innerWidth;
    const height = innerHeight;
    const left = x;
    const right = width - x;
    const top = y;
    const bottom = height - y;
    const zone = Math.min(CORNER_ZONE, width / 4, height / 4);
    if (Math.min(left, right) < zone && Math.min(top, bottom) < zone) {
      this.apply({
        minimized: true,
        corner: (top < bottom ? "t" : "b") + (left < right ? "l" : "r"),
      });
      return;
    }
    const nearest = Math.min(left, right, top, bottom);
    const dock =
      nearest === bottom ? "bottom" : nearest === top ? "top" : nearest === left ? "left" : "right";
    this.apply({ dock, minimized: false });
  }

  destroy() {
    this.enableDrag(false);
    removeEventListener("pointermove", this._onMove);
    removeEventListener("pointerup", this._onUp);
    removeEventListener("pointercancel", this._onUp);
    this.bubble.remove();
  }
}

export { EDGE };

// 普通工具栏的位置：贴上边还是下边。状态写在 <html> 上，CSS 据此摆位置。
//
// 拖动、贴左右、收进角落这些是笔具盘（beta）的能力，那条工具盘自带一套，
// 见 pkpicker.js，这里不重复实现。

export const DOCKS = ["bottom", "top"];

const DOCK_KEY = "whiteboard.toolbar";

/** 读上次停的位置；老版本存的是 JSON，也可能是 "top" / "bottom" 这样的字符串。 */
export function loadDock() {
  try {
    const raw = localStorage.getItem(DOCK_KEY);
    if (!raw) return "bottom";
    if (DOCKS.includes(raw)) return raw;
    const saved = JSON.parse(raw);
    return DOCKS.includes(saved && saved.dock) ? saved.dock : "bottom";
  } catch (err) {
    return "bottom";
  }
}

export class ToolDock {
  constructor({ bar }) {
    this.bar = bar;
    this.dock = loadDock();
  }

  apply(dock = this.dock, remember = true) {
    this.dock = DOCKS.includes(dock) ? dock : "bottom";
    document.documentElement.dataset.toolbar = this.dock;
    if (!remember) return;
    try {
      localStorage.setItem(DOCK_KEY, this.dock);
    } catch (err) {
      /* 记不住就下次回到默认位置 */
    }
  }

  toggle() {
    this.apply(this.dock === "top" ? "bottom" : "top");
  }
}

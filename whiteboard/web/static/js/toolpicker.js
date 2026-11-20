// 普通工具栏的位置：横在底部还是竖在左边。状态写在 <html> 上，CSS 据此摆位置。
// Mac 上顶上是标题栏，工具栏不再往上放；老版本存的「上边」当成左边。
//
// 拖动、贴左右、收进角落这些是笔具盘（beta）的能力，那条工具盘自带一套，
// 见 pkpicker.js，这里不重复实现。

export const DOCKS = ["bottom", "left"];
const LEGACY = { top: "left" };

const DOCK_KEY = "whiteboard.toolbar";

/** 读上次停的位置；老版本存的是 JSON，也可能是 "top" / "bottom" 这样的字符串。 */
export function loadDock() {
  try {
    const raw = localStorage.getItem(DOCK_KEY);
    if (!raw) return "bottom";
    if (DOCKS.includes(raw)) return raw;
    if (LEGACY[raw]) return LEGACY[raw];
    const saved = JSON.parse(raw);
    const dock = saved && (LEGACY[saved.dock] || saved.dock);
    return DOCKS.includes(dock) ? dock : "bottom";
  } catch (err) {
    // 忽略：读不了或存的不是合法格式，就回到默认位置
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
      /* 忽略：记不住就下次回到默认位置 */
    }
  }

  toggle() {
    this.apply(this.dock === "left" ? "bottom" : "left");
  }
}

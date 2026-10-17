// 点工具栏图标（或按快捷键 Alt+Shift+S，即 _execute_action）：打开速写小窗口；已经开着就切到它。
// 小窗口放在当前浏览器窗口的右上角，大小与 iPad 横屏的比例接近（float.js 中可以展开、置顶）。

const MINI = { width: 380, height: 316 };

async function openWindowId() {
  try {
    const { floatWindow } = await chrome.storage.session.get("floatWindow");
    if (typeof floatWindow !== "number") return null;
    await chrome.windows.get(floatWindow);
    return floatWindow;
  } catch (err) {
    return null; // 记下的窗口已经关了
  }
}

async function toggleFloat() {
  const existing = await openWindowId();
  if (existing !== null) {
    await chrome.windows.update(existing, { focused: true, state: "normal" });
    return existing;
  }
  const options = { url: "float.html", type: "popup", ...MINI };
  try {
    const anchor = await chrome.windows.getLastFocused();
    if (typeof anchor.left === "number" && typeof anchor.width === "number") {
      options.left = Math.max(0, anchor.left + anchor.width - MINI.width - 24);
      options.top = Math.max(0, (anchor.top || 0) + 72);
    }
  } catch (err) {
    /* 取不到当前窗口就用默认位置 */
  }
  const created = await chrome.windows.create(options);
  await chrome.storage.session.set({ floatWindow: created.id });
  return created.id;
}

chrome.action.onClicked.addListener(() => {
  toggleFloat().catch((err) => console.error(err));
});

chrome.windows.onRemoved.addListener(async (windowId) => {
  const { floatWindow } = await chrome.storage.session.get("floatWindow");
  if (floatWindow === windowId) await chrome.storage.session.remove("floatWindow");
});

self.toggleFloat = toggleFloat; // 测试用

// 通用小工具。

export const TAU = Math.PI * 2;

export const clamp = (value, low, high) => (value < low ? low : value > high ? high : value);

export function uid(length = 10) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  const random = crypto.getRandomValues(new Uint8Array(length));
  for (const byte of random) out += chars[byte % chars.length];
  return out;
}

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

export function debounce(fn, ms) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** 供 JSON / IndexedDB 使用：去掉缓存字段（Path2D 不可序列化）。 */
export function plainStroke(stroke) {
  const plain = {
    id: stroke.id,
    tool: stroke.tool,
    color: stroke.color,
    w: stroke.w,
    p: stroke.p,
    n: stroke.n,
    dev: stroke.dev,
  };
  // 没被橡皮动过的笔画不带这两个字段，省得每一笔都多一个 0 和一个空数组
  if (stroke.cut) plain.cut = stroke.cut;
  if (stroke.m && stroke.m.length) plain.m = stroke.m.map((chain) => chain.slice());
  return plain;
}

/**
 * 键盘事件是不是打在一个能打字的地方（文本输入框、多行文本、可编辑区域）。
 * 白板的快捷键和「按住空格拖动」要让开：在改名框里按 ⌘Z 撤销的应该是打的字，
 * 不是白板上的上一笔。勾选框这类不能打字的输入框不算。
 */
export function isTextField(node) {
  if (!node || node.nodeType !== 1) return false;
  if (node.isContentEditable) return true;
  if (node.tagName === "TEXTAREA") return true;
  if (node.tagName !== "INPUT") return false;
  const type = (node.getAttribute("type") || "text").toLowerCase();
  return !["checkbox", "radio", "button", "submit", "reset", "file", "range", "color", "image", "hidden"].includes(type);
}

/**
 * 输入法正在组字（拼音还没选定候选词）时的按键。这时的回车是选候选词、Esc 是
 * 取消组字，都不该当成「确认」「放弃」处理。WebKit 有时把选词那一下的
 * isComposing 报成 false，所以同时看 keyCode 229。
 */
export function isComposing(event) {
  return !!event.isComposing || event.keyCode === 229;
}

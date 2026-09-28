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

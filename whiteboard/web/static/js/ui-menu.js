// 菜单：右键菜单、长按菜单、按钮弹出的菜单，样子照 macOS 的上下文菜单。
//
// showMenu(items, at) 在 at 处弹出一张菜单。items 的每一项：
//   { label, onSelect, shortcut, danger, disabled, checked, submenu: [...] }
//   "-" 是分隔线
// at 可以是 {x, y}（窗口坐标）或者一个元素（菜单贴着它开）。
// 同一时间只有一张菜单：再开一张会先关掉上一张。点菜单外面、按 Esc、
// 窗口失焦都会关上。

import { leave } from "./motion.js";
import { el } from "/inksync/util.js";

let open = null;

/** 关掉当前的菜单（没有就什么都不做）。 */
/**
 * 按 Esc 关掉菜单或面板之后，焦点还停在打开它的那个按钮上，浏览器会给它画一圈焦点框。
 * 顶栏、工具栏上的按钮是用鼠标点的，这一圈只是噪音，所以把焦点拿掉（和 macOS 一样）。
 */
export function dropChromeFocus() {
  const active = document.activeElement;
  if (active && active.closest && active.closest("#topbar, #toolbar, #topleft, #topright, #zoombar, .lib-head")) active.blur();
}

export function closeMenu() {
  if (!open) return;
  const current = open;
  open = null;
  current.teardown();
}

export function menuOpen() {
  return !!open;
}

function chevron() {
  return `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.5 4.5 10 8l-3.5 3.5"/></svg>`;
}

function check() {
  return `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.4 6.5 11.2 12.5 4.8"/></svg>`;
}

/** 一张菜单的 DOM。子菜单在指到（或点到）那一项时另开一张，挂在同一层。 */
function buildPanel(items, layer, depth, close) {
  const panel = el("div", { class: "menu", role: "menu" });
  panel.dataset.depth = String(depth);
  let child = null;
  const closeChild = () => {
    if (child) child.remove();
    child = null;
    for (const node of panel.querySelectorAll(".menu-item.open")) node.classList.remove("open");
  };
  const marked = items.some((item) => item && item !== "-" && item.checked !== undefined);
  for (const item of items) {
    if (!item) continue;
    if (item === "-") {
      panel.append(el("div", { class: "menu-sep", role: "separator" }));
      continue;
    }
    const row = el("button", {
      class: `menu-item${item.danger ? " danger" : ""}${marked ? " marked" : ""}`,
      type: "button",
      role: item.checked === undefined ? "menuitem" : "menuitemcheckbox",
    });
    if (item.checked !== undefined) row.setAttribute("aria-checked", String(!!item.checked));
    if (item.disabled) row.disabled = true;
    if (marked) row.append(el("span", { class: "menu-check", html: item.checked ? check() : "" }));
    row.append(el("span", { class: "menu-label", text: item.label }));
    if (item.shortcut) row.append(el("span", { class: "menu-shortcut", text: item.shortcut }));
    if (item.submenu) {
      row.setAttribute("aria-haspopup", "menu");
      row.append(el("span", { class: "menu-more", html: chevron() }));
      const openChild = () => {
        if (row.classList.contains("open")) return;
        closeChild();
        row.classList.add("open");
        child = buildPanel(item.submenu, layer, depth + 1, close);
        layer.append(child);
        const box = row.getBoundingClientRect();
        place(child, { x: box.right - 4, y: box.top - 5 }, { flipX: box.left + 4 });
      };
      row.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "mouse") openChild();
      });
      row.addEventListener("click", (event) => {
        event.stopPropagation();
        openChild();
      });
    } else {
      row.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "mouse") closeChild();
      });
      row.addEventListener("click", (event) => {
        event.stopPropagation();
        if (item.disabled) return;
        close();
        if (item.onSelect) item.onSelect();
      });
    }
    panel.append(row);
  }
  return panel;
}

/** 放在 at 处，超出窗口就往回挪；横向放不下而给了 flipX 就翻到另一边。 */
function place(panel, at, { flipX = null } = {}) {
  const width = panel.offsetWidth;
  const height = panel.offsetHeight;
  let x = at.x;
  if (x + width > innerWidth - 8) x = flipX !== null ? flipX - width : innerWidth - 8 - width;
  let y = at.y;
  if (y + height > innerHeight - 8) y = Math.max(8, innerHeight - 8 - height);
  panel.style.left = `${Math.max(8, x)}px`;
  panel.style.top = `${Math.max(8, y)}px`;
}

/**
 * 弹出菜单。返回关掉它的函数。``at`` 是元素时，菜单开在它下面、左边对齐；
 * 下面放不下就开在上面（工具栏上的按钮）。
 */
export function showMenu(items, at, { root = document.body, label = "", className = "" } = {}) {
  closeMenu();
  const layer = el("div", { class: `menu-layer${className ? ` ${className}` : ""}` });
  const close = () => {
    if (open && open.layer === layer) closeMenu();
  };
  const panel = buildPanel(items, layer, 0, close);
  if (label) panel.setAttribute("aria-label", label);
  layer.append(panel);
  root.append(layer);

  if (at instanceof Element) {
    const box = at.getBoundingClientRect();
    const below = box.bottom + 6;
    const fitsBelow = below + panel.offsetHeight < innerHeight - 8;
    place(panel, { x: box.left, y: fitsBelow ? below : box.top - 6 - panel.offsetHeight });
    // 进场动画从按钮那一角长出来（app.css 的 menu-in）
    if (!fitsBelow) panel.style.transformOrigin = "bottom left";
  } else {
    place(panel, at);
  }

  // 落在任何一张菜单（含子菜单）以外就关；这一下本身照常落到底下的东西上
  const outside = (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target || !target.closest(".menu") || !layer.contains(target)) close();
  };
  const key = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      close();
      dropChromeFocus();
    }
  };
  // 菜单都是在一次点按（或右键、长按）结束时打开的，那一下的按下已经过去了，
  // 所以马上开始听；晚一拍再听的话，手快时紧接着的那一下会漏掉
  addEventListener("pointerdown", outside, true);
  addEventListener("keydown", key, true);
  addEventListener("blur", close);
  open = {
    layer,
    teardown() {
      removeEventListener("pointerdown", outside, true);
      removeEventListener("keydown", key, true);
      removeEventListener("blur", close);
      leave(layer);
    },
  };
  return close;
}

// 关掉时的动效：菜单、面板、弹窗、提示条不是一下子没了，而是先淡出再从页面上拿掉。
//
// leave(node) 给节点加上 .leaving（app.css 里写了每种东西怎么退场），同时让它不再
// 接收点击和键盘（inert），退场动画走完再 remove()。淡出的那一小会儿里，底下的东西
// 照常能点。系统设置了「减弱动态效果」时直接拿掉。时长和 app.css 的 --t-leave 一致。
// 设计见 docs/design/motion.zh-CN.md。

export const LEAVE_MS = 140;

const reduced = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;

/** 让 node 退场。已经在退场或者不在页面上的，什么都不做。 */
export function leave(node) {
  if (!node || !node.isConnected || node.classList.contains("leaving")) return;
  if (reduced && reduced.matches) {
    node.remove();
    return;
  }
  node.classList.add("leaving");
  node.inert = true;
  setTimeout(() => node.remove(), LEAVE_MS);
}

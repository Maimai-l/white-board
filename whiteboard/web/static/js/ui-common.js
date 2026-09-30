// ui.js 和它拆出去的几个文件共用的小工具。

import { icon } from "./icons.js";
import { el } from "/inksync/util.js";

const KIND_NAMES = { board: "白板", note: "笔记", doc: "文档" };

/** 卡片上显示的名字。没起名就按延伸方式给个默认，文档板退回原件的文件名。 */
export function boardLabel(board) {
  if (board.name) return board.name;
  const doc = board.kind === "doc" ? board.doc : null;
  if (doc && doc.name) {
    const dot = doc.name.lastIndexOf(".");
    return dot > 0 ? doc.name.slice(0, dot) : doc.name;
  }
  return KIND_NAMES[board.kind] || KIND_NAMES.board;
}

export function iconButton(name, title, onClick, extraClass = "") {
  return el("button", {
    class: `icon-btn ${extraClass}`.trim(),
    title,
    "aria-label": title,
    html: icon(name),
    onclick: onClick,
  });
}

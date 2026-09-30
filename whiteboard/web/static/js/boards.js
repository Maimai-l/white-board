// 白板应用对白板元数据的理解（inksync 2.0 的元数据，见 whiteboard/models.py）：
//
//   data.folder  所在的文件夹
//   data.doc     文档板的原件信息 {type, name, ext, pages}
//   canvas.mode  column 是笔记，其余按大白板处理（文档板另外由 data.doc 判断）
//
// 界面上的代码读 kind / folder / doc 这几个名字，boardView 把它们补到元数据上。

import { thumbBlob } from "/inksync/exporter.js";

export function docOf(meta) {
  const doc = meta && meta.data && meta.data.doc;
  return doc && Array.isArray(doc.pages) && doc.pages.length ? doc : null;
}

export function folderOf(meta) {
  return (meta && meta.data && typeof meta.data.folder === "string" && meta.data.folder) || "";
}

export function kindOf(meta) {
  if (docOf(meta)) return "doc";
  return meta && meta.canvas && meta.canvas.mode === "column" ? "note" : "board";
}

export function patternOf(meta) {
  const background = meta && meta.background;
  if (typeof background === "string") return background;
  return (background && background.pattern) || "grid";
}

/** 界面用的元数据：补上 kind、folder、doc、pattern。 */
export function boardView(meta) {
  if (!meta) return meta;
  const view = { ...meta, kind: kindOf(meta), pattern: patternOf(meta) };
  const folder = folderOf(meta);
  if (folder) view.folder = folder;
  else delete view.folder;
  const doc = docOf(meta);
  if (doc) view.doc = doc;
  return view;
}

/** 上传白板选择界面用的缩略图（Mac 端）。文档板的缩略图由服务端渲染原件首页。 */
export async function uploadThumb(state, boardId) {
  if (!boardId || docOf(state.meta)) return false;
  try {
    const blob = await thumbBlob(state);
    if (!blob) return false;
    await fetch(`/api/thumb/${boardId}`, { method: "POST", body: blob });
    return true;
  } catch (err) {
    return false;
  }
}

const FINGER_FLAG = "whiteboard.fingerDraw";

/** 白板应用记住的「手指书写」设置（嵌入的手写板各自由宿主决定）。 */
export function loadFingerDraw() {
  try {
    return localStorage.getItem(FINGER_FLAG) === "1";
  } catch (err) {
    return false;
  }
}

export function saveFingerDraw(enabled) {
  try {
    localStorage.setItem(FINGER_FLAG, enabled ? "1" : "0");
  } catch (err) {
    /* 记不住就只在本次会话内生效 */
  }
}

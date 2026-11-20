// 白板应用对白板元数据的理解（inksync 0.2 的元数据，见 whiteboard/models.py）：
//
//   data.folder  所在的文件夹
//   data.doc     文档板的原件信息 {type, name, ext, pages}
//   canvas.mode  column 是笔记，其余按大白板处理（文档板另外由 data.doc 判断）
//
// 界面上的代码读 kind / folder / doc 这几个名字，boardView 把它们补到元数据上。

import { thumbBlob } from "/inksync/exporter.js";
import { reportError } from "/inksync/report.js";

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
    const response = await fetch(`/api/thumb/${boardId}`, { method: "POST", body: blob });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return true;
  } catch (err) {
    // 白板列表里只是还显示旧的缩略图，界面上看不出来，所以上报
    reportError("boards.uploadThumb 缩略图上传失败", { board: boardId, message: String(err) });
    return false;
  }
}

const FINGER_FLAG = "whiteboard.fingerDraw";

/** 白板应用记住的「手指书写」设置（嵌入的手写板各自由宿主决定）。 */
export function loadFingerDraw() {
  try {
    return localStorage.getItem(FINGER_FLAG) === "1";
  } catch (err) {
    // 忽略：读不了 localStorage（例如隐私模式）就按默认的关闭处理
    return false;
  }
}

export function saveFingerDraw(enabled) {
  try {
    localStorage.setItem(FINGER_FLAG, enabled ? "1" : "0");
  } catch (err) {
    /* 忽略：记不住就只在本次会话内生效 */
  }
}

const SHAPES_FLAG = "whiteboard.shapeLines";

/** 「形状识别」（停住变直线）：和手指书写一样是每台设备自己的输入偏好，默认关。 */
export function loadShapeLines() {
  try {
    return localStorage.getItem(SHAPES_FLAG) === "1";
  } catch (err) {
    console.warn("boards.js loadShapeLines：读不到 localStorage，按关闭处理", err);
    return false;
  }
}

export function saveShapeLines(enabled) {
  try {
    localStorage.setItem(SHAPES_FLAG, enabled ? "1" : "0");
  } catch (err) {
    console.warn("boards.js saveShapeLines：记不住，只在本次打开期间生效", err);
  }
}

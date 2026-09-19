// 导出 PNG 与生成白板缩略图。

import { Renderer } from "./renderer.js";

const MAX_EXPORT_PIXELS = 4096;
const THUMB_WIDTH = 420;

export function renderBoard(state, targetMax) {
  const [boardW, boardH] = state.size();
  const scale = Math.min(2, targetMax / Math.max(boardW, boardH));
  return Renderer.renderToCanvas(state, { scale: Math.max(0.05, scale) });
}

export function exportDataURL(state) {
  return renderBoard(state, MAX_EXPORT_PIXELS).toDataURL("image/png");
}

export function thumbBlob(state) {
  const [boardW] = state.size();
  const canvas = Renderer.renderToCanvas(state, {
    scale: THUMB_WIDTH / Math.max(1, boardW),
  });
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

export async function uploadThumb(state, boardId) {
  if (!boardId) return false;
  try {
    const blob = await thumbBlob(state);
    if (!blob) return false;
    await fetch(`/api/thumb/${boardId}`, { method: "POST", body: blob });
    return true;
  } catch (err) {
    return false;
  }
}

export function downloadDataURL(dataUrl, filename) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}

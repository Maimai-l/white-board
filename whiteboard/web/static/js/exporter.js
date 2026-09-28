// 导出 PNG 与生成白板缩略图。
//
// 画布是无限的，所以导出范围按内容外扩一圈决定；空白板导出一屏大小的空白。

import { Renderer } from "./renderer.js";

const MAX_EXPORT_PIXELS = 4096;
const THUMB_WIDTH = 420;
const MARGIN = 64;
const EMPTY_SIZE = 1200;

/** 内容范围（世界坐标）外扩一圈；白板为空时返回 null。 */
export function contentBounds(state, margin = MARGIN) {
  const bounds = state.contentBounds();
  if (!bounds) return null;
  return {
    x0: bounds.x0 - margin,
    y0: bounds.y0 - margin,
    x1: bounds.x1 + margin,
    y1: bounds.y1 + margin,
  };
}

function exportArea(state) {
  const limits = state.limits;
  const content = state.contentBounds();
  if (limits) {
    // 笔记：宽度就是页宽，纵向从页首到内容末尾。
    const width = limits.x1 - limits.x0;
    return {
      x0: limits.x0,
      y0: limits.y0,
      x1: limits.x1,
      y1: content ? content.y1 + MARGIN : limits.y0 + width * 1.4,
    };
  }
  return (
    contentBounds(state) || {
      x0: -EMPTY_SIZE / 2,
      y0: (-EMPTY_SIZE * 0.7) / 2,
      x1: EMPTY_SIZE / 2,
      y1: (EMPTY_SIZE * 0.7) / 2,
    }
  );
}

function renderArea(state, area, targetMax) {
  const width = Math.max(1, area.x1 - area.x0);
  const height = Math.max(1, area.y1 - area.y0);
  const scale = Math.min(2, targetMax / Math.max(width, height));
  return Renderer.renderToCanvas(state, { scale: Math.max(0.05, scale), bounds: area });
}

export function exportDataURL(state) {
  return renderArea(state, exportArea(state), MAX_EXPORT_PIXELS).toDataURL("image/png");
}

export function thumbBlob(state) {
  const canvas = renderArea(state, exportArea(state), THUMB_WIDTH);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

export async function uploadThumb(state, boardId) {
  if (!boardId) return false;
  // 文档板的缩略图由服务端直接渲染原件首页，这边画出来的只有笔迹没有底图。
  if (state.kind === "doc") return false;
  try {
    const blob = await thumbBlob(state);
    if (!blob) return false;
    await fetch(`/api/thumb/${boardId}`, { method: "POST", body: blob });
    return true;
  } catch (err) {
    return false;
  }
}

/**
 * 触发一次下载。
 *
 * 一律用带 download 的链接，不要给 ``location.href`` 赋值：iPad 外壳（WKWebView）
 * 里那是一次导航，页面会被换走，白板和 WebSocket 一起没了，界面上看到的是一句
 * 连接断开。带 download 的链接外壳会当成下载，存好之后弹系统的分享面板。
 */
export function downloadURL(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "";
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}

/** 导出 PNG。data: 链接在 WKWebView 里下载不了，先转成 blob: 再下。 */
export async function downloadDataURL(dataUrl, filename) {
  const blob = await (await fetch(dataUrl)).blob();
  const url = URL.createObjectURL(blob);
  downloadURL(url, filename);
  // 立刻撤销的话有的浏览器还没来得及读，留一分钟再收
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

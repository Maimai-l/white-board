// 导出对话框用到的页面几何：纯函数，和服务端 whiteboard/pagination.py 用同一套约定。
//
// 一页是 {x, y, w, h, paper: [宽, 高], margin?}：x/y/w/h 是要印出来的那块白板
// （白板坐标），paper 是纸张（pt，已经按横竖摆好）。页框等比缩放进纸张四周留白
// margin（默认 36 pt）之后的区域，水平居中、顶端对齐。

export const MARGIN = 36;
export const PAPERS = { a4: [595.28, 841.89], letter: [612, 792] };
// 单页 PNG 的像素上限，和 whiteboard/export.py 的 MAX_PAGE_PIXELS 一致
export const MAX_PAGE_PIXELS = 40_000_000;
// 调整页框时最小能缩到多大（白板单位，按短边算）
export const MIN_FRAME = 40;

/** 页框放到纸上：{scale, left, top}，单位 pt。 */
export function placement(page) {
  const margin = page.margin ?? MARGIN;
  const [pw, ph] = page.paper;
  const aw = Math.max(1, pw - 2 * margin);
  const ah = Math.max(1, ph - 2 * margin);
  const scale = Math.min(aw / page.w, ah / page.h);
  return { scale, left: margin + (aw - page.w * scale) / 2, top: margin };
}

/** 视口中心落在哪一页：在页框里的那一页，否则离得最近的那一页。没有页时返回 -1。 */
export function currentPage(pages, wx, wy) {
  let best = -1;
  let bestDistance = Infinity;
  pages.forEach((page, index) => {
    const dx = Math.max(page.x - wx, 0, wx - (page.x + page.w));
    const dy = Math.max(page.y - wy, 0, wy - (page.y + page.h));
    const distance = Math.hypot(dx, dy);
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

/** 按 dpi 导出 PNG 时这一页的像素尺寸 [宽, 高]；超过像素上限时和服务端一样按比例降。 */
export function pixelSize(page, dpi) {
  const [pw, ph] = page.paper;
  let k = dpi / 72;
  if (pw * ph * k * k > MAX_PAGE_PIXELS) k = Math.sqrt(MAX_PAGE_PIXELS / (pw * ph));
  return [Math.max(1, Math.round(pw * k)), Math.max(1, Math.round(ph * k))];
}

/**
 * 拖角上的把手改页框大小：对角那个点不动，宽高比不变。
 *
 * ``corner`` 是 "nw" / "ne" / "sw" / "se"，``(wx, wy)`` 是指针当前的白板坐标。
 */
export function resizeFrame(frame, corner, wx, wy) {
  const ratio = frame.w / frame.h;
  const east = corner.includes("e");
  const south = corner.includes("s");
  const ax = east ? frame.x : frame.x + frame.w;
  const ay = south ? frame.y : frame.y + frame.h;
  const dx = Math.abs(wx - ax);
  const dy = Math.abs(wy - ay);
  // 取两个方向里拉得更远的那一个，另一边按比例跟上
  let w = Math.max(dx, dy * ratio);
  const least = MIN_FRAME * Math.max(1, ratio);
  if (w < least) w = least;
  const h = w / ratio;
  return {
    ...frame,
    x: east ? ax : ax - w,
    y: south ? ay : ay - h,
    w,
    h,
  };
}

/** 新加的一页：以 (cx, cy) 为中心，大小照着 ``template``（没有就按纸张 1:1 竖放）。 */
export function frameAt(cx, cy, template, paper) {
  if (template) {
    const { w, h } = template;
    return { x: cx - w / 2, y: cy - h / 2, w, h, paper: [...template.paper] };
  }
  const [short, long] = [Math.min(...paper), Math.max(...paper)];
  const w = short - 2 * MARGIN;
  const h = long - 2 * MARGIN;
  return { x: cx - w / 2, y: cy - h / 2, w, h, paper: [short, long] };
}

/** Content-Disposition 里的文件名（filename*=UTF-8''…）。 */
export function filenameOf(disposition, fallback) {
  const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
  if (!match) return fallback;
  try {
    return decodeURIComponent(match[1].trim().replace(/^"|"$/g, ""));
  } catch (err) {
    return fallback;
  }
}

/** 发给 POST /api/export/{board} 的请求体。``frames`` 为 null 时用原件的页（文档板）。 */
export function exportRequest({ format, frames, selected, paper, dpi }) {
  const body = { paper, select: [...selected].sort((a, b) => a - b) };
  if (format === "pdf") body.format = "pdf";
  else {
    body.format = body.select.length === 1 ? "png" : "zip";
    body.dpi = dpi;
  }
  body.frames = frames ? frames.map(({ x, y, w, h, paper: sheet }) => ({ x, y, w, h, paper: sheet })) : "auto";
  return body;
}

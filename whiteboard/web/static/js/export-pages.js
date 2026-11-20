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
// 纸张最长边（pt），和 whiteboard/pagination.py 的 MAX_PAPER 一致
export const MAX_PAPER = 14400;

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

/** Python 的 round()：正好一半时取偶数（服务端用它算像素，两边要一致）。 */
export function roundHalfEven(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * 按 dpi 导出 PNG 时这一页的像素尺寸 [宽, 高]：照抄 whiteboard/export.py 的 png_size，
 * 超过像素上限时按比例降、四舍五入之后再确认没有超。两边的数在
 * tests/fixtures/png-sizes.json 里对过。
 */
export function pixelSize(page, dpi) {
  const [pw, ph] = page.paper;
  let k = dpi / 72;
  if (pw * ph * k * k > MAX_PAGE_PIXELS) {
    let lower = Math.sqrt(MAX_PAGE_PIXELS / (pw * ph));
    while (roundHalfEven(pw * lower) * roundHalfEven(ph * lower) > MAX_PAGE_PIXELS) lower *= 0.999;
    k = lower;
  }
  return [Math.max(1, roundHalfEven(pw * k)), Math.max(1, roundHalfEven(ph * k))];
}

/** 「跟随内容」的纸：页框加四周留白（pt），和 pagination.content_sheet 一样超过 MAX_PAPER 时整页缩小。 */
export function contentSheet(w, h, margin = MARGIN) {
  const pw = w + 2 * margin;
  const ph = h + 2 * margin;
  const shrink = Math.min(1, MAX_PAPER / Math.max(pw, ph));
  return [pw * shrink, ph * shrink];
}

/** 页框换了大小之后的纸：「跟随内容」时纸跟着页框走，否则纸不变。 */
export function withPaper(frame, paper) {
  if (paper) return frame;
  const { margin, ...rest } = frame;
  return { ...rest, paper: contentSheet(frame.w, frame.h) };
}

/**
 * 拖角上的把手改页框大小：对角那个点不动。
 *
 * ``corner`` 是 "nw" / "ne" / "sw" / "se"，``(wx, wy)`` 是指针当前的白板坐标。
 * ``paper`` 是选中的纸张（[短, 长] pt）时宽高比不变；为 null（纸张跟随内容）时
 * 宽高各自跟着指针走，纸跟着页框变。
 */
export function resizeFrame(frame, corner, wx, wy, paper = frame.paper) {
  const east = corner.includes("e");
  const south = corner.includes("s");
  const ax = east ? frame.x : frame.x + frame.w;
  const ay = south ? frame.y : frame.y + frame.h;
  const dx = Math.abs(wx - ax);
  const dy = Math.abs(wy - ay);
  let w;
  let h;
  if (paper) {
    const ratio = frame.w / frame.h;
    // 取两个方向里拉得更远的那一个，另一边按比例跟上
    w = Math.max(dx, dy * ratio);
    const least = MIN_FRAME * Math.max(1, ratio);
    if (w < least) w = least;
    h = w / ratio;
  } else {
    w = Math.max(dx, MIN_FRAME);
    h = Math.max(dy, MIN_FRAME);
  }
  return withPaper(
    {
      ...frame,
      x: east ? ax : ax - w,
      y: south ? ay : ay - h,
      w,
      h,
    },
    paper,
  );
}

/**
 * 在白板上拖出来的页框：从按下的点 (x0, y0) 拖到 (x1, y1)（白板坐标）。
 *
 * ``paper`` 为 null（纸张跟随内容）时就是这两个点围成的矩形，纸是页框加留白；
 * 给了纸张（[宽, 高] pt）时保持纸张内容区的比例，拖得横就横放、拖得竖就竖放，
 * 和拖角缩放一样取拉得更远的那一边。每边至少 ``MIN_FRAME``。
 */
export function drawnFrame(x0, y0, x1, y1, paper) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  let w;
  let h;
  let sheet;
  if (paper) {
    const short = Math.min(...paper);
    const long = Math.max(...paper);
    sheet = Math.abs(dx) > Math.abs(dy) ? [long, short] : [short, long];
    const ratio = (sheet[0] - 2 * MARGIN) / (sheet[1] - 2 * MARGIN);
    w = Math.max(Math.abs(dx), Math.abs(dy) * ratio, MIN_FRAME * Math.max(1, ratio));
    h = w / ratio;
  } else {
    w = Math.max(Math.abs(dx), MIN_FRAME);
    h = Math.max(Math.abs(dy), MIN_FRAME);
    sheet = contentSheet(w, h);
  }
  return { x: dx >= 0 ? x0 : x0 - w, y: dy >= 0 ? y0 : y0 - h, w, h, paper: sheet };
}

/**
 * 新加的一页：以 (cx, cy) 为中心，大小照着 ``template``；没有模板就是竖放纸张的内容区
 * （``paper`` 为 null，即纸张跟随内容时按竖放的 A4）。
 */
export function frameAt(cx, cy, template, paper) {
  if (template) {
    const { w, h } = template;
    return { x: cx - w / 2, y: cy - h / 2, w, h, paper: [...template.paper] };
  }
  const base = paper || PAPERS.a4;
  const [short, long] = [Math.min(...base), Math.max(...base)];
  const w = short - 2 * MARGIN;
  const h = long - 2 * MARGIN;
  const sheet = paper ? [short, long] : contentSheet(w, h);
  return { x: cx - w / 2, y: cy - h / 2, w, h, paper: sheet };
}

/**
 * 白板文件下载时的文件名，照 whiteboard/export.py 的 raw_name：去掉文件系统不认的字符，
 * 最长 80 字；没有名字时是 ``whiteboard-日期``（本机的日期）。
 */
export function rawName(name, now = new Date()) {
  let base = typeof name === "string" ? name.trim() : "";
  if (!base) {
    const pad = (n) => String(n).padStart(2, "0");
    base = `whiteboard-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }
  // eslint-disable-next-line no-control-regex
  base = base.replace(/[\x00-\x1f/\\:*?"<>|]+/g, "-").replace(/^[ .]+|[ .]+$/g, "") || "whiteboard";
  return `${[...base].slice(0, 80).join("")}.wbz`;
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

/**
 * 发给 POST /api/export/{board} 的请求体。
 *
 * ``format`` 是 pdf / png / wbz；``frames`` 是页框列表、"whole"（整块一页）或 null
 * （文档板，用原件的页）。纸张跟随内容时页框不带纸，服务端按页框加留白配纸。
 */
export function exportRequest({ format, frames, selected, paper, dpi }) {
  if (format === "wbz") return { format: "wbz" };
  if (frames === "whole") {
    const body = { format, paper, frames: "whole" };
    if (format === "png") body.dpi = dpi;
    return body;
  }
  const body = { paper, select: [...selected].sort((a, b) => a - b) };
  if (format === "pdf") body.format = "pdf";
  else {
    body.format = body.select.length === 1 ? "png" : "zip";
    body.dpi = dpi;
  }
  const content = paper === "content";
  body.frames = frames
    ? frames.map(({ x, y, w, h, paper: sheet }) => (content ? { x, y, w, h } : { x, y, w, h, paper: sheet }))
    : "auto";
  return body;
}

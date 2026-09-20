// 笔具盘（beta）用的工具造型。
//
// iPad 的 PencilKit 把每件工具画成一支立着的笔，笔尖染成当前的墨色，选中的那支
// 往上抬出来一截——不用文字就说清了「现在用哪支、写出来是什么颜色」。这里照着
// 这个思路重画一套，但形状、圆角和配色按 Material 3 来：笔身用 surface 色阶，
// 选中态靠 secondary container 托住，墨色只出现在笔尖。
//
// 画布是 28×60 的竖条：0–18 是笔尖，18–60 是笔身。

const VIEW = "0 0 28 60";

/** 笔尖随粗细变化：细笔尖窄一点，粗笔尖宽一点。 */
function nibScale(widthIndex, count) {
  const t = count > 1 ? widthIndex / (count - 1) : 0.5;
  return 0.62 + t * 0.38;
}

function penNib(k) {
  const half = 7.4 * k;
  return `<path class="nib" d="M14 2.5 L${14 + half} 18 H${14 - half} Z"/>`;
}

function markerNib(k) {
  const half = 8.2 * k;
  // 斜切的笔头，像马克笔
  return `<path class="nib" d="M${14 - half} 18 H${14 + half} L${14 + half * 0.72} 6.5
    H${14 - half * 0.2} Z" stroke-linejoin="round"/>`;
}

function highlighterNib(k) {
  const half = 9.4 * k;
  return `<path class="nib" d="M${14 - half} 18 H${14 + half} V8.2 a2 2 0 0 0-2-2
    H${14 - half + 2} a2 2 0 0 0-2 2 Z"/>`;
}

function eraserNib() {
  return `<path class="nib nib-plain" d="M5.4 18 H22.6 V8.6 a2.6 2.6 0 0 0-2.6-2.6
    H8 a2.6 2.6 0 0 0-2.6 2.6 Z"/>`;
}


// 笔身宽度也分一分：钢笔最细、荧光笔最粗，缩到很小也认得出是哪一支
const body = (half) => `<path class="body" d="M${14 - half} 18 H${14 + half} V54
  a4 4 0 0 1-4 4 H${14 - half + 4} a4 4 0 0 1-4-4 Z"/>`;
// 笔身上的一道箍，纯装饰
const collar = (half, y) => `<path class="collar" d="M${14 - half} ${y} H${14 + half}"/>`;

const NIBS = {
  pen: (k) => penNib(k) + body(7.4) + collar(7.4, 24),
  marker: (k) => markerNib(k) + body(8.4) + collar(8.4, 26),
  highlighter: (k) => highlighterNib(k) + body(9.6) + collar(9.6, 28),
  eraser: () => eraserNib() + body(8.6) + collar(8.6, 25),
};

/**
 * 一件工具的造型。
 *
 * @param {string} tool pen / marker / highlighter / eraser
 * @param {object} opts {color, widthIndex, widthCount, size}
 */
export function toolArt(tool, { color = "#1b1b1f", widthIndex = 1, widthCount = 5, size = 60 } = {}) {
  const build = NIBS[tool] || NIBS.pen;
  const ink = tool === "eraser" ? "" : `style="--ink:${color}"`;
  return `<svg class="tool-art" viewBox="${VIEW}" width="${(size * 28) / 60}" height="${size}"
    ${ink} aria-hidden="true">${build(nibScale(widthIndex, widthCount))}</svg>`;
}

/** 圆形气泡里用的小一号造型。 */
export function toolBubbleArt(tool, options) {
  return toolArt(tool, { ...options, size: 40 });
}

// 视口：世界坐标 ↔ 屏幕坐标。
//
// 画布是无限的，没有边界，所以这里只管缩放范围，不做任何位置约束。

import { clamp } from "./util.js";

export const MIN_SCALE = 0.05;
export const MAX_SCALE = 8;

export class Viewport {
  constructor() {
    this.scale = 1;
    this.x = 0; // 屏幕 = 世界 * scale + (x, y)
    this.y = 0;
  }

  toScreen(wx, wy) {
    return [wx * this.scale + this.x, wy * this.scale + this.y];
  }

  toWorld(sx, sy) {
    return [(sx - this.x) / this.scale, (sy - this.y) / this.scale];
  }

  /** 可见区域对应的世界矩形。 */
  visibleRect(viewW, viewH) {
    const [x0, y0] = this.toWorld(0, 0);
    const [x1, y1] = this.toWorld(viewW, viewH);
    return { x0, y0, x1, y1 };
  }

  panBy(dx, dy) {
    this.x += dx;
    this.y += dy;
  }

  zoomAt(factor, sx, sy) {
    const next = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
    const ratio = next / this.scale;
    this.x = sx - (sx - this.x) * ratio;
    this.y = sy - (sy - this.y) * ratio;
    this.scale = next;
  }

  setScaleAt(scale, sx, sy) {
    this.zoomAt(clamp(scale, MIN_SCALE, MAX_SCALE) / this.scale, sx, sy);
  }

  /** 把一块世界矩形放进视口。 */
  fit(bounds, viewW, viewH, padding = 48) {
    const width = Math.max(1, bounds.x1 - bounds.x0);
    const height = Math.max(1, bounds.y1 - bounds.y0);
    const scale = Math.min(
      (viewW - padding * 2) / width,
      (viewH - padding * 2) / height
    );
    this.scale = clamp(scale, MIN_SCALE, MAX_SCALE);
    this.centerOn((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2, viewW, viewH);
  }

  centerOn(wx, wy, viewW, viewH) {
    this.x = viewW / 2 - wx * this.scale;
    this.y = viewH / 2 - wy * this.scale;
  }

  /** 笔记模式：按页宽铺满，并停在页首。 */
  fitWidth(limits, viewW, viewH, padding = 0) {
    const width = Math.max(1, limits.x1 - limits.x0);
    this.scale = clamp((viewW - padding * 2) / width, MIN_SCALE, MAX_SCALE);
    this.x = viewW / 2 - ((limits.x0 + limits.x1) / 2) * this.scale;
    this.y = padding - limits.y0 * this.scale;
  }

  /**
   * 笔记模式的约束：左右不能划出纸外（纸比视口窄时居中），
   * 上面不能翻过页首，下面不限。
   */
  clampToPage(limits, viewW, viewH) {
    const width = (limits.x1 - limits.x0) * this.scale;
    const left = limits.x0 * this.scale;
    if (width <= viewW) this.x = (viewW - width) / 2 - left;
    else this.x = clamp(this.x, viewW - width - left, -left);
    const top = limits.y0 * this.scale;
    this.y = Math.min(this.y, viewH * 0.1 - top);
  }
}

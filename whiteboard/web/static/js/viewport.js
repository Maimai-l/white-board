// 视口：世界坐标 ↔ 屏幕坐标，以及缩放 / 平移的边界约束。
//
// 白板是「伪无限」的：默认九宫格，主屏在正中间，四周八个方向各扩一屏。

import { clamp } from "./util.js";

export const MIN_SCALE = 0.1;
export const MAX_SCALE = 5;

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

  fit(boardW, boardH, viewW, viewH, padding = 24) {
    const scale = Math.min(
      (viewW - padding * 2) / boardW,
      (viewH - padding * 2) / boardH
    );
    this.scale = clamp(scale, MIN_SCALE, MAX_SCALE);
    this.centerOn(boardW / 2, boardH / 2, viewW, viewH);
  }

  centerOn(wx, wy, viewW, viewH) {
    this.x = viewW / 2 - wx * this.scale;
    this.y = viewH / 2 - wy * this.scale;
  }

  /** 画布小于视口时居中，大于视口时限制在边缘附近，避免划到虚空里。 */
  clampTo(boardW, boardH, viewW, viewH) {
    const margin = 0.12;
    const width = boardW * this.scale;
    const height = boardH * this.scale;
    if (width <= viewW) this.x = (viewW - width) / 2;
    else this.x = clamp(this.x, viewW - width - viewW * margin, viewW * margin);
    if (height <= viewH) this.y = (viewH - height) / 2;
    else this.y = clamp(this.y, viewH - height - viewH * margin, viewH * margin);
  }
}

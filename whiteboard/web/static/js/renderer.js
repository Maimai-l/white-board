// 双层画布渲染。
//
// base：已提交的笔画。只在视口变化或内容变化时整屏重绘；新笔画直接增量画上去。
// live：正在书写的笔画（本机的和对端的）。每帧清空重画，保证落笔即见。

import { drawStroke, strokeBBox } from "./stroke.js";
import { TAU } from "./util.js";

const PAPER = "#ffffff";
const OUTSIDE = "#e8eaf0";
const LINE = "#d7dbe6";
const BORDER = "#c3c8d6";
const GRID_STEP = 40;
const MAX_DPR = 2.5;

export class Renderer {
  constructor(baseCanvas, liveCanvas, state, viewport) {
    this.base = baseCanvas;
    this.live = liveCanvas;
    this.baseCtx = baseCanvas.getContext("2d", { alpha: false });
    this.liveCtx = liveCanvas.getContext("2d");
    this.state = state;
    this.viewport = viewport;
    this.dpr = 1;
    this.viewW = 0;
    this.viewH = 0;
    this.fullDirty = true;
    this.liveStrokes = new Map();
    this.cursor = null;
    this._liveDrawn = false;
    this._liveClip = null;
    this.resize();
  }

  resize() {
    const rect = this.base.getBoundingClientRect();
    this.viewW = Math.max(1, Math.round(rect.width));
    this.viewH = Math.max(1, Math.round(rect.height));
    this.dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    for (const canvas of [this.base, this.live]) {
      canvas.width = Math.round(this.viewW * this.dpr);
      canvas.height = Math.round(this.viewH * this.dpr);
    }
    this._liveClip = null;
    this.fullDirty = true;
  }

  requestFull() {
    this.fullDirty = true;
  }

  _applyTransform(ctx) {
    const { scale, x, y } = this.viewport;
    ctx.setTransform(scale * this.dpr, 0, 0, scale * this.dpr, x * this.dpr, y * this.dpr);
  }

  // ------------------------------------------------------------------ 背景

  drawBackground(ctx) {
    const { scale, x, y } = this.viewport;
    const [boardW, boardH] = this.state.size();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = OUTSIDE;
    ctx.fillRect(0, 0, this.viewW, this.viewH);

    const left = x;
    const top = y;
    const width = boardW * scale;
    const height = boardH * scale;
    ctx.fillStyle = PAPER;
    ctx.fillRect(left, top, width, height);

    const background = this.state.meta ? this.state.meta.background : "blank";
    // 缩小时把网格逐级合并，避免糊成一片灰。
    let step = GRID_STEP * scale;
    while (step > 0 && step < 14) step *= 2;
    if (background !== "blank" && step >= 6) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(left, top, width, height);
      ctx.clip();
      ctx.strokeStyle = LINE;
      ctx.fillStyle = LINE;
      ctx.lineWidth = Math.max(0.5, Math.min(1, scale));
      const x0 = Math.max(left, 0);
      const y0 = Math.max(top, 0);
      const x1 = Math.min(left + width, this.viewW);
      const y1 = Math.min(top + height, this.viewH);
      const startX = left + Math.floor((x0 - left) / step) * step;
      const startY = top + Math.floor((y0 - top) / step) * step;

      if (background === "dots") {
        const radius = Math.max(0.8, Math.min(1.8, 1.2 * scale));
        for (let gx = startX; gx <= x1; gx += step) {
          for (let gy = startY; gy <= y1; gy += step) {
            ctx.beginPath();
            ctx.arc(gx, gy, radius, 0, TAU);
            ctx.fill();
          }
        }
      } else {
        ctx.beginPath();
        if (background === "grid") {
          for (let gx = startX; gx <= x1; gx += step) {
            ctx.moveTo(Math.round(gx) + 0.5, y0);
            ctx.lineTo(Math.round(gx) + 0.5, y1);
          }
        }
        for (let gy = startY; gy <= y1; gy += step) {
          ctx.moveTo(x0, Math.round(gy) + 0.5);
          ctx.lineTo(x1, Math.round(gy) + 0.5);
        }
        ctx.stroke();
      }
      ctx.restore();
    }

    ctx.strokeStyle = BORDER;
    ctx.lineWidth = 1;
    ctx.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1);
  }

  // ------------------------------------------------------------------ 绘制

  fullRedraw() {
    const ctx = this.baseCtx;
    this.drawBackground(ctx);
    this._applyTransform(ctx);
    const view = this.viewport.visibleRect(this.viewW, this.viewH);
    for (const stroke of this.state.strokes) {
      const bbox = strokeBBox(stroke);
      if (bbox.x1 < view.x0 || bbox.x0 > view.x1 || bbox.y1 < view.y0 || bbox.y0 > view.y1) {
        continue;
      }
      drawStroke(ctx, stroke);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /** 增量画一条已提交的笔画，避免整屏重绘。 */
  drawCommitted(stroke) {
    if (this.fullDirty) return;
    const ctx = this.baseCtx;
    this._applyTransform(ctx);
    drawStroke(ctx, stroke);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  setLive(id, stroke) {
    if (stroke) this.liveStrokes.set(id, stroke);
    else this.liveStrokes.delete(id);
  }

  clearLive() {
    this.liveStrokes.clear();
  }

  /** 正在书写的那一笔占多大（画布像素），用来只清这一块而不是整屏。 */
  liveBounds() {
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const stroke of this.liveStrokes.values()) {
      if (stroke.p.length < 3) continue;
      const bbox = strokeBBox(stroke);
      if (bbox.x0 < x0) x0 = bbox.x0;
      if (bbox.y0 < y0) y0 = bbox.y0;
      if (bbox.x1 > x1) x1 = bbox.x1;
      if (bbox.y1 > y1) y1 = bbox.y1;
    }
    if (this.cursor) {
      const { x, y, r } = this.cursor;
      if (x - r < x0) x0 = x - r;
      if (y - r < y0) y0 = y - r;
      if (x + r > x1) x1 = x + r;
      if (y + r > y1) y1 = y + r;
    }
    if (x0 === Infinity) return null;

    const { scale, x, y } = this.viewport;
    const pad = 3;
    const left = Math.max(0, Math.floor((x0 * scale + x - pad) * this.dpr));
    const top = Math.max(0, Math.floor((y0 * scale + y - pad) * this.dpr));
    const right = Math.min(this.live.width, Math.ceil((x1 * scale + x + pad) * this.dpr));
    const bottom = Math.min(this.live.height, Math.ceil((y1 * scale + y + pad) * this.dpr));
    if (right <= left || bottom <= top) return null;
    return [left, top, right - left, bottom - top];
  }

  drawLive() {
    const ctx = this.liveCtx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // 只清上一帧画过的范围：iPad 是 2 倍像素的全屏画布，整屏清空每帧都要好几毫秒。
    if (this._liveClip) ctx.clearRect(...this._liveClip);
    else ctx.clearRect(0, 0, this.live.width, this.live.height);
    this._liveClip = this.liveBounds();
    if (!this._liveClip) return;
    this._applyTransform(ctx);
    for (const stroke of this.liveStrokes.values()) {
      if (stroke.p.length >= 3) drawStroke(ctx, stroke);
    }
    if (this.cursor) {
      ctx.lineWidth = 1 / this.viewport.scale;
      ctx.strokeStyle = "rgba(0,0,0,.45)";
      ctx.beginPath();
      ctx.arc(this.cursor.x, this.cursor.y, this.cursor.r, 0, TAU);
      ctx.stroke();
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  tick() {
    if (this.fullDirty) {
      this.fullDirty = false;
      this.fullRedraw();
    }
    const hasLive = this.liveStrokes.size > 0 || this.cursor !== null;
    if (hasLive || this._liveDrawn) {
      this.drawLive();
      this._liveDrawn = hasLive;
    }
  }

  /** 离屏导出：整块白板（含背景）渲染成一张 canvas。 */
  static renderToCanvas(state, { scale = 1, background = true, bounds = null } = {}) {
    const [boardW, boardH] = state.size();
    const area = bounds || { x0: 0, y0: 0, x1: boardW, y1: boardH };
    const width = Math.max(1, Math.round((area.x1 - area.x0) * scale));
    const height = Math.max(1, Math.round((area.y1 - area.y0) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (background) {
      ctx.fillStyle = PAPER;
      ctx.fillRect(0, 0, width, height);
      let step = GRID_STEP * scale;
      while (step > 0 && step < 14) step *= 2;
      const kind = state.meta ? state.meta.background : "blank";
      if (kind !== "blank" && step >= 6) {
        ctx.strokeStyle = LINE;
        ctx.fillStyle = LINE;
        ctx.lineWidth = Math.max(0.5, scale);
        const offsetX = -((area.x0 * scale) % step);
        const offsetY = -((area.y0 * scale) % step);
        if (kind === "dots") {
          for (let gx = offsetX; gx <= width; gx += step) {
            for (let gy = offsetY; gy <= height; gy += step) {
              ctx.beginPath();
              ctx.arc(gx, gy, Math.max(0.8, 1.2 * scale), 0, TAU);
              ctx.fill();
            }
          }
        } else {
          ctx.beginPath();
          if (kind === "grid") {
            for (let gx = offsetX; gx <= width; gx += step) {
              ctx.moveTo(gx, 0);
              ctx.lineTo(gx, height);
            }
          }
          for (let gy = offsetY; gy <= height; gy += step) {
            ctx.moveTo(0, gy);
            ctx.lineTo(width, gy);
          }
          ctx.stroke();
        }
      }
    }
    ctx.setTransform(scale, 0, 0, scale, -area.x0 * scale, -area.y0 * scale);
    for (const stroke of state.strokes) drawStroke(ctx, stroke);
    return canvas;
  }
}

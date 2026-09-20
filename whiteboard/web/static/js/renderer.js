// 双层画布渲染。
//
// base：已提交的笔画。只在视口变化或内容变化时整屏重绘；新笔画直接增量画上去。
// live：正在书写的笔画（本机的和对端的）。每帧清空重画，保证落笔即见。

import { DocPages } from "./docpages.js";
import { drawStroke, strokeBBox } from "./stroke.js";
import { TAU } from "./util.js";

const PAPER = "#ffffff";
const OUTSIDE = "#e6e8ee";
const EDGE = "#d2d6e0";
const LINE = "#d7dbe6";
const GRID_STEP = 40;
const MIN_PATTERN_PX = 14;
const MAX_DPR = 2.5;

/** 缩小时把网格逐级合并，避免糊成一片灰；返回屏幕像素步长。 */
function patternStep(scale) {
  let step = GRID_STEP * scale;
  if (step <= 0) return 0;
  while (step < MIN_PATTERN_PX) step *= 2;
  return step;
}

/**
 * 画无限延伸的背景纹理。
 *
 * `tx`/`ty` 是世界原点在目标画布上的位置，纹理据此对齐，所以平移缩放之后
 * 线条仍然落在同样的世界坐标上。
 */
function drawPattern(ctx, kind, scale, tx, ty, width, height) {
  const step = patternStep(scale);
  if (kind === "blank" || step < 6) return;
  const offsetX = ((tx % step) + step) % step;
  const offsetY = ((ty % step) + step) % step;

  ctx.save();
  ctx.strokeStyle = LINE;
  ctx.fillStyle = LINE;
  ctx.lineWidth = Math.max(0.5, Math.min(1, scale));
  if (kind === "dots") {
    const radius = Math.max(0.8, Math.min(1.8, 1.2 * scale));
    for (let x = offsetX; x <= width; x += step) {
      for (let y = offsetY; y <= height; y += step) {
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, TAU);
        ctx.fill();
      }
    }
  } else {
    ctx.beginPath();
    if (kind === "grid") {
      for (let x = offsetX; x <= width; x += step) {
        ctx.moveTo(Math.round(x) + 0.5, 0);
        ctx.lineTo(Math.round(x) + 0.5, height);
      }
    }
    for (let y = offsetY; y <= height; y += step) {
      ctx.moveTo(0, Math.round(y) + 0.5);
      ctx.lineTo(width, Math.round(y) + 0.5);
    }
    ctx.stroke();
  }
  ctx.restore();
}

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
    // 文档板的页面底图：解码完一页就重画一次
    this.docPages = new DocPages(() => this.requestFull());
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

  /** 纸张（笔记页 / 文档页）在屏幕上的范围；大白板返回 null。 */
  pageRect() {
    const limits = this.state.limits;
    if (!limits) return null;
    const { scale, x, y } = this.viewport;
    const left = x + limits.x0 * scale;
    const top = y + limits.y0 * scale;
    return {
      left,
      top,
      width: (limits.x1 - limits.x0) * scale,
      height:
        limits.y1 === undefined
          ? this.viewH - Math.min(top, 0) + 2
          : (limits.y1 - limits.y0) * scale,
    };
  }

  /** 把后续绘制限制在纸张内（笔记模式）；返回是否需要 restore。 */
  clipToPage(ctx) {
    const page = this.pageRect();
    if (!page) return false;
    ctx.save();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.beginPath();
    ctx.rect(page.left, page.top, page.width, page.height);
    ctx.clip();
    return true;
  }

  /** 文档板：页面自上而下铺开，底图来自 Mac 端渲染的原件。 */
  drawDocBackground(ctx) {
    const { scale, x, y } = this.viewport;
    ctx.fillStyle = OUTSIDE;
    ctx.fillRect(0, 0, this.viewW, this.viewH);

    const boxes = this.state.pages;
    this.docPages.setBoard(this.state.id, boxes);
    const view = this.viewport.visibleRect(this.viewW, this.viewH);
    this.docPages.update(view, scale, this.dpr);

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    for (let index = 0; index < boxes.length; index++) {
      const box = boxes[index];
      if (box.y + box.h < view.y0 || box.y > view.y1) continue;
      const left = x + box.x * scale;
      const top = y + box.y * scale;
      const width = box.w * scale;
      const height = box.h * scale;
      ctx.fillStyle = PAPER;
      ctx.fillRect(left, top, width, height);
      const img = this.docPages.get(index);
      if (img) {
        try {
          ctx.drawImage(img, left, top, width, height);
        } catch (err) {
          /* 图还没解码好，下一帧再说 */
        }
      }
      ctx.strokeStyle = EDGE;
      ctx.lineWidth = 1;
      ctx.strokeRect(Math.round(left) + 0.5, Math.round(top) + 0.5, width - 1, height - 1);
    }
  }

  drawBackground(ctx) {
    const { scale, x, y } = this.viewport;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.state.kind === "doc") {
      this.drawDocBackground(ctx);
      return;
    }
    const page = this.pageRect();
    if (!page) {
      ctx.fillStyle = PAPER;
      ctx.fillRect(0, 0, this.viewW, this.viewH);
      const background = this.state.meta ? this.state.meta.background : "blank";
      drawPattern(ctx, background, scale, x, y, this.viewW, this.viewH);
      return;
    }

    // 笔记：纸张之外是底色，页首上方也不画纸。
    ctx.fillStyle = OUTSIDE;
    ctx.fillRect(0, 0, this.viewW, this.viewH);
    ctx.save();
    ctx.beginPath();
    ctx.rect(page.left, page.top, page.width, page.height);
    ctx.clip();
    ctx.fillStyle = PAPER;
    ctx.fillRect(page.left, page.top, page.width, page.height);
    drawPattern(ctx, this.state.meta.background, scale, x, y, this.viewW, this.viewH);
    ctx.restore();
    ctx.strokeStyle = EDGE;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(page.left) + 0.5, page.top);
    ctx.lineTo(Math.round(page.left) + 0.5, page.top + page.height);
    ctx.moveTo(Math.round(page.left + page.width) - 0.5, page.top);
    ctx.lineTo(Math.round(page.left + page.width) - 0.5, page.top + page.height);
    if (page.top > 0) {
      ctx.moveTo(page.left, Math.round(page.top) + 0.5);
      ctx.lineTo(page.left + page.width, Math.round(page.top) + 0.5);
    }
    ctx.stroke();
  }

  // ------------------------------------------------------------------ 绘制

  fullRedraw() {
    const ctx = this.baseCtx;
    this.drawBackground(ctx);
    const clipped = this.clipToPage(ctx);
    this._applyTransform(ctx);
    const view = this.viewport.visibleRect(this.viewW, this.viewH);
    for (const stroke of this.state.strokes) {
      const bbox = strokeBBox(stroke);
      if (bbox.x1 < view.x0 || bbox.x0 > view.x1 || bbox.y1 < view.y0 || bbox.y0 > view.y1) {
        continue;
      }
      drawStroke(ctx, stroke);
    }
    if (clipped) ctx.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /** 增量画一条已提交的笔画，避免整屏重绘。 */
  drawCommitted(stroke) {
    if (this.fullDirty) return;
    const ctx = this.baseCtx;
    const clipped = this.clipToPage(ctx);
    this._applyTransform(ctx);
    drawStroke(ctx, stroke);
    if (clipped) ctx.restore();
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
    const clipped = this.clipToPage(ctx);
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
    if (clipped) ctx.restore();
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
  /** 离屏导出：把指定的世界矩形（含背景）渲染成一张 canvas。 */
  static renderToCanvas(state, { scale = 1, background = true, bounds } = {}) {
    const area = bounds || { x0: 0, y0: 0, x1: 1, y1: 1 };
    const width = Math.max(1, Math.round((area.x1 - area.x0) * scale));
    const height = Math.max(1, Math.round((area.y1 - area.y0) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (background) {
      ctx.fillStyle = PAPER;
      ctx.fillRect(0, 0, width, height);
      const kind = state.meta ? state.meta.background : "blank";
      drawPattern(ctx, kind, scale, -area.x0 * scale, -area.y0 * scale, width, height);
    }
    ctx.setTransform(scale, 0, 0, scale, -area.x0 * scale, -area.y0 * scale);
    for (const stroke of state.strokes) drawStroke(ctx, stroke);
    return canvas;
  }
}

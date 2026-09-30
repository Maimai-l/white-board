// 双层画布渲染。
//
// base：已提交的笔画。只在视口变化或内容变化时整屏重绘；新笔画直接增量画上去。
// live：正在书写的笔画（本机的和对端的）。每帧清空重画，保证落笔即见。

import { ImageLayers, layerBox } from "./layers.js";
import { addMaskPath, drawStroke, maskBounds, strokeBBox } from "./stroke.js";
import { TAU } from "./util.js";

const PAPER = "#ffffff";
// 纸张之外那一片。原来是 #e6e8ee，偏蓝（蓝比红高 8），和界面上那套 iOS 系统灰
// 不是一个色系；改成 systemGray5 #e5e5ea，和 app.css 里的 --surface（#f2f2f7，
// 也就是 systemGray6）同族，只差明度。
const OUTSIDE = "#e5e5ea";
// 纸的边缘不描线，改成一层投影：线在高分屏上是一根硬边，纸看着像贴在底色上；
// 投影才有纸浮在上面的意思。颜色用 iOS 分隔线那一套的基色 rgb(60,60,67)。
const SHADOW = "rgba(60, 60, 67, .2)";
const SHADOW_BLUR = 16;
const SHADOW_LIFT = 3;
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

/** 背景样式：``meta.background`` 是 ``{pattern, paper}``（1.x 是字符串）。 */
export function backgroundOf(meta) {
  const background = meta && meta.background;
  if (typeof background === "string") return { pattern: background, paper: PAPER };
  return {
    pattern: (background && background.pattern) || (meta ? "grid" : "blank"),
    paper: (background && background.paper) || PAPER,
  };
}

function strokeRect(stroke) {
  const box = strokeBBox(stroke);
  return [box.x0, box.y0, box.x1, box.y1];
}

/** 两个包围盒有没有交叠。 */
function overlaps(a, b) {
  return !(a.x1 < b.x0 || a.x0 > b.x1 || a.y1 < b.y0 || a.y0 > b.y1);
}

/**
 * 按层叠顺序画一批笔画，并把被橡皮啃掉的地方用背景补回去。
 *
 * 为什么是「把背景画回去」而不是「从墨迹里减掉」：减掉一组互相重叠的胶囊做不到。
 * even-odd 算的是对称差不是并集，而一次拖动里相邻两段胶囊在共用的那个圆端点处
 * 必然重叠，重叠处被算两次成了偶数、判定成「不擦」，擦痕就成了一排断开的小块；
 * nonzero 换个绕向也减不干净。反过来，把并集**填**出来是容易的：同向绕的子路径
 * 用 nonzero 正好就是并集。
 *
 * 顺序上：一条笔画的遮罩可以盖住它自己和它下面的笔画——下面那些当时被同一下橡皮
 * 一起擦到了，盖住是对的；它上面的笔画是擦完之后才画的，不能盖。所以遇到一条压在
 * 待补区域上、自己又没有遮罩的笔画时，先把背景补上再画它。
 *
 * 同一下橡皮擦到的那些笔画在层叠顺序上通常是连着的，所以这里攒成一批补一次，
 * 而不是每条笔画补一次。
 */
function paintStrokes(ctx, strokes, repaintBackground) {
  let path = null;
  let box = null;
  const flush = () => {
    if (!path) return;
    ctx.save();
    ctx.clip(path);
    repaintBackground(ctx);
    ctx.restore();
    path = null;
    box = null;
  };
  for (const stroke of strokes) {
    const masked = stroke.m && stroke.m.length;
    if (box && !masked && overlaps(strokeBBox(stroke), box)) flush();
    drawStroke(ctx, stroke);
    if (!masked) continue;
    if (!path) path = new Path2D();
    addMaskPath(path, stroke);
    const grown = maskBounds(stroke.m);
    if (!box) box = grown;
    else {
      box.x0 = Math.min(box.x0, grown.x0);
      box.y0 = Math.min(box.y0, grown.y0);
      box.x1 = Math.max(box.x1, grown.x1);
      box.y1 = Math.max(box.y1, grown.y1);
    }
  }
  flush();
}

export class Renderer {
  constructor(baseCanvas, liveCanvas, state, viewport) {
    this.base = baseCanvas;
    this.live = liveCanvas;
    this.baseCtx = baseCanvas.getContext("2d", { alpha: false });
    // 实时层**不要**开 desynchronized。试过，为的是「延迟低一点、慢帧上少丢指针
    // 事件」，结果是：采样率一点没变（面板上的「新位置」还是 64/s），而写字时
    // 出现闪烁。原因在 drawLive——它每帧先 clearRect 掉上一帧的范围再重画整条
    // 实时笔画，而 desynchronized 绕开了合成器同步，清和画之间的中间状态会被
    // 显示出来。要再试的话先把 drawLive 改成不清空重画。
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
    this.dirty = null;
    // 图片层（题图、文档页）：解码完一张就重画一次
    this.images = new ImageLayers(() => this.requestFull());
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
    this.dirty = null;
  }

  /**
   * 只重画世界坐标里的这一块。擦除用它：整屏重绘是「和白板上一共有多少笔成正比」，
   * 几千笔的板上一次就要十几毫秒，而擦除每一帧都要重来一次——iPad 上就是这里卡。
   *
   * 加笔画有 drawCommitted 可以直接往上叠，删笔画不行：墨迹已经合成在底图里了，
   * 只能把那一块连背景一起重画。好在有了空间索引，重画一小块只需要碰到那一小块
   * 里的笔画。
   */
  requestRect(x0, y0, x1, y1) {
    if (this.fullDirty) return;
    const box = this.dirty;
    if (!box) {
      this.dirty = { x0, y0, x1, y1 };
      return;
    }
    if (x0 < box.x0) box.x0 = x0;
    if (y0 < box.y0) box.y0 = y0;
    if (x1 > box.x1) box.x1 = x1;
    if (y1 > box.y1) box.y1 = y1;
  }

  _applyTransform(ctx) {
    const { scale, x, y } = this.viewport;
    ctx.setTransform(scale * this.dpr, 0, 0, scale * this.dpr, x * this.dpr, y * this.dpr);
  }

  // ------------------------------------------------------------------ 背景

  /** 铺一张纸，四周带投影。投影是屏幕像素单位的，不跟着缩放变。 */
  paintPaper(ctx, left, top, width, height, color = PAPER) {
    ctx.save();
    ctx.shadowColor = SHADOW;
    ctx.shadowBlur = SHADOW_BLUR;
    ctx.shadowOffsetY = SHADOW_LIFT;
    ctx.fillStyle = color;
    ctx.fillRect(left, top, width, height);
    ctx.restore();
  }

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
      open: limits.y1 === undefined,
    };
  }

  /** 把后续绘制限制在画布范围内（column、fixed 画布）；返回是否需要 restore。 */
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

  /** 这一帧要用的图片层：换了白板或图片层就重新来，并按视野加载。 */
  syncImages() {
    const layers = this.state.layers;
    this.images.setLayers(this.state.id, layers);
    if (layers.length) {
      const view = this.viewport.visibleRect(this.viewW, this.viewH);
      this.images.update(view, this.viewport.scale, this.dpr);
    }
    return layers;
  }

  /**
   * 画图片层。``which``：``below``（笔迹下方，不含 sheet）、``sheets``（带纸张的层）或
   * ``above``（笔迹上方）。坐标是屏幕像素（调用方已设好 dpr 变换）。
   */
  drawImages(ctx, which) {
    const layers = this.state.layers;
    if (!layers.length) return;
    const { scale, x, y } = this.viewport;
    const view = this.viewport.visibleRect(this.viewW, this.viewH);
    const paper = backgroundOf(this.state.meta).paper;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    for (let index = 0; index < layers.length; index++) {
      const layer = layers[index];
      const above = layer.z === "above";
      if (which === "above" ? !above : above) continue;
      if ((which === "sheets") !== !!layer.sheet && which !== "above") continue;
      const box = this.images.box(index);
      if (box.y + box.h < view.y0 || box.y > view.y1 || box.x + box.w < view.x0 || box.x > view.x1) continue;
      const left = x + box.x * scale;
      const top = y + box.y * scale;
      const width = box.w * scale;
      const height = box.h * scale;
      if (layer.sheet) this.paintPaper(ctx, left, top, width, height, paper);
      const img = this.images.get(index);
      if (!img) continue;
      try {
        ctx.drawImage(img, left, top, width, height);
      } catch (err) {
        /* 图还没解码好，下一帧再说 */
      }
    }
  }

  drawBackground(ctx) {
    const { scale, x, y } = this.viewport;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const layers = this.syncImages();
    const { pattern, paper } = backgroundOf(this.state.meta);

    // 带纸张的图片层（例如文档页）：底色上一页页铺开，不画背景纹理
    if (layers.some((layer) => layer.sheet)) {
      ctx.fillStyle = OUTSIDE;
      ctx.fillRect(0, 0, this.viewW, this.viewH);
      this.drawImages(ctx, "sheets");
      this.drawImages(ctx, "below");
      return;
    }
    const page = this.pageRect();
    if (!page) {
      ctx.fillStyle = paper;
      ctx.fillRect(0, 0, this.viewW, this.viewH);
      drawPattern(ctx, pattern, scale, x, y, this.viewW, this.viewH);
      this.drawImages(ctx, "below");
      return;
    }

    // column、fixed 画布：纸张之外是底色，页首上方也不画纸。
    ctx.fillStyle = OUTSIDE;
    ctx.fillRect(0, 0, this.viewW, this.viewH);
    // column 只向下无限延伸，下边没有真正的纸边。pageRect 给的高度到屏幕底就截住了，
    // 直接画的话投影会在屏幕底下压出一条暗边，所以往下多铺出一个模糊半径。
    const tail = page.open ? SHADOW_BLUR * 2 : 0;
    this.paintPaper(ctx, page.left, page.top, page.width, page.height + tail, paper);
    ctx.save();
    ctx.beginPath();
    ctx.rect(page.left, page.top, page.width, page.height);
    ctx.clip();
    drawPattern(ctx, pattern, scale, x, y, this.viewW, this.viewH);
    this.drawImages(ctx, "below");
    ctx.restore();
  }

  /** 笔迹上方的图片层（``z: "above"``）：画在笔迹之后。 */
  drawForeground(ctx) {
    if (!this.state.layers.some((layer) => layer.z === "above")) return;
    ctx.save();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawImages(ctx, "above");
    ctx.restore();
  }

  // ------------------------------------------------------------------ 绘制

  fullRedraw() {
    const ctx = this.baseCtx;
    this.drawBackground(ctx);
    const clipped = this.clipToPage(ctx);
    this._applyTransform(ctx);
    const view = this.viewport.visibleRect(this.viewW, this.viewH);
    const visible = this.state.strokes.filter((stroke) => {
      const bbox = strokeBBox(stroke);
      return overlaps(bbox, view);
    });
    paintStrokes(ctx, visible, (target) => this.drawBackground(target));
    if (clipped) ctx.restore();
    this.drawForeground(ctx);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /** 只重画脏矩形那一块：背景和落在里面的笔画，都夹在这块里画。 */
  redrawRect(box) {
    const ctx = this.baseCtx;
    const { scale, x, y } = this.viewport;
    const pad = 2;
    const left = Math.max(0, Math.floor(box.x0 * scale + x) - pad);
    const top = Math.max(0, Math.floor(box.y0 * scale + y) - pad);
    const right = Math.min(this.viewW, Math.ceil(box.x1 * scale + x) + pad);
    const bottom = Math.min(this.viewH, Math.ceil(box.y1 * scale + y) + pad);
    if (right <= left || bottom <= top) return;
    // 一整屏都脏了的话，夹着画反而多一层开销，不如老老实实整屏来
    if ((right - left) * (bottom - top) > this.viewW * this.viewH * 0.6) {
      this.fullRedraw();
      return;
    }

    ctx.save();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.beginPath();
    ctx.rect(left, top, right - left, bottom - top);
    ctx.clip();
    this.drawBackground(ctx);
    const clipped = this.clipToPage(ctx);
    this._applyTransform(ctx);
    const inside = this.state.near(box.x0, box.y0, box.x1, box.y1, 0).filter((stroke) => {
      const bbox = strokeBBox(stroke);
      return overlaps(bbox, box);
    });
    paintStrokes(ctx, inside, (target) => this.drawBackground(target));
    if (clipped) ctx.restore();
    this.drawForeground(ctx);
    ctx.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /** 增量画一条已提交的笔画，避免整屏重绘。 */
  drawCommitted(stroke) {
    if (this.fullDirty) return;
    // 笔迹上方还有图片层时不能直接往上叠，得连那一层一起重画
    if (this.state.layers.some((layer) => layer.z === "above")) {
      this.requestRect(...strokeRect(stroke));
      return;
    }
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
      this.dirty = null;
      this.fullRedraw();
    } else if (this.dirty) {
      const box = this.dirty;
      this.dirty = null;
      this.redrawRect(box);
    }
    const hasLive = this.liveStrokes.size > 0 || this.cursor !== null;
    if (hasLive || this._liveDrawn) {
      this.drawLive();
      this._liveDrawn = hasLive;
    }
  }

  /**
   * 离屏导出：把指定的世界矩形渲染成一张 canvas。
   *
   * ``background``：画纸张和背景纹理；``images``：与 ``meta.layers`` 一一对应的已加载图片
   * （省略时不画图片层）。
   */
  static renderToCanvas(state, { scale = 1, background = true, bounds, images = null } = {}) {
    const area = bounds || { x0: 0, y0: 0, x1: 1, y1: 1 };
    const width = Math.max(1, Math.round((area.x1 - area.x0) * scale));
    const height = Math.max(1, Math.round((area.y1 - area.y0) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const { pattern, paper } = backgroundOf(state.meta);
    const layers = state.layers || [];
    const drawLayers = (target, above) => {
      if (!images) return;
      layers.forEach((layer, index) => {
        if ((layer.z === "above") !== above || !images[index]) return;
        const box = layerBox(layer, images[index]);
        target.drawImage(images[index], box.x, box.y, box.w, box.h);
      });
    };
    const paint = (target) => {
      target.save();
      target.setTransform(1, 0, 0, 1, 0, 0);
      if (background || images) {
        target.fillStyle = paper;
        target.fillRect(0, 0, width, height);
        if (background && !layers.some((layer) => layer.sheet)) {
          drawPattern(target, pattern, scale, -area.x0 * scale, -area.y0 * scale, width, height);
        }
      } else {
        target.clearRect(0, 0, width, height);
      }
      target.setTransform(scale, 0, 0, scale, -area.x0 * scale, -area.y0 * scale);
      drawLayers(target, false);
      target.restore();
    };
    paint(ctx);
    ctx.setTransform(scale, 0, 0, scale, -area.x0 * scale, -area.y0 * scale);
    paintStrokes(ctx, state.strokes, paint);
    drawLayers(ctx, true);
    return canvas;
  }
}

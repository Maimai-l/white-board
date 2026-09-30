// 当前白板的内容（元数据 + 笔画），按层叠序号 n 升序保存。

import { strokeBBox } from "./stroke.js";

// 笔记页的宽度（世界坐标）。两端设备共用同一个值，换设备看到的排版才一致。
export const PAGE_WIDTH = 1000;

// 文档板的页间距，必须和 whiteboard/docs.py 里的 PAGE_GAP 一致，
// 否则导出的时候笔迹会落到别的页上。
export const PAGE_GAP = 24;

/** 文档页自上而下排列，横向按最宽的一页居中；与 docs.layout 同一套算法。 */
export function docLayout(pages) {
  if (!pages || !pages.length) return [];
  const widest = Math.max(...pages.map((p) => p[0]));
  const boxes = [];
  let y = 0;
  for (const [w, h] of pages) {
    boxes.push({ x: (widest - w) / 2, y, w, h });
    y += h + PAGE_GAP;
  }
  return boxes;
}

// 空间索引的网格边长（世界坐标）。橡皮和命中判定只看扫过的那几格，
// 不必每次都把整块白板的笔画过一遍。
const CELL = 256;
// 跨了太多格的笔画（满屏乱画的那种）单独放一处，每次都扫；否则光登记就够呛。
const MAX_CELLS = 64;

export class BoardState {
  constructor() {
    this.meta = null;
    this.strokes = [];
    this.byId = new Map();
    this.grid = new Map();
    this.oversized = new Set();
  }

  /* ---------------------------------------------------------- 空间索引 */

  _cells(stroke) {
    const box = strokeBBox(stroke);
    const x0 = Math.floor(box.x0 / CELL);
    const y0 = Math.floor(box.y0 / CELL);
    const x1 = Math.floor(box.x1 / CELL);
    const y1 = Math.floor(box.y1 / CELL);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > MAX_CELLS) return null;
    const keys = [];
    for (let cy = y0; cy <= y1; cy++) {
      for (let cx = x0; cx <= x1; cx++) keys.push(cx + "," + cy);
    }
    return keys;
  }

  _index(stroke) {
    const keys = this._cells(stroke);
    if (!keys) {
      this.oversized.add(stroke);
      return;
    }
    stroke._cells = keys;
    for (const key of keys) {
      let bucket = this.grid.get(key);
      if (!bucket) {
        bucket = new Set();
        this.grid.set(key, bucket);
      }
      bucket.add(stroke);
    }
  }

  _unindex(stroke) {
    this.oversized.delete(stroke);
    for (const key of stroke._cells || []) {
      const bucket = this.grid.get(key);
      if (!bucket) continue;
      bucket.delete(stroke);
      if (!bucket.size) this.grid.delete(key);
    }
    stroke._cells = null;
  }

  /**
   * 和 ``(x0,y0)-(x1,y1)`` 这条线段（外扩 ``pad``）可能有关的笔画。
   *
   * 不保证精确，只保证「真正相交的一定在里面」：调用方照旧要自己做细判。
   * 笔画多起来之后，擦除的开销从「和总数成正比」变成「和扫过的面积成正比」。
   */
  near(x0, y0, x1, y1, pad) {
    const out = [];
    const seen = new Set();
    const cx0 = Math.floor((Math.min(x0, x1) - pad) / CELL);
    const cy0 = Math.floor((Math.min(y0, y1) - pad) / CELL);
    const cx1 = Math.floor((Math.max(x0, x1) + pad) / CELL);
    const cy1 = Math.floor((Math.max(y0, y1) + pad) / CELL);
    for (let cy = cy0; cy <= cy1; cy++) {
      for (let cx = cx0; cx <= cx1; cx++) {
        const bucket = this.grid.get(cx + "," + cy);
        if (!bucket) continue;
        for (const stroke of bucket) {
          if (seen.has(stroke)) continue;
          seen.add(stroke);
          out.push(stroke);
        }
      }
    }
    for (const stroke of this.oversized) {
      if (!seen.has(stroke)) out.push(stroke);
    }
    return out;
  }

  reset(meta, strokes) {
    this.meta = meta;
    this._pages = null;
    this._pagesFor = null;
    this.strokes = [];
    this.byId = new Map();
    this.grid = new Map();
    this.oversized = new Set();
    this.add(strokes || []);
  }

  get id() {
    return this.meta ? this.meta.id : null;
  }

  /** "board"：四向无限；"note"：只向下延伸；"doc"：PDF / 图片，范围固定。 */
  get kind() {
    const kind = this.meta && this.meta.kind;
    return kind === "note" || kind === "doc" ? kind : "board";
  }

  /** 文档板的原件信息（页数 / 每页尺寸），其它白板返回 null。 */
  get doc() {
    if (this.kind !== "doc") return null;
    const doc = this.meta.doc;
    return doc && doc.pages && doc.pages.length ? doc : null;
  }

  /** 文档页在世界坐标里的位置，结果缓存在 meta 上。 */
  get pages() {
    const doc = this.doc;
    if (!doc) return [];
    if (!this._pages || this._pagesFor !== this.meta.id) {
      this._pages = docLayout(doc.pages);
      this._pagesFor = this.meta.id;
    }
    return this._pages;
  }

  /** 可书写范围；大白板没有范围限制，返回 null。 */
  get limits() {
    if (this.kind === "note") return { x0: 0, x1: PAGE_WIDTH, y0: 0 };
    const boxes = this.pages;
    if (!boxes.length) return null;
    const last = boxes[boxes.length - 1];
    const widest = Math.max(...boxes.map((b) => b.w));
    return { x0: 0, x1: widest, y0: 0, y1: last.y + last.h };
  }

  /**
   * 按层叠序号 ``n`` 插到该在的位置。
   *
   * 正常落笔的 n 一路递增，走 push 那条；像素橡皮擦切出来的段沿用原来那条的 n，
   * 会插到中间——以前这种情况是整个数组重排一次，擦一下就排一次，笔画一多就卡。
   */
  _place(stroke) {
    const list = this.strokes;
    const last = list[list.length - 1];
    if (!last || stroke.n >= last.n) {
      list.push(stroke);
      return false;
    }
    let lo = 0;
    let hi = list.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (list[mid].n <= stroke.n) lo = mid + 1;
      else hi = mid;
    }
    list.splice(lo, 0, stroke);
    return true;
  }

  /** 加入若干笔画；返回真正新增的那些（重复 id 会被忽略）。 */
  add(strokes) {
    const added = [];
    let reordered = false;
    for (const stroke of strokes) {
      if (!stroke) continue;
      const existing = this.byId.get(stroke.id);
      if (existing) {
        // 服务端回执会带上正式的层叠序号，用它替换本地的临时值。
        if (typeof stroke.n === "number" && existing.n !== stroke.n) {
          existing.n = stroke.n;
          this.strokes.sort((a, b) => a.n - b.n);
          reordered = true;
        }
        continue;
      }
      if (typeof stroke.n !== "number") {
        const last = this.strokes[this.strokes.length - 1];
        stroke.n = last ? last.n + 1 : 0;
      }
      if (this._place(stroke)) reordered = true;
      this.byId.set(stroke.id, stroke);
      this._index(stroke);
      added.push(stroke);
    }
    return { added, reordered };
  }

  remove(ids) {
    const removed = [];
    for (const id of ids) {
      const stroke = this.byId.get(id);
      if (!stroke) continue;
      this.byId.delete(id);
      this._unindex(stroke);
      removed.push(stroke);
    }
    // 一次只删掉几条时，挨个 splice 比整个数组重建一遍便宜得多；
    // 清一大片（比如撤销一次擦除）才值得走 filter。
    if (removed.length > 16) {
      const gone = new Set(removed);
      this.strokes = this.strokes.filter((s) => !gone.has(s));
    } else {
      for (const stroke of removed) {
        const at = this.strokes.indexOf(stroke);
        if (at >= 0) this.strokes.splice(at, 1);
      }
    }
    return removed;
  }

  clear() {
    const all = this.strokes;
    this.strokes = [];
    this.byId = new Map();
    this.grid = new Map();
    this.oversized = new Set();
    return all;
  }

  /** 由新到旧找命中的笔画（后画的先被擦掉）。 */
  hitTest(x, y, radius, hitFn) {
    const hits = [];
    for (const stroke of this.near(x, y, x, y, radius)) {
      if (hitFn(stroke, x, y, radius)) hits.push(stroke);
    }
    // 网格是按格子遍历出来的，顺序和图层无关，这里补回「后画的先被擦掉」
    hits.sort((a, b) => b.n - a.n);
    return hits.map((stroke) => stroke.id);
  }

  contentBounds() {
    if (!this.strokes.length) return null;
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const stroke of this.strokes) {
      const bbox = strokeBBox(stroke);
      if (bbox.x0 < x0) x0 = bbox.x0;
      if (bbox.y0 < y0) y0 = bbox.y0;
      if (bbox.x1 > x1) x1 = bbox.x1;
      if (bbox.y1 > y1) y1 = bbox.y1;
    }
    return { x0, y0, x1, y1 };
  }
}

// 当前白板的内容（元数据 + 笔画），按层叠序号 n 升序保存。

import { strokeBBox } from "./stroke.js";

export class BoardState {
  constructor() {
    this.meta = null;
    this.strokes = [];
    this.byId = new Map();
  }

  reset(meta, strokes) {
    this.meta = meta;
    this.strokes = [];
    this.byId = new Map();
    this.add(strokes || []);
  }

  get id() {
    return this.meta ? this.meta.id : null;
  }

  size() {
    if (!this.meta) return [0, 0];
    const [unitW, unitH] = this.meta.unit;
    return [this.meta.cols * unitW, this.meta.rows * unitH];
  }

  /** 加入若干笔画；返回真正新增的那些（重复 id 会被忽略）。 */
  add(strokes) {
    const added = [];
    let needSort = false;
    for (const stroke of strokes) {
      if (!stroke) continue;
      const existing = this.byId.get(stroke.id);
      if (existing) {
        // 服务端回执会带上正式的层叠序号，用它替换本地的临时值。
        if (typeof stroke.n === "number" && existing.n !== stroke.n) {
          existing.n = stroke.n;
          needSort = true;
        }
        continue;
      }
      if (typeof stroke.n !== "number") {
        const last = this.strokes[this.strokes.length - 1];
        stroke.n = last ? last.n + 1 : 0;
      }
      const last = this.strokes[this.strokes.length - 1];
      if (last && stroke.n < last.n) needSort = true;
      this.strokes.push(stroke);
      this.byId.set(stroke.id, stroke);
      added.push(stroke);
    }
    if (needSort) this.strokes.sort((a, b) => a.n - b.n);
    return { added, reordered: needSort };
  }

  remove(ids) {
    const removed = [];
    for (const id of ids) {
      const stroke = this.byId.get(id);
      if (!stroke) continue;
      this.byId.delete(id);
      removed.push(stroke);
    }
    if (removed.length) {
      const gone = new Set(removed.map((s) => s.id));
      this.strokes = this.strokes.filter((s) => !gone.has(s.id));
    }
    return removed;
  }

  clear() {
    const all = this.strokes;
    this.strokes = [];
    this.byId = new Map();
    return all;
  }

  /** 由新到旧找命中的笔画（后画的先被擦掉）。 */
  hitTest(x, y, radius, hitFn) {
    const ids = [];
    for (let i = this.strokes.length - 1; i >= 0; i--) {
      const stroke = this.strokes[i];
      if (hitFn(stroke, x, y, radius)) ids.push(stroke.id);
    }
    return ids;
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

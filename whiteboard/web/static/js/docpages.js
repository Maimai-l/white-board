// 文档板的页面底图。
//
// 原件（PDF / 图片）在 Mac 上渲染成位图，这边按需取：只保留视野里的几页，
// 离开视野就丢掉。iPad 上一张 A4 的位图解码后就有十几 MB，整本文档全留着
// 会被 Safari 直接干掉，所以这里必须限制同时存在的页数。
//
// 分辨率按缩放分档，跨档才重新取一张；新图解码完成之前继续画旧的，不闪。

const BUCKETS = [640, 1024, 1600, 2400];
const KEEP_AROUND = 1; // 视野外再多留几页，翻页时不至于空白
const NEIGHBOUR_MAX = 1024; // 视野外的那几页取低分辨率，省内存

function bucketFor(pixels) {
  for (const width of BUCKETS) {
    if (pixels <= width) return width;
  }
  return BUCKETS[BUCKETS.length - 1];
}

export class DocPages {
  constructor(onChange) {
    this.onChange = onChange || (() => {});
    this.boardId = null;
    this.boxes = [];
    this.entries = new Map(); // index -> {img, bucket, pending, failed}
  }

  setBoard(boardId, boxes) {
    if (this.boardId === boardId) {
      this.boxes = boxes || [];
      return;
    }
    this.clear();
    this.boardId = boardId;
    this.boxes = boxes || [];
  }

  clear() {
    for (const entry of this.entries.values()) {
      if (entry.img) entry.img.src = "";
    }
    this.entries.clear();
    this.boardId = null;
    this.boxes = [];
  }

  /** 视野（世界坐标）里覆盖到的页码区间。 */
  visibleRange(view) {
    let first = -1;
    let last = -1;
    for (let i = 0; i < this.boxes.length; i++) {
      const box = this.boxes[i];
      if (box.y + box.h < view.y0 || box.y > view.y1) continue;
      if (first < 0) first = i;
      last = i;
    }
    if (first < 0) return null;
    return [first, last];
  }

  /** 按当前缩放把该出现的页取回来，并把用不到的丢掉。 */
  update(view, scale, dpr) {
    if (!this.boardId || !this.boxes.length) return;
    const range = this.visibleRange(view);
    if (!range) return;
    const from = Math.max(0, range[0] - KEEP_AROUND);
    const to = Math.min(this.boxes.length - 1, range[1] + KEEP_AROUND);

    for (const index of [...this.entries.keys()]) {
      if (index < from || index > to) {
        const entry = this.entries.get(index);
        if (entry && entry.img) entry.img.src = "";
        this.entries.delete(index);
      }
    }
    for (let index = from; index <= to; index++) {
      const visible = index >= range[0] && index <= range[1];
      let wanted = bucketFor(Math.ceil(this.boxes[index].w * scale * dpr));
      if (!visible) wanted = Math.min(wanted, NEIGHBOUR_MAX);
      const entry = this.entries.get(index);
      if (entry && (entry.pending === wanted || (entry.bucket >= wanted && !entry.failed))) {
        continue;
      }
      if (entry && entry.failed && entry.pending) continue;
      this.load(index, wanted);
    }
  }

  load(index, width) {
    const entry = this.entries.get(index) || { img: null, bucket: 0, pending: 0, failed: false };
    entry.pending = width;
    this.entries.set(index, entry);
    const boardId = this.boardId;
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      if (this.boardId !== boardId || this.entries.get(index) !== entry) return;
      // 只有更清楚的图才换上去，避免缩小时把清晰的底图换成糊的
      if (!entry.img || width >= entry.bucket) {
        entry.img = img;
        entry.bucket = width;
      }
      entry.failed = false;
      entry.pending = 0;
      this.onChange();
    };
    img.onerror = () => {
      if (this.entries.get(index) !== entry) return;
      entry.pending = 0;
      entry.failed = true;
    };
    img.src = `/api/doc/${boardId}/${index}?w=${width}`;
  }

  get(index) {
    const entry = this.entries.get(index);
    return entry && entry.img && entry.img.complete ? entry.img : null;
  }
}

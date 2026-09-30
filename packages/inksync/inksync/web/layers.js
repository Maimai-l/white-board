// 图片层（meta.layers）：题图、文档页这类画在笔迹下方（或上方）的图片。
//
// 只加载视野里的层，以及视野上下各 1 层；其余层释放图片。iPad 上一张 A4 的位图
// 解码后就有十几 MB，几百页的文档全留着会被 Safari 直接干掉。
//
// 地址里带 ``{w}`` 的层按分辨率分档加载：换成不小于「显示宽度 × 设备像素比」的最小
// 档位，跨档才重新取一张；新图解码完成之前继续画旧的，不闪。视野外的层最多取 1024。
// 档位固定，服务端可以长期缓存每个档位的图片。不带 ``{w}`` 的层按原图加载一次。

export const BUCKETS = [640, 1024, 1600, 2400];
const KEEP_AROUND = 1;
const NEIGHBOUR_MAX = 1024;

export function bucketFor(pixels) {
  for (const width of BUCKETS) {
    if (pixels <= width) return width;
  }
  return BUCKETS[BUCKETS.length - 1];
}

/** 一层在世界坐标里的范围；没给高度时按已加载图片的比例，还没加载时先按宽度算。 */
export function layerBox(layer, img) {
  let height = layer.height;
  if (!(height > 0)) {
    height = img && img.naturalWidth ? (layer.width * img.naturalHeight) / img.naturalWidth : layer.width;
  }
  return { x: layer.x || 0, y: layer.y || 0, w: layer.width, h: height };
}

export class ImageLayers {
  constructor(onChange) {
    this.onChange = onChange || (() => {});
    this.key = null;
    this.layers = [];
    this.entries = new Map(); // index -> {img, bucket, pending, failed, src}
  }

  /** 换了白板或图片层：``key`` 不同就全部释放。 */
  setLayers(key, layers) {
    const list = layers || [];
    const signature = `${key}|${list.map((l) => l.src).join("\n")}`;
    if (this.key === signature) {
      this.layers = list;
      return;
    }
    this.clear();
    this.key = signature;
    this.layers = list;
  }

  clear() {
    for (const entry of this.entries.values()) {
      if (entry.img) entry.img.src = "";
    }
    this.entries.clear();
    this.key = null;
    this.layers = [];
  }

  box(index) {
    const entry = this.entries.get(index);
    return layerBox(this.layers[index], entry && entry.img && entry.img.complete ? entry.img : null);
  }

  /** 与视野（世界坐标）相交的层的序号区间。 */
  visibleRange(view) {
    let first = -1;
    let last = -1;
    for (let i = 0; i < this.layers.length; i++) {
      const box = this.box(i);
      if (box.y + box.h < view.y0 || box.y > view.y1 || box.x + box.w < view.x0 || box.x > view.x1) continue;
      if (first < 0) first = i;
      last = i;
    }
    return first < 0 ? null : [first, last];
  }

  /** 按当前缩放把该出现的层取回来，并把用不到的释放。 */
  update(view, scale, dpr) {
    if (!this.layers.length) return;
    const range = this.visibleRange(view);
    const from = range ? Math.max(0, range[0] - KEEP_AROUND) : 0;
    const to = range ? Math.min(this.layers.length - 1, range[1] + KEEP_AROUND) : -1;
    for (const index of [...this.entries.keys()]) {
      if (index < from || index > to) {
        const entry = this.entries.get(index);
        if (entry && entry.img) entry.img.src = "";
        this.entries.delete(index);
      }
    }
    for (let index = from; index <= to; index++) {
      const layer = this.layers[index];
      const entry = this.entries.get(index);
      if (!layer.src.includes("{w}")) {
        if (!entry) this.load(index, 0);
        continue;
      }
      const visible = range && index >= range[0] && index <= range[1];
      let wanted = bucketFor(Math.ceil(layer.width * scale * dpr));
      if (!visible) wanted = Math.min(wanted, NEIGHBOUR_MAX);
      if (entry && (entry.pending === wanted || (entry.bucket >= wanted && !entry.failed))) continue;
      if (entry && entry.failed && entry.pending) continue;
      this.load(index, wanted);
    }
  }

  load(index, width) {
    const layer = this.layers[index];
    const entry = this.entries.get(index) || { img: null, bucket: 0, pending: 0, failed: false };
    entry.pending = width || -1;
    this.entries.set(index, entry);
    const key = this.key;
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      if (this.key !== key || this.entries.get(index) !== entry) return;
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
    img.src = width ? layer.src.split("{w}").join(String(width)) : layer.src;
  }

  get(index) {
    const entry = this.entries.get(index);
    return entry && entry.img && entry.img.complete && entry.img.naturalWidth ? entry.img : null;
  }
}

/** 导出用：把全部图片层按原图（``{w}`` 取最大档）加载好，返回与层一一对应的图片（失败为 null）。 */
export function loadAll(layers) {
  return Promise.all(
    (layers || []).map(
      (layer) =>
        new Promise((resolve) => {
          const img = new Image();
          img.crossOrigin = "anonymous";
          img.onload = () => resolve(img);
          img.onerror = () => resolve(null);
          img.src = layer.src.split("{w}").join(String(BUCKETS[BUCKETS.length - 1]));
        })
    )
  );
}

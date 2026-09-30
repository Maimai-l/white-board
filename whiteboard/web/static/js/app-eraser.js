// 橡皮擦落到白板上的那部分：像素橡皮记遮罩、遮罩超限时落实成切分、对象橡皮先按
// 缺口切开再删、只重画碰到的地方、一次拖动里的网络操作攒到每帧发一次。
//
// 这些方法装到 InkPad.prototype 上（见 inkpad.js），里面的 this 就是那块手写板。
// 从原来的 app.js 原样搬过来，只是换了个文件放。

import {
  MASK_LIMIT,
  addMask,
  eraseKind,
  maskBounds,
  maskSize,
  simplifyMask,
  splitStroke,
  strokeBBox,
} from "./stroke.js";
import { plainStroke, uid } from "./util.js";

export function installEraser(proto) {
  Object.assign(proto, methods);
}

const methods = {
  /**
   * 像素橡皮擦：把扫过的那一段记进笔画的遮罩（``m``），渲染和导出时从轮廓里裁掉。
   *
   * 遮罩太大、抽稀之后仍然超过 ``MASK_LIMIT`` 时，才把它落实成切分（见
   * ``bakeMask``）：用切剩的几段换掉原来那一条，发出去的是一条 ``remove`` 加一条
   * ``restore``，切出来的段沿用原来那条的层叠序号 ``n``，叠放关系不会变。
   */
  erasePixels(x, y, radius, from) {
    const [fromX, fromY] = from || [x, y];
    const removed = [];
    const added = [];
    // 只看扫过的那几格里的笔画。以前是每个事件把整块白板过一遍，笔画一多，
    // 擦得越快每个事件要走的距离越长、要比的笔画却一点没少。
    // near() 拿到的是候选，精确判定在 eraseKind 里做。
    const bitten = [];
    const candidates = this.state.near(fromX, fromY, x, y, radius);
    for (const stroke of candidates) {
      // 像素橡皮只有一种处理方式：记进遮罩（见 stroke.js 的 eraseKind）
      if (eraseKind(stroke, fromX, fromY, x, y, radius) === null) continue;
      const before = stroke.m ? stroke.m.map((c) => c.slice()) : null;
      addMask(stroke, fromX, fromY, x, y, radius);
      if (maskSize(stroke) > MASK_LIMIT) {
        // 先抽稀。擦一大块时相邻几段胶囊几乎完全重合，抽掉之后形状看不出变化，
        // 段数能少一个量级。落实成切分是看得见的变化——啃出来的形状换成平口
        // 断面，还会把贴边的细条一起清掉——能不走就不走。
        simplifyMask(stroke);
        // 抽完还超，说明橡皮真的覆盖了这么多互不重合的地方，再抽也抽不动
        if (maskSize(stroke) > MASK_LIMIT) {
          const baked = this.bakeMask(stroke);
          if (baked) {
            removed.push(baked.removed);
            added.push(...baked.added);
            continue;
          }
        }
      }
      bitten.push({ stroke, before });
    }
    if (bitten.length) this.noteBites(bitten, fromX, fromY, x, y, radius);
    if (!removed.length) return [];

    const ids = removed.map((s) => s.id);
    this.state.remove(ids);
    if (added.length) this.state.add(added.map((s) => ({ ...s })));
    this.queueErase(removed, added);
    // 被切的那几笔原来占的地方就是要重画的范围，切剩的段还在里面
    this.dirtyFor(removed);
    // 一次拖动里切出来的段还可能被再切一次。撤销记录只保留最外面那一层：
    // removed 是这次拖动开始前就存在的那些，added 是此刻还留在板上的那些，
    // 中途产生又被切掉的段两边都不进。
    const gone = new Set(ids);
    const mine = new Set(this.pixelBatch.added.map((s) => s.id));
    for (const stroke of removed) {
      if (!mine.has(stroke.id)) this.pixelBatch.removed.push(stroke);
    }
    this.pixelBatch.added = this.pixelBatch.added.filter((s) => !gone.has(s.id));
    this.pixelBatch.added.push(...added);
    return ids;
  },

  /**
   * 记下这一帧的啃咬：发一条 mask 操作、只标胶囊那一小块脏区、并入撤销批次。
   *
   * mask 操作发的是整条笔画当前的遮罩而不是增量——遮罩本来就小，全量发在
   * 断线重连、乱序到达的情况下都不会错，不用管顺序。
   */
  noteBites(bitten, fromX, fromY, x, y, radius) {
    const box = maskBounds([[radius, fromX, fromY, x, y]]);
    this.renderer.requestRect(box.x0, box.y0, box.x1, box.y1);
    for (const { stroke, before } of bitten) {
      // 一次拖动里同一条笔画会被啃很多下，撤销只记这次拖动开始前的那个遮罩
      if (!this.pixelBatch.bites.has(stroke.id)) {
        this.pixelBatch.bites.set(stroke.id, before);
      }
      this.eraseQueue.mask.set(stroke.id, stroke.m.map((c) => c.slice()));
    }
    if (!this.eraseFlush) {
      this.eraseFlush = requestAnimationFrame(() => this.flushErase());
    }
  },

  /**
   * 对象橡皮碰到被像素橡皮啃过的笔画：先按缺口把它切成独立的几条。
   *
   * 像素橡皮不拆笔画，只给笔画挂一条遮罩（见 docs/format.md）。所以看上去断成
   * 两截的笔画其实还是一条，对象橡皮点哪一截都会把整条删掉——这不是橡皮的问题，
   * 是那两截本来就共用一个 id。切开之后每一截是独立的一条，点哪一截删哪一截。
   *
   * 只对真正被碰到的那几条做，不碰橡皮路过的。切分表达不了「削掉半边」，所以
   * 贴边的细条会在这一步被清掉——反正接下来就要删它，看不出区别。
   *
   * 返回有没有切过。切过的话调用方要重新判一次命中，id 全换了。
   */
  splitBitten(ids) {
    const removed = [];
    const added = [];
    for (const id of ids) {
      const stroke = this.state.byId.get(id);
      if (!stroke || !stroke.m || !stroke.m.length) continue;
      const baked = this.bakeMask(stroke);
      if (!baked) continue;
      removed.push(baked.removed);
      added.push(...baked.added);
    }
    if (!removed.length) return false;
    this.state.remove(removed.map((s) => s.id));
    if (added.length) this.state.add(added.map((s) => ({ ...s })));
    this.queueErase(removed, added);
    this.dirtyFor(removed);
    this.pixelBatch.removed.push(...removed);
    this.pixelBatch.added.push(...added);
    return true;
  },

  /**
   * 对象橡皮删掉的这几笔记进撤销批次。
   *
   * 这次拖动里没切过分的话照旧走 eraseBatch。切过的话就得用切分那套记法：
   * 刚切出来又被删掉的段两边都不进——撤销只需要把原来那一条换回来。
   */
  noteObjectErase(removed) {
    const cut = this.pixelBatch;
    if (!cut.removed.length && !cut.added.length) {
      this.eraseBatch.push(...removed);
      return;
    }
    const gone = new Set(removed.map((s) => s.id));
    const mine = new Set(cut.added.map((s) => s.id));
    cut.added = cut.added.filter((s) => !gone.has(s.id));
    for (const stroke of removed) {
      if (!mine.has(stroke.id)) cut.removed.push(plainStroke(stroke));
    }
  },

  /**
   * 遮罩攒到上限了：把它落实成切分，换掉原来那一条笔画。
   *
   * 裁剪开销和胶囊数成正比，所以遮罩不能无限堆。日常来回涂同一块地方由
   * addMask 的「新胶囊盖住旧的就丢掉」挡掉；真涂够 MASK_LIMIT 次不同的地方，
   * 就按这些胶囊把笔画切开，遮罩清空。
   *
   * 切分表达不了「削掉半边」，所以这一步会把贴着边的细条一起清掉。涂到这个
   * 次数本来就是在使劲擦，把残边一起清掉是符合意图的。
   */
  bakeMask(stroke) {
    const base = plainStroke(stroke);
    if (!base.m || !base.m.length) return null;
    let pieces = [{ p: base.p, cut: base.cut | 0 }];
    for (const chain of base.m) {
      const radius = chain[0];
      for (let i = 1; i + 3 < chain.length; i += 2) {
        const next = [];
        for (const piece of pieces) {
          const probe = { tool: base.tool, w: base.w, p: piece.p };
          const runs = splitStroke(
            probe, chain[i], chain[i + 1], chain[i + 2], chain[i + 3], radius
          );
          if (runs === null) {
            next.push(piece);
            continue;
          }
          runs.forEach((run, index) => {
            let cut = run.cut;
            // 这一段最外面那两头沿用原来那一段的切口标记
            if (index === 0) cut |= piece.cut & 1;
            if (index === runs.length - 1) cut |= piece.cut & 2;
            next.push({ p: run.p, cut });
          });
        }
        pieces = next;
      }
    }
    delete base.m;
    return {
      removed: { ...base, m: stroke.m },
      added: pieces.map((piece) => ({
        ...base,
        id: uid(12),
        p: piece.p,
        cut: piece.cut,
      })),
    };
  },

  /** 把这几笔占的地方标成脏区，下一帧只重画这一块。 */
  dirtyFor(strokes) {
    for (const stroke of strokes) {
      const box = strokeBBox(stroke);
      this.renderer.requestRect(box.x0, box.y0, box.x1, box.y1);
    }
  },

  /**
   * 擦除产生的操作攒起来每帧发一次，而不是每个指针事件发一条。
   *
   * 擦得快的时候指针事件本来就密（iPad 上 120Hz），像素橡皮擦一次还要发
   * 「删掉原来那条」加「补上切剩的几段」两条，一次拖动能发出两百多条、上百 KB。
   * 攒一帧再发，顺带把「这一帧里刚切出来又被再切掉」的段两边对消掉，
   * 那些段从来没出现在别的设备上，根本不用发。
   */
  queueErase(removed, added) {
    const queue = this.eraseQueue;
    for (const stroke of removed) {
      queue.mask.delete(stroke.id);
      if (queue.add.has(stroke.id)) queue.add.delete(stroke.id);
      else queue.remove.set(stroke.id, stroke);
    }
    for (const stroke of added) queue.add.set(stroke.id, stroke);
    if (!this.eraseFlush) {
      this.eraseFlush = requestAnimationFrame(() => this.flushErase());
    }
  },

  flushErase() {
    if (this.eraseFlush) {
      cancelAnimationFrame(this.eraseFlush);
      this.eraseFlush = 0;
    }
    const queue = this.eraseQueue;
    const ids = [...queue.remove.keys()];
    const strokes = [...queue.add.values()];
    const masks = [...queue.mask.entries()].map(([id, m]) => ({ id, m }));
    queue.remove.clear();
    queue.add.clear();
    queue.mask.clear();
    if (ids.length) this.net.sendOp({ op: "remove", ids });
    if (strokes.length) this.net.sendOp({ op: "restore", strokes });
    // 被删掉的笔画不用再发它的遮罩
    const gone = new Set(ids);
    const live = masks.filter((entry) => !gone.has(entry.id));
    if (live.length) this.net.sendOp({ op: "mask", masks: live });
  },
};

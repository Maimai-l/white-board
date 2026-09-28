// 橡皮：对象橡皮和像素橡皮的半径、扫过的轨迹、光标圈。
//
// 这些方法装到 InputController.prototype 上（见 input.js 末尾），里面的 this
// 就是那个 InputController。从 input.js 原样搬过来，只是换了个文件放。

// 橡皮的直径，单位是**屏幕像素**。橡皮是工具不是墨水，尺寸恒定在屏幕上，
// 放大就等于擦得更细。这一条有实测依据：同一个倾角在 zoom 1 和 zoom 2.02 下，
// 印记在 drawing 坐标里差一倍，乘回缩放之后对得上（见 docs/eraser.md）。
const ERASER_TIP = 6;

/**
 * 像素橡皮的直径随笔身与屏幕的夹角变化，实测自 iPad 原生 PencilKit。
 *
 * 采集方式：在一大片实心墨迹上点一排孤立的像素橡皮点，每个点固定一个笔身角度，
 * 再把 PKStroke.mask 里对应的那个洞的面积换算成等效直径。二十一个点全部对上，
 * 中心偏差都在 3 pt 以内。
 *
 * 结论有三条，都和我原来拍脑袋定的不一样：
 *
 * * 变粗从 80° 就开始，25° 左右饱和——不是 20° 以上一律笔尖。常握笔大约 50°，
 *   那里原生已经是 17 了，而原来的实现还停在 6，细得没法用橡皮写字。
 * * 最粗约 81，不是 45。
 * * 力度不参与：同一倾角段里力度从 0.15 到 0.51，直径不跟着动。
 *
 * 孤立点只采到 80° / 50° / 35°～27° / ≤25° 这几档，中间两段一开始是线性插值填的，
 * 后来拿拖动那一批做逐像素对照发现两处都不对：68° 处原生已经是 16.5 而插值只给
 * 11.4（窄 45%），44° 处原生还是 17～19 而插值给到 23（宽 30%）。
 *
 * 平台段的下端后来又往下挪过一次。会话 a 有五条拖动落在 37°～43°，原生垂直宽度
 * 全在 15.5～16.0，而当时的曲线在这一段给到 20～28（宽 25%～70%）。加上 44°～68°
 * 那一批，**37° 到 68° 之间是平的，约 16.5**，37° 以下才陡升。
 *
 * 37° 和 35° 之间这个台阶很陡：16.5 跳到 35.2。两侧是两种量法——35° 及以下来自
 * 孤立点的**洞面积换算的等效直径**，37° 及以上来自沿拖动路径量的**垂直宽度**，
 * 只有落笔形状是圆的时候两者才是同一个数。按 35° 处的两个值反推，那里的落笔更像
 * 一个 16.5 宽、75 长的椭圆。
 *
 * 曾经因此把 37° 以下改成「两个锚点之间连直线」，想把台阶抹平。逐像素对照否了这个
 * 改动：抹平之后 test1 的 IoU 从 0.931 掉到 0.862、bench1 从 0.871 掉到 0.830，
 * 而孤立点那几档原样留着时两边都不掉。也就是说，这几档虽然量的是等效直径，拿来
 * 当曲线却更贴近原生擦掉的实际范围，所以留着不动。
 *
 * 台阶带来的手感问题不在曲线上，在「整笔粗细跟不跟着倾斜走」——见 startErase。
 */
const ERASER_CURVE = [
  [90, 6],
  [80, 7.5],
  [68, 16.5],
  [37, 16.5],
  [35, 35],
  [32, 52],
  [28, 75],
  [25, 81],
  [0, 81],
];

/** 查 ERASER_CURVE，按角度线性插值出直径。 */
function eraserDiameter(deg) {
  const curve = ERASER_CURVE;
  if (deg >= curve[0][0]) return curve[0][1];
  for (let i = 0; i + 1 < curve.length; i++) {
    const [aHigh, dHigh] = curve[i];
    const [aLow, dLow] = curve[i + 1];
    if (deg >= aLow) {
      const t = (aHigh - deg) / (aHigh - aLow);
      return dHigh + (dLow - dHigh) * t;
    }
  }
  return curve[curve.length - 1][1];
}

// 由 input.js 传进来（反过来 import 会成环）
let penAltitude;

export function installErase(proto, deps) {
  ({ penAltitude } = deps);
  Object.assign(proto, methods);
}

const methods = {
  /**
   * 橡皮此刻的半径。
   *
   * **对象橡皮擦**碰到哪一笔就整笔删掉，作用点永远是笔尖那么大，不变。
   *
   * **像素橡皮擦**把扫过的那一段从笔画里裁掉，擦多宽由笔身与屏幕的夹角决定：
   * 立着是笔尖，压下去才变宽，25° 以下最粗（见 ``ERASER_CURVE``）。两种都不用
   * 手动调尺寸——想擦大片就把笔压下去，想抠一笔就立起来。
   *
   * 鼠标、手指，以及报不出倾斜的笔，像素模式按中间那一档给：没有倾斜可依据时
   * 一直是笔尖等于没法用，一直最粗又太凶。
   */
  eraserRadius(event) {
    if (this.getTool().eraserMode !== "pixel") return ERASER_TIP / 2;
    // 鼠标和手指没有倾斜可依据，按常握笔的角度给一档，不然一直是笔尖等于没法用
    if (!event || event.pointerType !== "pen") return eraserDiameter(50) / 2;
    return eraserDiameter((penAltitude(event) * 180) / Math.PI) / 2;
  },

  /** 抬笔的位置和最后一个采样点是不是同一处，是的话就不用再扫一段。 */
  sameSpot(last, event) {
    if (!last) return false;
    const [wx, wy] = this.toWorld(event);
    return Math.abs(last[0] - wx) < 1e-6 && Math.abs(last[1] - wy) < 1e-6;
  },

  /**
   * 橡皮的粗细在落笔那一刻定下来，整笔不再跟着倾斜走。
   *
   * 原生就是这样：92 条原生橡皮笔画里，65% 整笔只报一个倾角读数，九成笔内极差
   * 在 2.36° 以内，中位数是 0。而我们原来每个采样点都重新平滑一次（那个平滑
   * 已经去掉了），iPad 报的 ``tiltX`` / ``tiltY`` 又是整度的，写字时笔身
   * 本来就在晃，于是同一笔里直径能差 2.7 倍——擦痕一节粗一节细，像一串香肠。
   *
   * 悬停时的光标圈照旧跟着倾斜走（``updateCursor``），那是预览，本来就该跟手。
   */
  startErase(event) {
    this.erase = {
      pointerId: event.pointerId,
      ids: [],
      radius: this.eraserRadius(event),
      last: null,
    };
    this.moveErase(event);
  },

  /**
   * 屏幕半径换算成世界半径：判定和光标都在世界坐标里做。
   *
   * 橡皮在屏幕上恒定大小，两个方向都成立，所以直接除以缩放。放大方向早就量过
   * （同一倾角在 zoom 1 和 zoom 2.02 下，印记在 drawing 坐标里差一倍）；缩小方向
   * 由会话 c 定下来：zoom 0.25、倾角 10.4°，原生擦出来的垂直宽度是 322 个 drawing
   * 单位，乘回缩放是 80.5 个屏幕单位，曲线在那个倾角给的是 81。
   *
   * 这里曾经有过一个 `Math.max(scale, 1)` 的下限，依据是 bench2：zoom 0.25 下
   * 2361 个橡皮采样、其中 200 个直接压在可见墨迹上，二十条笔画却一个遮罩都没有。
   * 会话 c 在同样的缩放下擦得很彻底，所以那不是缩放规则，bench2 那次是整条橡皮
   * 没有被 PencilKit 记录下来。详见 docs/eraser.md。
   */
  worldRadius(screenRadius) {
    return screenRadius / this.viewport.scale;
  },

  /**
   * 橡皮走到一个采样点：扫过上一点到这一点之间那一段。
   *
   * 粗细在落笔时定下，整笔不再变（见 startErase）。
   *
   * 擦得快的时候两次采样之间能隔开一大段，判定要按扫过的这条线段来，
   * 只看当前这个点会留下一串没擦到的缝。
   */
  eraseAt(erase, event) {
    const [wx, wy] = this.toWorld(event);
    const radius = this.worldRadius(erase.radius);
    this.renderer.cursor = { x: wx, y: wy, r: radius };
    const hit = this.hooks.onErase(wx, wy, radius, erase.last);
    erase.last = [wx, wy];
    if (hit && hit.length) erase.ids.push(...hit);
  },

  /**
   * 橡皮和画线一样要吃掉一帧里的全部合并采样点。
   *
   * iPad 上笔是 120Hz 而 pointermove 一帧才来一次，中间那些点都塞在
   * getCoalescedEvents 里。只取最后一个，等于把一帧里的一段曲线压成一条直线，
   * 擦得越快压得越狠，擦痕边上就出现一节一节的直棱。画线那边一直是取全部的，
   * 橡皮这边漏了。
   */
  moveErase(event) {
    const erase = this.erase;
    if (!erase || erase.pointerId !== event.pointerId) return;
    const events = event.getCoalescedEvents ? event.getCoalescedEvents() : null;
    if (events && events.length > 1) {
      for (const sample of events) this.eraseAt(erase, sample);
    } else {
      this.eraseAt(erase, event);
    }
  },

  endErase(event) {
    const erase = this.erase;
    // 抬笔那一下的位置也要擦掉。最后一个 pointermove 停在上一帧，笔离开屏幕
    // 之前还走了一段，这一段只有 pointerup 里有。不补的话擦痕停在上一帧的
    // 位置，末端留下一道正好是橡皮直径宽的硬边——手感上就是「一松手就多出个
    // 断面」，而且笔在那一段上真正压过的地方还留着墨。
    if (erase && event && !this.sameSpot(erase.last, event)) {
      this.eraseAt(erase, event);
    }
    this.erase = null;
    this.renderer.cursor = null;
    // 一律通知抬笔：啃边不删任何笔画，ids 是空的，但撤销记录要在这里收口
    if (erase) this.hooks.onEraseEnd(erase.ids);
  },

  updateCursor(event) {
    const tool = this.getTool();
    if (tool.tool !== "eraser") {
      if (this.renderer.cursor) this.renderer.cursor = null;
      return;
    }
    const [wx, wy] = this.toWorld(event);
    this.renderer.cursor = { x: wx, y: wy, r: this.worldRadius(this.eraserRadius(event)) };
  },
};

// 尺子在输入这一侧的两件事：手指落在尺子上就拖尺子而不是拖画布；笔从尺边附近起笔
// 就把整笔投影到尺边上。几何在 ruler.js 里。
//
// 这些方法装到 InputController.prototype 上（见 input.js 末尾），里面的 this
// 就是那个 InputController。
//
// 手指怎么分（尺子显示着的时候）：
//   * 没有别的手势时，手指落在尺身上就是「尺子手指」：一根平移尺子，两根平移加旋转；
//   * 已经有一根尺子手指，第二根也落在尺身上，两根一起转尺子；第二根落在尺子外面，
//     说明这是在捏合画布：先前那根交给画布手势，两根一起平移缩放，尺子停在原地；
//   * 画布手势已经开始（第一根落在尺子外面），之后落下的手指都归画布，落在尺子上
//     也一样；
//   * 手掌屏蔽照旧优先：Pencil 正在写的时候新落下的手指一律不管。
// Pencil 不归尺子管，落在尺身上照样写字（贴到近的那条边）。

import { moveRuler, projectOntoLine, rulerContains, snapEdge } from "./ruler.js";

export function installRuler(proto) {
  Object.assign(proto, methods);
}

const methods = {
  /** 接上一把尺子（ruler.js 的 Ruler）；传 null 拿掉。 */
  setRuler(ruler) {
    this.ruler = ruler || null;
    this.rulerGrab = null;
  },

  rulerShown() {
    return !!(this.ruler && this.ruler.visible);
  },

  /** 这个指针事件落在尺身上没有。 */
  onRuler(event) {
    if (!this.rulerShown()) return false;
    const [x, y] = this.toScreen(event);
    return rulerContains(this.ruler, x, y);
  },

  /**
   * 手指落下：按上面的规则决定它归尺子（返回 "ruler"）还是照 classify 的结果走。
   * 第二根手指落在尺子外面时，把先前那根尺子手指交给画布手势。
   */
  routeRulerTouch(event, role) {
    if (event.pointerType !== "touch" || !this.rulerShown()) return role;
    const grab = this.rulerGrab;
    if (grab && grab.points.size) {
      if (grab.points.size >= 2 || this.onRuler(event)) return "ruler";
      this.handRulerToGesture();
      return "gesture";
    }
    if (this.gesture || this.draw || this.erase) return role;
    return this.onRuler(event) ? "ruler" : role;
  },

  startRulerPointer(event) {
    const grab = this.rulerGrab || (this.rulerGrab = { points: new Map(), start: null, from: [], ids: [] });
    const [x, y] = this.toScreen(event);
    grab.points.set(event.pointerId, { x, y, clientX: event.clientX, clientY: event.clientY });
    this.rebaseRuler();
  },

  /** 从现在的尺子和手指位置重新算起：手指数变了、或者暂停跟手之后都要重来一次。 */
  rebaseRuler() {
    const grab = this.rulerGrab;
    if (!grab || !this.ruler) return;
    grab.ids = [...grab.points.keys()].slice(0, 2);
    grab.from = grab.ids.map((id) => {
      const point = grab.points.get(id);
      return [point.x, point.y];
    });
    grab.start = this.ruler.state();
  },

  moveRulerPointer(event) {
    const grab = this.rulerGrab;
    const point = grab && grab.points.get(event.pointerId);
    if (!point) return;
    const [x, y] = this.toScreen(event);
    Object.assign(point, { x, y, clientX: event.clientX, clientY: event.clientY });
    if (!this.rulerShown()) return;
    // 正在沿尺边写的时候尺子不动：这一笔的直线在落笔时就定了，尺子再跟着手指走，
    // 画出来的线就不在尺边上了。抬笔之后从当时的位置接着跟手，不会跳。
    if (this.draw) {
      this.rebaseRuler();
      return;
    }
    if (!grab.ids.includes(event.pointerId)) return;
    const to = grab.ids.map((id) => {
      const p = grab.points.get(id);
      return [p.x, p.y];
    });
    this.ruler.set(moveRuler(grab.start, grab.from, to, this.ruler.bounds));
  },

  endRulerPointer(pointerId) {
    const grab = this.rulerGrab;
    if (!grab) return;
    grab.points.delete(pointerId);
    if (!grab.points.size) this.rulerGrab = null;
    else this.rebaseRuler();
  },

  /** 尺子手指转给画布手势：从它现在的位置开始算，尺子留在刚才拖到的地方。 */
  handRulerToGesture() {
    const grab = this.rulerGrab;
    this.rulerGrab = null;
    if (!grab) return;
    for (const [pointerId, point] of grab.points) {
      const entry = this.pointers.get(pointerId);
      if (!entry) continue;
      entry.role = "gesture";
      this.startGesture({ pointerId, clientX: point.clientX, clientY: point.clientY });
    }
  },

  /**
   * 落笔时决定这一笔贴不贴尺边：贴的话返回尺边在**白板坐标**里的直线，否则 null。
   * 判定按屏幕像素（见 ruler.js 的 SNAP_DISTANCE），和缩放无关。
   */
  rulerSnap(event) {
    if (!this.rulerShown()) return null;
    const [sx, sy] = this.toScreen(event);
    const edge = snapEdge(this.ruler, sx, sy);
    if (!edge) return null;
    const [x, y] = this.viewport.toWorld(edge.x, edge.y);
    return { x, y, ux: edge.ux, uy: edge.uy };
  },

  /** 这一笔贴着尺边的话，把白板坐标里的点投影到尺边上。 */
  snapToRuler(point) {
    const line = this.draw && this.draw.snap;
    return line ? projectOntoLine(line, point[0], point[1]) : point;
  },
};

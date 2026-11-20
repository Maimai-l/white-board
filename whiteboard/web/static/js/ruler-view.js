// 尺子的样子：画布上面单独一层，只管画，不吃事件。
//
// 手指和笔怎么跟尺子打交道在 inksync 的 input-ruler.js 里，几何和状态在 ruler.js 里；
// 这里只根据 Ruler 的状态摆一个元素。样式在 css/app.css 的「尺子」那一节，照 vendor 里
// PencilKit 复刻的直尺画。
//
// 结构：
//   .ruler                铺满画布区域的一层，pointer-events: none；转动时带 data-rotating
//     .ruler-bar          尺身。以尺子中心为原点旋转；长度不小于画布对角线的两倍，
//                         怎么转两头都在屏幕外面，看上去没有尽头。长度取 200 的
//                         整数倍，中心离两头都是 100 的整数倍，正好落在一道长刻度上
//       svg.ruler-ticks   两条长边上的刻度：每 10 一道短的，每 50 一道长的
//       .ruler-angle      当前角度，0–90°（见 ruler.js 的 displayAngle），放在尺子上
//                         离屏幕中心最近的地方；只在转动时显示

import { displayAngle } from "/inksync/ruler.js";
import { el } from "/inksync/util.js";

const SVG = "http://www.w3.org/2000/svg";
const TICK_STEP = 10;
const TICK_SHORT = 9;
const TICK_LONG = 16;
// 停止转动多久之后角度淡出（毫秒）
const ANGLE_LINGER_MS = 700;

export class RulerView {
  constructor(stage, ruler) {
    this.stage = stage;
    this.ruler = ruler;
    this.ticks = document.createElementNS(SVG, "svg");
    this.ticks.setAttribute("class", "ruler-ticks");
    this.ticks.setAttribute("aria-hidden", "true");
    this.label = el("span", { class: "ruler-angle" });
    this.bar = el("div", { class: "ruler-bar" }, [this.ticks, this.label]);
    this.root = el("div", { class: "ruler", "aria-hidden": "true", hidden: "" }, [this.bar]);
    stage.append(this.root);
    this.length = 0;
    this.lastAngle = null;
    this.unsubscribe = ruler.onChange(() => this.render());
    this.measure = () => ruler.setBounds(stage.clientWidth, stage.clientHeight);
    addEventListener("resize", this.measure);
    this.measure();
    this.render();
  }

  render() {
    const ruler = this.ruler;
    this.root.hidden = !ruler.visible;
    if (!ruler.visible) {
      this.lastAngle = null;
      return;
    }
    const length = Math.ceil(Math.hypot(ruler.bounds.w, ruler.bounds.h) / 100) * 200;
    if (length !== this.length || ruler.width !== this.thickness) this.drawTicks(length, ruler.width);
    const bar = this.bar.style;
    bar.width = `${length}px`;
    bar.height = `${ruler.width}px`;
    bar.transform =
      `translate(${ruler.cx}px, ${ruler.cy}px) rotate(${ruler.angle}rad) translate(-50%, -50%)`;
    this.label.textContent = `${displayAngle(ruler.angle)}°`;
    // 角度标签沿尺子放到离屏幕中心最近的地方（屏幕中心在尺子方向上的投影）
    const dx = ruler.bounds.w / 2 - ruler.cx;
    const dy = ruler.bounds.h / 2 - ruler.cy;
    const along = dx * Math.cos(ruler.angle) + dy * Math.sin(ruler.angle);
    const limit = length / 2 - 60;
    this.label.style.left = `${length / 2 + Math.max(-limit, Math.min(limit, along))}px`;
    if (this.lastAngle !== null && ruler.angle !== this.lastAngle) this.showAngle();
    this.lastAngle = ruler.angle;
  }

  drawTicks(length, thickness) {
    this.length = length;
    this.thickness = thickness;
    const mid = length / 2;
    const count = Math.floor(mid / TICK_STEP);
    let d = "";
    for (let k = -count; k <= count; k++) {
      const x = mid + k * TICK_STEP;
      const size = k % 5 === 0 ? TICK_LONG : TICK_SHORT;
      d += `M${x} 0V${size}M${x} ${thickness}V${thickness - size}`;
    }
    this.ticks.setAttribute("viewBox", `0 0 ${length} ${thickness}`);
    this.ticks.setAttribute("width", String(length));
    this.ticks.setAttribute("height", String(thickness));
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    path.setAttribute("stroke", "rgba(0,0,0,.55)");
    path.setAttribute("stroke-width", "1");
    this.ticks.replaceChildren(path);
  }

  /** 转动时显示角度，停下 ANGLE_LINGER_MS 之后淡出。 */
  showAngle() {
    this.root.dataset.rotating = "";
    clearTimeout(this.angleTimer);
    this.angleTimer = setTimeout(() => delete this.root.dataset.rotating, ANGLE_LINGER_MS);
  }

  destroy() {
    clearTimeout(this.angleTimer);
    this.unsubscribe();
    removeEventListener("resize", this.measure);
    this.root.remove();
  }
}

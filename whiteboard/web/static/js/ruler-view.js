// 尺子的样子：画布上面单独一层，只管画，不吃事件。
//
// 手指和笔怎么跟尺子打交道在 inksync 的 input-ruler.js 里，几何和状态在 ruler.js 里；
// 这里只根据 Ruler 的状态摆一个元素。样式全在 css/app.css 的「尺子」那一节，
// 换外观只改那一节和这个文件，逻辑不用动。
//
// 结构：
//   .ruler                铺满画布区域的一层，pointer-events: none
//     .ruler-bar          尺身。以尺子中心为原点旋转；长度不小于画布对角线的两倍，
//                         怎么转两头都在屏幕外面，看上去没有尽头。长度取 200 的
//                         整数倍：刻度从左端每 100 一道大刻度，中心就正好在一道上
//       .ruler-angle      当前角度，0–90°（见 ruler.js 的 displayAngle）

import { displayAngle } from "/inksync/ruler.js";
import { el } from "/inksync/util.js";

export class RulerView {
  constructor(stage, ruler) {
    this.stage = stage;
    this.ruler = ruler;
    this.label = el("span", { class: "ruler-angle" });
    this.bar = el("div", { class: "ruler-bar" }, [this.label]);
    this.root = el("div", { class: "ruler", "aria-hidden": "true", hidden: "" }, [this.bar]);
    stage.append(this.root);
    this.unsubscribe = ruler.onChange(() => this.render());
    this.measure = () => ruler.setBounds(stage.clientWidth, stage.clientHeight);
    addEventListener("resize", this.measure);
    this.measure();
    this.render();
  }

  render() {
    const ruler = this.ruler;
    this.root.hidden = !ruler.visible;
    if (!ruler.visible) return;
    const length = Math.ceil(Math.hypot(ruler.bounds.w, ruler.bounds.h) / 100) * 200;
    const bar = this.bar.style;
    bar.width = `${length}px`;
    bar.height = `${ruler.width}px`;
    bar.transform =
      `translate(${ruler.cx}px, ${ruler.cy}px) rotate(${ruler.angle}rad) translate(-50%, -50%)`;
    this.label.textContent = `${displayAngle(ruler.angle)}°`;
  }

  destroy() {
    this.unsubscribe();
    removeEventListener("resize", this.measure);
    this.root.remove();
  }
}

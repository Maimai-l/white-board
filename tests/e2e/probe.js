// 浏览器用例在页面里用的小工具，挂在 window.__t 上。
//
// support.prepare() 在页面加载之前注入（page.add_init_script），刷新之后还在。
// 以前每个用例各写一份「合成指针事件」「摆一条宽笔画」「量像素」，这里各留一份。
(() => {
  const stage = () => document.getElementById("stage");

  /**
   * 合成一个指针事件并派发出去。默认是笔、编号 1、按下状态；抬笔时 buttons 是 0。
   * ``init`` 里的字段覆盖默认值；``target`` 默认是画布。
   */
  function fire(type, x, y, init = {}, target = stage()) {
    const up = type === "pointerup";
    target.dispatchEvent(
      new PointerEvent(type, {
        clientX: x,
        clientY: y,
        pointerType: "pen",
        pointerId: 1,
        pressure: up ? 0 : 0.5,
        buttons: up ? 0 : 1,
        bubbles: true,
        cancelable: true,
        isPrimary: true,
        ...init,
      }),
    );
  }

  /** 一整笔：落笔、依次移动、在最后一个点抬笔。 */
  function stroke(points, init = {}) {
    fire("pointerdown", points[0][0], points[0][1], init);
    for (const [x, y] of points.slice(1)) fire("pointermove", x, y, init);
    const [x, y] = points[points.length - 1];
    fire("pointerup", x, y, { ...init, pressure: 0 });
  }

  /**
   * 不经过浏览器、直接交给 whiteboard.input 的采样（回放走的也是这条路）。
   * 坐标相对画布左上角；getCoalescedEvents 默认只有它自己。
   */
  function sample(x, y, init = {}) {
    const rect = stage().getBoundingClientRect();
    return {
      clientX: rect.left + x,
      clientY: rect.top + y,
      pointerType: "pen",
      pointerId: 3,
      pressure: 0.5,
      buttons: 1,
      button: 0,
      isPrimary: true,
      preventDefault() {},
      getCoalescedEvents() {
        return [this];
      },
      ...init,
    };
  }

  /** 用一串采样走完一次输入：第一个落笔、中间移动、最后一个抬笔。 */
  function drive(samples) {
    const input = whiteboard.input;
    input.onDown(samples[0]);
    for (const s of samples.slice(1, -1)) input.onMove(s);
    input.onUp({ ...samples[samples.length - 1], buttons: 0 });
  }

  /**
   * 橡皮用例的起点：板上只剩一条很宽的横向笔画 ``band``，工具切到像素橡皮，
   * 网络发送换成空操作（这些用例只看本地状态）。
   */
  function band({ width = 96, count = 200, from = -400, step = 4, y = -10 } = {}) {
    whiteboard.net.send = () => {};
    whiteboard.state.remove(whiteboard.state.strokes.map((s) => s.id));
    const p = [];
    for (let i = 0; i < count; i++) p.push(from + i * step, y, 1);
    whiteboard.state.add([{ id: "band", tool: "pen", color: "#000", w: width, p, n: 1 }]);
    whiteboard.tool = { ...whiteboard.tool, tool: "eraser", eraserMode: "pixel" };
    return whiteboard.state.byId.get("band");
  }

  /** 这条笔画现在的遮罩（没有就是空数组）。 */
  const mask = (id = "band") => (whiteboard.state.byId.get(id) || {}).m || [];

  /** 视口的平移量。 */
  const view = () => [whiteboard.viewport.x, whiteboard.viewport.y];

  /** 元素外框的中心点（屏幕坐标）。 */
  function center(selector) {
    const r = document.querySelector(selector).getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }

  /**
   * 把一个 Path2D 按 nonzero 填进一块画布，返回每个像素是否有墨。
   * ``box`` 是世界坐标里的范围 [x0, y0, x1, y1]，``scale`` 是每个世界单位几个像素。
   */
  function raster(path, box, scale = 2) {
    const [x0, y0, x1, y1] = box;
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil((x1 - x0) * scale);
    canvas.height = Math.ceil((y1 - y0) * scale);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.setTransform(scale, 0, 0, scale, -x0 * scale, -y0 * scale);
    ctx.fillStyle = "#000";
    ctx.fill(path, "nonzero");
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const ink = new Uint8Array(canvas.width * canvas.height);
    for (let i = 0; i < ink.length; i++) ink[i] = data[i * 4 + 3] > 127 ? 1 : 0;
    return { ink, width: canvas.width, height: canvas.height };
  }

  /** 把整块白板渲染到一块画布上（不带背景），给出画布和它对应的世界范围。 */
  function render() {
    const bounds = whiteboard.state.contentBounds();
    const canvas = whiteboard.Renderer.renderToCanvas(whiteboard.state, {
      scale: 1,
      background: false,
      bounds,
    });
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const inkAt = (wx, wy) => {
      const x = Math.round(wx - bounds.x0);
      const y = Math.round(wy - bounds.y0);
      if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return null;
      return data[(y * canvas.width + x) * 4 + 3] > 127;
    };
    return { canvas, bounds, data, inkAt };
  }

  window.__t = { fire, stroke, sample, drive, band, mask, view, center, raster, render };
})();

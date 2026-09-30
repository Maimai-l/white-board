// 在其他页面里嵌入一块手写板。用法见 docs/embed.md。
//
//   import { createInkPad } from "/sdk/inkpad.js";
//   const pad = createInkPad(document.querySelector("#answer"), {
//     app: "qb", board: "qb-9709-s23-12-q3", name: "第 3 题", folder: "刷题",
//   });
//
// 手写板固定在 ``board`` 指定的白板上（docs/protocol.md「固定白板的连接」）：
// 不存在就由服务端新建，内容同步到 Mac，在白板选择界面里可以看到。
// ``transport`` 选项可以改成不同步、连别的服务，或者自定义传输。

import { InkPad, deviceClientId, defaultTool } from "./inkpad.js";
import { exportDataURL } from "./exporter.js";
import { uid } from "./util.js";

const STYLE_ID = "inkpad-style";
const STYLE = `
.inkpad-stage {
  position: relative; width: 100%; height: 100%; overflow: hidden;
  background: #fdfbff; touch-action: none;
  -webkit-touch-callout: none; -webkit-user-select: none; user-select: none;
}
.inkpad-stage canvas {
  position: absolute; inset: 0; width: 100%; height: 100%; display: block;
  -webkit-touch-callout: none; -webkit-user-drag: none;
}
.inkpad-stage canvas.inkpad-live { pointer-events: none; }
`;

function installStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLE;
  document.head.append(style);
}

function detectRole() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || navigator.maxTouchPoints > 1 ? "ipad" : "mac";
}

/**
 * 在 ``container`` 里建一块手写板，尺寸跟随容器。返回的对象就是 InkPad，另外有
 * ``exportPNG()`` 和 ``destroy()``。
 *
 * @param {HTMLElement} container
 * @param {object} options
 * @param {string} options.app      应用名，``^[a-z0-9-]{1,32}$``；不同步（transport: "local"）时可以省略
 * @param {string} options.board    白板 id，``^[A-Za-z0-9_-]{1,64}$``；同一 id 在各设备上是同一块白板
 * @param {string} [options.name]   新建白板时的名称
 * @param {string} [options.kind]   新建白板时的类型：``board``（默认）或 ``note``
 * @param {string} [options.folder] 新建白板时放进的文件夹
 * @param {{src: string, width: number}} [options.underlay]  底图（例如题图），``src`` 须在 /apps/ 下
 * @param {object} [options.tool]   初始工具：{tool, color, width, eraserMode}
 * @param {boolean} [options.fingerDraw]  手指是否书写（默认只有 Apple Pencil 书写）
 * @param {"mac"|"ipad"} [options.role]    默认按设备判断
 * @param {string|object|Function} [options.transport]  同步方式，见 inkpad.js 的 createTransport
 * @param {{meta?: object, strokes?: object[]}} [options.initial]  不同步时的初始内容
 */
export function createInkPad(container, options) {
  if (!container) throw new Error("createInkPad：缺少容器元素");
  const local = options && (options.transport === "local" || options.transport === null);
  if (!options || !options.board || (!options.app && !local)) {
    throw new Error("createInkPad：需要 board，同步时还需要 app");
  }
  installStyle();

  const stage = document.createElement("div");
  stage.className = "inkpad-stage";
  const base = document.createElement("canvas");
  const live = document.createElement("canvas");
  live.className = "inkpad-live";
  stage.append(base, live);
  container.append(stage);

  const pin = {
    board: options.board,
    app: options.app,
    ...(options.name ? { name: options.name } : {}),
    ...(options.kind ? { kind: options.kind } : {}),
    ...(options.folder ? { folder: options.folder } : {}),
    ...(options.underlay ? { underlay: options.underlay } : {}),
  };
  const pad = new InkPad({
    stage,
    base,
    live,
    role: options.role || detectRole(),
    // 同一台设备上白板应用和各块手写板各用一条连接，id 不能相同：
    // 服务端按 id 登记连接，相同的 id 会顶掉另一条
    clientId: `${deviceClientId()}-${uid(6)}`,
    pin,
    tool: { ...defaultTool(), ...(options.tool || {}) },
    embedded: true,
    transport: options.transport,
    initial: options.initial,
  });
  // 直接设，不经过 setFingerDraw：那里会写进白板应用自己的设置
  if (options.fingerDraw !== undefined) pad.input.fingerDraw = !!options.fingerDraw;

  // 容器改变尺寸时画布跟着改；嵌入的页面里窗口大小不变、容器却可能变
  const observer = new ResizeObserver(() => {
    if (pad.destroyed) return;
    pad.renderer.resize();
    pad.input.invalidateRect();
    pad.clampView();
    pad.renderer.requestFull();
  });
  observer.observe(stage);
  // 页面滚动之后书写区域在屏幕上的位置变了
  const onScroll = () => pad.input.invalidateRect();
  addEventListener("scroll", onScroll, true);

  pad.exportPNG = () => exportDataURL(pad.state);
  pad.clear = () => pad.clearBoard();
  const destroy = pad.destroy.bind(pad);
  pad.destroy = () => {
    observer.disconnect();
    removeEventListener("scroll", onScroll, true);
    destroy();
    stage.remove();
  };
  return pad.start();
}

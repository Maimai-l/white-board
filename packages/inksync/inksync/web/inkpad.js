// 手写板的对外接口（docs/design/inksync-interface.zh-CN.md 第 4 节）。
//
//   import { createInkPad } from "/inksync/inkpad.js";
//   const pad = createInkPad(document.querySelector("#answer"), {
//     board: "u42-9709-s23-12-q3",
//     create: { name: "第 3 题", canvas: { mode: "fixed", width: 800, height: 1400 },
//               layers: [{ src: "/static/q3.png", x: 0, y: 0, width: 800 }] },
//   });
//
// 返回的对象只有文档里列出的成员；手写板内部的类不对外。

import { InkPad, deviceClientId, defaultTool } from "./pad.js";
import { exportImage } from "./exporter.js";
import { setReportURL } from "./report.js";
import { uid } from "./util.js";
import { VERSION } from "./version.js";

export { VERSION };

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
const BOARD_RE = /^[A-Za-z0-9_-]{1,64}$/;
const SPACE_RE = /^[a-z0-9-]{0,32}$/;

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
 * 在 ``container`` 里建一块手写板，尺寸跟随容器。
 *
 * @param {HTMLElement} container
 * @param {object} options  见 docs/design/inksync-interface.zh-CN.md 4.1 节
 */
export function createInkPad(container, options = {}) {
  if (!container) throw new Error("createInkPad：缺少容器元素");
  if (!options.board || !BOARD_RE.test(options.board)) {
    throw new Error("createInkPad：board 必须符合 ^[A-Za-z0-9_-]{1,64}$");
  }
  const space = options.space || "";
  if (!SPACE_RE.test(space)) throw new Error("createInkPad：space 必须符合 ^[a-z0-9-]{0,32}$");
  if (options.report !== undefined) setReportURL(options.report);
  installStyle();

  const stage = document.createElement("div");
  stage.className = "inkpad-stage";
  const base = document.createElement("canvas");
  const live = document.createElement("canvas");
  live.className = "inkpad-live";
  stage.append(base, live);
  container.append(stage);

  const storage = options.storage || `inksync:${space}`;
  const core = new InkPad({
    stage,
    base,
    live,
    role: options.role || detectRole(),
    // 同一台设备上的几块手写板各用一条连接，id 不能相同：服务端按 id 登记连接
    clientId: `${deviceClientId(storage)}-${uid(6)}`,
    target: {
      space,
      board: options.board,
      create: options.create || undefined,
      readonly: !!options.readonly,
      follow: false,
    },
    storage,
    tool: { ...defaultTool(), ...(options.tool || {}) },
    fingerDraw: !!options.fingerDraw,
    undoLimit: options.undoLimit,
    embedded: true,
    transport: options.transport,
    initial: options.initial,
  });

  // 容器改变尺寸时画布跟着改；嵌入的页面里窗口大小不变、容器却可能变
  const observer = new ResizeObserver(() => {
    if (core.destroyed) return;
    core.renderer.resize();
    core.input.invalidateRect();
    core.clampView();
    core.renderer.requestFull();
  });
  observer.observe(stage);
  // 页面滚动之后书写区域在屏幕上的位置变了
  const onScroll = () => core.input.invalidateRect();
  addEventListener("scroll", onScroll, true);

  const pad = {
    open: (board, opts = {}) => {
      if (!board || !BOARD_RE.test(board)) return Promise.reject(new Error("board 不合法"));
      return core.open(board, opts);
    },
    setTool: (tool) => core.setTool({ ...core.tool, ...(tool || {}) }),
    undo: () => core.undo(),
    redo: () => core.redo(),
    clear: () => core.clearBoard(),
    fit: () => core.fit(),
    zoom: (factor) => core.zoom(factor),
    setMeta: (patch) => core.setMeta(patch),
    exportPNG: (opts = {}) => exportImage(core.state, opts),
    snapshot: () => core.snapshot(),
    load: (board) => core.load(board),
    on: (event, listener) => core.on(event, listener),
    destroy: () => {
      observer.disconnect();
      removeEventListener("scroll", onScroll, true);
      core.destroy();
      stage.remove();
    },
  };
  Object.defineProperties(pad, {
    board: { get: () => (core.state.meta ? JSON.parse(JSON.stringify(core.state.meta)) : null), enumerable: true },
    status: { get: () => core.net.status, enumerable: true },
    tool: { get: () => ({ ...core.tool }), enumerable: true },
    caps: { get: () => ({ ...core.caps }), enumerable: true },
    locked: { get: () => core.locked || null, enumerable: true },
    shell: { get: () => ({ ...core.shell }), enumerable: true },
    info: { get: () => ({ ...core.info }), enumerable: true },
    version: { value: VERSION, enumerable: true },
  });
  core.start();
  return Object.freeze(pad);
}

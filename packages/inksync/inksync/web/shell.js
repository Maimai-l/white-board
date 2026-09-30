// 与 iPad 外壳的握手（docs/ipad-shell.md 第 5、6 节）。
//
// 外壳是一个装着 WKWebView 的原生应用，加载的就是这个页面。它在 UIKit 那一层拿到
// Pencil 的全部采样（约 240/s、坐标带小数、带预测），再转给网页；网页把这些采样
// 当成 Pencil 的输入来源，其余一切照旧。
//
// 不在外壳里的时候（直接用 Safari 或 Mac 窗口打开），这里什么都不做：
// window.webkit.messageHandlers.whiteboard 不存在，Pencil 照旧走指针事件。

import { SHELL_BRIDGE } from "./input.js";

function shellHandler() {
  const handlers = window.webkit && window.webkit.messageHandlers;
  return (handlers && handlers.whiteboard) || null;
}

/** 页面是不是跑在 iPad 外壳里。 */
export function inShell() {
  return shellHandler() !== null;
}

/** 给外壳发一条命令（目前只有「换一台 Mac」）。不在外壳里就什么都不做。 */
export function shellCommand(type) {
  const handler = shellHandler();
  if (!handler) return false;
  try {
    handler.postMessage({ type });
  } catch (err) {
    return false;
  }
  return true;
}

/** 外壳报来的接口版本在不在网页认得的范围里。 */
export function bridgeSupported(bridge) {
  return Number.isInteger(bridge) && bridge >= SHELL_BRIDGE[0] && bridge <= SHELL_BRIDGE[1];
}

/**
 * 挂上 ``window.whiteboardShell``，外壳通过它把采样和握手结果送进来；有外壳的话
 * 顺带发出握手。返回是否在外壳里。
 *
 * ``active`` 以外壳的回复为准（版本不兼容时它会自己回 false 并提示更新外壳），
 * 这边再按自己的范围核对一遍：两边都认才切到外壳来源。
 */
export function connectShell(app) {
  pads.add(app);
  if (shellReply) applyShell(app, shellReply);
  if (window.whiteboardShell) return shellHandler() !== null;
  window.whiteboardShell = {
    // 页面上可以有几块手写板（嵌入的情况）：每一批都交给每一块，各自只接
    // 落笔点在自己区域里的笔画（input.js 的 onStage）
    receive(batch) {
      for (const pad of pads) {
        try {
          pad.input.receiveShell(batch);
        } catch (err) {
          console.error("外壳采样处理出错", err);
        }
      }
    },
    hello(info) {
      shellReply = info || {};
      for (const pad of pads) applyShell(pad, shellReply);
    },
  };
  const handler = shellHandler();
  if (!handler) return false;
  try {
    handler.postMessage({ type: "hello", bridge: SHELL_BRIDGE });
  } catch (err) {
    return false;
  }
  return true;
}

/** 手写板被移除时调用，之后不再收外壳的采样。 */
export function disconnectShell(app) {
  pads.delete(app);
}

const pads = new Set();
let shellReply = null;

function applyShell(app, reply) {
  const active = reply.active === true && bridgeSupported(reply.bridge);
  const shell = { active, version: String(reply.shellVersion || ""), bridge: reply.bridge | 0 };
  app.input.setShell(shell);
  // 不改宿主页面：告诉手写板，由宿主决定要不要反映到页面上
  if (typeof app.setShellInfo === "function") app.setShellInfo(shell);
}

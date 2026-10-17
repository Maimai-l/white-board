// 速写页面（/sketch）：在 iPad 上随手写，在 Mac 上复制为 PNG（透明背景）。
//
// 打开页面时请求 POST /api/sketch：服务端建一块临时白板（只在内存中，不存盘），
// 跟随当前白板的设备（iPad、Mac 窗口）一起切过去。这个页面关闭几秒后速写板被丢弃，
// 那些设备回到原来的当前白板。Edge 插件（extensions/sketch）的弹出页嵌的就是这个页面。

import { createInkPad } from "/inksync/inkpad.js";

const SKETCH = "sketch";
const FIT_DELAY = 120;
const EXPORT = { scale: 2, background: false };
const STATUS_NAMES = { online: "已连接", syncing: "同步中", offline: "未连接", local: "本地" };

const $ = (id) => document.getElementById(id);

let pad = null;
let restarting = null;
let fitTimer = 0;
let noticeTimer = 0;
let drawingHere = false;

/**
 * 视图始终显示全部笔迹：iPad 的屏幕比速写窗口大得多，各设备的视图又各自独立，
 * 不这样做的话写在 iPad 右下角的笔迹会落在速写窗口的视野之外。最多放大到 1 倍。
 */
function scheduleFit() {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => {
    if (pad && !drawingHere) pad.fit({ maxScale: 1 });
  }, FIT_DELAY);
}

/** 浮在画布底部的提示。成功的几秒后消失，出错的一直留着。 */
function notice(text, kind = "") {
  const el = $("notice");
  el.textContent = text || "";
  el.dataset.kind = kind;
  clearTimeout(noticeTimer);
  if (text && kind !== "error") noticeTimer = setTimeout(() => (el.textContent = ""), 2500);
}

function setStatus(state) {
  const el = $("status");
  el.dataset.state = state;
  el.title = STATUS_NAMES[state] || state;
  el.setAttribute("aria-label", el.title);
}

function strokeCount() {
  return pad ? pad.snapshot().strokes.length : 0;
}

function refresh() {
  const empty = strokeCount() === 0;
  $("copy").disabled = empty;
  $("clear").disabled = empty;
}

async function copy() {
  if (!pad || strokeCount() === 0) return;
  try {
    const url = await pad.exportPNG(EXPORT);
    const blob = await (await fetch(url)).blob();
    if (blob.type !== "image/png" || blob.size === 0) throw new Error("生成图片失败");
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    notice("已复制");
    window.dispatchEvent(new CustomEvent("sketch-copied", { detail: { size: blob.size } }));
  } catch (err) {
    notice(`复制失败：${err && err.message ? err.message : err}`, "error");
  }
}

/** 开始（或继续）速写：服务端建好速写板之后才能连接它。 */
async function start() {
  const response = await fetch("/api/sketch", { method: "POST" });
  if (response.status === 403) {
    throw Object.assign(new Error("这台设备没有「管理白板」权限，不能开始速写。"), { fatal: true });
  }
  if (!response.ok) throw new Error(`服务端返回 ${response.status}`);
  return response.json();
}

/** 速写板没了（服务端重启，或在别处结束了速写）：重新开始一块空白的。 */
function restart() {
  if (!restarting) {
    restarting = (async () => {
      try {
        await start();
        await pad.open(SKETCH);
        notice("");
      } catch (err) {
        notice(err.message || String(err), "error");
      } finally {
        restarting = null;
        refresh();
      }
    })();
  }
  return restarting;
}

async function main() {
  try {
    await start();
  } catch (err) {
    setStatus("offline");
    notice(err.fatal ? err.message : "连接不到白板应用。请确认 Mac 上的白板应用正在运行。", "error");
    return;
  }
  pad = createInkPad($("pad"), { board: SKETCH, storage: "inksync:sketch:" });
  pad.on("status", setStatus);
  for (const event of ["change", "op", "meta"]) pad.on(event, refresh);
  for (const event of ["change", "meta"]) pad.on(event, scheduleFit);
  pad.on("strokestart", () => {
    drawingHere = true;
  });
  pad.on("strokeend", () => {
    drawingHere = false;
    scheduleFit();
  });
  new ResizeObserver(scheduleFit).observe($("pad"));
  pad.on("deleted", () => restart());
  pad.on("error", ({ reason }) => {
    if (reason === "board") restart();
    else if (reason === "denied") notice("这台设备不能打开速写板。", "error");
  });

  $("copy").addEventListener("click", copy);
  $("clear").addEventListener("click", () => {
    pad.clear();
    refresh();
  });
  document.addEventListener("keydown", (event) => {
    const selection = window.getSelection && String(window.getSelection());
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "c" && !selection) {
      event.preventDefault();
      copy();
    }
  });
  refresh();
  window.sketchPad = pad; // 测试用
}

main();

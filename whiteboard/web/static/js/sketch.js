// 速写页面（/sketch）：在 iPad 上随手写，在 Mac 上复制或拖出 PNG。
//
// 打开页面时请求 POST /api/sketch：服务端建一块临时白板（只在内存中，不存盘），
// 跟随当前白板的设备（iPad、Mac 窗口）一起切过去。这个页面关闭几秒后速写板被丢弃，
// 那些设备回到原来的当前白板。Edge 侧边栏插件（extensions/sketch）嵌的就是这个页面。

import { createInkPad } from "/inksync/inkpad.js";

const SKETCH = "sketch";
const PREVIEW_DELAY = 250;

const $ = (id) => document.getElementById(id);
const statusNames = { online: "已连接", syncing: "同步中", offline: "未连接", local: "本地" };

let pad = null;
let previewTimer = 0;
let previewURL = "";
let restarting = null;

function notice(text, kind = "") {
  const el = $("notice");
  el.textContent = text || "";
  el.dataset.kind = kind;
}

function strokeCount() {
  return pad ? pad.snapshot().strokes.length : 0;
}

function exportOptions() {
  return { scale: 2, background: !$("transparent").checked };
}

/** 更新按钮状态和右下角的预览图（也是拖出去的那张图）。 */
function refresh() {
  const empty = strokeCount() === 0;
  $("copy").disabled = empty;
  $("clear").disabled = empty;
  clearTimeout(previewTimer);
  if (empty) {
    $("preview").hidden = true;
    previewURL = "";
    return;
  }
  previewTimer = setTimeout(async () => {
    previewURL = await pad.exportPNG(exportOptions());
    $("preview").src = previewURL;
    $("preview").hidden = false;
  }, PREVIEW_DELAY);
}

async function pngBlob() {
  const url = await pad.exportPNG(exportOptions());
  const blob = await (await fetch(url)).blob();
  if (blob.type !== "image/png" || blob.size === 0) throw new Error("生成图片失败");
  return blob;
}

async function copy() {
  if (!pad || strokeCount() === 0) return;
  try {
    const blob = await pngBlob();
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    notice("已复制，可以粘贴到 Claude 等网页。");
    window.dispatchEvent(new CustomEvent("sketch-copied", { detail: { size: blob.size } }));
  } catch (err) {
    notice(`复制失败：${err && err.message ? err.message : err}。可以把右下角的图片拖到网页中。`, "error");
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
    $("status").textContent = "未开始";
    notice(err.fatal ? err.message : "连接不到白板应用。请确认 Mac 上的白板应用正在运行，然后刷新。", "error");
    return;
  }
  pad = createInkPad($("pad"), { board: SKETCH, storage: "inksync:sketch:" });
  pad.on("status", (status) => {
    $("status").textContent = statusNames[status] || status;
    $("status").dataset.state = status;
  });
  for (const event of ["change", "op", "meta"]) pad.on(event, refresh);
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
  $("transparent").addEventListener("change", refresh);
  document.addEventListener("keydown", (event) => {
    const selection = window.getSelection && String(window.getSelection());
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "c" && !selection) {
      event.preventDefault();
      copy();
    }
  });
  $("preview").addEventListener("dragstart", (event) => {
    if (!previewURL) return;
    const data = event.dataTransfer;
    data.effectAllowed = "copy";
    data.setData("text/uri-list", previewURL);
    data.setData("DownloadURL", `image/png:速写.png:${previewURL}`);
    try {
      // 带上文件本身：接受拖入图片的网页（例如 Claude 的输入框）读的是 dataTransfer.files
      const bytes = atob(previewURL.split(",")[1]);
      const buffer = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i += 1) buffer[i] = bytes.charCodeAt(i);
      data.items.add(new File([buffer], "速写.png", { type: "image/png" }));
    } catch (err) {
      /* 浏览器不允许在拖动中附带文件时，只拖地址 */
    }
  });
  refresh();
  window.sketchPad = pad; // 测试用
}

main();

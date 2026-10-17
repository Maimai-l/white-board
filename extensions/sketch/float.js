// 速写小窗口：嵌入白板应用的速写页面（http://127.0.0.1:<端口>/sketch）。
// 书写、复制、拖出图片都在速写页面里；这里负责找到白板应用、记住端口、展开和置顶。
// 窗口关闭后速写页面随之关闭，几秒后服务端丢弃速写板，iPad 回到原来的白板。

const DEFAULT_PORT = 8848;
const RETRY_MS = 3000;
// 小窗口与展开后的大小：内容区的比例接近 iPad 横屏（4:3），另加顶部一行按钮
const MINI = { width: 380, height: 316 };
const LARGE = { width: 980, height: 760 };
const ALLOW = "clipboard-write; clipboard-read";

const $ = (id) => document.getElementById(id);
const frame = $("sketch");
let port = DEFAULT_PORT;
let timer = 0;
let pip = null;

function base() {
  return `http://127.0.0.1:${port}`;
}

async function loadPort() {
  try {
    const stored = await chrome.storage.local.get("port");
    const value = Number(stored.port);
    if (Number.isInteger(value) && value > 0 && value < 65536) port = value;
  } catch (err) {
    /* 读不到就用默认端口 */
  }
  $("port").value = String(port);
}

/** 白板应用是否在运行：任何 HTTP 回复都算（来源检查会对插件的请求回 403，这也说明它在）。 */
async function reachable() {
  try {
    await fetch(`${base()}/api/info`, { cache: "no-store" });
    return true;
  } catch (err) {
    return false;
  }
}

function showFrame(show) {
  frame.hidden = !show || !!pip;
  $("pinned").hidden = !pip;
}

async function connect() {
  clearTimeout(timer);
  if (await reachable()) {
    $("offline").hidden = true;
    if (frame.dataset.src !== base() && !pip) {
      frame.dataset.src = base();
      frame.src = `${base()}/sketch`;
    }
    showFrame(true);
    return;
  }
  showFrame(false);
  frame.removeAttribute("src");
  delete frame.dataset.src;
  $("offline").hidden = false;
  $("address").textContent = `正在查找 ${base()}`;
  timer = setTimeout(connect, RETRY_MS);
}

// ------------------------------------------------------------------ 展开 / 收起

async function currentWindow() {
  try {
    return await chrome.windows.getCurrent();
  } catch (err) {
    return null;
  }
}

async function toggleExpand() {
  const win = await currentWindow();
  if (!win) return;
  const large = win.width >= LARGE.width - 40;
  // 屏幕放不下就按屏幕的可用大小来（screen 的值不像一块显示器时不参考它）
  const fits = (size, avail) => (avail > 600 ? Math.min(size, avail - 40) : size);
  const target = large ? MINI : {
    width: fits(LARGE.width, screen.availWidth),
    height: fits(LARGE.height, screen.availHeight),
  };
  const update = { ...target, state: "normal" };
  // 展开时保持右上角不动，向左下方扩展
  if (typeof win.left === "number") update.left = Math.max(0, win.left + win.width - target.width);
  await chrome.windows.update(win.id, update);
  $("expand").textContent = large ? "展开" : "收起";
}

// ------------------------------------------------------------------ 置顶（文档画中画）

/**
 * 置顶：用文档画中画打开一个始终显示在其他窗口上方的浮窗，速写页面移进去，
 * 本窗口最小化。浮窗不能比打开它的窗口存活得更久，所以本窗口只能最小化，不能关闭。
 */
async function pinWindow() {
  if (pip) {
    pip.close();
    return;
  }
  const opened = await window.documentPictureInPicture.requestWindow({ width: MINI.width, height: MINI.height - 30 });
  pip = opened;
  const doc = opened.document;
  doc.title = "速写";
  const style = doc.createElement("style");
  style.textContent = "html,body{height:100%;margin:0}iframe{display:block;width:100%;height:100%;border:0}";
  doc.head.append(style);
  const inner = doc.createElement("iframe");
  inner.title = "速写";
  inner.allow = ALLOW;
  inner.src = `${base()}/sketch`;
  doc.body.append(inner);
  // 同一时间只留一个速写页面：本窗口的那个先卸掉
  frame.removeAttribute("src");
  delete frame.dataset.src;
  $("pin").textContent = "取消置顶";
  showFrame(false);
  const win = await currentWindow();
  if (win) chrome.windows.update(win.id, { state: "minimized" }).catch(() => {});
  opened.addEventListener("pagehide", async () => {
    pip = null;
    $("pin").textContent = "置顶";
    const current = await currentWindow();
    if (current) chrome.windows.update(current.id, { state: "normal", focused: true }).catch(() => {});
    connect();
  });
}

// ------------------------------------------------------------------ 按钮

$("retry").addEventListener("click", connect);
$("expand").addEventListener("click", () => toggleExpand().catch((err) => console.error(err)));
if ("documentPictureInPicture" in window) {
  $("pin").hidden = false;
  $("pin").addEventListener("click", () => pinWindow().catch((err) => console.error(err)));
}
$("gear").addEventListener("click", () => {
  const form = $("settings");
  form.hidden = !form.hidden;
  $("gear").setAttribute("aria-expanded", String(!form.hidden));
});
$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = Number($("port").value);
  if (!Number.isInteger(value) || value <= 0 || value >= 65536) return;
  port = value;
  try {
    await chrome.storage.local.set({ port });
  } catch (err) {
    /* 存不下就只对这次有效 */
  }
  $("settings").hidden = true;
  $("gear").setAttribute("aria-expanded", "false");
  connect();
});

loadPort().then(connect);

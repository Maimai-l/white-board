// 侧边栏：把白板应用的速写页面（http://127.0.0.1:<端口>/sketch）嵌进来。
// 书写、复制、拖出图片都在速写页面里；这里只负责找到白板应用、记住端口。
// 侧边栏关闭时速写页面随之关闭，几秒后服务端丢弃速写板，iPad 回到原来的白板。

const DEFAULT_PORT = 8848;
const RETRY_MS = 3000;

const frame = document.getElementById("sketch");
const offline = document.getElementById("offline");
let port = DEFAULT_PORT;
let timer = 0;

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
  document.getElementById("port").value = String(port);
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

async function connect() {
  clearTimeout(timer);
  if (await reachable()) {
    offline.hidden = true;
    frame.hidden = false;
    if (frame.dataset.src !== base()) {
      frame.dataset.src = base();
      frame.src = `${base()}/sketch`;
    }
    return;
  }
  frame.hidden = true;
  frame.removeAttribute("src");
  delete frame.dataset.src;
  offline.hidden = false;
  document.getElementById("address").textContent = `正在查找 ${base()}`;
  timer = setTimeout(connect, RETRY_MS);
}

document.getElementById("retry").addEventListener("click", connect);
document.getElementById("save").addEventListener("click", async () => {
  const value = Number(document.getElementById("port").value);
  if (!Number.isInteger(value) || value <= 0 || value >= 65536) return;
  port = value;
  try {
    await chrome.storage.local.set({ port });
  } catch (err) {
    /* 存不下就只对这次有效 */
  }
  document.getElementById("settings").open = false;
  connect();
});

loadPort().then(connect);

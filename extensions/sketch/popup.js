// 工具栏弹出的速写：嵌入白板应用的速写页面（http://127.0.0.1:<端口>/sketch）。
// 书写、复制、清空都在速写页面里。弹出页关闭几秒后服务端丢弃速写板，iPad 回到原来的白板。
//
// 白板应用的端口默认 8848，被占用时自动顺延，所以依次试 8848～8857；找到的端口记下来，
// 下次先试它。

const PORTS = Array.from({ length: 10 }, (_, i) => 8848 + i);
const RETRY_MS = 2000;

const frame = document.getElementById("sketch");
const offline = document.getElementById("offline");

/** 白板应用是否在这个端口上：任何 HTTP 回复都算（来源检查会对插件的请求回 403，这也说明它在）。 */
async function reachable(port) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 800);
  try {
    await fetch(`http://127.0.0.1:${port}/api/info`, { cache: "no-store", signal: controller.signal });
    return true;
  } catch (err) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function candidates() {
  let remembered = null;
  try {
    remembered = Number((await chrome.storage.local.get("port")).port) || null;
  } catch (err) {
    /* 读不到就按默认顺序 */
  }
  return remembered ? [remembered, ...PORTS.filter((p) => p !== remembered)] : PORTS;
}

async function connect() {
  for (const port of await candidates()) {
    if (await reachable(port)) {
      try {
        await chrome.storage.local.set({ port });
      } catch (err) {
        /* 记不下就算了 */
      }
      offline.hidden = true;
      frame.src = `http://127.0.0.1:${port}/sketch`;
      frame.hidden = false;
      return;
    }
  }
  offline.hidden = false;
  setTimeout(connect, RETRY_MS);
}

connect();

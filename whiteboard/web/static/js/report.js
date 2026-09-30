// 页面报错上报：iPad 上没有控制台，页面里的报错直接送到 Mac 的终端日志里（限量，避免刷屏）。

let reportedErrors = 0;

export function reportError(kind, detail) {
  if (reportedErrors >= 5) return;
  reportedErrors += 1;
  try {
    fetch("/api/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, ...detail }),
      keepalive: true,
    }).catch(() => {});
  } catch (err) {
    /* 上报失败就算了 */
  }
}

/** 把页面上没有被捕获的报错也送去上报。整个页面装一次。 */
export function reportUncaughtErrors() {
  addEventListener("error", (event) => {
    reportError("脚本报错", {
      message: String(event.message || ""),
      source: `${event.filename || ""}:${event.lineno || 0}`,
      stack: event.error && event.error.stack ? String(event.error.stack).slice(0, 400) : "",
    });
  });
  addEventListener("unhandledrejection", (event) => {
    reportError("未处理的 Promise", { message: String(event.reason).slice(0, 400) });
  });
}

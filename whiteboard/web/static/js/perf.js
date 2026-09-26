// 真机上的卡顿诊断面板。
//
// iPad 上没法接开发者工具的时候，用它把帧间隔、渲染耗时、采样率和写盘耗时
// 直接显示在屏幕上。用 ?debug=1 打开，或者连点左上角的状态圆点三下。

const WINDOW_MS = 3000;
// 上报间隔，以及「值得上报」的帧间隔门槛。
const REPORT_MS = 2000;
const JANK_MS = 60;

export class PerfMonitor {
  constructor() {
    this.enabled = false;
    this.node = null;
    this.frames = [];
    this.renderMs = 0;
    this.samples = [];
    this.marks = new Map();
    this.worst = [];
    this.startedAt = performance.now();
    this._lastPaint = 0;
    this._lastReport = 0;
    this._reportedCancels = 0;
    this.role = "";
  }

  toggle(on) {
    this.enabled = on === undefined ? !this.enabled : !!on;
    if (this.enabled && !this.node) {
      this.node = document.createElement("div");
      this.node.id = "perf";
      document.getElementById("ui").append(this.node);
    }
    if (this.node) this.node.style.display = this.enabled ? "block" : "none";
    // 立刻填一次内容。paint 平时是每帧循环里每 250ms 才跑一次，打开面板到第一次
    // paint 之间它是空的；而版本号这一行是拿来对截图的，不能等。
    if (this.enabled) this.paint();
  }

  /** 每帧调用：dt 是距离上一帧的间隔，renderMs 是这一帧渲染花的时间。 */
  frame(now, dt, renderMs) {
    if (!this.enabled) return;
    // 窗口切到后台时 rAF 会整个停掉，那种「超长帧」不是卡顿。
    if (document.hidden || dt > 5000) {
      this._lastPaint = now;
      this._lastReport = now;
      return;
    }
    this.frames.push([now, dt]);
    this.renderMs = this.renderMs * 0.8 + renderMs * 0.2;
    if (dt > 32) {
      this.worst.push([((now - this.startedAt) / 1000).toFixed(1), Math.round(dt)]);
      if (this.worst.length > 4) this.worst.shift();
    }
    this._trim(now);
    if (now - this._lastPaint > 250) {
      this._lastPaint = now;
      this.paint();
    }
    if (now - this._lastReport > REPORT_MS) {
      this._lastReport = now;
      this.report(this.maxFrame());
    }
  }

  countSamples(n) {
    if (!this.enabled || !n) return;
    this.samples.push([performance.now(), n]);
  }

  /** 指针事件统计，由输入模块提供。 */
  setInput(stats) {
    this.input = stats;
  }

  /** 记一次耗时操作（写盘、整屏重绘……）。 */
  mark(name, ms) {
    if (!this.enabled) return;
    this.marks.set(name, Math.round(ms));
  }

  maxFrame() {
    let max = 0;
    for (const [, dt] of this.frames) if (dt > max) max = dt;
    return max;
  }

  _trim(now) {
    while (this.frames.length && now - this.frames[0][0] > WINDOW_MS) this.frames.shift();
    while (this.samples.length && now - this.samples[0][0] > 1000) this.samples.shift();
  }

  /** 把这一轮的卡顿情况报给服务端，Mac 的终端里能直接看到。 */
  report(maxFrame) {
    const input = this.input || { cancel: 0, maxGap: 0, down: 0 };
    const newCancels = input.cancel - this._reportedCancels;
    if (maxFrame < JANK_MS && newCancels <= 0 && input.maxGap < JANK_MS) return;
    this._reportedCancels = input.cancel;
    this.frames = []; // 同一次卡顿只报一次
    const body = {
      role: this.role,
      最长帧: Math.round(maxFrame),
      渲染: +this.renderMs.toFixed(1),
      事件间隔: Math.round(input.maxGap),
      笔画数: input.down,
      中断: input.cancel,
      笔被抢: input.penCancel || 0,
      触摸: input.touch || 0,
      拦不住: input.uncancelable || 0,
      耗时: Object.fromEntries(this.marks),
    };
    input.maxGap = 0;
    try {
      fetch("/api/debug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive: true,
      }).catch(() => {});
    } catch (err) {
      /* 上报失败无所谓 */
    }
  }

  paint() {
    const dts = this.frames.map((f) => f[1]);
    const max = this.maxFrame();
    const avg = dts.length ? dts.reduce((a, b) => a + b, 0) / dts.length : 0;
    const rate = this.samples.reduce((a, b) => a + b[1], 0);
    const marks = [...this.marks.entries()].map(([k, v]) => `${k} ${v}ms`).join("  ");
    const worst = this.worst.map(([t, ms]) => `${ms}ms@${t}s`).join("  ");
    const input = this.input || { down: 0, move: 0, cancel: 0, maxGap: 0, coalesced: 0, touch: 0 };
    // 笔的倾斜：报不出来的浏览器 altitudeAngle 恒等于 90，看这一行就知道走的是哪一套
    const tilt =
      `倾斜 tiltX ${input.tiltX || 0} tiltY ${input.tiltY || 0}` +
      `  altitude ${input.altRaw === null || input.altRaw === undefined ? "无" : input.altRaw + "°"}` +
      `  实际取 ${input.tiltDeg === undefined ? 90 : input.tiltDeg}°`;
    const build = document.documentElement.dataset.build || "";
    this.node.textContent = [
      build ? `版本 ${build}  ${this.role || ""}` : "",
      `帧 ${avg ? (1000 / avg).toFixed(0) : 0}fps  最长 ${max.toFixed(0)}ms`,
      `渲染 ${this.renderMs.toFixed(1)}ms  采样 ${rate}/s`,
      `笔 ${input.down}下 ${input.cancel}断  事件间隔 ${input.maxGap.toFixed(0)}ms  合并 ${input.coalesced}`,
      `触摸 ${input.touch || 0}  拦不住 ${input.uncancelable || 0}  笔被抢 ${input.penCancel || 0}`,
      tilt,
      marks,
      worst ? `卡顿 ${worst}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  }
}

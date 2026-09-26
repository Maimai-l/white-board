// 输入录制与回放：把原始指针事件连同工具、视口和板上内容一起记下来，
// 回放时逐字喂回 InputController。
//
// 为什么需要它：有些问题只有真笔能触发——压感沿笔画变化、倾角一直在动、
// iPad 上一帧能来二十几个合并采样点。这些在开发机上用鼠标敲不出来，靠人反复
// 手动复现又太慢。录一次，剩下的都在开发机上跑。
//
// 录的是**原始指针事件**，不是笔画。笔画是输入经过整条链路之后的结果，拿结果
// 回放就只能复现渲染，复现不了判定、平滑、合并采样、抬笔那一刻的时序。

import { plainStroke } from "./util.js";

const SCHEMA = 1;

/**
 * 一个指针事件里所有会被 InputController 读到的字段。
 *
 * 一律原值，不做四舍五入。录制的用途是逐字复现，回放出来的笔画要和录制时
 * 一模一样才能当基准；坐标少留三位小数，笔画就对不上了，差异是真是假分不清。
 */
function snapEvent(event) {
  const out = { x: event.clientX, y: event.clientY, p: event.pressure || 0 };
  if (event.tiltX) out.tx = event.tiltX;
  if (event.tiltY) out.ty = event.tiltY;
  if (typeof event.altitudeAngle === "number") out.alt = event.altitudeAngle;
  if (typeof event.azimuthAngle === "number") out.az = event.azimuthAngle;
  if (event.twist) out.tw = event.twist;
  return out;
}

/** 把录下来的一条事件还原成 InputController 认得的样子。 */
function reviveEvent(entry) {
  const coalesced = (entry.c || []).map((s) => revivePoint(s, entry));
  const event = revivePoint(entry, entry);
  event.type = entry.type;
  event.pointerId = entry.id;
  event.pointerType = entry.pt;
  event.isPrimary = entry.primary !== false;
  event.button = entry.btn | 0;
  event.buttons = entry.btns | 0;
  event.preventDefault = () => {};
  event.stopPropagation = () => {};
  event.getCoalescedEvents = () => (coalesced.length ? coalesced : [event]);
  return event;
}

function revivePoint(point, owner) {
  return {
    clientX: point.x,
    clientY: point.y,
    pressure: point.p || 0,
    tiltX: point.tx || 0,
    tiltY: point.ty || 0,
    twist: point.tw || 0,
    altitudeAngle: point.alt,
    azimuthAngle: point.az,
    pointerId: owner.id,
    pointerType: owner.pt,
    preventDefault: () => {},
  };
}

export class Recorder {
  constructor(app) {
    this.app = app;
    this.data = null;
    this.onChange = null;
    this._bound = false;
  }

  get recording() {
    return this.data !== null;
  }

  /** 从 stage 的捕获阶段收事件：先于 InputController 看到，也不改变它看到的东西。 */
  attach(stage) {
    if (this._bound) return;
    this._bound = true;
    const names = ["pointerdown", "pointermove", "pointerup", "pointercancel"];
    for (const name of names) {
      stage.addEventListener(name, (e) => this.note(name, e), { capture: true });
    }
  }

  start(name) {
    const app = this.app;
    const stage = app.input.stage;
    const rect = stage.getBoundingClientRect();
    this.data = {
      schema: SCHEMA,
      name: name || "",
      at: new Date().toISOString(),
      agent: navigator.userAgent,
      role: app.role,
      dpr: window.devicePixelRatio || 1,
      stage: [+rect.left.toFixed(2), +rect.top.toFixed(2),
              +rect.width.toFixed(2), +rect.height.toFixed(2)],
      viewport: this.viewport(),
      fingerDraw: !!app.input.fingerDraw,
      // 起始板面：回放前先恢复成这个样子，不然同样的输入落在不同的墨上
      before: app.state.strokes.map(plainStroke),
      meta: app.state.meta,
      events: [],
      after: null,
    };
    this.t0 = performance.now();
    this._vp = this.data.viewport;
    this.changed();
    return this.data;
  }

  viewport() {
    const v = this.app.viewport;
    return { scale: v.scale, x: v.x, y: v.y };
  }

  note(type, event) {
    const data = this.data;
    if (!data) return;
    const entry = {
      type,
      t: +(performance.now() - this.t0).toFixed(2),
      id: event.pointerId,
      pt: event.pointerType,
      btn: event.button | 0,
      btns: event.buttons | 0,
      ...snapEvent(event),
    };
    if (event.isPrimary === false) entry.primary = false;
    if (type === "pointerdown") {
      // 工具是随时能换的，每一次落笔都要记当时是什么工具
      entry.tool = { ...this.app.input.getTool() };
    }
    // 视口一变就记下来。只在落笔时记是不够的：中途用手指平移缩放，后面那些笔
    // 回放时就会落在错的世界坐标上，而且错得很隐蔽——擦的还是那几条笔画，
    // 只是位置偏了，结果对不上却看不出为什么。
    const vp = this.viewport();
    const last = this._vp;
    if (!last || last.scale !== vp.scale || last.x !== vp.x || last.y !== vp.y) {
      entry.viewport = vp;
      this._vp = vp;
    }
    if (type === "pointermove" && event.getCoalescedEvents) {
      const list = event.getCoalescedEvents();
      // 只有多于一个才值得记：一个的时候它就是事件本身
      if (list && list.length > 1) entry.c = list.map(snapEvent);
    }
    data.events.push(entry);
  }

  stop() {
    const data = this.data;
    if (!data) return null;
    data.after = this.app.state.strokes.map(plainStroke);
    data.viewportAtEnd = this.viewport();
    this.data = null;
    this.changed();
    return data;
  }

  changed() {
    if (this.onChange) this.onChange(this.recording);
  }

  /**
   * 回放：先把板恢复到录制开始时的样子，再按记录的时间把事件喂回去。
   *
   * 喂的是普通对象而不是合成 PointerEvent——PointerEvent 的构造函数不收
   * altitudeAngle / azimuthAngle，也造不出 getCoalescedEvents，而这两样恰好是
   * 橡皮和笔宽最依赖的。直接调 InputController 的处理函数，读到的字段一模一样。
   */
  async replay(data, options = {}) {
    const app = this.app;
    const input = app.input;
    const speed = options.speed > 0 ? options.speed : 1;
    const wait = options.wait !== false;

    app.state.reset(data.meta || app.state.meta, data.before.map((s) => ({ ...s })));
    const v = data.viewport;
    if (v) {
      app.viewport.scale = v.scale;
      app.viewport.x = v.x;
      app.viewport.y = v.y;
    }
    input.fingerDraw = !!data.fingerDraw;
    app.renderer.requestFull();

    let last = 0;
    for (const entry of data.events) {
      if (wait && entry.t > last) {
        await new Promise((done) => setTimeout(done, (entry.t - last) / speed));
      }
      last = entry.t;
      if (entry.tool) app.tool = { ...entry.tool };
      if (entry.viewport) {
        app.viewport.scale = entry.viewport.scale;
        app.viewport.x = entry.viewport.x;
        app.viewport.y = entry.viewport.y;
      }
      // 每条事件都重新量一次画布位置：回放时窗口尺寸不一定和录制时一样
      input._rect = null;
      const event = reviveEvent(entry);
      if (entry.type === "pointerdown") input.onDown(event);
      else if (entry.type === "pointermove") input.onMove(event);
      else input.onUp(event, entry.type === "pointercancel");
    }
    return app.state.strokes.map(plainStroke);
  }

  /** 把录像交给 Mac 上的服务端存成文件；存不下就退回浏览器下载。 */
  async save(data) {
    const body = JSON.stringify(data);
    try {
      const res = await fetch("/api/recording", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (res.ok) return await res.json();
    } catch (err) {
      /* 落到下载 */
    }
    const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `recording-${Date.now()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return { saved: false };
  }
}

/**
 * 诊断面板上的录制那一格：开始 / 停止，停止就把录像送到 Mac 上。
 *
 * 和卡顿诊断同一个开关（连点左上角状态圆点三下，或 ``?debug=1``）。iPad 是靠
 * 配置描述文件装的 Web Clip 打开的，没有地址栏，改不了查询参数，所以入口只能
 * 是屏幕上点得到的东西；而这个开关用户本来就知道。
 *
 * 返回一个 ``toggle(on)``，跟着诊断面板一起显示和隐藏。录着的时候把面板关掉，
 * 先把录像停下来存好，不然那一段就白录了。
 */
export function mountRecorderPanel(recorder) {
  const box = document.createElement("div");
  box.id = "rec";
  box.hidden = true;
  const button = document.createElement("button");
  const note = document.createElement("span");
  note.className = "rec-note";
  box.append(button, note);
  (document.getElementById("ui") || document.body).appendChild(box);

  const paint = () => {
    const on = recorder.recording;
    button.textContent = on ? "停止录制" : "录制输入";
    box.dataset.on = on ? "1" : "";
    if (on) note.textContent = `${recorder.data.events.length} 条`;
  };
  const finish = async () => {
    note.textContent = "正在保存…";
    const data = recorder.stop();
    const res = await recorder.save(data);
    note.textContent = res && res.path ? `已存到 ${res.path}` : "已下载";
  };
  recorder.onChange = paint;
  button.addEventListener("click", () => {
    if (recorder.recording) return finish();
    recorder.start();
    note.textContent = "0 条";
  });
  setInterval(() => {
    if (recorder.recording) note.textContent = `${recorder.data.events.length} 条`;
  }, 500);
  paint();
  return {
    node: box,
    toggle(on) {
      box.hidden = !on;
      if (!on && recorder.recording) finish();
    },
  };
}

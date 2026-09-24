// 主控：把状态、渲染、输入、网络和界面接到一起。

import { BoardState } from "./boardstate.js";
import { Cache } from "./cache.js";
import { InputController, penAltitude } from "./input.js";
import { Net } from "./net.js";
import { PerfMonitor } from "./perf.js";
import { Recorder, mountRecorderPanel } from "./recorder.js";
import { Renderer } from "./renderer.js";
import { UI } from "./ui.js";
import { Viewport } from "./viewport.js";
import {
  addMask,
  buildPath,
  eraseKind,
  maskBounds,
  maskFor,
  maskSize,
  MASK_LIMIT,
  simplifyMask,
  splitStroke,
  strokeBBox,
  strokeHit,
} from "./stroke.js";
import { debounce, plainStroke, uid } from "./util.js";
import { contentBounds, downloadDataURL, exportDataURL, uploadThumb } from "./exporter.js";

const UNDO_LIMIT = 200;
// 写 IndexedDB 会卡主线程（iOS 上首次写事务尤其慢），离最后一次落笔足够远才写。
const SAVE_DEBOUNCE = 4000;
const SAVE_IDLE = 1500;
// 笔记：缩放到接近「刚好一页宽」时自动吸附到屏幕两边。
const SNAP_RATIO = 0.08;
const SNAP_MS = 180;
const REMOTE_LIVE_TTL = 5000;

function resolveRole() {
  const params = new URLSearchParams(location.search);
  const asked = params.get("role");
  if (asked === "mac" || asked === "ipad") return asked;
  const fromServer = document.documentElement.dataset.role;
  if (navigator.maxTouchPoints > 1) return "ipad";
  return fromServer === "ipad" ? "ipad" : "mac";
}

/** 服务端发下来的权限清单。真正的拦截在服务端，这里只决定画不画那个入口。 */
function resolvePerms() {
  const raw = document.documentElement.dataset.perms || "";
  return new Set(raw.split(" ").filter(Boolean));
}

function clientId() {
  try {
    let id = localStorage.getItem("whiteboard.client");
    if (!id) {
      id = uid(12);
      localStorage.setItem("whiteboard.client", id);
    }
    return id;
  } catch (err) {
    return uid(12);
  }
}

// iPad 上没有控制台，页面里的报错直接送到 Mac 的终端日志里（限量，避免刷屏）。
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

function nativeApi() {
  return (window.pywebview && window.pywebview.api) || null;
}

class App {
  constructor() {
    this.role = resolveRole();
    document.documentElement.dataset.role = this.role;
    this.clientId = clientId();
    this.state = new BoardState();
    this.viewport = new Viewport();
    this.renderer = new Renderer(
      document.getElementById("base"),
      document.getElementById("live"),
      this.state,
      this.viewport
    );
    this.cache = new Cache();
    this.undoStack = [];
    this.redoStack = [];
    this.remoteLive = new Map();
    this.eraseBatch = [];
    // 像素橡皮擦一笔下去是「删掉原来那条、补上切剩的几段」，整个拖动过程攒在一起
    // 才是一次撤销
    this.pixelBatch = { removed: [], added: [], bites: new Map() };
    // 擦除产生的操作先攒在这里，每帧发一次，见 queueErase
    this.eraseQueue = { remove: new Map(), add: new Map(), mask: new Map() };
    this.eraseFlush = 0;
    this.viewAnim = 0;
    this.pendingRestored = false;

    this.perf = new PerfMonitor();
    this.perf.role = this.role;
    if (new URLSearchParams(location.search).get("debug") === "1") this.perf.toggle(true);

    this.ui = new UI({
      role: this.role,
      native: !!nativeApi(),
      perms: resolvePerms(),
      actions: this.actions(),
    });
    this.tool = this.ui.toolState();

    this.net = new Net({
      clientId: this.clientId,
      role: this.role,
      onMessage: (msg) => this.onMessage(msg),
      onStatus: (status) => this.ui.setStatus(status),
    });
    // 待发队列同样走「空闲才写」，不然每写一笔就有两次写盘插进来。
    this.net.onOutboxChange = () => this.saveCache();

    this.input = new InputController({
      stage: document.getElementById("stage"),
      viewport: this.viewport,
      renderer: this.renderer,
      device: this.role,
      // 会话段必不可少：光用客户端 id + 计数器的话，重开页面后计数器从头数，
      // 新笔画的 id 会和上次的撞车，被当成重复项丢掉（表现为抬笔即消失）。
      strokePrefix: `${this.clientId}-${uid(4)}`,
      getTool: () => this.tool,
      getLimits: () => this.state.limits,
      hooks: this.inputHooks(),
    });
    this.perf.setInput(this.input.stats);

    this.saveCache = debounce(() => this.persistWhenIdle(), SAVE_DEBOUNCE);
    this.saveView = debounce(() => this.persistView(), 400);
    this.pushThumb = debounce(() => {
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    }, 5000);

    this.bindWindow();
    this.watchUpdates();
    this.bootstrap();
    this.loop();
  }

  // ------------------------------------------------------------ 启动

  async bootstrap() {
    // 先用本地缓存把上次的内容画出来，网络就绪后再对齐服务端。
    const lastId = await this.cache.getLast();
    if (lastId) {
      const cached = await this.cache.loadBoard(lastId);
      if (cached && cached.meta) {
        this.applyBoard(cached.meta, cached.strokes || [], 0, { fromCache: true });
        this.net.boardId = cached.meta.id;
        // epoch 对得上才能按序号续传；服务端重启过就会对不上，届时补整块白板。
        this.net.epoch = cached.epoch || null;
        this.net.lastSeq = cached.epoch ? cached.seq || 0 : 0;
      }
    }
    const pending = await this.cache.loadPending();
    if (pending && pending.length) {
      this.net.restoreOutbox(pending);
      for (const item of pending) this.applyOp(item.op, { local: true });
    }
    this.pendingRestored = true;
    // 预热：IndexedDB 的第一次写事务最慢，趁还没开始书写先把它跑掉。
    this.cache.set("warmup", Date.now());
    this.net.connect();
  }

  /** 版本号这类只有本地进程知道的信息，取回来给设置界面用。 */
  async refreshNativeInfo() {
    const api = nativeApi();
    if (!api || !api.info) return;
    try {
      const native = await api.info();
      this.ui.setInfo({ ...(this.ui.info || {}), ...native });
    } catch (err) {
      /* 取不到就不显示版本号 */
    }
  }

  /** 打包成 .app 时，启动后台检查的结果会在这里被取走并提示。 */
  watchUpdates() {
    const poll = async () => {
      const api = nativeApi();
      if (!api || !api.pending_update) return;
      try {
        const info = await api.pending_update();
        if (info) await this.offerUpdate(info);
      } catch (err) {
        /* 取不到就算了 */
      }
    };
    addEventListener("pywebviewready", () => {
      this.refreshNativeInfo();
      setTimeout(poll, 2500);
      setTimeout(poll, 60000);
    });
    setTimeout(() => this.refreshNativeInfo(), 1200);
    setTimeout(poll, 4000);
  }

  async offerUpdate(info) {
    const api = nativeApi();
    if (!info || !api || this.offeredUpdate === info.version) return;
    this.offeredUpdate = info.version;
    const state = (await api.update_state()) || { info };
    this.ui.showUpdateDialog(state, {
      poll: () => api.update_state(),
      download: () => api.download_update(),
      setAuto: (enabled) => api.set_auto_update(enabled),
      skip: () => api.skip_update(),
      installOnQuit: () => api.install_update("quit"),
      installNow: () => api.install_update("now"),
    });
  }

  bindWindow() {
    const onResize = () => {
      this.renderer.resize();
      this.clampView();
      this.renderer.requestFull();
    };
    addEventListener("resize", onResize);
    addEventListener("orientationchange", onResize);
    if (window.visualViewport) visualViewport.addEventListener("resize", onResize);

    addEventListener("keydown", (event) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) this.redo();
        else this.undo();
      } else if (meta && event.key.toLowerCase() === "y") {
        event.preventDefault();
        this.redo();
      } else if (meta && (event.key === "0" || event.key === ")")) {
        event.preventDefault();
        this.fit();
      } else if (meta && (event.key === "=" || event.key === "+")) {
        event.preventDefault();
        this.zoom(1.25);
      } else if (meta && event.key === "-") {
        event.preventDefault();
        this.zoom(0.8);
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.persist();
    });
    addEventListener("pagehide", () => {
      this.persist();
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    });

    if (this.role === "mac") this.bindDrop();
  }

  /** 把 PDF / 图片拖进窗口就新建一块文档板。 */
  bindDrop() {
    const accept = (event) => {
      const items = event.dataTransfer && event.dataTransfer.types;
      return items && [...items].includes("Files");
    };
    addEventListener("dragover", (event) => {
      if (!accept(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      document.body.classList.add("dropping");
    });
    addEventListener("dragleave", (event) => {
      if (event.relatedTarget === null) document.body.classList.remove("dropping");
    });
    addEventListener("drop", (event) => {
      if (!accept(event)) return;
      event.preventDefault();
      document.body.classList.remove("dropping");
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) this.importDoc(file);
    });
  }

  /** 上传一份 PDF / 图片，服务端建好板之后会广播切换，这里不用自己跳。 */
  async importDoc(file) {
    if (this.importing) return;
    this.importing = true;
    const done = this.ui.message(`正在打开 ${file.name}…`, "doc", 60000);
    try {
      await uploadThumb(this.state, this.state.id);
      const response = await fetch(`/api/doc?name=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file,
      });
      if (!response.ok) {
        const text = (await response.text()) || `${response.status}`;
        this.ui.message(`打不开：${text.slice(0, 80)}`, "close", 6000);
      }
    } catch (err) {
      this.ui.message(`打不开：${String(err).slice(0, 80)}`, "close", 6000);
    } finally {
      done();
      this.importing = false;
    }
  }

  /** 文档板导出：笔迹合进原件，打包成 PDF / 图片。 */
  async exportDoc() {
    const boardId = this.state.id;
    if (!boardId) return;
    const api = nativeApi();
    const done = this.ui.message("正在导出…", "download", 60000);
    try {
      if (api && api.export_doc) {
        const path = await api.export_doc(boardId);
        done();
        if (path) this.ui.toast("check");
        else if (path === false) this.ui.message("导出失败，日志里有详细原因", "close", 6000);
      } else {
        location.href = `/api/export/${boardId}`;
        done();
        this.ui.toast("check");
      }
    } catch (err) {
      done();
      this.ui.message(`导出失败：${String(err).slice(0, 80)}`, "close", 6000);
    }
  }

  loop() {
    let previous = performance.now();
    const frame = () => {
      const started = performance.now();
      // 这一帧里出什么岔子都不能让渲染循环停下来，所以下一帧放在 finally 里排。
      try {
        this.input.flushLive();
        this.renderer.tick();
        if (this.perf.enabled) {
          this.perf.countSamples(this.input.takeSampleCount());
          this.perf.frame(started, started - previous, performance.now() - started);
        }
      } catch (err) {
        this.frameErrors = (this.frameErrors || 0) + 1;
        if (this.frameErrors <= 3) {
          console.error("渲染帧出错", err);
          reportError("渲染帧出错", {
            message: String(err && err.message ? err.message : err),
            stack: err && err.stack ? String(err.stack).slice(0, 400) : "",
          });
        }
      } finally {
        previous = started;
        requestAnimationFrame(frame);
      }
    };
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------- 输入回调

  inputHooks() {
    return {
      onStrokeStart: (stroke) => {
        document.documentElement.dataset.drawing = "1";
        this.ui.strokeStarted();
        this.net.sendLive({
          t: "live",
          id: stroke.id,
          phase: "b",
          tool: stroke.tool,
          color: stroke.color,
          w: stroke.w,
        });
      },
      onLivePoints: (stroke, points) => {
        this.net.sendLive({ t: "live", id: stroke.id, phase: "m", p: points });
      },
      onStrokeEnd: (stroke) => {
        delete document.documentElement.dataset.drawing;
        this.net.sendLive({ t: "live", id: stroke.id, phase: "e" });
        this.commitStroke(stroke);
      },
      onStrokeCancel: (stroke) => {
        delete document.documentElement.dataset.drawing;
        this.net.sendLive({ t: "live", id: stroke.id, phase: "x" });
      },
      onErase: (x, y, radius, from) => {
        if (this.tool.eraserMode === "pixel") return this.erasePixels(x, y, radius, from);
        const ids = this.state.hitTest(x, y, radius, strokeHit);
        if (!ids.length) return [];
        const removed = this.state.remove(ids);
        this.eraseBatch.push(...removed);
        // 只重画被删掉的那几笔占的地方，整屏重绘在笔多的板上每帧要十几毫秒
        this.dirtyFor(removed);
        this.queueErase(removed, []);
        return ids;
      },
      onEraseEnd: () => {
        this.flushErase();
        const cut = this.pixelBatch;
        if (cut.removed.length || cut.bites.size) {
          const bites = [...cut.bites.entries()].map(([id, before]) => ({
            id,
            before,
            after: (this.state.byId.get(id) || {}).m || null,
          }));
          this.pushUndo({
            type: "split",
            removed: cut.removed,
            added: cut.added,
            bites: bites.filter((b) => b.after || b.before),
          });
          this.pixelBatch = { removed: [], added: [], bites: new Map() };
        } else if (this.eraseBatch.length) {
          this.pushUndo({ type: "removed", strokes: this.eraseBatch.map(plainStroke) });
        } else {
          return;
        }
        this.eraseBatch = [];
        this.viewAnim = 0;
        this.saveCache();
        this.pushThumb();
      },
      onInteractionStart: () => this.stopViewAnimation(),
      onGestureEnd: () => this.snapToPageWidth(),
      onPenInterrupted: (count) => {
        // 页面这侧已经把能拦的都拦了，连续被打断只可能是系统级的随手写。
        if (count !== 3) return;
        this.ui.showNotice(
          "scribble",
          "笔迹被系统打断了几次。iPad 的「随手写」会抢走 Apple Pencil 的输入，" +
            "在 设置 → Apple Pencil 里关掉它即可。"
        );
        reportError("笔迹被系统打断", { count });
      },
      onViewChange: () => {
        this.clampView();
        this.renderer.requestFull();
        this.saveView();
      },
    };
  }

  commitStroke(stroke) {
    // 兜底：id 撞上已有笔画就换一个，绝不允许一笔画完之后凭空消失。
    for (let guard = 0; this.state.byId.has(stroke.id) && guard < 50; guard += 1) {
      stroke.id = this.input.newStrokeId();
    }
    const { added } = this.state.add([stroke]);
    if (added.length) {
      this.renderer.drawCommitted(stroke);
    } else {
      reportError("笔画提交失败", { id: stroke.id, points: stroke.p.length / 3 });
      this.renderer.requestFull();
    }
    // 重做要把这一笔原样放回去，所以连内容一起记下来
    this.pushUndo({ type: "added", ids: [stroke.id], strokes: [plainStroke(stroke)] });
    this.net.sendOp({ op: "add", strokes: [plainStroke(stroke)] });
    this.saveCache();
    this.pushThumb();
  }

  // ------------------------------------------------------- 撤销与重做

  /**
   * 一条历史记录长这样：``{type, ids, strokes}``。
   *
   * type 记的是「当初干了什么」：added 是画上去，removed 是擦掉 / 清屏。
   * 撤销就是反着来，重做就是再来一遍，所以两边都要有完整的笔画内容——
   * 只记 id 的话，撤销掉一笔之后就没东西可以放回去了。
   */
  pushUndo(action) {
    this.undoStack.push(action);
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
    // 新动作一出现，原来那条重做的分支就作废了
    this.redoStack.length = 0;
    this.syncHistory();
  }

  syncHistory() {
    this.ui.setUndoEnabled(this.undoStack.length > 0);
    this.ui.setRedoEnabled(this.redoStack.length > 0);
  }

  /** 把一条记录正着或反着应用到白板上。 */
  /**
   * 像素橡皮擦：把扫过的笔画切开，用切剩的几段换掉原来那一条。
   *
   * 笔迹仍然是矢量的，所以同步、撤销、导出都不用改格式——发出去的就是一条
   * ``remove`` 加一条 ``restore``，切出来的段沿用原来那条的层叠序号 ``n``，
   * 叠放关系不会变。
   */
  erasePixels(x, y, radius, from) {
    const [fromX, fromY] = from || [x, y];
    const removed = [];
    const added = [];
    // 只看扫过的那几格里的笔画。以前是每个事件把整块白板过一遍，笔画一多，
    // 擦得越快每个事件要走的距离越长、要比的笔画却一点没少。
    // near() 拿到的是候选，精确判定照旧在 splitStroke 里做。
    const bitten = [];
    const candidates = this.state.near(fromX, fromY, x, y, radius);
    for (const stroke of candidates) {
      // 橡皮不比笔细就切断，比笔细就只能啃——啃出来的形状切笔画表达不了
      const kind = eraseKind(stroke, fromX, fromY, x, y, radius);
      if (kind === null) continue;
      if (kind === "bite") {
        const before = stroke.m ? stroke.m.map((c) => c.slice()) : null;
        addMask(stroke, fromX, fromY, x, y, radius);
        if (maskSize(stroke) > MASK_LIMIT) {
          // 先抽稀。擦一大块时相邻几段胶囊几乎完全重合，抽掉之后形状看不出变化，
          // 段数能少一个量级。落实成切分是看得见的变化——啃出来的形状换成平口
          // 断面，还会把贴边的细条一起清掉——能不走就不走。
          simplifyMask(stroke);
          // 抽完还超，说明橡皮真的覆盖了这么多互不重合的地方，再抽也抽不动
          if (maskSize(stroke) > MASK_LIMIT) {
            const baked = this.bakeMask(stroke);
            if (baked) {
              removed.push(baked.removed);
              added.push(...baked.added);
              continue;
            }
          }
        }
        bitten.push({ stroke, before });
        continue;
      }
      const runs = splitStroke(stroke, fromX, fromY, x, y, radius);
      if (runs === null) continue;
      const base = plainStroke(stroke);
      removed.push(base);
      for (const run of runs) {
        const piece = { ...base, id: uid(12), p: run.p, cut: run.cut };
        // 切出来的每一段只留自己够得着的那几段胶囊
        if (base.m) piece.m = maskFor(base.m, strokeBBox(piece));
        if (piece.m && !piece.m.length) delete piece.m;
        added.push(piece);
      }
    }
    if (bitten.length) this.noteBites(bitten, fromX, fromY, x, y, radius);
    if (!removed.length) return [];

    const ids = removed.map((s) => s.id);
    this.state.remove(ids);
    if (added.length) this.state.add(added.map((s) => ({ ...s })));
    this.queueErase(removed, added);
    // 被切的那几笔原来占的地方就是要重画的范围，切剩的段还在里面
    this.dirtyFor(removed);
    // 一次拖动里切出来的段还可能被再切一次。撤销记录只保留最外面那一层：
    // removed 是这次拖动开始前就存在的那些，added 是此刻还留在板上的那些，
    // 中途产生又被切掉的段两边都不进。
    const gone = new Set(ids);
    const mine = new Set(this.pixelBatch.added.map((s) => s.id));
    for (const stroke of removed) {
      if (!mine.has(stroke.id)) this.pixelBatch.removed.push(stroke);
    }
    this.pixelBatch.added = this.pixelBatch.added.filter((s) => !gone.has(s.id));
    this.pixelBatch.added.push(...added);
    return ids;
  }

  /**
   * 记下这一帧的啃咬：发一条 mask 操作、只标胶囊那一小块脏区、并入撤销批次。
   *
   * mask 操作发的是整条笔画当前的遮罩而不是增量——遮罩本来就小，全量发在
   * 断线重连、乱序到达的情况下都不会错，不用管顺序。
   */
  noteBites(bitten, fromX, fromY, x, y, radius) {
    const box = maskBounds([[radius, fromX, fromY, x, y]]);
    this.renderer.requestRect(box.x0, box.y0, box.x1, box.y1);
    for (const { stroke, before } of bitten) {
      // 一次拖动里同一条笔画会被啃很多下，撤销只记这次拖动开始前的那个遮罩
      if (!this.pixelBatch.bites.has(stroke.id)) {
        this.pixelBatch.bites.set(stroke.id, before);
      }
      this.eraseQueue.mask.set(stroke.id, stroke.m.map((c) => c.slice()));
    }
    if (!this.eraseFlush) {
      this.eraseFlush = requestAnimationFrame(() => this.flushErase());
    }
  }

  /**
   * 遮罩攒到上限了：把它落实成切分，换掉原来那一条笔画。
   *
   * 裁剪开销和胶囊数成正比，所以遮罩不能无限堆。日常来回涂同一块地方由
   * addMask 的「新胶囊盖住旧的就丢掉」挡掉；真涂够 MASK_LIMIT 次不同的地方，
   * 就按这些胶囊把笔画切开，遮罩清空。
   *
   * 切分表达不了「削掉半边」，所以这一步会把贴着边的细条一起清掉。涂到这个
   * 次数本来就是在使劲擦，把残边一起清掉是符合意图的。
   */
  bakeMask(stroke) {
    const base = plainStroke(stroke);
    if (!base.m || !base.m.length) return null;
    let pieces = [{ p: base.p, cut: base.cut | 0 }];
    for (const chain of base.m) {
      const radius = chain[0];
      for (let i = 1; i + 3 < chain.length; i += 2) {
        const next = [];
        for (const piece of pieces) {
          const probe = { tool: base.tool, w: base.w, p: piece.p };
          const runs = splitStroke(
            probe, chain[i], chain[i + 1], chain[i + 2], chain[i + 3], radius
          );
          if (runs === null) {
            next.push(piece);
            continue;
          }
          runs.forEach((run, index) => {
            let cut = run.cut;
            // 这一段最外面那两头沿用原来那一段的切口标记
            if (index === 0) cut |= piece.cut & 1;
            if (index === runs.length - 1) cut |= piece.cut & 2;
            next.push({ p: run.p, cut });
          });
        }
        pieces = next;
      }
    }
    delete base.m;
    return {
      removed: { ...base, m: stroke.m },
      added: pieces.map((piece) => ({
        ...base,
        id: uid(12),
        p: piece.p,
        cut: piece.cut,
      })),
    };
  }

  /** 把这几笔占的地方标成脏区，下一帧只重画这一块。 */
  dirtyFor(strokes) {
    for (const stroke of strokes) {
      const box = strokeBBox(stroke);
      this.renderer.requestRect(box.x0, box.y0, box.x1, box.y1);
    }
  }

  /**
   * 擦除产生的操作攒起来每帧发一次，而不是每个指针事件发一条。
   *
   * 擦得快的时候指针事件本来就密（iPad 上 120Hz），像素橡皮擦一次还要发
   * 「删掉原来那条」加「补上切剩的几段」两条，一次拖动能发出两百多条、上百 KB。
   * 攒一帧再发，顺带把「这一帧里刚切出来又被再切掉」的段两边对消掉，
   * 那些段从来没出现在别的设备上，根本不用发。
   */
  queueErase(removed, added) {
    const queue = this.eraseQueue;
    for (const stroke of removed) {
      queue.mask.delete(stroke.id);
      if (queue.add.has(stroke.id)) queue.add.delete(stroke.id);
      else queue.remove.set(stroke.id, stroke);
    }
    for (const stroke of added) queue.add.set(stroke.id, stroke);
    if (!this.eraseFlush) {
      this.eraseFlush = requestAnimationFrame(() => this.flushErase());
    }
  }

  flushErase() {
    if (this.eraseFlush) {
      cancelAnimationFrame(this.eraseFlush);
      this.eraseFlush = 0;
    }
    const queue = this.eraseQueue;
    const ids = [...queue.remove.keys()];
    const strokes = [...queue.add.values()];
    const masks = [...queue.mask.entries()].map(([id, m]) => ({ id, m }));
    queue.remove.clear();
    queue.add.clear();
    queue.mask.clear();
    if (ids.length) this.net.sendOp({ op: "remove", ids });
    if (strokes.length) this.net.sendOp({ op: "restore", strokes });
    // 被删掉的笔画不用再发它的遮罩
    const gone = new Set(ids);
    const live = masks.filter((entry) => !gone.has(entry.id));
    if (live.length) this.net.sendOp({ op: "mask", masks: live });
  }

  applyHistory(action, undoing) {
    if (action.type === "split") {
      // 撤销就是把切出来的段换回原来那几条，重做反过来
      const gone = undoing ? action.added : action.removed;
      const back = undoing ? action.removed : action.added;

      const ids = gone.map((s) => s.id);
      if (ids.length) {
        this.state.remove(ids);
        this.net.sendOp({ op: "remove", ids });
      }
      if (back.length) {
        this.state.add(back.map((s) => ({ ...s })));
        this.net.sendOp({ op: "restore", strokes: back });
      }
      // 遮罩要在笔画换回来之后再设，不然会落到已经被删掉的 id 上
      const masks = [];
      for (const bite of action.bites || []) {
        const stroke = this.state.byId.get(bite.id);
        if (!stroke) continue;
        const m = undoing ? bite.before : bite.after;
        if (m && m.length) stroke.m = m.map((c) => c.slice());
        else delete stroke.m;
        masks.push({ id: bite.id, m: stroke.m || [] });
      }
      if (masks.length) this.net.sendOp({ op: "mask", masks });
      this.renderer.requestFull();
      this.saveCache();
      this.pushThumb();
      return;
    }
    const removing = undoing ? action.type === "added" : action.type === "removed";
    const ids = action.ids || action.strokes.map((s) => s.id);
    if (removing) {
      this.state.remove(ids);
      this.net.sendOp({ op: "remove", ids });
    } else {
      this.state.add(action.strokes.map((s) => ({ ...s })));
      this.net.sendOp({ op: "restore", strokes: action.strokes });
    }
    this.renderer.requestFull();
    this.saveCache();
    this.pushThumb();
  }

  undo() {
    const action = this.undoStack.pop();
    if (!action) {
      this.syncHistory();
      return;
    }
    this.applyHistory(action, true);
    this.redoStack.push(action);
    this.syncHistory();
  }

  redo() {
    const action = this.redoStack.pop();
    if (!action) {
      this.syncHistory();
      return;
    }
    this.applyHistory(action, false);
    this.undoStack.push(action);
    this.syncHistory();
  }

  clearBoard() {
    const all = this.state.clear();
    if (!all.length) return;
    this.pushUndo({ type: "removed", strokes: all.map(plainStroke) });
    this.net.sendOp({ op: "clear" });
    this.renderer.requestFull();
    this.saveCache();
    this.pushThumb();
  }

  // ------------------------------------------------------------ 网络

  onMessage(msg) {
    switch (msg.t) {
      case "init":
      case "switch":
        this.onSnapshot(msg);
        break;
      case "sync":
        this.onSync(msg);
        break;
      case "op":
        this.applyOp(msg.op, { mine: msg.mine });
        break;
      case "live":
        this.onRemoteLive(msg);
        break;
      case "boards":
        // 只是列表变了（比如别处给某块白板改了名），不必重来一遍整块白板
        if (msg.board && msg.board.id === this.state.id) {
          this.state.meta = msg.board;
          this.ui.setMeta(msg.board);
        }
        this.ui.setBoards(msg.boards || [], this.state.id);
        break;
      default:
        break;
    }
  }

  onSnapshot(msg) {
    if (msg.info) this.ui.setInfo(msg.info);
    const switched = this.state.id !== msg.board.id;
    this.net.boardId = msg.board.id;
    this.net.lastSeq = msg.seq || 0;
    if (msg.epoch) this.net.epoch = msg.epoch;
    this.remoteLive.clear();
    this.renderer.clearLive();
    if (switched) {
      this.undoStack = [];
    this.redoStack = [];
      this.ui.setUndoEnabled(false);
    }
    this.applyBoard(msg.board, msg.strokes || [], msg.seq || 0, { keepView: !switched });
    if (msg.boards) this.ui.setBoards(msg.boards, msg.board.id);
    this.reapplyPending();
  }

  onSync(msg) {
    if (msg.info) this.ui.setInfo(msg.info);
    if (msg.board) {
      this.state.meta = msg.board;
      this.ui.setMeta(msg.board);
    }
    for (const op of msg.ops || []) this.applyOp(op, { remote: true });
    if (msg.boards) this.ui.setBoards(msg.boards, msg.board ? msg.board.id : this.state.id);
    this.renderer.requestFull();
    this.reapplyPending();
    this.saveCache();
  }

  /** 快照会覆盖本地内容，这里把还没发出去的操作重新贴回来。 */
  reapplyPending() {
    if (!this.pendingRestored) return;
    for (const item of this.net.outbox) this.applyOp(item.op, { local: true });
  }

  applyBoard(meta, strokes, seq, options = {}) {
    this.state.reset(meta, strokes.map((s) => ({ ...s })));
    this.ui.setMeta(meta);
    if (!options.keepView) this.restoreView(meta.id);
    this.renderer.requestFull();
    if (!options.fromCache) this.saveCache();
    this.cache.setLast(meta.id);
  }

  applyOp(op, context = {}) {
    if (!op) return;
    switch (op.op) {
      case "add": {
        const strokes = op.strokes.map((s) => ({ ...s }));
        const { added, reordered } = this.state.add(strokes);
        for (const stroke of strokes) this.dropRemoteLive(stroke.id);
        if (reordered) this.renderer.requestFull();
        else for (const stroke of added) this.renderer.drawCommitted(stroke);
        break;
      }
      case "remove":
        this.state.remove(op.ids || []);
        this.renderer.requestFull();
        break;
      case "clear":
        this.state.clear();
        this.renderer.requestFull();
        break;
      case "restore": {
        this.state.add((op.strokes || []).map((s) => ({ ...s })));
        this.renderer.requestFull();
        break;
      }
      case "mask": {
        for (const entry of op.masks || []) {
          const stroke = this.state.byId.get(entry.id);
          if (!stroke) continue;
          if (entry.m && entry.m.length) stroke.m = entry.m.map((c) => c.slice());
          else delete stroke.m;
          }
        this.renderer.requestFull();
        break;
      }
      case "meta":
        this.state.meta = op.meta;
        this.ui.setMeta(op.meta);
        this.renderer.requestFull();
        break;
      default:
        return;
    }
    if (!context.local) this.saveCache();
  }

  onRemoteLive(msg) {
    const key = `r:${msg.id}`;
    if (msg.phase === "b") {
      const stroke = {
        id: msg.id,
        tool: msg.tool,
        color: msg.color,
        w: msg.w,
        p: msg.p ? [...msg.p] : [],
      };
      this.remoteLive.set(msg.id, stroke);
      this.renderer.setLive(key, stroke);
      return;
    }
    const stroke = this.remoteLive.get(msg.id);
    if (!stroke) return;
    if (msg.phase === "m" && msg.p) {
      stroke.p.push(...msg.p);
      stroke._path = null;
      stroke._bbox = null;
    } else if (msg.phase === "e") {
      if (msg.p) stroke.p.push(...msg.p);
      stroke._path = null;
      // 正式操作到达前先留着，避免笔迹闪一下消失。
      setTimeout(() => this.dropRemoteLive(msg.id), REMOTE_LIVE_TTL);
    } else if (msg.phase === "x") {
      this.dropRemoteLive(msg.id);
    }
  }

  dropRemoteLive(id) {
    if (!this.remoteLive.has(id)) return;
    this.remoteLive.delete(id);
    this.renderer.setLive(`r:${id}`, null);
  }

  // ------------------------------------------------------------ 视图

  restoreView(boardId) {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(`whiteboard.view.${boardId}`) || "null");
    } catch (err) {
      saved = null;
    }
    if (saved && saved.scale > 0) {
      this.viewport.scale = saved.scale;
      this.viewport.x = saved.x;
      this.viewport.y = saved.y;
      this.clampView();
      return;
    }
    const limits = this.state.limits;
    if (limits) {
      // 笔记：按页宽铺满、停在页首
      this.viewport.fitWidth(limits, this.renderer.viewW, this.renderer.viewH);
      return;
    }
    const bounds = contentBounds(this.state);
    if (this.role === "ipad") {
      // iPad 以 1:1 显示，落笔位置和手感与本机屏幕一致；有内容就停在内容上。
      this.viewport.scale = 1;
      const cx = bounds ? (bounds.x0 + bounds.x1) / 2 : 0;
      const cy = bounds ? (bounds.y0 + bounds.y1) / 2 : 0;
      this.viewport.centerOn(cx, cy, this.renderer.viewW, this.renderer.viewH);
      return;
    }
    if (bounds) this.viewport.fit(bounds, this.renderer.viewW, this.renderer.viewH);
    else {
      this.viewport.scale = 1;
      this.viewport.centerOn(0, 0, this.renderer.viewW, this.renderer.viewH);
    }
  }

  persistView() {
    if (!this.state.id) return;
    try {
      localStorage.setItem(
        `whiteboard.view.${this.state.id}`,
        JSON.stringify({ scale: this.viewport.scale, x: this.viewport.x, y: this.viewport.y })
      );
    } catch (err) {
      /* 存不下就算了，不影响使用 */
    }
  }

  /** 写缓存要把整块白板序列化一遍，绝不能在落笔前后插进来。 */
  persistWhenIdle() {
    const input = this.input;
    const busy =
      input && (input.draw || input.erase || performance.now() - input.lastInputAt < SAVE_IDLE);
    if (busy) {
      this.saveCache();
      return;
    }
    const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 0));
    idle(() => this.persist(), { timeout: 2000 });
  }

  persist() {
    if (!this.state.meta) return;
    const started = performance.now();
    const payload = {
      meta: this.state.meta,
      strokes: this.state.strokes.map(plainStroke),
      seq: this.net.lastSeq,
      epoch: this.net.epoch,
    };
    const built = performance.now();
    this.cache.saveBoard(this.state.id, payload).then(() => {
      this.perf.mark("写盘", performance.now() - built);
    });
    this.cache.savePending(this.net.outbox);
    this.perf.mark("序列化", built - started);
  }

  stopViewAnimation() {
    if (this.viewAnim) {
      cancelAnimationFrame(this.viewAnim);
      this.viewAnim = 0;
    }
  }

  /** 缓动到指定视角。 */
  animateView(target, ms = SNAP_MS) {
    this.stopViewAnimation();
    const from = { scale: this.viewport.scale, x: this.viewport.x, y: this.viewport.y };
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      this.viewport.scale = from.scale + (target.scale - from.scale) * eased;
      this.viewport.x = from.x + (target.x - from.x) * eased;
      this.viewport.y = from.y + (target.y - from.y) * eased;
      this.clampView();
      this.renderer.requestFull();
      if (t < 1) {
        this.viewAnim = requestAnimationFrame(step);
      } else {
        this.viewAnim = 0;
        this.saveView();
      }
    };
    this.viewAnim = requestAnimationFrame(step);
  }

  /**
   * 笔记：松手（或滚轮停下）时若已经接近「一页正好占满屏幕宽」，就吸附过去，
   * 纸的左右边贴住屏幕两侧。返回是否接管了这次收尾。
   */
  snapToPageWidth() {
    const limits = this.state.limits;
    if (!limits) return false;
    const pageWidth = limits.x1 - limits.x0;
    const target = this.renderer.viewW / pageWidth;
    const current = this.viewport.scale;
    const offset = Math.abs(current - target);
    if (offset > target * SNAP_RATIO) return false; // 离得还远，不打扰
    if (offset < target * 0.002) return false; // 已经贴住了

    // 缩放绕视口中心进行，纵向位置保持不动
    const [, centerWorldY] = this.viewport.toWorld(
      this.renderer.viewW / 2,
      this.renderer.viewH / 2
    );
    this.animateView({
      scale: target,
      x: -limits.x0 * target,
      y: this.renderer.viewH / 2 - centerWorldY * target,
    });
    return true;
  }

  /** 笔记模式不能划出纸外，大白板不受约束。 */
  clampView() {
    const limits = this.state.limits;
    if (limits) this.viewport.clampToPage(limits, this.renderer.viewW, this.renderer.viewH);
  }

  zoom(factor) {
    this.viewport.zoomAt(factor, this.renderer.viewW / 2, this.renderer.viewH / 2);
    this.clampView();
    this.renderer.requestFull();
    this.saveView();
  }

  /** 回到内容：大白板把笔迹全装进视口；笔记按页宽铺满并回到内容开头。 */
  fit() {
    const limits = this.state.limits;
    const bounds = contentBounds(this.state);
    if (limits) {
      this.viewport.fitWidth(limits, this.renderer.viewW, this.renderer.viewH);
      if (bounds) {
        this.viewport.y = 24 - bounds.y0 * this.viewport.scale;
        this.clampView();
      }
    } else if (bounds) {
      this.viewport.fit(bounds, this.renderer.viewW, this.renderer.viewH);
    } else {
      this.viewport.scale = 1;
      this.viewport.centerOn(0, 0, this.renderer.viewW, this.renderer.viewH);
    }
    this.renderer.requestFull();
    this.saveView();
  }

  actionsOpenUrl(url) {
    const api = nativeApi();
    if (api && api.open_external) api.open_external(url);
    else window.open(url, "_blank");
  }

  // ------------------------------------------------------------ 界面动作

  actions() {
    return {
      isNative: () => !!nativeApi(),
      onToggleDebug: () => {
        this.perf.toggle();
        // 录制和卡顿诊断同一个开关：iPad 是 Web Clip 打开的，没有地址栏
        this.recorderPanel?.toggle(this.perf.enabled);
      },
      onCheckUpdate: async () => {
        const api = nativeApi();
        if (!api || !api.check_update_now) return "这个窗口没有本地接口";
        this.offeredUpdate = null; // 手动检查时即使之前提示过也要再弹一次
        const result = (await api.check_update_now()) || {};
        switch (result.status) {
          case "update":
            await this.offerUpdate(result);
            return `发现新版本 ${result.version}`;
          case "latest":
            return `已是最新（v${result.version || ""}）`;
          case "skipped":
            return `${result.version} 已被跳过`;
          case "source":
            return `源码运行（v${result.version || ""}），更新请用 git pull`;
          case "error":
            return `检查失败：${result.message || "未知原因"}`;
          default:
            return "检查失败";
        }
      },
      onFingerDraw: (enabled) => this.input.setFingerDraw(enabled),
      onToolChange: (tool) => {
        this.tool = tool;
      },
      onUndo: () => this.undo(),
      onRedo: () => this.redo(),
      onClear: () => this.clearBoard(),
      onZoom: (factor) => this.zoom(factor),
      onFit: () => this.fit(),
      onBoardsOpen: () => uploadThumb(this.state, this.state.id),
      onSelectBoard: async (boardId) => {
        if (boardId === this.state.id) return;
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "sel", board: boardId });
      },
      onNewBoard: async (kind) => {
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "newboard", kind });
      },
      onDeleteBoard: (boardId) => this.net.send({ t: "delboard", board: boardId }),
      onRenameBoard: (boardId, name) => this.net.send({ t: "rename", board: boardId, name }),
      onRemotePermission: async (name, enabled) => {
        const api = nativeApi();
        if (!api || !api.set_remote_permission) return null;
        const granted = await api.set_remote_permission(name, enabled);
        await this.refreshNativeInfo();
        return granted;
      },
      onMeta: (patch) => {
        const meta = { ...this.state.meta, ...patch };
        this.state.meta = meta;
        this.ui.setMeta(meta);
        this.renderer.requestFull();
        this.net.sendOp({ op: "meta", meta: patch });
      },
      onNewDoc: (file) => this.importDoc(file),
      onExport: async () => {
        if (this.state.kind === "doc") {
          await this.exportDoc();
          return;
        }
        const dataUrl = exportDataURL(this.state);
        const api = nativeApi();
        const name = `whiteboard-${new Date().toISOString().slice(0, 10)}.png`;
        if (api && api.save_png) {
          const path = await api.save_png(dataUrl, name);
          if (path) this.ui.toast("check");
        } else {
          downloadDataURL(dataUrl, name);
          this.ui.toast("check");
        }
      },
      onProfile: async () => {
        const api = nativeApi();
        if (api && api.save_profile) {
          const path = await api.save_profile();
          if (path) this.ui.toast("check");
        } else {
          location.href = "/profile.mobileconfig";
        }
      },
      onChooseDir: async () => {
        const api = nativeApi();
        if (!api || !api.choose_data_dir) return null;
        return api.choose_data_dir();
      },
      onOpenReleases: () => {
        const info = this.ui.info || {};
        this.actionsOpenUrl(info.releases || "https://github.com/Maimai-l/white-board/releases");
      },
      onOpenLog: () => {
        const api = nativeApi();
        if (api && api.open_log) api.open_log();
      },
      onOpenDir: () => {
        const api = nativeApi();
        if (api && api.open_data_dir) api.open_data_dir();
      },
      onOpenUrl: (url) => {
        const api = nativeApi();
        if (api && api.open_external) api.open_external(url);
        else window.open(url, "_blank");
      },
    };
  }
}

window.whiteboard = new App();
// 输入录制：平时只是挂着不花钱，?record=1 才画出那个开始 / 停止的小面板。
// 真笔才触发得了的问题（压感、倾角、一帧二十几个合并采样点）靠它带回开发机。
window.whiteboard.recorder = new Recorder(window.whiteboard);
window.whiteboard.recorder.attach(window.whiteboard.input.stage);
window.whiteboard.recorderPanel = mountRecorderPanel(window.whiteboard.recorder);
window.whiteboard.recorderPanel.toggle(window.whiteboard.perf.enabled);
// 切笔画的几何是纯函数，挂出来给端到端测试直接调
window.whiteboard.splitStroke = splitStroke;
window.whiteboard.buildPath = buildPath;
window.whiteboard.eraseKind = eraseKind;
window.whiteboard.strokeBBox = strokeBBox;
window.whiteboard.plainStroke = plainStroke;
window.whiteboard.maskSize = maskSize;
window.whiteboard.simplifyMask = simplifyMask;
window.whiteboard.Renderer = Renderer;
window.whiteboard.strokeHit = strokeHit;
window.whiteboard.penAltitude = penAltitude;

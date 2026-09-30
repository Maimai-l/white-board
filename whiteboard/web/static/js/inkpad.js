// 手写板：书写、橡皮擦、撤销、同步、离线缓存和视口，不含任何界面。
//
// 白板应用（app.js）在它上面加白板选择界面、设置、导入导出和更新；其他应用（例如
// 刷题页面）可以直接用它嵌入一块手写区域。和界面有关的事情一律以事件通知出去：
//
//   status    连接状态："online" / "syncing" / "offline"
//   history   {undo, redo}：撤销、重做是否可用
//   meta      白板元数据变了（换白板、改背景、改名）
//   boards    {boards, current, folders}：白板列表（只有跟随当前白板的连接收得到）
//   info      服务端信息（版本、地址等）
//   locked    {board, locked, unlock}：白板以只读方式打开，或者解除了只读
//   strokestart / strokeend    开始、结束一笔
//   interrupted {count}        笔迹连续被系统打断（通常是「随手写」）
//   change    白板内容变了（写、擦、撤销、清空）
//   deleted   {board}：固定的白板被删除
//   error     {reason}：服务端拒绝了连接（例如 pin 无效）
//
// 协议见 docs/protocol.md；「固定白板的连接」一节说明 ``pin`` 选项。

import { BoardState } from "./boardstate.js";
import { Cache } from "./cache.js";
import { InputController } from "./input.js";
import { Net } from "./net.js";
import { PerfMonitor } from "./perf.js";
import { connectShell } from "./shell.js";
import { Renderer } from "./renderer.js";
import { Viewport } from "./viewport.js";
import { clearStrokeCache, splitLongStroke, strokeHit } from "./stroke.js";
import { debounce, plainStroke, uid } from "./util.js";
import { contentBounds } from "./exporter.js";
import { installEraser } from "./app-eraser.js";
import { reportError } from "./report.js";

const UNDO_LIMIT = 200;
// 写 IndexedDB 会卡主线程（iOS 上首次写事务尤其慢），离最后一次落笔足够远才写。
const SAVE_DEBOUNCE = 4000;
const SAVE_IDLE = 1500;
// 笔记：缩放到接近「刚好一页宽」时自动吸附到屏幕两边。
const SNAP_RATIO = 0.08;
const SNAP_MS = 180;
const REMOTE_LIVE_TTL = 5000;

/** 本机的客户端 id，存在 localStorage 里，同一台设备上的所有页面共用。 */
export function deviceClientId() {
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

/** 默认工具。白板应用用界面上选中的工具替换它。 */
export function defaultTool() {
  return { tool: "pen", color: "#1b1b1f", width: 3, eraserMode: "object" };
}

export class InkPad {
  /**
   * @param {object} options
   * @param {HTMLElement} options.stage   接收指针事件的元素，两块画布叠在它里面
   * @param {HTMLCanvasElement} options.base  已提交笔画的画布
   * @param {HTMLCanvasElement} options.live  正在书写的笔画的画布
   * @param {"mac"|"ipad"} [options.role]   决定初始视图和默认输入方式
   * @param {string} [options.clientId]     连接的客户端 id
   * @param {object} [options.pin]          固定白板：{board, app, name, kind, folder}
   * @param {object} [options.tool]         初始工具
   * @param {boolean} [options.shell]       接收 iPad 外壳的 Pencil 采样（默认接收）
   */
  constructor(options) {
    this.role = options.role || "mac";
    this.pin = options.pin || null;
    this.clientId = options.clientId || deviceClientId();
    this.listeners = new Map();
    this.state = new BoardState();
    this.viewport = new Viewport();
    this.renderer = new Renderer(options.base, options.live, this.state, this.viewport);
    // 固定白板的手写板各自一份缓存和待发队列，和白板应用、别的手写板互不干扰
    this.cache = new Cache(this.pin ? `pin:${this.pin.board}` : "");
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
    this.tool = options.tool || defaultTool();

    this.perf = new PerfMonitor();
    this.perf.role = this.role;

    this.net = new Net({
      clientId: this.clientId,
      role: this.role,
      pin: this.pin,
      onMessage: (msg) => this.onMessage(msg),
      onStatus: (status) => this.emit("status", status),
    });
    // 待发队列同样走「空闲才写」，不然每写一笔就有两次写盘插进来。
    this.net.onOutboxChange = () => this.saveCache();

    this.input = new InputController({
      stage: options.stage,
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
    // iPad 外壳：在外壳里时 Pencil 的采样由外壳提供，见 shell.js
    if (options.shell !== false) connectShell(this);

    this.saveCache = debounce(() => this.persistWhenIdle(), SAVE_DEBOUNCE);
    this.saveView = debounce(() => this.persistView(), 400);
  }

  /** 开始工作：画出缓存、连上服务端、启动渲染循环。子类先接好事件再调用。 */
  start() {
    this.bindWindow();
    this.bootstrap();
    this.loop();
    return this;
  }

  // ------------------------------------------------------------ 事件

  on(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(listener);
    return () => this.listeners.get(name).delete(listener);
  }

  emit(name, detail) {
    const listeners = this.listeners.get(name);
    if (!listeners) return;
    for (const listener of listeners) {
      try {
        listener(detail);
      } catch (err) {
        console.error(`手写板事件 ${name} 的处理出错`, err);
      }
    }
  }

  /** 内容变了：写、擦、撤销、清空之后调用。 */
  pushThumb() {
    this.emit("change");
  }

  // ------------------------------------------------------------ 启动

  async bootstrap() {
    // 先用本地缓存把上次的内容画出来，网络就绪后再对齐服务端。
    const lastId = this.pin ? this.pin.board : await this.cache.getLast();
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

  bindWindow() {
    const onResize = () => {
      this.renderer.resize();
      this.clampView();
      this.renderer.requestFull();
    };
    addEventListener("resize", onResize);
    addEventListener("orientationchange", onResize);
    if (window.visualViewport) visualViewport.addEventListener("resize", onResize);

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.persist();
    });
    addEventListener("pagehide", () => this.persist());
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
        this.emit("strokestart", stroke);
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
        this.emit("strokeend", stroke);
        this.net.sendLive({ t: "live", id: stroke.id, phase: "e" });
        this.commitStroke(stroke);
      },
      onStrokeCancel: (stroke) => {
        this.emit("strokeend", stroke);
        this.net.sendLive({ t: "live", id: stroke.id, phase: "x" });
      },
      onErase: (x, y, radius, from) => {
        if (this.tool.eraserMode === "pixel") return this.erasePixels(x, y, radius, from);
        let ids = this.state.hitTest(x, y, radius, strokeHit);
        if (!ids.length) return [];
        // 碰到被像素橡皮啃断过的笔画，先按缺口切成独立的几条，再只删碰到的那一条
        if (this.splitBitten(ids)) {
          ids = this.state.hitTest(x, y, radius, strokeHit);
          if (!ids.length) return [];
        }
        const removed = this.state.remove(ids);
        this.noteObjectErase(removed);
        // 只重画被删掉的那几笔占的地方，整屏重绘在笔多的板上每帧要十几毫秒
        this.dirtyFor(removed);
        this.queueErase(removed, []);
        return ids;
      },
      onEraseEnd: () => {
        this.flushErase();
        const cut = this.pixelBatch;
        // 对象橡皮碰到啃过的笔画时也会走切分，所以这一批可能两种都有，得并成一条
        const plain = this.eraseBatch.map(plainStroke);
        if (cut.removed.length || cut.added.length || cut.bites.size) {
          const bites = [...cut.bites.entries()].map(([id, before]) => ({
            id,
            before,
            after: (this.state.byId.get(id) || {}).m || null,
          }));
          this.pushUndo({
            type: "split",
            removed: [...cut.removed, ...plain],
            added: cut.added,
            bites: bites.filter((b) => b.after || b.before),
          });
          this.pixelBatch = { removed: [], added: [], bites: new Map() };
        } else if (plain.length) {
          this.pushUndo({ type: "removed", strokes: plain });
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
        this.emit("interrupted", { count });
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
    // 画得特别久的一笔在这里切成几段：服务端对超长点列是整条丢掉，不切的话
    // 这一笔本机看得见，对端和存档里没有。接缝共用一个点，看不出来。
    const pieces = splitLongStroke(stroke, () => this.input.newStrokeId());
    const { added } = this.state.add(pieces);
    if (added.length === pieces.length) {
      for (const piece of pieces) this.renderer.drawCommitted(piece);
    } else {
      reportError("笔画提交失败", { id: stroke.id, points: stroke.p.length / 3 });
      this.renderer.requestFull();
    }
    const plain = pieces.map(plainStroke);
    // 重做要把这一笔原样放回去，所以连内容一起记下来。切开的几段算一步撤销：
    // 用户画的是一笔，撤销就该一次全没。
    this.pushUndo({ type: "added", ids: plain.map((s) => s.id), strokes: plain });
    this.net.sendOp({ op: "add", strokes: plain });
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
    this.emit("history", { undo: this.undoStack.length > 0, redo: this.redoStack.length > 0 });
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
    if (this.locked) return;
    const all = this.state.clear();
    if (!all.length) return;
    this.pushUndo({ type: "removed", strokes: all.map(plainStroke) });
    this.net.sendOp({ op: "clear" });
    this.renderer.requestFull();
    this.saveCache();
    this.pushThumb();
  }

  /** 改白板设置（目前是背景）。本地先生效，再发给服务端。 */
  setMeta(patch) {
    if (this.locked) return;
    const meta = { ...this.state.meta, ...patch };
    this.state.meta = meta;
    this.emit("meta", meta);
    this.renderer.requestFull();
    this.net.sendOp({ op: "meta", meta: patch });
  }

  setTool(tool) {
    this.tool = tool;
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
          this.emit("meta", msg.board);
        }
        this.emit("boards", { boards: msg.boards || [], current: this.state.id, folders: msg.folders });
        break;
      case "deleted":
        this.emit("deleted", { board: msg.board });
        break;
      case "error":
        this.emit("error", { reason: msg.reason });
        break;
      default:
        break;
    }
  }

  onSnapshot(msg) {
    if (msg.info) this.emit("info", msg.info);
    this.setLocked(msg.board.id, msg.locked);
    const switched = this.state.id !== msg.board.id;
    this.net.boardId = msg.board.id;
    this.net.lastSeq = msg.seq || 0;
    if (msg.epoch) this.net.epoch = msg.epoch;
    this.remoteLive.clear();
    this.renderer.clearLive();
    if (switched) {
      this.undoStack = [];
      this.redoStack = [];
      this.syncHistory();
    }
    this.applyBoard(msg.board, msg.strokes || [], msg.seq || 0, { keepView: !switched });
    if (msg.boards) this.emit("boards", { boards: msg.boards, current: msg.board.id, folders: msg.folders });
    this.reapplyPending();
  }

  onSync(msg) {
    if (msg.info) this.emit("info", msg.info);
    if (msg.board) this.setLocked(msg.board.id, msg.locked);
    if (msg.board) {
      this.state.meta = msg.board;
      this.emit("meta", msg.board);
    }
    for (const op of msg.ops || []) this.applyOp(op, { remote: true });
    if (msg.boards) {
      this.emit("boards", {
        boards: msg.boards,
        current: msg.board ? msg.board.id : this.state.id,
        folders: msg.folders,
      });
    }
    this.renderer.requestFull();
    this.reapplyPending();
    this.saveCache();
  }

  /**
   * 服务端说这块白板的文件没能完整读出来（损坏、个别笔画解不开，或者是更新的
   * 版本写的），以只读方式打开。停止书写，并告诉用户为什么；有管理权限的设备
   * 可以选择仍然编辑——服务端会先备份原文件。同一块白板每次打开只提示一次。
   */
  setLocked(boardId, locked) {
    this.locked = locked || null;
    this.input.readOnly = !!this.locked;
    if (!this.locked) {
      // 别的设备上点了「仍然编辑」，这边还开着的提示也收起来
      this.lockNotified = null;
      this.emit("locked", { board: boardId, locked: null });
      return;
    }
    if (this.lockNotified === boardId) return;
    this.lockNotified = boardId;
    this.emit("locked", { board: boardId, locked: this.locked, unlock: () => this.net.send({ t: "unlock" }) });
  }

  /** 快照会覆盖本地内容，这里把还没发出去的操作重新贴回来。 */
  reapplyPending() {
    if (!this.pendingRestored) return;
    for (const item of this.net.outbox) this.applyOp(item.op, { local: true });
  }

  applyBoard(meta, strokes, seq, options = {}) {
    this.state.reset(meta, strokes.map((s) => ({ ...s })));
    this.emit("meta", meta);
    if (!options.keepView) this.restoreView(meta.id);
    this.renderer.requestFull();
    if (!options.fromCache) this.saveCache();
    if (!this.pin) this.cache.setLast(meta.id);
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
        // 自己发出去的遮罩不要再盖回来。发的是全量，而回执回到手里时本地往往
        // 已经又擦了几下——照盖就是拿旧快照覆盖新状态，擦掉的点白丢，而且
        // 下一段胶囊接不回去，链断开、断口处细成一道脖子。实测一次连续拖动、
        // 120 个采样，本该是一条链，盖回来之后变成 60 条、回执再慢一点变成
        // 24 条且只剩 52 个点。看上去就是擦痕一节一节，像一串香肠。
        //
        // 本地是自己这些改动的权威：发之前就已经原样应用过了。服务端只会在
        // 超出上限时改写遮罩，而上限已经放在前端上限之上（models.py 的
        // MAX_MASK_SEGMENTS，tests/test_models.py 里钉着），正常擦不到。
        if (context.mine) break;
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
        this.emit("meta", op.meta);
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
      clearStrokeCache(stroke);
    } else if (msg.phase === "e") {
      if (msg.p) stroke.p.push(...msg.p);
      clearStrokeCache(stroke);
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
}

// 橡皮擦的那部分（切笔画、记遮罩、攒网络操作）单独放在 app-eraser.js，方法装回 InkPad 上。
installEraser(InkPad.prototype);

// 主控：把状态、渲染、输入、网络和界面接到一起。

import { BoardState } from "./boardstate.js";
import { Cache } from "./cache.js";
import { InputController } from "./input.js";
import { Net } from "./net.js";
import { Renderer } from "./renderer.js";
import { UI } from "./ui.js";
import { Viewport } from "./viewport.js";
import { strokeHit } from "./stroke.js";
import { debounce, plainStroke, uid } from "./util.js";
import { downloadDataURL, exportDataURL, uploadThumb } from "./exporter.js";

const UNDO_LIMIT = 200;
const REMOTE_LIVE_TTL = 5000;

function resolveRole() {
  const params = new URLSearchParams(location.search);
  const asked = params.get("role");
  if (asked === "mac" || asked === "ipad") return asked;
  const fromServer = document.documentElement.dataset.role;
  if (navigator.maxTouchPoints > 1) return "ipad";
  return fromServer === "ipad" ? "ipad" : "mac";
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
    this.remoteLive = new Map();
    this.eraseBatch = [];
    this.pendingRestored = false;

    this.ui = new UI({ role: this.role, native: !!nativeApi(), actions: this.actions() });
    this.tool = this.ui.toolState();

    this.net = new Net({
      clientId: this.clientId,
      role: this.role,
      onMessage: (msg) => this.onMessage(msg),
      onStatus: (status) => this.ui.setStatus(status),
    });
    this.net.onOutboxChange = debounce((outbox) => this.cache.savePending(outbox), 400);

    this.input = new InputController({
      stage: document.getElementById("stage"),
      viewport: this.viewport,
      renderer: this.renderer,
      device: this.role,
      strokePrefix: this.clientId,
      getTool: () => this.tool,
      hooks: this.inputHooks(),
    });

    this.saveCache = debounce(() => this.persist(), 600);
    this.saveView = debounce(() => this.persistView(), 400);
    this.pushThumb = debounce(() => {
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    }, 5000);

    this.bindWindow();
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
    this.net.connect();
  }

  bindWindow() {
    const onResize = () => {
      this.renderer.resize();
      const [w, h] = this.state.size();
      this.viewport.clampTo(w, h, this.renderer.viewW, this.renderer.viewH);
      this.renderer.requestFull();
    };
    addEventListener("resize", onResize);
    addEventListener("orientationchange", onResize);
    if (window.visualViewport) visualViewport.addEventListener("resize", onResize);

    addEventListener("keydown", (event) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "z") {
        event.preventDefault();
        this.undo();
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

    addEventListener("pagehide", () => {
      this.persist();
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    });
  }

  loop() {
    const frame = () => {
      this.input.flushLive();
      this.renderer.tick();
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------- 输入回调

  inputHooks() {
    return {
      onStrokeStart: (stroke) => {
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
        this.net.sendLive({ t: "live", id: stroke.id, phase: "e" });
        this.commitStroke(stroke);
      },
      onStrokeCancel: (stroke) => {
        this.net.sendLive({ t: "live", id: stroke.id, phase: "x" });
      },
      onErase: (x, y, radius) => {
        const ids = this.state.hitTest(x, y, radius, strokeHit);
        if (!ids.length) return [];
        const removed = this.state.remove(ids);
        this.eraseBatch.push(...removed);
        this.renderer.requestFull();
        this.net.sendOp({ op: "remove", ids });
        return ids;
      },
      onEraseEnd: () => {
        if (!this.eraseBatch.length) return;
        this.pushUndo({ type: "removed", strokes: this.eraseBatch.map(plainStroke) });
        this.eraseBatch = [];
        this.saveCache();
        this.pushThumb();
      },
      onViewChange: () => {
        const [w, h] = this.state.size();
        this.viewport.clampTo(w, h, this.renderer.viewW, this.renderer.viewH);
        this.renderer.requestFull();
        this.saveView();
      },
      onPencilDetected: () => this.ui.toast("pen"),
    };
  }

  commitStroke(stroke) {
    const { added } = this.state.add([stroke]);
    if (added.length) this.renderer.drawCommitted(stroke);
    this.pushUndo({ type: "added", ids: [stroke.id] });
    this.net.sendOp({ op: "add", strokes: [plainStroke(stroke)] });
    this.saveCache();
    this.pushThumb();
  }

  // ------------------------------------------------------------ 撤销

  pushUndo(action) {
    this.undoStack.push(action);
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
    this.ui.setUndoEnabled(true);
  }

  undo() {
    const action = this.undoStack.pop();
    this.ui.setUndoEnabled(this.undoStack.length > 0);
    if (!action) return;
    if (action.type === "added") {
      this.state.remove(action.ids);
      this.net.sendOp({ op: "remove", ids: action.ids });
    } else {
      this.state.add(action.strokes.map((s) => ({ ...s })));
      this.net.sendOp({ op: "restore", strokes: action.strokes });
    }
    this.renderer.requestFull();
    this.saveCache();
    this.pushThumb();
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
    const [boardW, boardH] = this.state.size();
    if (!options.keepView) this.restoreView(meta.id, boardW, boardH);
    this.viewport.clampTo(boardW, boardH, this.renderer.viewW, this.renderer.viewH);
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
      case "meta":
        this.state.meta = op.meta;
        this.ui.setMeta(op.meta);
        this.viewport.clampTo(
          ...this.state.size(),
          this.renderer.viewW,
          this.renderer.viewH
        );
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

  restoreView(boardId, boardW, boardH) {
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
      return;
    }
    if (this.role === "ipad") {
      // iPad 以 1:1 显示，落笔位置和手感与本机屏幕一致，起始停在正中那一屏。
      this.viewport.scale = 1;
      this.viewport.centerOn(boardW / 2, boardH / 2, this.renderer.viewW, this.renderer.viewH);
    } else {
      this.viewport.fit(boardW, boardH, this.renderer.viewW, this.renderer.viewH);
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

  persist() {
    if (!this.state.meta) return;
    this.cache.saveBoard(this.state.id, {
      meta: this.state.meta,
      strokes: this.state.strokes.map(plainStroke),
      seq: this.net.lastSeq,
      epoch: this.net.epoch,
    });
    this.cache.savePending(this.net.outbox);
  }

  zoom(factor) {
    this.viewport.zoomAt(factor, this.renderer.viewW / 2, this.renderer.viewH / 2);
    const [w, h] = this.state.size();
    this.viewport.clampTo(w, h, this.renderer.viewW, this.renderer.viewH);
    this.renderer.requestFull();
    this.saveView();
  }

  fit() {
    const [w, h] = this.state.size();
    this.viewport.fit(w, h, this.renderer.viewW, this.renderer.viewH);
    this.renderer.requestFull();
    this.saveView();
  }

  // ------------------------------------------------------------ 界面动作

  actions() {
    return {
      isNative: () => !!nativeApi(),
      onToolChange: (tool) => {
        this.tool = tool;
      },
      onUndo: () => this.undo(),
      onClear: () => this.clearBoard(),
      onZoom: (factor) => this.zoom(factor),
      onFit: () => this.fit(),
      onSelectBoard: async (boardId) => {
        if (boardId === this.state.id) return;
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "sel", board: boardId });
      },
      onNewBoard: async () => {
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "newboard" });
      },
      onDeleteBoard: (boardId) => this.net.send({ t: "delboard", board: boardId }),
      onMeta: (patch) => {
        const meta = { ...this.state.meta, ...patch };
        this.state.meta = meta;
        this.ui.setMeta(meta);
        const [w, h] = this.state.size();
        this.viewport.clampTo(w, h, this.renderer.viewW, this.renderer.viewH);
        this.renderer.requestFull();
        this.net.sendOp({ op: "meta", meta: patch });
      },
      onExport: async () => {
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

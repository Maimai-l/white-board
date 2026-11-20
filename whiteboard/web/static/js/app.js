// 白板应用：在手写板（inksync 的 pad.js）上加白板选择界面、设置、导入导出、更新和快捷键。
//
// 书写、橡皮擦、撤销、同步和视口都在 InkPad 里；这里只把它的事件接到界面上，
// 再加上只有白板应用才有的功能。其他应用嵌入手写板时不需要这个文件。

import { InkPad, deviceClientId } from "/inksync/pad.js";
import { penAltitude } from "/inksync/input.js";
import { Recorder, mountRecorderPanel } from "./recorder.js";
import { Renderer } from "/inksync/renderer.js";
import { Ruler } from "/inksync/ruler.js";
import { RulerView } from "./ruler-view.js";
import { UI } from "./ui.js";
import {
  buildPath,
  strokeOutline,
  maskSize,
  pressureForFactor,
  simplifyMask,
  splitStroke,
  strokeHit,
  strokeRadius,
} from "/inksync/stroke.js";
import { debounce, isTextField } from "/inksync/util.js";
import {
  boardView,
  loadFingerDraw,
  loadShapeLines,
  saveFingerDraw,
  saveShapeLines,
  uploadThumb,
} from "./boards.js";
import { ExportDialog } from "./ui-export.js";
import { reportError, reportUncaughtErrors, setReportURL } from "/inksync/report.js";

export { reportError };

// iPad 上没有控制台：页面报错送到 Mac 的终端日志里
setReportURL("/api/debug");
reportUncaughtErrors();

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

function nativeApi() {
  return (window.pywebview && window.pywebview.api) || null;
}

class App extends InkPad {
  constructor() {
    const role = resolveRole();
    document.documentElement.dataset.role = role;
    super({
      role,
      clientId: deviceClientId(),
      // 白板应用的页面跟随 Mac 上的当前白板
      target: { space: "", follow: true },
      fingerDraw: loadFingerDraw(),
      shapeLines: loadShapeLines(),
      stage: document.getElementById("stage"),
      base: document.getElementById("base"),
      live: document.getElementById("live"),
    });
    if (new URLSearchParams(location.search).get("debug") === "1") this.perf.toggle(true);

    this.ui = new UI({
      role: this.role,
      native: !!nativeApi(),
      perms: resolvePerms(),
      actions: this.actions(),
    });
    this.tool = this.ui.toolState();
    this.bindPad();

    this.uploadThumbSoon = debounce(() => {
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    }, 5000);

    this.bindKeys();
    this.watchZoom();
    this.watchNativeInfo();
    this.start();
  }

  /** 把手写板的事件接到界面上。 */
  bindPad() {
    const root = document.documentElement;
    this.on("status", (status) => this.ui.setStatus(status));
    this.on("history", ({ undo, redo }) => {
      this.ui.setUndoEnabled(undo);
      this.ui.setRedoEnabled(redo);
    });
    this.on("meta", (meta) => {
      this.ui.setMeta(boardView(meta));
      // 换了白板：导出对话框是给上一块开的，关掉
      const dialog = this.ui.exportDialog;
      if (dialog && dialog.boardId !== this.state.id) dialog.close();
      if (this.pendingExport && this.pendingExport === this.state.id) {
        this.pendingExport = null;
        this.openExport();
      }
    });
    this.on("info", (info) => this.ui.setInfo(info));
    this.on("shell", (shell) => {
      root.dataset.shell = shell.active ? "active" : "inactive";
    });
    // 服务端升级了（例如 Mac 上装了新版本）：等手上没有进行中的书写，再重新载入页面
    this.on("outdated", ({ server }) => this.reloadWhenIdle(server));
    this.on("strokestart", () => {
      root.dataset.drawing = "1";
      this.ui.strokeStarted();
    });
    this.on("strokeend", () => {
      delete root.dataset.drawing;
    });
    this.on("interrupted", () => {
      this.ui.showNotice(
        "scribble",
        ["笔迹被随手写打断", "在 iPad 设置的 Apple Pencil 中关闭随手写"]
      );
    });
    this.on("locked", ({ locked, unlock }) => {
      root.toggleAttribute("data-locked", !!locked);
      if (locked) this.ui.showLocked(locked, unlock);
      else this.ui.hideLocked();
    });
    this.on("change", () => this.uploadThumbSoon());
  }

  /** 白板应用的扩展消息：白板列表（也随快照一起来）和权限。 */
  onMessage(msg) {
    super.onMessage(msg);
    if (msg.t === "boards" && msg.board && msg.board.id === this.state.id) {
      this.state.meta = msg.board;
      this.emit("meta", msg.board);
    }
    if (Array.isArray(msg.boards)) {
      this.ui.setBoards(msg.boards.map(boardView), this.state.id, msg.folders);
    }
    if (msg.t === "perms") this.ui.setPerms(new Set(msg.perms || []));
    if (msg.t === "devices") this.ui.setDevices(msg.devices || []);
  }

  reloadWhenIdle(build) {
    try {
      if (sessionStorage.getItem("inksync.reloaded") === build) return; // 同一个版本只重新载入一次
      sessionStorage.setItem("inksync.reloaded", build);
    } catch (err) {
      // 忽略：sessionStorage 用不了时记不住重新载入过没有，干脆不自动重新载入，免得反复刷新
      return;
    }
    const attempt = () => {
      if (this.input.draw || this.input.erase) {
        setTimeout(attempt, 1000);
        return;
      }
      this.persist();
      setTimeout(() => location.reload(), 300);
    };
    attempt();
  }

  bindKeys() {
    addEventListener("keydown", (event) => {
      // 在输入框里打字时这些键归输入框：⌘Z 撤销的是打的字，⌘+ ⌘- 也不该缩放白板
      if (isTextField(event.target)) return;
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
        this.syncZoom();
      } else if (meta && (event.key === "=" || event.key === "+")) {
        event.preventDefault();
        this.zoom(1.25);
        this.syncZoom();
      } else if (meta && event.key === "-") {
        event.preventDefault();
        this.zoom(0.8);
        this.syncZoom();
      }
    });
    addEventListener("pagehide", () => {
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    });
    if (this.role === "mac") this.bindDrop();
  }

  /**
   * 显示或收起尺子。第一次打开时才建：Mac 和别的设备没有尺子按钮，永远走不到这里。
   * 尺子只在本机，不同步，也不记住（重新载入页面就收起）。
   */
  showRuler(shown) {
    if (!this.ruler) {
      if (!shown) return;
      this.ruler = new Ruler();
      this.rulerView = new RulerView(this.input.stage, this.ruler);
      this.input.setRuler(this.ruler);
    }
    this.ruler.show(shown);
  }

  /**
   * 缩放按钮中间的百分比。视口在很多地方会变（滚轮、触控板捏合、手势、换白板、
   * 回到内容），没有统一的事件，所以在这些输入之后各量一次，比每帧轮询省。
   */
  watchZoom() {
    const later = () => requestAnimationFrame(() => this.syncZoom());
    for (const type of ["wheel", "pointerup", "gestureend", "resize"]) addEventListener(type, later, { passive: true });
    this.on("meta", later);
    later();
  }

  syncZoom() {
    if (this.viewport) this.ui.setZoom(this.viewport.scale || 1);
  }

  /** 版本号这类只有本地进程知道的信息，取回来给设置界面用。 */
  async refreshNativeInfo() {
    const api = nativeApi();
    if (!api || !api.info) return;
    try {
      const native = await api.info();
      this.ui.setInfo({ ...(this.ui.info || {}), ...native });
    } catch (err) {
      // 取不到就不显示版本号
      reportError("app.refreshNativeInfo 取不到本地信息", { message: String(err) });
    }
  }

  /** 版本号等本地信息：pywebview 的接口注入得比页面脚本晚，到了再取一次。 */
  watchNativeInfo() {
    addEventListener("pywebviewready", () => this.refreshNativeInfo());
    setTimeout(() => this.refreshNativeInfo(), 1200);
  }

  /**
   * 把 PDF / 图片拖进窗口：每个文件新建一块文档板。拖着经过时界面上盖一层提示
   * （ui-boards.js 的 showDrop），白板库开着时可以直接放到边栏的文件夹上。
   */
  bindDrop() {
    const accept = (event) => {
      const items = event.dataTransfer && event.dataTransfer.types;
      return items && [...items].includes("Files");
    };
    addEventListener("dragover", (event) => {
      if (!accept(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      this.ui.showDrop(event);
    });
    addEventListener("dragleave", (event) => {
      if (event.relatedTarget === null) this.ui.showDrop(null);
    });
    addEventListener("drop", (event) => {
      if (!accept(event)) return;
      event.preventDefault();
      this.ui.showDrop(null);
      const files = [...((event.dataTransfer && event.dataTransfer.files) || [])];
      if (files.length) this.importDocs(files, this.ui.dropFolder(this.state.id, event));
    });
  }

  /** 一个一个上传；服务端每建好一块就广播切换，最后停在最后那一块上。 */
  async importDocs(files, folder = "") {
    if (this.importing) return;
    this.importing = true;
    try {
      await uploadThumb(this.state, this.state.id);
      for (const file of files) await this.importDoc(file, folder);
    } finally {
      this.importing = false;
    }
  }

  /** 上传一份 PDF / 图片，服务端建好板之后会广播切换，这里不用自己跳。 */
  async importDoc(file, folder = "") {
    const done = this.ui.message(`正在导入 ${file.name}…`, "doc", 60000);
    try {
      const query = `name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(folder)}`;
      const response = await fetch(`/api/doc?${query}`, {
        method: "POST",
        body: file,
      });
      if (!response.ok) {
        const text = (await response.text()) || `${response.status}`;
        reportError("app.importDoc 打不开文件", { name: file.name, message: text.slice(0, 200) });
        this.ui.message(`打不开 ${file.name}`, "close", 6000);
      }
    } catch (err) {
      reportError("app.importDoc 上传文件失败", { name: file.name, message: String(err) });
      this.ui.message(`打不开 ${file.name}`, "close", 6000);
    } finally {
      done();
    }
  }

  /** 导出：打开导出对话框（分页、选页、格式都在里面，见 ui-export.js）。 */
  openExport() {
    if (!this.state.id) return;
    if (this.ui.exportDialog) this.ui.exportDialog.close();
    this.ui.exportDialog = new ExportDialog(this);
    this.ui.exportDialog.open();
  }

  // 导出对话框要用的几样东西：本地接口、视口中心、屏幕坐标换白板坐标

  native() {
    return nativeApi();
  }

  viewSize() {
    return [this.renderer.viewW, this.renderer.viewH];
  }

  viewCenter() {
    return this.viewport.toWorld(this.renderer.viewW / 2, this.renderer.viewH / 2);
  }

  toWorld(clientX, clientY) {
    const rect = this.input.stage.getBoundingClientRect();
    return this.viewport.toWorld(clientX - rect.left, clientY - rect.top);
  }

  /** 调整页框：先把全部页框放进视口（上面留出按钮条），返回原来的视口，结束时 restoreExportView。 */
  showExportArea(bounds) {
    const saved = { scale: this.viewport.scale, x: this.viewport.x, y: this.viewport.y };
    this.viewport.fit(bounds, this.renderer.viewW, this.renderer.viewH, 80);
    this.clampView();
    this.renderer.requestFull();
    return saved;
  }

  restoreExportView(saved) {
    Object.assign(this.viewport, saved);
    this.clampView();
    this.renderer.requestFull();
  }

  /**
   * 调整页框时不落笔（input.js 对只读的处理）。``claim`` 是调整视图接管单指拖动的
   * 处理器（InputController.setClaim）：在白板上单指拖动拖出页框，两指和滚轮照旧平移缩放。
   */
  setExportAdjusting(on, claim = null) {
    this.exportAdjusting = !!on;
    this.ui.root.toggleAttribute("data-adjusting", this.exportAdjusting);
    this.input.setClaim(this.exportAdjusting ? claim : null);
    this.updateReadOnly();
  }

  updateReadOnly() {
    super.updateReadOnly();
    if (this.exportAdjusting) this.input.readOnly = true;
  }


  /** 只读查看某个应用空间里的一块白板：浮层里放一块只读的手写板，关掉就断开。 */
  async viewAppBoard(space, board) {
    this.ui.closeGallery();
    const { createInkPad } = await import("/inksync/inkpad.js");
    const close = () => {
      pad.destroy();
      overlay.remove();
      removeEventListener("keydown", onKey, true);
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    };
    const title = document.createElement("span");
    title.className = "app-viewer-title";
    title.textContent = `${space} · ${board.name || board.id} · 只读`;
    const shut = document.createElement("button");
    shut.className = "icon-btn";
    shut.title = "关闭";
    shut.textContent = "×";
    shut.addEventListener("click", () => close());
    const head = document.createElement("div");
    head.className = "app-viewer-head";
    head.append(title, shut);
    const body = document.createElement("div");
    body.className = "app-viewer-body";
    const overlay = document.createElement("div");
    overlay.className = "app-viewer";
    overlay.append(head, body);
    document.body.append(overlay);
    const pad = createInkPad(body, { space, board: board.id, readonly: true, role: this.role });
    pad.on("error", ({ reason }) => {
      reportError("app.viewAppBoard 打不开应用的白板", { space, board: board.id, reason: String(reason) });
      title.textContent = "打不开这块白板";
    });
    addEventListener("keydown", onKey, true);
    this.appViewer = { pad, close };
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
        const result = (await api.check_update_now()) || {};
        switch (result.status) {
          case "sparkle":
            return "结果显示在更新窗口";
          case "source":
            return "源码运行时用 git pull 更新";
          case "unavailable":
            // 原因（result.message）app.py 的 check_update_now 已经写进日志
            return "这个版本不能自动更新";
          default:
            return "检查失败";
        }
      },
      onFingerDraw: (enabled) => {
        this.input.setFingerDraw(enabled);
        saveFingerDraw(enabled);
      },
      onShapeLines: (enabled) => {
        this.input.setShapeLines(enabled);
        saveShapeLines(enabled);
      },
      onToolChange: (tool) => this.setTool(tool),
      onRuler: (shown) => this.showRuler(shown),
      onUndo: () => this.undo(),
      onRedo: () => this.redo(),
      onClear: () => this.clearBoard(),
      onZoom: (factor) => {
        this.zoom(factor);
        this.syncZoom();
      },
      onZoomReset: () => {
        this.zoom(1 / (this.viewport.scale || 1));
        this.syncZoom();
      },
      onFit: () => {
        this.fit();
        this.syncZoom();
      },
      onBoardsOpen: () => uploadThumb(this.state, this.state.id),
      onSelectBoard: async (boardId) => {
        if (boardId === this.state.id) return;
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "sel", board: boardId });
      },
      onNewBoard: async (kind, folder = "") => {
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "newboard", kind, folder });
      },
      // 删除是移进回收站；回收站里的再删才是永久删除（purge）
      onDeleteBoard: (boardId) => this.net.send({ t: "delboard", board: boardId }),
      onRestoreBoard: (boardId) => this.net.send({ t: "restore", board: boardId }),
      onPurgeBoard: (boardId) => this.net.send({ t: "purge", board: boardId }),
      onEmptyTrash: () => this.net.send({ t: "emptytrash" }),
      onDuplicateBoard: (boardId) => this.net.send({ t: "duplicate", board: boardId }),
      onExportBoard: async (boardId) => {
        if (boardId === this.state.id) {
          this.openExport();
          return;
        }
        // 导出别的白板：先切过去，切换到了再开导出（见 bindPad 里的 meta）
        this.pendingExport = boardId;
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "sel", board: boardId });
      },
      onRenameBoard: (boardId, name) => this.net.send({ t: "rename", board: boardId, name }),
      onMoveBoard: (boardId, folder) => this.net.send({ t: "folder", board: boardId, folder }),
      onReorderBoards: (ids) => this.net.send({ t: "order", ids }),
      onNewFolder: (name) => this.net.send({ t: "newfolder", name }),
      onDeleteFolder: (name) => this.net.send({ t: "delfolder", name }),
      onRenameFolder: (name, to) => this.net.send({ t: "renamefolder", name, to }),
      onRemotePermission: async (name, enabled) => {
        const api = nativeApi();
        if (!api || !api.set_remote_permission) return null;
        const granted = await api.set_remote_permission(name, enabled);
        await this.refreshNativeInfo();
        return granted;
      },
      onMeta: (patch) => {
        // 设置面板给的是背景样式的名字；元数据里的背景是 {pattern, paper}
        if (typeof patch.background === "string") {
          const current = (this.state.meta && this.state.meta.background) || {};
          patch = { ...patch, background: { ...(typeof current === "object" ? current : {}), pattern: patch.background } };
        }
        this.setMeta(patch);
      },
      onNewDoc: (files, folder) => this.importDocs([].concat(files), folder),
      onViewAppBoard: (space, board) => this.viewAppBoard(space, board),
      onExport: () => this.openExport(),
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
      onIpadHome: async (name) => {
        const api = nativeApi();
        if (!api || !api.set_ipad_home) return null;
        return api.set_ipad_home(name);
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
// 输入录制：平时只是挂着不花钱，诊断面板打开时（连点状态圆点三下，或者地址后面
// 加 ?debug=1）才画出那个开始 / 停止的小面板。
// 真笔才触发得了的问题（压感、倾角、同一个位置投两遍）靠它带回开发机。
window.whiteboard.recorder = new Recorder(window.whiteboard);
window.whiteboard.recorder.attach(window.whiteboard.input.stage);
window.whiteboard.recorderPanel = mountRecorderPanel(window.whiteboard.recorder);
window.whiteboard.recorderPanel.toggle(window.whiteboard.perf.enabled);
// 切笔画的几何是纯函数，挂出来给端到端测试直接调
window.whiteboard.splitStroke = splitStroke;
window.whiteboard.buildPath = buildPath;
window.whiteboard.maskSize = maskSize;
window.whiteboard.simplifyMask = simplifyMask;
window.whiteboard.Renderer = Renderer;
window.whiteboard.strokeHit = strokeHit;
window.whiteboard.strokeRadius = strokeRadius;
window.whiteboard.strokeOutline = strokeOutline;
window.whiteboard.pressureForFactor = pressureForFactor;
window.whiteboard.penAltitude = penAltitude;

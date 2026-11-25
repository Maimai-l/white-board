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
  prepareThumb,
  uploadThumb,
} from "./boards.js";
import { ExportDialog } from "./ui-export.js";
import { reportError, reportUncaughtErrors, setReportURL } from "/inksync/report.js";
import { ImportDialog } from "./ui-import.js";
import { PageScrubber } from "./page-scrubber.js";

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


// 文档板上手指按住一页多久弹出页面菜单（毫秒），和白板库里长按卡片一样
const PAGE_HOLD_MS = 500;
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
    // 连着这台 Mac 的别的设备：init / sync 里带着一份，之后有变化单独发 devices
    if (Array.isArray(msg.devices)) this.ui.setDevices(msg.devices);
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
    this.bindPageMenu();
    // 文档白板右边的页码滑条（多于一页时才有）
    this.scrubber = new PageScrubber(this);
    this.on("meta", () => this.scrubber.setBoard());
    this.on("view", () => this.scrubber.onView());
    addEventListener("resize", () => this.scrubber.place(), { passive: true });
    this.scrubber.setBoard();
  }

  // ------------------------------------------------------------ 文档板的页

  /**
   * 文档板上 (clientX, clientY) 那一页：``{ index, blank }``，不是文档板或不在任何一页附近
   * 时返回 null。落在页间空隙里的算最近的一页（和服务端的 doc_pages 同一条规则）。
   */
  docPageAt(clientX, clientY) {
    return this.docPageNear(this.toWorld(clientX, clientY));
  }

  /** 屏幕正中那一页（更多菜单里的在这一页后面插入空白页）。 */
  currentDocPage() {
    return this.docPageNear(this.viewCenter());
  }

  docPageNear(point) {
    const doc = this.state.meta && this.state.meta.data && this.state.meta.data.doc;
    if (!doc || !point) return null;
    const y = point[1]; // viewport.toWorld 给的是 [x, y]
    const sheets = this.state.layers.filter((layer) => layer.sheet);
    let best = null;
    let bestDistance = Infinity;
    sheets.forEach((layer, index) => {
      const top = layer.y;
      const bottom = layer.y + (layer.height || layer.width);
      const distance = y < top ? top - y : y > bottom ? y - bottom : 0;
      if (distance < bestDistance) {
        best = { index, blank: !layer.src };
        bestDistance = distance;
      }
    });
    return best;
  }

  /** 插入或删除空白页（服务端整块换掉白板，所有设备马上看到）。 */
  async changePages(action, page) {
    const failed = action === "delete" ? "没能删除空白页" : "没能插入空白页";
    try {
      const response = await fetch(`/api/board/${encodeURIComponent(this.state.id)}/pages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, page }),
      });
      if (!response.ok) {
        reportError("app.changePages 服务端没做成", { action, page, status: response.status, message: (await response.text()).slice(0, 200) });
        this.ui.message(failed, "close", 4000);
      }
    } catch (err) {
      reportError("app.changePages 请求失败", { action, page, message: String(err) });
      this.ui.message(failed, "close", 4000);
    }
  }

  /**
   * 文档板上一页的菜单：Mac 上右键，触摸设备上手指按住不动。手指书写打开时手指是在写字，
   * 不认长按，用更多菜单里的那一项。
   */
  bindPageMenu() {
    // 菜单开在手指底下：有的浏览器长按松手后还会补一下 click，它会落在菜单项上，不吞掉就等于
    // 替用户选了一项（Chromium 不补，测试里看不出来；这里是保险）
    const swallowLiftClick = (pointerId) => {
      const lifted = (event) => {
        if (event.pointerId !== pointerId) return;
        removeEventListener("pointerup", lifted, true);
        removeEventListener("pointercancel", lifted, true);
        const eat = (click) => {
          click.stopPropagation();
          click.preventDefault();
        };
        addEventListener("click", eat, { capture: true, once: true });
        setTimeout(() => removeEventListener("click", eat, true), 400);
      };
      addEventListener("pointerup", lifted, true);
      addEventListener("pointercancel", lifted, true);
    };
    const open = (clientX, clientY) => {
      if (!this.ui.may("manage") || this.locked) return false;
      const page = this.docPageAt(clientX, clientY);
      if (!page) return false;
      this.ui.pageMenu(page, { x: clientX, y: clientY }, (action) => this.changePages(action, page.index));
      return true;
    };
    this.input.stage.addEventListener("contextmenu", (event) => {
      if (open(event.clientX, event.clientY)) event.preventDefault();
    });
    let hold = null;
    const cancel = () => {
      if (hold) clearTimeout(hold.timer);
      hold = null;
    };
    addEventListener(
      "pointerdown",
      (event) => {
        // 真手指落在 #stage 里面的画布上（#base、#live），不是 #stage 本身：以前只认 #stage，
        // 真机上长按从来不弹
        if (event.pointerType !== "touch" || this.input.fingerDraw || !this.input.stage.contains(event.target)) return;
        if (hold) {
          cancel(); // 第二根手指：是捏合缩放，不是长按
          return;
        }
        const { clientX, clientY, pointerId } = event;
        hold = { pointerId, x: clientX, y: clientY, timer: setTimeout(() => {
          hold = null;
          if (open(clientX, clientY)) swallowLiftClick(pointerId);
        }, PAGE_HOLD_MS) };
      },
      true
    );
    addEventListener(
      "pointermove",
      (event) => {
        if (hold && event.pointerId === hold.pointerId && Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > 10) cancel();
      },
      true
    );
    for (const type of ["pointerup", "pointercancel"]) {
      addEventListener(type, (event) => {
        if (hold && event.pointerId === hold.pointerId) cancel();
      }, true);
    }
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
      for (const file of files) {
        const pdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
        await (pdf ? this.importPdf(file, folder) : this.importDoc(file, folder));
      }
    } finally {
      this.importing = false;
    }
  }

  /**
   * 导入一份 PDF：先暂存，多于一页就弹出导入对话框选页（ui-import.js），选好了再建板。
   * 只有一页的 PDF 直接建板。图片和旧的路子一样直接上传（importDoc）。
   */
  async importPdf(file, folder = "") {
    const notice = this.ui.message(`正在导入 ${file.name}…`, "doc", 60000);
    let staged = null;
    try {
      const response = await fetch(`/api/doc/stage?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      if (!response.ok) {
        const text = (await response.text()) || `${response.status}`;
        reportError("app.importPdf 暂存不了", { name: file.name, status: response.status, message: text.slice(0, 200) });
      } else {
        staged = await response.json();
      }
    } catch (err) {
      reportError("app.importPdf 上传失败", { name: file.name, size: file.size, message: String(err) });
    }
    if (!staged) {
      notice.update(`打不开 ${file.name}`, "close", 6000);
      return;
    }
    await this.importStaged(staged, folder, notice);
  }

  /**
   * 暂存好的原件：多于一页弹出导入对话框，选好了建板；取消就删掉暂存。Mac 上从访达用
   * 打开方式交过来的 PDF 也走这里（app.py 的 FileOpener 在服务端暂存后调用）。
   */
  async importStaged(staged, folder = "", notice = () => {}) {
    const name = staged.name;
    let choice = { pages: [0], blanks: false };
    if (staged.pages.length > 1) {
      notice();
      choice = await new ImportDialog(this.ui, staged).open();
      if (!choice) {
        fetch(`/api/doc/stage/${encodeURIComponent(staged.stage)}`, { method: "DELETE" }).catch((err) =>
          reportError("app.importStaged 取消时没能删掉暂存", { message: String(err) })
        );
        return;
      }
    }
    try {
      const response = await fetch(`/api/doc/stage/${encodeURIComponent(staged.stage)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...choice, folder }),
      });
      if (!response.ok) {
        reportError("app.importStaged 建板没做成", { name, status: response.status, message: (await response.text()).slice(0, 200) });
        this.ui.message(`打不开 ${name}`, "close", 6000);
        return;
      }
    } catch (err) {
      reportError("app.importStaged 建板请求失败", { name, message: String(err) });
      this.ui.message(`打不开 ${name}`, "close", 6000);
      return;
    }
    notice();
    if (staged.total > staged.pages.length) {
      this.ui.message([`只能从前 ${staged.pages.length} 页里选`, "把 PDF 拆成几份分别导入"], "info", 10000);
    }
  }

  /** 上传一份 PDF / 图片，服务端建好板之后会广播切换，这里不用自己跳。 */
  async importDoc(file, folder = "") {
    // 一个文件只有一条消息：进行中的那条，出错时原地换成打不开，成功就收起
    const done = this.ui.message(`正在导入 ${file.name}…`, "doc", 60000);
    let failed = false;
    let partial = 0;
    try {
      const query = `name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(folder)}`;
      const response = await fetch(`/api/doc?${query}`, {
        method: "POST",
        body: file,
      });
      if (response.ok) {
        // 超过页数上限的 PDF 只导入前面的页：回复里 total 是原件一共的页数
        const body = await response.json();
        const imported = body.board?.data?.doc?.pages?.length || 0;
        if (body.total > imported) partial = imported;
      } else {
        const text = (await response.text()) || `${response.status}`;
        reportError("app.importDoc 打不开文件", { name: file.name, status: response.status, message: text.slice(0, 200) });
        failed = true;
      }
    } catch (err) {
      reportError("app.importDoc 上传文件失败", { name: file.name, size: file.size, message: String(err) });
      failed = true;
    }
    if (failed) done.update(`打不开 ${file.name}`, "close", 6000);
    else if (partial) done.update([`只导入了前 ${partial} 页`, "把 PDF 拆成几份分别导入"], "info", 10000);
    else done();
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
    // 上面有按钮条（Mac 上还有顶栏），四周多留一些，页框不会钻到它们底下
    const bar = document.getElementById("topbar");
    this.viewport.fit(bounds, this.renderer.viewW, this.renderer.viewH, 80 + (bar ? bar.offsetHeight : 0));
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
      onInsertBlankHere: () => {
        const page = this.currentDocPage();
        if (page) this.changePages("insert-after", page.index);
      },
      onBoardsOpen: () => uploadThumb(this.state, this.state.id),
      onSelectBoard: async (boardId) => {
        if (boardId === this.state.id) return;
        // 图趁现在画（白板一换就画不了这块了），传等 sel 发出去之后再传，不拦着切换
        const sendThumb = await prepareThumb(this.state, this.state.id);
        this.net.send({ t: "sel", board: boardId });
        if (sendThumb) sendThumb();
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
      onZoomWindow: () => {
        const api = nativeApi();
        if (api && api.zoom_window) api.zoom_window();
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

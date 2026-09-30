// 白板应用：在手写板（inkpad.js）上加白板选择界面、设置、导入导出、更新和快捷键。
//
// 书写、橡皮擦、撤销、同步和视口都在 InkPad 里；这里只把它的事件接到界面上，
// 再加上只有白板应用才有的功能。其他应用嵌入手写板时不需要这个文件。

import { InkPad, deviceClientId } from "./inkpad.js";
import { penAltitude } from "./input.js";
import { Recorder, mountRecorderPanel } from "./recorder.js";
import { Renderer } from "./renderer.js";
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
} from "./stroke.js";
import { debounce, isTextField } from "./util.js";
import { downloadDataURL, downloadURL, exportDataURL, uploadThumb } from "./exporter.js";
import { reportError, reportUncaughtErrors } from "./report.js";

export { reportError };

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
    this.watchUpdates();
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
    this.on("meta", (meta) => this.ui.setMeta(meta));
    this.on("boards", ({ boards, current, folders }) => this.ui.setBoards(boards, current, folders));
    this.on("info", (info) => this.ui.setInfo(info));
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
        "笔迹被系统打断了几次。iPad 的「随手写」会抢走 Apple Pencil 的输入，" +
          "在 设置 → Apple Pencil 里关掉它即可。"
      );
    });
    this.on("locked", ({ locked, unlock }) => {
      root.toggleAttribute("data-locked", !!locked);
      if (locked) this.ui.showLocked(locked, unlock);
      else this.ui.hideLocked();
    });
    this.on("change", () => this.uploadThumbSoon());
    this.on("perms", (perms) => this.ui.setPerms(new Set(perms)));
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
      } else if (meta && (event.key === "=" || event.key === "+")) {
        event.preventDefault();
        this.zoom(1.25);
      } else if (meta && event.key === "-") {
        event.preventDefault();
        this.zoom(0.8);
      }
    });
    addEventListener("pagehide", () => {
      if (this.role === "mac") uploadThumb(this.state, this.state.id);
    });
    if (this.role === "mac") this.bindDrop();
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
      if (file) this.importDoc(file, this.ui.dropFolder(this.state.id));
    });
  }

  /** 上传一份 PDF / 图片，服务端建好板之后会广播切换，这里不用自己跳。 */
  async importDoc(file, folder = "") {
    if (this.importing) return;
    this.importing = true;
    const done = this.ui.message(`正在打开 ${file.name}…`, "doc", 60000);
    try {
      await uploadThumb(this.state, this.state.id);
      const query = `name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(folder)}`;
      const response = await fetch(`/api/doc?${query}`, {
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
        // 文件名由服务端的 Content-Disposition 决定，这里不写死
        downloadURL(`/api/export/${boardId}`, "");
        done();
        this.ui.toast("check");
      }
    } catch (err) {
      done();
      this.ui.message(`导出失败：${String(err).slice(0, 80)}`, "close", 6000);
    }
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
      onToolChange: (tool) => this.setTool(tool),
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
      onNewBoard: async (kind, folder = "") => {
        await uploadThumb(this.state, this.state.id);
        this.net.send({ t: "newboard", kind, folder });
      },
      onDeleteBoard: (boardId) => this.net.send({ t: "delboard", board: boardId }),
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
      onMeta: (patch) => this.setMeta(patch),
      onNewDoc: (file, folder) => this.importDoc(file, folder),
      onExport: async () => {
        if (this.state.kind === "doc") {
          await this.exportDoc();
          return;
        }
        const name = `whiteboard-${new Date().toISOString().slice(0, 10)}.png`;
        try {
          const dataUrl = exportDataURL(this.state);
          const api = nativeApi();
          if (api && api.save_png) {
            const path = await api.save_png(dataUrl, name);
            if (path) this.ui.toast("check");
          } else {
            await downloadDataURL(dataUrl, name);
            this.ui.toast("check");
          }
        } catch (err) {
          this.ui.message(`导出失败：${String(err).slice(0, 80)}`, "close", 6000);
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

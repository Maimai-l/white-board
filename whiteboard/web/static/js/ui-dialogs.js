// 对话框：关于、更新、确认、只读提示。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 从 ui.js 原样搬过来，只是换了个文件放。

import { icon } from "./icons.js";
import { renderNotes } from "./notes.js";
import { iconButton } from "./ui-common.js";
import { el } from "/inksync/util.js";

export function installDialogs(proto) {
  Object.assign(proto, methods);
}

const methods = {
  /** 关于：图标、名字、版本，以及更新相关的入口都收在这里。 */
  /** 关于：图标、名字、一句说明、几行信息，链接在下面，检查更新在最底下。 */
  openAbout() {
    this.closeSheet();
    const info = this.info || {};
    const rows = [["版本", info.version || "—"]];
    // 从源码跑的时候，页面上那份 data-build 是「分支@提交」，和版本号不是一回事，
    // 两个都给才说得清手上这一份到底是什么。打包版两者相同，就不重复显示了。
    const build = document.documentElement.dataset.build || "";
    if (build && build !== info.version) rows.push(["构建", build]);
    if (info.hostname) rows.push(["地址", `${info.hostname}:${info.port || ""}`]);

    const link = (text, onclick) => el("button", { class: "link", text, onclick });
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog about" }, [
      el("img", { class: "about-icon", src: "/icon.png", alt: "" }),
      el("div", { class: "about-name", text: "白板" }),
      el(
        "div",
        { class: "about-rows" },
        rows.flatMap(([key, value]) => [
          el("span", { class: "about-key", text: key }),
          el("span", { class: "about-val", text: String(value) }),
        ])
      ),
      el("div", { class: "about-links" }, [
        link("更新日志", () => this.actions.onOpenReleases()),
        link("日志文件", () => this.actions.onOpenLog()),
      ]),
      this.updateRow(),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
    return scrim;
  },

  updateRow() {
    const note = el("span", { class: "check-note" });
    const button = el("button", { class: "btn", text: "检查更新" });
    button.addEventListener("click", async () => {
      button.setAttribute("disabled", "");
      note.textContent = "检查中…";
      let message = "";
      try {
        message = await this.actions.onCheckUpdate();
      } catch (err) {
        message = "检查失败";
      }
      note.textContent = message || "";
      button.removeAttribute("disabled");
    });
    // 结果放在按钮上方，并且始终占着一行高度：按钮不会因为出结果而被顶着移动。
    return el("div", { class: "row about-update" }, [note, button]);
  },

  /**
   * 更新对话框：图标、标题、更新说明、自动下载开关、三个按钮。
   *
   * ``state`` 来自本地进程（update_state），``actions`` 里是四个回调：
   * skip / installNow / installOnQuit / setAuto，另外 poll 用来刷新下载进度。
   */
  showUpdateDialog(state, actions) {
    this.closeUpdateDialog();
    const info = state.info || {};
    const version = info.version || "";

    const title = el("h2", { text: `新版本的白板可以安装了` });
    const subtitle = el("p");
    const notes = el("div", { class: "update-notes", html: renderNotes(info.notes) });
    notes.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link) return;
      event.preventDefault();
      this.actions.onOpenUrl(link.getAttribute("href"));
    });

    const bar = el("i");
    const progress = el("div", { class: "update-progress" }, [bar]);

    const checkbox = el("input", { type: "checkbox" });
    checkbox.checked = !!state.auto;
    checkbox.addEventListener("change", () => actions.setAuto(checkbox.checked));
    const auto = el("label", { class: "update-auto" }, [
      checkbox,
      el("span", { text: "以后自动下载更新" }),
    ]);

    const skip = el("button", { class: "btn", text: "跳过这个版本" });
    const later = el("button", { class: "btn", text: "退出应用时安装" });
    const now = el("button", { class: "btn primary", text: "安装并重启应用" });

    const render = (current) => {
      const staged = !!current.staged;
      this.updateStaged = staged;
      const downloading = !!current.downloading;
      subtitle.textContent = staged
        ? `白板 ${version} 已下载完毕并可以使用。要立刻安装并重启白板吗？`
        : downloading
          ? `正在下载白板 ${version}…`
          : `白板 ${version} 可以下载安装，当前版本 ${current.current || ""}。`;
      progress.style.display = downloading || (current.progress > 0 && !staged) ? "" : "none";
      bar.style.width = `${Math.round((current.progress || 0) * 100)}%`;
      now.textContent = staged ? "安装并重启应用" : "下载并安装";
    };
    render(state);

    /** 下载 / 校验失败时把服务端给的具体原因显示出来，而不是一句「失败」。 */
    const failure = async (fallback) => {
      if (!actions.poll) return fallback;
      const current = await actions.poll();
      const reason = current && current.error;
      return reason ? `${reason}，这次先不更新。` : fallback;
    };

    skip.addEventListener("click", async () => {
      await actions.skip();
      this.closeUpdateDialog();
    });
    later.addEventListener("click", async () => {
      later.setAttribute("disabled", "");
      now.setAttribute("disabled", "");
      if (!this.updateStaged) {
        progress.style.display = "";
        if (!(await actions.download())) {
          subtitle.textContent = await failure("下载失败了，稍后再试。");
          later.removeAttribute("disabled");
          now.removeAttribute("disabled");
          return;
        }
      }
      const ok = await actions.installOnQuit();
      this.closeUpdateDialog();
      this.toast(ok ? "check" : "close");
    });
    now.addEventListener("click", async () => {
      now.setAttribute("disabled", "");
      later.setAttribute("disabled", "");
      // 先下载再安装：两步分开，进度条才有东西可显示
      if (!this.updateStaged) {
        subtitle.textContent = `正在下载白板 ${version}…`;
        progress.style.display = "";
        const downloaded = await actions.download();
        if (!downloaded) {
          subtitle.textContent = await failure("下载失败了，稍后再试。");
          now.removeAttribute("disabled");
          later.removeAttribute("disabled");
          return;
        }
      }
      subtitle.textContent = "正在安装，应用会重新打开…";
      const ok = await actions.installNow();
      if (!ok) {
        subtitle.textContent = "安装失败了，稍后再试。";
        now.removeAttribute("disabled");
        later.removeAttribute("disabled");
      }
    });

    const dialog = el("div", { class: "update-dialog" }, [
      el("div", { class: "update-head" }, [
        el("img", { src: "/icon.png", alt: "" }),
        el("div", {}, [title, subtitle]),
      ]),
      notes,
      progress,
      auto,
      el("div", { class: "update-actions" }, [
        skip,
        el("span", { class: "spacer" }),
        later,
        now,
      ]),
    ]);
    const scrim = el("div", { class: "scrim" });
    scrim.append(dialog);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(scrim);
    this.updateDialog = { scrim, render };

    // 下载中就跟着刷新进度
    if (actions.poll) {
      this._updateTimer = setInterval(async () => {
        if (!this.updateDialog) return;
        const current = await actions.poll();
        if (current) render(current);
      }, 400);
    }
    return this.updateDialog;
  },

  closeUpdateDialog() {
    clearInterval(this._updateTimer);
    this._updateTimer = 0;
    if (this.updateDialog) {
      this.updateDialog.scrim.remove();
      this.updateDialog = null;
    }
  },

  /** 问一句再动手。``question`` 就是那句话，图标只是陪衬。 */
  confirm(iconName, question, onYes) {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog ask" }, [
      el("div", { class: "ask-icon", html: icon(iconName, 28) }),
      el("p", { class: "ask-text", text: question }),
      el("div", { class: "ask-actions" }, [
        iconButton("close", "取消", () => scrim.remove()),
        // 要动手的那个染成警示色，两个灰勾灰叉分不清谁是谁
        iconButton("check", "确定", () => {
          scrim.remove();
          onYes();
        }, "danger"),
      ]),
    ]);
    scrim.append(dialog);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(scrim);
  },

  /**
   * 白板以只读方式打开时的提示。``locked.reason`` 见服务端 store.open_board。
   * 有管理权限的设备多一个「仍然编辑」：是用户自己的选择，但要先说清楚后果。
   */
  showLocked(locked, onUnlock) {
    const newer = locked.reason === "newer";
    const text = newer
      ? "这块白板是用更新版本的白板程序保存的，当前版本可能无法完整识别其中的内容。" +
        "为避免损坏数据，现在以只读方式打开。"
      : "这块白板的文件没能完整读出来（文件可能已损坏，或者存储位置暂时无法访问）。" +
        "为避免覆盖原文件，现在以只读方式打开，能读出的内容照常显示。";
    const risk = newer
      ? "仍然编辑的话，保存时这一版不认识的内容可能丢失。"
      : "仍然编辑的话，保存时会用现在读出来的内容覆盖原文件，读不出来的部分会丢失。";
    const canUnlock = this.may("manage");
    this.hideLocked();
    const scrim = el("div", { class: "scrim" });
    const close = () => {
      scrim.remove();
      if (this.lockedDialog === scrim) this.lockedDialog = null;
    };
    this.lockedDialog = scrim;
    const actions = [iconButton("close", "保持只读", close)];
    if (canUnlock) {
      actions.push(
        iconButton("check", "仍然编辑（先备份原文件）", () => {
          close();
          onUnlock();
        }, "danger"),
      );
    }
    const dialog = el("div", { class: "dialog ask locked" }, [
      el("div", { class: "ask-icon", html: icon("info", 28) }),
      el("p", { class: "ask-text", text }),
      el("p", {
        class: "ask-note",
        text: canUnlock
          ? `${risk}原文件会先备份到存储目录的 backups 文件夹。左边保持只读，右边仍然编辑。`
          : "需要在 Mac 上决定是否继续编辑。",
      }),
      el("div", { class: "ask-actions" }, actions),
    ]);
    scrim.append(dialog);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(scrim);
  },

  hideLocked() {
    if (this.lockedDialog) this.lockedDialog.remove();
    this.lockedDialog = null;
  },

  confirmClear() {
    this.confirm("trash", "清空白板？", () => this.actions.onClear());
  },
};

// 对话框：关于（含检查更新）、确认、只读提示。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 从 ui.js 原样搬过来，只是换了个文件放。

import { icon } from "./icons.js";
import { iconButton } from "./ui-common.js";
import { closeMenu } from "./ui-menu.js";
import { el } from "/inksync/util.js";
import { reportError } from "/inksync/report.js";

export function installDialogs(proto) {
  Object.assign(proto, methods);
}

const methods = {
  /**
   * 关于：一个小窗口的样子，图标、名字、版本、构建号，下面是检查更新和两个链接。
   * 地址不放这里，在连接 iPad 里。
   */
  openAbout() {
    closeMenu();
    this.closePanel();
    const info = this.info || {};
    // 从源码跑的时候，页面上那份 data-build 是「分支@提交」，和版本号不是一回事，
    // 两个都给才说得清手上这一份到底是什么。打包版两者相同，就不重复显示了。
    const build = document.documentElement.dataset.build || "";
    const link = (text, onclick) => el("button", { class: "link", type: "button", text, onclick });
    const scrim = el("div", { class: "scrim clear", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog about", role: "dialog", "aria-label": "关于白板" }, [
      el("div", { class: "about-lights" }, [
        el("button", { class: "about-close", type: "button", title: "关闭", "aria-label": "关闭", onclick: () => scrim.remove() }),
        el("span", { "aria-hidden": "true" }),
        el("span", { "aria-hidden": "true" }),
      ]),
      el("img", { class: "about-icon", src: "/icon.png", alt: "" }),
      el("div", { class: "about-name", text: "白板" }),
      el("div", { class: "about-version", text: `版本 ${info.version || "—"}` }),
      build && build !== info.version ? el("div", { class: "about-build", text: build }) : null,
      this.updateRow(),
      el("div", { class: "about-links" }, [
        link("更新日志", () => this.actions.onOpenReleases()),
        link("日志文件", () => this.actions.onOpenLog()),
      ]),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
    return scrim;
  },

  updateRow() {
    const note = el("span", { class: "check-note" });
    const button = el("button", { class: "lib-btn tinted", type: "button", text: "检查更新" });
    button.addEventListener("click", async () => {
      button.setAttribute("disabled", "");
      note.textContent = "检查中…";
      let message = "";
      try {
        message = await this.actions.onCheckUpdate();
      } catch (err) {
        reportError("ui.updateRow 检查更新失败", { message: String(err) });
        message = "检查失败";
      }
      note.textContent = message || "";
      button.removeAttribute("disabled");
    });
    // 结果放在按钮上方，并且始终占着一行高度：按钮不会因为出结果而被顶着移动。
    return el("div", { class: "row about-update" }, [note, button]);
  },

  /**
   * 问一句再动手。``question`` 说要做的事（不用问号，docs/ui-text.md），``verb`` 是
   * 确认按钮上的动词，``note`` 是可选的第二句。图标只是陪衬。
   */
  confirm(iconName, question, onYes, { verb = "确定", note = "" } = {}) {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const dialog = el("div", { class: "dialog ask" }, [
      el("div", { class: "ask-icon", html: icon(iconName, 28) }),
      el("p", { class: "ask-text", text: question }),
      note ? el("p", { class: "ask-note", text: note }) : null,
      el("div", { class: "ask-actions" }, [
        iconButton("close", "取消", () => scrim.remove()),
        // 要动手的那个染成警示色，两个灰勾灰叉分不清谁是谁
        iconButton("check", verb, () => {
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
   * 两句话：出了什么事，能怎么办（docs/ui-text.md）。有管理权限的设备多一个
   * 「仍然编辑」：是用户自己的选择，服务端会先备份原文件。
   */
  showLocked(locked, onUnlock) {
    const newer = locked.reason === "newer";
    const text = newer ? "这块白板由更新版本保存" : "这块白板没能完整读出来";
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
        iconButton("check", "仍然编辑", () => {
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
        text: canUnlock ? "仍然编辑会先备份原文件" : "可以在 Mac 上选择仍然编辑",
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
    this.confirm("trash", "清空这块白板", () => this.actions.onClear(), { verb: "清空" });
  },
};

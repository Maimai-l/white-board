// 对话框：确认、只读提示。（关于白板是 macOS 自己的关于窗口，见 whiteboard/appmenu.py）
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 从 ui.js 原样搬过来，只是换了个文件放。

import { icon } from "./icons.js";
import { el } from "/inksync/util.js";

/** 对话框里的文字按钮。title 和文字一样，按钮靠文字认，不靠图标。 */
function textButton(text, onclick, kind = "") {
  return el("button", { class: `lib-btn${kind ? ` ${kind}` : ""}`, type: "button", title: text, text, onclick });
}

export function installDialogs(proto) {
  Object.assign(proto, methods);
}

const methods = {
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
      // 和 macOS 的提醒框一样用文字按钮：取消在左，要动手的那个在右、红色
      el("div", { class: "ask-actions" }, [
        textButton("取消", () => scrim.remove()),
        textButton(verb, () => {
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
    const actions = [textButton("保持只读", close, canUnlock ? "" : "primary")];
    if (canUnlock) {
      actions.push(
        textButton("仍然编辑", () => {
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

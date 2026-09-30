// 白板设置抽屉：背景、存储目录、别的设备的权限、连接 iPad。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 从 ui.js 原样搬过来，只是换了个文件放。

import { icon } from "./icons.js";
import { inShell, shellCommand } from "/inksync/shell.js";
import { iconButton } from "./ui-common.js";
import { el } from "/inksync/util.js";

// 可以放开给别的设备的权限，顺序就是设置面板里的顺序；键与 config.REMOTE_PERMISSIONS 一致。
const PERMISSIONS = [
  { key: "manage", title: "管理白板", note: "切换、新建或删除白板" },
  { key: "settings", title: "设置白板", note: "背景纹理" },
  { key: "clear", title: "清空白板", note: "" },
  { key: "export", title: "导出白板", note: "" }, // 没有必要写note, 而且UI上的文字需要仔细推敲的, 不是让你在上面写random prose的, 比如至少短语结构应该统一
];

export function installSettings(proto) {
  Object.assign(proto, methods);
}

const methods = {
  backgroundOptions() {
    const kinds = [
      ["blank", ""],
      ["grid", `<path d="M0 14h56M0 30h56M14 0v44M28 0v44M42 0v44" stroke="currentColor" stroke-width="1.5"/>`],
      ["lines", `<path d="M0 14h56M0 24h56M0 34h56" stroke="currentColor" stroke-width="1.5"/>`],
      ["dots", (() => {
        let dots = "";
        for (let x = 8; x < 56; x += 12) {
          for (let y = 8; y < 44; y += 12) dots += `<circle cx="${x}" cy="${y}" r="1.6" fill="currentColor"/>`;
        }
        return dots;
      })()],
    ];
    const row = el("div", { class: "bg-opts" });
    for (const [kind, markup] of kinds) {
      row.append(
        el("button", {
          class: `bg-opt${this.meta && this.meta.pattern === kind ? " active" : ""}`,
          html: `<svg viewBox="0 0 56 44">${markup}</svg>`,
          title: kind,
          onclick: () => this.actions.onMeta({ background: kind }),
        })
      );
    }
    return row;
  },

  openSettings() {
    const groups = [];
    // 文档板的底是原件本身，背景纹理没有意义。
    const isDoc = this.meta && this.meta.kind === "doc";
    if (!isDoc && this.may("settings")) {
      groups.push(this.settingsGroup("背景", [this.backgroundOptions()]));
    }
    // 在 iPad 外壳里才有这一段：局域网里有好几台 Mac 时，从这里换一台连。
    if (inShell()) {
      groups.push(this.settingsGroup("Mac", [this.macCard()]));
    }
    // 选目录要开本地文件对话框，只有 pywebview 窗口里才有；别的设备看不到这两段。
    if (this.native) {
      groups.push(this.settingsGroup("存储目录", [this.dataDirCard()]));
      groups.push(
        this.settingsGroup("其他设备权限", [
          el("div", { class: "perm-list" }, PERMISSIONS.map((item) => this.permissionRow(item))),
        ])
      );
      // 存储目录 apps/ 下装了应用时才有这一段
      const apps = (this.info && this.info.apps) || [];
      if (apps.length) groups.push(this.settingsGroup("iPad 首页", [this.ipadHomeCard(apps)]));
    }
    const sheet = this.openSheet(groups);
    sheet.parentElement.dataset.kind = "settings";
  },

  /** 设置面板里的一段：一个小标题加内容。 */
  settingsGroup(title, children) {
    return el("section", { class: "group" }, [
      el("h2", { class: "group-title", text: title }),
      ...children,
    ]);
  },

  /** 外壳里多的那一段：现在连的是哪台 Mac，以及换一台。 */
  macCard() {
    const info = this.info || {};
    const host = info.hostname || "";
    return el("div", { class: "card" }, [
      el("div", { class: "addr", text: host ? `${host}:${info.port || ""}` : "未知" }),
      iconButton("refresh", "换一台 Mac", () => {
        if (shellCommand("rediscover")) this.closeSheet();
      }),
    ]);
  },

  dataDirCard() {
    const path = (this.info && this.info.data_dir) || "";
    return el("div", { class: "card" }, [
      el("div", { class: "addr", text: path }),
      // 合着的文件夹是「换一个」，开着的是「打开看看」，和原来那一行一致
      iconButton("folder", "更换目录", async () => {
        const dir = await this.actions.onChooseDir();
        if (!dir) return;
        if (this.info) this.info.data_dir = dir;
        this.toast("check");
        this.openSettings();
      }),
      iconButton("folderOpen", "打开目录", () => this.actions.onOpenDir()),
    ]);
  },

  /**
   * 一项权限一行：名字、一句话说明、一个开关。真正的拦截在服务端，
   * 这里改的是本地进程里的配置。
   */
  permissionRow({ key, title, note }) {
    const granted = (this.info && this.info.remote_permissions) || {};
    const input = el("input", { type: "checkbox" });
    input.checked = !!granted[key];
    input.addEventListener("change", async () => {
      const next = await this.actions.onRemotePermission(key, input.checked);
      if (!next) return;
      input.checked = !!next[key];
      if (this.info) this.info.remote_permissions = next;
    });
    return el("label", { class: "perm-row" }, [
      el("span", { class: "perm-text" }, [
        el("span", { class: "perm-title", text: title }),
        note ? el("span", { class: "perm-note", text: note }) : null,
      ]),
      input,
    ]);
  },

  /** iPad 打开时进入白板还是某个应用（docs/embed.md）。 */
  ipadHomeCard(apps) {
    const current = (this.info && this.info.ipad_home) || "";
    const select = el("select", { class: "home-select", title: "iPad 首页" }, [
      el("option", { value: "", text: "白板" }),
      ...apps.map((name) => el("option", { value: name, text: name })),
    ]);
    select.value = current;
    select.addEventListener("change", async () => {
      const next = await this.actions.onIpadHome(select.value);
      if (next === null || next === undefined) {
        select.value = current;
        return;
      }
      if (this.info) this.info.ipad_home = next;
      this.toast("check");
    });
    return el("div", { class: "card" }, [select]);
  },

  connectCard() {
    const url = this.info && this.info.urls && this.info.urls.length ? this.info.urls[0] : "";
    return el("div", { class: "card" }, [
      el("div", { html: icon("tablet"), style: { color: "var(--primary)" } }),
      el("div", { class: "addr", text: url }),
      iconButton("download", "下载 iPad 描述文件", () => this.actions.onProfile()),
      iconButton("link", "在浏览器打开", () => this.actions.onOpenUrl(url)),
      // iPad 外壳的安装页：在 iPad 的 Safari 里打开它，装外壳或者把这台 Mac 交给外壳。
      // 描述文件保留，给没有 TrollStore 的 iPad 用
      el("div", { class: "addr shell-addr", text: url ? `外壳安装页 ${url}ipad` : "" }),
    ]);
  },

  openConnect() {
    const sheet = this.openSheet([el("div", { class: "group" }, [this.connectCard()])]);
    sheet.parentElement.dataset.kind = "connect";
  },
};

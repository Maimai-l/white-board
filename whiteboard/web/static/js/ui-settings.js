// 顶栏按钮弹出的几个面板：白板设置、连接 iPad（Mac）、连接状态（iPad）。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 面板贴着按钮往下开，同一时间只开一个；点外面或者按 Esc 关上，再点一次按钮也关上。

import { icon } from "./icons.js";
import { inShell, shellCommand } from "/inksync/shell.js";
import { closeMenu, dropChromeFocus } from "./ui-menu.js";
import { el } from "/inksync/util.js";
import { reportError } from "/inksync/report.js";

// 可以放开给别的设备的权限，顺序就是设置面板里的顺序；键与 config.REMOTE_PERMISSIONS 一致。
// 每一行只有名字，都是「动词 + 白板」，不加说明（docs/ui-text.md）。
const PERMISSIONS = [
  { key: "manage", title: "管理白板" },
  { key: "settings", title: "设置白板" },
  { key: "clear", title: "清空白板" },
  { key: "export", title: "导出白板" },
];

const BACKGROUNDS = [
  { key: "blank", title: "空白" },
  { key: "grid", title: "方格" },
  { key: "lines", title: "横线" },
  { key: "dots", title: "点阵" },
];

export function installSettings(proto) {
  Object.assign(proto, methods);
}

/** 存储位置显示成访达里那样：家目录换成 ~。 */
function shortPath(path) {
  return String(path || "").replace(/^\/Users\/[^/]+/, "~");
}

const methods = {
  // ------------------------------------------------------------ 面板

  /** 点按钮：没开就开，开着的是同一个就关。 */
  togglePanel(kind, anchor) {
    if (this.panel && this.panel.kind === kind) {
      this.closePanel();
      return;
    }
    this.openPanel(kind, anchor);
  },

  openPanel(kind, anchor) {
    this.closePanel();
    closeMenu();
    this.closePopover();
    const body = this.panelContent(kind);
    if (!body) return;
    const panel = el("div", { class: `panel panel-${kind}`, role: "dialog", "aria-label": body.label }, body.children);
    panel.addEventListener("pointerdown", (event) => event.stopPropagation());
    this.root.append(panel);
    const state = { kind, anchor, node: panel };
    this.panel = state;
    this.placePanel(state);
    anchor.classList.add("active");
    state.outside = (event) => {
      if (panel.contains(event.target) || anchor.contains(event.target)) return;
      this.closePanel();
    };
    state.keys = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      this.closePanel();
      dropChromeFocus();
    };
    // 面板是在按钮的 click 里打开的，按下已经过去了：马上开始听（见 ui-menu.js）
    addEventListener("pointerdown", state.outside, true);
    addEventListener("keydown", state.keys, true);
  },

  /** 面板的右边和按钮的右边对齐，放不下就往左挪。 */
  placePanel(state) {
    const box = state.anchor.getBoundingClientRect();
    const width = state.node.offsetWidth;
    const left = Math.max(12, Math.min(box.right - width + 8, innerWidth - width - 12));
    state.node.style.left = `${left}px`;
    // 顶栏比按钮高：面板从顶栏下沿往下开，不压住顶栏
    const bar = this.root.querySelector("#topbar");
    const floor = bar ? bar.getBoundingClientRect().bottom + 6 : 0;
    state.node.style.top = `${Math.max(box.bottom + 8, floor)}px`;
  },

  closePanel() {
    const state = this.panel;
    if (!state) return;
    this.panel = null;
    removeEventListener("pointerdown", state.outside, true);
    removeEventListener("keydown", state.keys, true);
    state.anchor.classList.remove("active");
    state.node.remove();
  },

  /** 内容变了（换了白板、权限、连接的设备）：原地重画，不关面板。 */
  refreshPanel() {
    const state = this.panel;
    if (!state) return;
    const body = this.panelContent(state.kind);
    if (!body) {
      this.closePanel();
      return;
    }
    state.node.replaceChildren(...body.children);
    this.placePanel(state);
  },

  panelContent(kind) {
    if (kind === "settings") return this.settingsContent();
    if (kind === "connect") return this.connectContent();
    if (kind === "status") return this.statusContent();
    return null;
  },

  /** 面板里的一段：小标题加内容；两段之间一道细线。 */
  section(title, children) {
    return el("section", { class: "panel-section" }, [
      title ? el("h2", { class: "group-title", text: title }) : null,
      ...children,
    ]);
  },

  // ------------------------------------------------------------ 白板设置

  settingsContent() {
    const sections = [];
    // 文档板的底是原件本身，背景纹理没有意义。
    const isDoc = this.meta && this.meta.kind === "doc";
    if (!isDoc && this.may("settings")) sections.push(this.section("背景", [this.backgroundOptions()]));
    // 在 iPad 外壳里才有这一段：局域网里有好几台 Mac 时，从这里换一台连。
    if (inShell()) sections.push(this.section("Mac", [this.macCard()]));
    // 选目录要开本地文件对话框，只有 pywebview 窗口里才有；别的设备看不到这几段。
    if (this.native) {
      sections.push(
        this.section("iPad 可以", [el("div", { class: "perm-list" }, PERMISSIONS.map((item) => this.permissionRow(item)))])
      );
      sections.push(this.section("存储位置", [this.dataDirCard()]));
      // 存储目录 apps/ 下装了应用时才有这一段
      const apps = (this.info && this.info.apps) || [];
      if (apps.length) sections.push(this.section("iPad 首页", [this.ipadHomeCard(apps)]));
    }
    if (!sections.length) return null;
    const children = [];
    sections.forEach((node, index) => {
      if (index) children.push(el("div", { class: "panel-rule" }));
      children.push(node);
    });
    return { label: "白板设置", children };
  },

  backgroundOptions() {
    const row = el("div", { class: "bg-opts", role: "radiogroup", "aria-label": "背景" });
    const current = (this.meta && this.meta.pattern) || "grid";
    for (const { key, title } of BACKGROUNDS) {
      row.append(
        el(
          "button",
          {
            class: `bg-opt${current === key ? " active" : ""}`,
            type: "button",
            role: "radio",
            "aria-checked": String(current === key),
            title,
            onclick: () => this.actions.onMeta({ background: key }),
          },
          [el("span", { class: `bg-swatch bg-${key}` }), el("span", { class: "bg-label", text: title })]
        )
      );
    }
    return row;
  },

  /** 外壳里多的那一段：现在连的是哪台 Mac，以及换一台。 */
  macCard() {
    const info = this.info || {};
    const host = info.hostname || "";
    return el("div", { class: "panel-row" }, [
      el("span", { class: "panel-row-text", text: host ? `${host}:${info.port || ""}` : "未知" }),
      el("button", {
        class: "lib-btn tinted small",
        type: "button",
        text: "换一台 Mac",
        onclick: () => {
          if (shellCommand("rediscover")) this.closePanel();
        },
      }),
    ]);
  },

  dataDirCard() {
    const path = (this.info && this.info.data_dir) || "";
    return el("div", { class: "panel-stack" }, [
      el("div", { class: "panel-row" }, [
        el("span", { class: "panel-row-icon", html: icon("folder", 16) }),
        el("span", { class: "panel-row-text addr", text: shortPath(path), title: path }),
        el("button", {
          class: "lib-btn tinted small",
          type: "button",
          text: "更改…",
          onclick: async () => {
            const dir = await this.actions.onChooseDir();
            if (!dir) return;
            if (this.info) this.info.data_dir = dir;
            this.toast("check");
            this.refreshPanel();
          },
        }),
      ]),
      el("button", { class: "link", type: "button", text: "在访达中显示", onclick: () => this.actions.onOpenDir() }),
    ]);
  },

  /**
   * 一项权限一行：名字和一个开关。真正的拦截在服务端，
   * 这里改的是本地进程里的配置。
   */
  permissionRow({ key, title }) {
    const granted = (this.info && this.info.remote_permissions) || {};
    const input = el("input", { type: "checkbox", role: "switch" });
    input.checked = !!granted[key];
    input.addEventListener("change", async () => {
      const next = await this.actions.onRemotePermission(key, input.checked);
      if (!next) return;
      input.checked = !!next[key];
      if (this.info) this.info.remote_permissions = next;
    });
    return el("label", { class: "perm-row" }, [
      el("span", { class: "perm-text" }, [el("span", { class: "perm-title", text: title })]),
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
    return select;
  },

  // ------------------------------------------------------------ 连接 iPad

  /**
   * Mac 上的连接面板：二维码、地址、连着的设备。下面那个链接把二维码换成外壳的
   * 安装页，用 iPad 相机一扫就能装。
   */
  connectContent() {
    const urls = (this.info && this.info.urls) || [];
    const base = urls[0] || "";
    const install = !!this.connectInstall;
    const url = install && base ? `${base}ipad` : base;
    const qr = url
      ? el("img", { class: "connect-qr", src: `/api/qr?text=${encodeURIComponent(url)}`, alt: "连接二维码", width: "156", height: "156" })
      : el("div", { class: "connect-qr empty" });
    const shown = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
    const copy = el("button", {
      class: "lib-btn tinted small",
      type: "button",
      text: "拷贝",
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(url);
          copy.textContent = "已拷贝";
        } catch (err) {
          reportError("ui-settings.connectContent 拷贝地址失败", { message: String(err) });
          this.message("没能拷贝", "close", 3000);
        }
      },
    });
    const devices = this.devices.length
      ? this.devices.map((device) =>
          el("div", { class: "device-row" }, [
            el("span", { class: "device-icon", html: icon("connect", 18) }),
            el("span", { text: device.label }),
          ])
        )
      : [el("p", { class: "panel-note", text: "没有连接的设备" })];
    return {
      label: "连接 iPad",
      children: [
        el("h2", { class: "panel-title", text: "连接 iPad" }),
        el("p", { class: "panel-note center", text: install ? "用 iPad 相机扫码安装白板" : "用 iPad 相机扫码连接" }),
        qr,
        el("div", { class: "panel-row boxed" }, [el("span", { class: "panel-row-text addr", text: shown }), copy]),
        el("div", { class: "panel-rule" }),
        el("div", { class: "panel-stack" }, [el("h3", { class: "group-title", text: "已连接" }), ...devices]),
        el("div", { class: "panel-links" }, [
          el("button", {
            class: "link",
            type: "button",
            text: install ? "扫码连接白板" : "在 iPad 上安装白板",
            onclick: () => {
              this.connectInstall = !install;
              this.refreshPanel();
            },
          }),
          el("button", { class: "link", type: "button", text: "存储描述文件", onclick: () => this.actions.onProfile() }),
        ]),
      ],
    };
  },

  /** iPad 上的连接状态：连的是哪台 Mac；外壳里可以换一台。 */
  statusContent() {
    const info = this.info || {};
    const online = this.netStatus === "online";
    const host = info.hostname ? `${info.hostname}:${info.port || ""}` : "Mac";
    const children = [
      el("div", { class: "panel-row" }, [
        el("span", { class: `status-dot ${online ? "on" : ""}` }),
        el("span", { class: "panel-row-text", text: online ? `已连接到 ${host}` : "未连接" }),
      ]),
    ];
    if (inShell()) {
      children.push(
        el("button", {
          class: "lib-btn tinted",
          type: "button",
          text: "换一台 Mac",
          onclick: () => {
            if (shellCommand("rediscover")) this.closePanel();
          },
        })
      );
    }
    return { label: "连接状态", children };
  },

  /** 兼容老入口：设置、连接都改成从顶栏按钮弹出的面板。 */
  openSettings() {
    const anchor = this.root.querySelector('#topright button[title="白板设置"]');
    if (anchor) this.openPanel("settings", anchor);
  },

  openConnect() {
    if (this.status) this.openPanel(this.touchDevice ? "status" : "connect", this.status);
  },
};

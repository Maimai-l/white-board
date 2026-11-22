// 白板库：左边一栏（最近、全部白板、文件夹、应用、回收站），右边是白板的格子或列表。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
//
// Mac 上：单击选中（⌘ 单击多选），双击打开，右键出菜单，空白处右键是新建 / 导入 /
// 显示方式。iPad 上：点一下打开，长按拎起来，不挪就出菜单，挪了就是拖进文件夹
// （dragsort.js 的 onStill）。卡片都能拖到边栏的文件夹或回收站上。
//
// 列表随时会被广播刷新（别处改了名、新建、删除），所以界面状态（看的是哪一栏、
// 选中了哪些、正在给谁改名）都放在 UI 上，重建时照着画回来。

import { icon } from "./icons.js";
import { startCardDrag } from "./dragsort.js";
import { closeMenu, menuOpen, showMenu } from "./ui-menu.js";
import { leave } from "./motion.js";
import { boardLabel, iconButton } from "./ui-common.js";
import { el, isComposing } from "/inksync/util.js";
import { reportError } from "/inksync/report.js";

const APP_PAGE = 60;
const DAY = 24 * 3600 * 1000;
// 拖到回收站那一行：dragsort 只认文件夹名，回收站用一个不可能是文件夹名的值
const TRASH_TARGET = "\u0000trash";
const VIEW_KEY = "whiteboard.library.view";
const SORT_KEY = "whiteboard.library.sort";
// 手动排列就是服务端记的顺序，只有这时候拖卡片能调顺序（和访达的排序方式「无」一样）
const SORTS = [
  { key: "updated", label: "修改时间" },
  { key: "name", label: "名称" },
  { key: "manual", label: "手动排列" },
];

function remembered(key, allowed, fallback) {
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value) ? value : fallback;
  } catch (err) {
    console.warn("ui-boards.js remembered：读不到 localStorage，用默认值", key, err);
    return fallback;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    console.warn("ui-boards.js remember：记不住，只在本次打开期间生效", key, err);
  }
}

function startOfDay(ms) {
  const day = new Date(ms);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

/** 卡片下面那行时间：今天只给时刻，昨天加上昨天，今年给月日，更早给年月日。 */
export function boardDate(seconds, now = Date.now()) {
  const ms = seconds * 1000;
  const when = new Date(ms);
  const clock = when.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const today = startOfDay(now);
  if (ms >= today) return clock;
  if (ms >= today - DAY) return `昨天 ${clock}`;
  if (when.getFullYear() === new Date(now).getFullYear()) return `${when.getMonth() + 1}月${when.getDate()}日`;
  return `${when.getFullYear()}年${when.getMonth() + 1}月${when.getDate()}日`;
}

/** 最近那一栏的分组：今天、本周（前 7 天）、更早。 */
function recentGroup(seconds, now = Date.now()) {
  const today = startOfDay(now);
  const ms = seconds * 1000;
  if (ms >= today) return "today";
  if (ms >= today - 6 * DAY) return "week";
  return "earlier";
}

/** 搜索匹配的范围：显示出来的名字、文件夹名，加上文档板的原件文件名。 */
function boardHaystack(board) {
  const doc = board.kind === "doc" && board.doc ? board.doc.name || "" : "";
  return `${boardLabel(board)} ${board.name || ""} ${board.folder || ""} ${doc}`.toLowerCase();
}

/** 卡片上那个小图标：向下延伸、四向延伸、文档。 */
function kindIcon(board) {
  if (board.kind === "doc") return "doc";
  return board.kind === "note" ? "note" : "board";
}

const KIND_TITLES = { note: "向下延伸", board: "四向延伸", doc: "文档" };

export function installBoards(proto) {
  Object.assign(proto, methods);
}

const methods = {
  setBoards(boards, currentId, folders) {
    // 这里是列表变了（别处改了名、新建、归类），不是重新打开界面
    this.boards = boards;
    if (folders) this.folders = folders;
    this.currentBoardId = currentId;
    const nav = this.libNav || "recent";
    // 正在看的文件夹被别处删掉了，就退回最近，否则会停在一个空壳里
    if (nav.startsWith("folder:") && !this.folders.includes(nav.slice(7))) this.libNav = "recent";
    if (this.selected) {
      const alive = new Set(boards.map((board) => board.id));
      for (const id of [...this.selected]) if (!alive.has(id)) this.selected.delete(id);
    }
    // 新建文件夹并把白板移进去之后，等名单回来再让名字进入编辑
    if (this.pendingFolderEdit && this.folders.includes(this.pendingFolderEdit)) {
      this.folderEdit = { name: this.pendingFolderEdit };
      this.pendingFolderEdit = null;
    }
    if (this.gallery) this.renderBoards({ opening: false });
  },

  closeGallery() {
    // 正在起名的新文件夹，关界面就按当前输入的名字建好（和点别处一样）
    if (!this.rerendering && this.folderEdit && this.folderEdit.commit) this.folderEdit.commit();
    closeMenu();
    this.closeNewBoard();
    if (this.gallery) {
      leave(this.gallery);
      this.gallery = null;
    }
    if (this._libraryKeys) {
      removeEventListener("keydown", this._libraryKeys, true);
      this._libraryKeys = null;
    }
  },

  /**
   * 打开白板库。先把当前白板的缩略图刷新一遍，免得看到的是旧图。``nav`` 给了就直接停在
   * 那一栏（白板设置面板的所有设置…停在设置）。
   */
  async openBoards(nav = null) {
    this.boardQuery = "";
    this.libView = remembered(VIEW_KEY, ["grid", "list"], "grid");
    this.libSort = remembered(SORT_KEY, SORTS.map((sort) => sort.key), "updated");
    // 打开时停在当前白板所在的文件夹，并且选中它；没归类就停在最近
    const current = this.boards.find((board) => board.id === this.currentBoardId);
    this.libNav = nav || (current && current.folder ? `folder:${current.folder}` : "recent");
    this.selected = new Set(current && !nav ? [current.id] : []);
    this.renaming = null;
    this.folderEdit = null;
    if (this.sidebarHidden === undefined) this.sidebarHidden = this.touchDevice && innerWidth < 900;
    if (this.actions.onBoardsOpen) await this.actions.onBoardsOpen();
    await this.loadApps();
    this.renderBoards();
  },

  /** 已安装的应用：每个应用的白板在它自己的空间里，边栏里各占一行。 */
  async loadApps() {
    try {
      const response = await fetch("/api/apps");
      this.apps = response.ok ? (await response.json()).apps || [] : [];
    } catch (err) {
      reportError("ui.loadApps 取不到应用列表", { message: String(err) });
      this.apps = [];
    }
  },

  // ------------------------------------------------------------ 数据

  liveBoards() {
    return this.boards.filter((board) => !board.trashed);
  },

  trashedBoards() {
    return this.boards.filter((board) => board.trashed);
  },

  /** 现有的文件夹名，按名字排序。空文件夹也在里面，所以名单是服务端给的。 */
  folderNames() {
    return [...this.folders].sort((a, b) => a.localeCompare(b, "zh"));
  },

  /** 还没用过的文件夹名，用来给新建的文件夹起个默认名。 */
  freeFolderName() {
    const base = "未命名文件夹";
    if (!this.folders.includes(base)) return base;
    for (let n = 2; ; n += 1) {
      const name = `${base} ${n}`;
      if (!this.folders.includes(name)) return name;
    }
  },

  /** 正在看的文件夹（不在文件夹里是空串）：新建、导入的白板落在这里。 */
  viewFolder() {
    const nav = this.libNav || "recent";
    return nav.startsWith("folder:") ? nav.slice(7) : "";
  },

  sorted(boards) {
    const list = [...boards];
    if (this.libSort === "manual") return list;
    if (this.libSort === "name") {
      list.sort((a, b) => boardLabel(a).localeCompare(boardLabel(b), "zh"));
    } else {
      list.sort((a, b) => (b.updated || 0) - (a.updated || 0));
    }
    return list;
  },

  /** 主区域要显示的内容：标题和若干段（段名可以为空）。 */
  libraryContent() {
    const nav = this.libNav || "recent";
    const query = (this.boardQuery || "").trim().toLowerCase();
    if (query) {
      const found = this.liveBoards().filter((board) => boardHaystack(board).includes(query));
      return { title: "搜索结果", sections: [{ label: "", boards: this.sorted(found) }], empty: "没有匹配的白板" };
    }
    if (nav === "trash") {
      const boards = [...this.trashedBoards()].sort((a, b) => (b.trashed || 0) - (a.trashed || 0));
      return { title: "回收站", sections: [{ label: "", boards }], empty: "回收站是空的", trash: true };
    }
    if (nav.startsWith("folder:")) {
      const name = nav.slice(7);
      const boards = this.liveBoards().filter((board) => board.folder === name);
      return { title: name, sections: [{ label: "", boards: this.sorted(boards) }], empty: "这个文件夹是空的", create: true };
    }
    if (nav === "all") {
      return { title: "全部白板", sections: [{ label: "", boards: this.sorted(this.liveBoards()) }], empty: "还没有白板", create: true };
    }
    const groups = { today: [], week: [], earlier: [] };
    for (const board of this.liveBoards()) groups[recentGroup(board.updated || 0)].push(board);
    const byTime = (list) => [...list].sort((a, b) => (b.updated || 0) - (a.updated || 0));
    return {
      title: "最近",
      sections: [
        { label: "今天", boards: byTime(groups.today) },
        { label: "本周", boards: byTime(groups.week) },
        { label: "更早", boards: byTime(groups.earlier) },
      ].filter((section) => section.boards.length),
      empty: "还没有白板",
      create: true,
    };
  },

  // ------------------------------------------------------------ 整体

  /** 画出整个白板库。广播刷新时也走这里，所以先记住焦点，建完再放回去。 */
  renderBoards({ opening = true } = {}) {
    const active = document.activeElement;
    const inside = active && this.gallery && this.gallery.contains(active);
    const focusKey = inside ? active.dataset.focusKey : null;
    const caret = focusKey && active.setSelectionRange ? [active.selectionStart, active.selectionEnd] : null;
    const scroll = this.libScroll ? this.libScroll.scrollTop : 0;

    // 重建会把正在编辑的名字框从页面上拿掉；这不是「改完了」，别让 blur 提交
    this.rerendering = true;
    const wasOpen = !!this.gallery;
    if (this.gallery) this.gallery.remove();
    this.gallery = null;
    this.closeSheet();
    this.rerendering = false;

    const gallery = el("div", {
      class: `gallery library${opening && !wasOpen ? " opening" : ""}`,
      "data-touch": this.touchDevice ? "1" : "",
      "data-side": this.sidebarHidden ? "hidden" : "",
    });
    this.gallery = gallery;
    this.libSide = this.renderSidebar();
    this.libMain = el("main", { class: "lib-main" });
    gallery.append(this.libSide, this.libMain);
    this.root.append(gallery);
    this.renderMain();
    if (!opening || wasOpen) this.libScroll.scrollTop = scroll;
    this.bindLibraryKeys();

    if (!focusKey) return;
    const back = gallery.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
    if (!back) return;
    back.focus();
    if (caret && back.setSelectionRange) back.setSelectionRange(caret[0], caret[1]);
  },

  /** 只重画右边。搜索时不碰边栏，输入框和输入法状态才不会被打断。 */
  fillBoardGrid() {
    if (!this.gallery) return;
    this.renderMain({ keepHead: true });
  },

  setNav(nav) {
    this.libNav = nav;
    this.boardQuery = "";
    this.selected = new Set();
    this.renaming = null;
    closeMenu();
    if (this.touchDevice && innerWidth < 900) this.sidebarHidden = true;
    this.renderBoards({ opening: false });
  },

  toggleSidebar() {
    this.sidebarHidden = !this.sidebarHidden;
    if (this.gallery) this.gallery.dataset.side = this.sidebarHidden ? "hidden" : "";
  },

  // ------------------------------------------------------------ 边栏

  renderSidebar() {
    const nav = this.libNav || "recent";
    const live = this.liveBoards();
    const row = ({ key, label, iconName, count, folder, onclick }) => {
      const current = nav === key;
      const button = el(
        "button",
        {
          class: `lib-nav${current ? " current" : ""}`,
          type: "button",
          "aria-current": current ? "page" : "false",
          onclick,
        },
        [
          el("span", { class: "lib-nav-icon", html: icon(iconName, 18) }),
          el("span", { class: "lib-nav-label", text: label }),
          el("span", { class: "lib-nav-count", text: count === undefined ? "" : String(count) }),
        ]
      );
      if (folder !== undefined) {
        button.dataset.folder = folder;
        button.dataset.dropRow = "";
      }
      return button;
    };

    const top = el("div", { class: "lib-side-top pywebview-drag-region" });
    if (this.touchDevice) {
      top.append(iconButton("sidebar", "隐藏边栏", () => this.toggleSidebar()));
      top.append(el("h1", { class: "lib-side-title", text: "白板" }));
    }

    const list = el("div", { class: "lib-nav-list" }, [
      row({ key: "recent", label: "最近", iconName: "clock", onclick: () => this.setNav("recent") }),
      row({ key: "all", label: "全部白板", iconName: "boards", count: live.length, folder: "", onclick: () => this.setNav("all") }),
    ]);

    const folderHead = el("div", { class: "lib-side-head" }, [
      el("h2", { text: "文件夹" }),
      iconButton("add", "新建文件夹", () => this.startNewFolder(), "lib-side-add"),
    ]);
    list.append(folderHead);
    for (const name of this.folderNames()) {
      if (this.folderEdit && this.folderEdit.name === name && !this.folderEdit.draft) {
        list.append(this.folderEditRow(name));
        continue;
      }
      const count = live.filter((board) => board.folder === name).length;
      const button = row({
        key: `folder:${name}`,
        label: name,
        iconName: "folder",
        count,
        folder: name,
        onclick: () => this.setNav(`folder:${name}`),
      });
      button.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        this.folderMenu(name, { x: event.clientX, y: event.clientY });
      });
      button.addEventListener("dblclick", () => this.editFolder(name));
      this.longPress(button, (at) => this.folderMenu(name, at));
      list.append(button);
    }
    if (this.folderEdit && this.folderEdit.draft) list.append(this.folderEditRow(null));

    if (this.apps && this.apps.length && this.may("manage")) {
      list.append(el("div", { class: "lib-side-head" }, [el("h2", { text: "应用" })]));
      for (const name of this.apps) {
        list.append(row({ key: `app:${name}`, label: name, iconName: "tablet", onclick: () => this.setNav(`app:${name}`) }));
      }
    }

    list.append(el("div", { class: "lib-side-spacer" }));
    const trash = row({
      key: "trash",
      label: "回收站",
      iconName: "trash",
      count: this.trashedBoards().length || undefined,
      onclick: () => this.setNav("trash"),
    });
    trash.dataset.folder = TRASH_TARGET;
    trash.dataset.dropRow = "";
    list.append(trash);
    // 整个应用的设置：连接其他设备、权限、存储位置（白板设置面板里是这块白板的快捷入口）
    list.append(row({ key: "settings", label: "设置", iconName: "settings", onclick: () => this.setNav("settings") }));

    return el("nav", { class: "lib-side", "aria-label": "白板库" }, [top, list]);
  },

  /** 边栏里正在起名或改名的那一行。``name`` 为空表示新建。 */
  folderEditRow(name) {
    const edit = this.folderEdit;
    const fallback = name || edit.value || this.freeFolderName();
    if (edit.value === undefined) edit.value = fallback;
    const input = el("input", {
      class: "lib-nav-edit",
      type: "text",
      value: edit.value,
      "aria-label": "文件夹名称",
      spellcheck: "false",
      maxlength: "64",
      "data-focus-key": "folder-edit",
      oninput: () => {
        edit.value = input.value;
      },
      onkeydown: (event) => {
        if (isComposing(event)) return; // 输入法选词的回车不是「改完了」
        if (event.key === "Enter") {
          event.preventDefault();
          if (commit({ keepEditing: true })) input.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          if (name) {
            this.folderEdit = null;
            this.renderBoards({ opening: false });
          } else {
            input.value = fallback;
            commit();
          }
        }
      },
      onblur: () => commit(),
    });
    const commit = ({ keepEditing = false } = {}) => {
      if (this.rerendering || this.folderEdit !== edit) return true;
      const next = input.value.trim() || fallback;
      if (next !== name && this.folders.includes(next)) {
        this.message("已经有同名的文件夹了", "close", 4000);
        if (keepEditing) {
          input.select();
          return false;
        }
        this.folderEdit = null;
        this.renderBoards({ opening: false });
        return true;
      }
      this.folderEdit = null;
      input.readOnly = true; // 等服务端广播新名单后，这一行会换成正式的文件夹
      if (!name) this.actions.onNewFolder(next);
      else if (next !== name) {
        if (this.libNav === `folder:${name}`) this.libNav = `folder:${next}`;
        this.actions.onRenameFolder(name, next);
      } else this.renderBoards({ opening: false });
      return true;
    };
    edit.commit = commit;
    setTimeout(() => {
      if (!input.isConnected || document.activeElement === input) return;
      input.focus();
      input.select();
    }, 0);
    return el("div", { class: "lib-nav editing" }, [
      el("span", { class: "lib-nav-icon", html: icon("folder", 18) }),
      input,
    ]);
  },

  /** 新建文件夹：边栏最后多一行，名字直接进入编辑、全选默认名，和访达一样。 */
  startNewFolder() {
    closeMenu();
    this.folderEdit = { draft: true, value: this.freeFolderName() };
    this.renderBoards({ opening: false });
    // iPad 要在点按的那一刻同步聚焦，才会弹出键盘
    const input = this.gallery && this.gallery.querySelector('[data-focus-key="folder-edit"]');
    if (input) {
      input.focus();
      input.select();
    }
  },

  editFolder(name) {
    closeMenu();
    this.folderEdit = { name };
    this.renderBoards({ opening: false });
    const input = this.gallery && this.gallery.querySelector('[data-focus-key="folder-edit"]');
    if (input) {
      input.focus();
      input.select();
    }
  },

  folderMenu(name, at) {
    showMenu(
      [
        { label: "重命名", onSelect: () => this.editFolder(name) },
        { label: "在此新建白板", onSelect: () => this.openNewBoard(null, name) },
        "-",
        {
          label: "删除文件夹",
          onSelect: () =>
            this.confirm("trash", "删除这个文件夹", () => this.actions.onDeleteFolder(name), {
              verb: "删除",
              note: "里面的白板会留在全部白板中",
            }),
        },
      ],
      at,
      { root: this.root, label: name }
    );
  },

  // ------------------------------------------------------------ 主区域

  renderMain({ keepHead = false } = {}) {
    const main = this.libMain;
    if (!main) return;
    const nav = this.libNav || "recent";
    if (nav.startsWith("app:")) {
      this.renderAppMain(nav.slice(4));
      return;
    }
    if (nav === "settings") {
      this.renderSettingsMain();
      return;
    }
    const content = this.libraryContent();
    if (!keepHead || !this.libHead) {
      main.textContent = "";
      this.libHead = this.renderHead(content);
      this.libScroll = el("div", { class: "lib-scroll" });
      this.libScroll.addEventListener("click", (event) => {
        if (event.target.closest(".board-item, .lib-row")) return;
        this.clearSelection();
      });
      this.libScroll.addEventListener("contextmenu", (event) => {
        if (event.target.closest(".board-item, .lib-row")) return;
        event.preventDefault();
        this.clearSelection();
        this.backgroundMenu({ x: event.clientX, y: event.clientY });
      });
      main.append(this.libHead, this.libScroll);
    } else {
      this.updateHead(content);
    }
    this.fillContent(content);
  },

  renderHead(content) {
    const search = el("input", {
      class: "board-search",
      type: "search",
      placeholder: "搜索",
      "aria-label": "搜索白板",
      value: this.boardQuery || "",
      spellcheck: "false",
      "data-focus-key": "search",
      oninput: () => {
        this.boardQuery = search.value;
        this.fillBoardGrid();
      },
      onkeydown: (event) => {
        if (isComposing(event)) return; // 输入法组字时的 Esc 是取消组字
        if (event.key !== "Escape" || !search.value) return;
        event.stopPropagation(); // 别让 Esc 顺手把整个界面关掉
        search.value = this.boardQuery = "";
        this.fillBoardGrid();
      },
    });
    this.libTitle = el("h1", { class: "lib-title", text: content.title });
    this.libNote = el("span", { class: "lib-note" });
    const head = el("header", { class: "lib-head pywebview-drag-region" });
    if (this.touchDevice) {
      head.append(iconButton("sidebar", "显示边栏", () => this.toggleSidebar(), "lib-show-side"));
    }
    head.append(el("div", { class: "lib-heading" }, [this.libTitle, this.libNote]));

    const viewSeg = el("div", { class: "lib-seg", role: "group", "aria-label": "显示方式" });
    for (const [key, iconName, title] of [
      ["grid", "boards", "图标"],
      ["list", "list", "列表"],
    ]) {
      viewSeg.append(
        el("button", {
          type: "button",
          title,
          "aria-label": title,
          "aria-pressed": String(this.libView === key),
          html: icon(iconName, 16),
          onclick: () => this.setLibView(key),
        })
      );
    }
    const sortLabel = (SORTS.find((sort) => sort.key === this.libSort) || SORTS[0]).label;
    const sort = el("button", {
      class: "lib-sort",
      type: "button",
      "aria-label": `排序方式 ${sortLabel}`,
      "aria-haspopup": "menu",
      onclick: (event) => this.sortMenu(event.currentTarget),
    }, [el("span", { text: sortLabel }), el("span", { class: "lib-sort-chevron", html: icon("chevronDown", 12) })]);

    const box = el("label", { class: "search-box" }, [
      el("span", { class: "search-icon", html: icon("search", 15) }),
      search,
    ]);
    this.libSortButton = sort;
    this.libViewSeg = viewSeg;
    head.append(viewSeg, sort, box);
    this.libAction = el("div", { class: "lib-action" });
    head.append(this.libAction);
    this.updateHead(content);
    return head;
  },

  /** 搜索时标题和右上角的按钮跟着变，但不重建输入框。 */
  updateHead(content) {
    if (this.libTitle) this.libTitle.textContent = content.title;
    this.updateSelectionNote();
    const trash = !!content.trash;
    if (this.libViewSeg) this.libViewSeg.hidden = false;
    if (this.libSortButton) this.libSortButton.hidden = trash || (this.libNav || "recent") === "recent";
    const action = this.libAction;
    if (!action) return;
    action.textContent = "";
    if (trash) {
      const empty = el("button", {
        class: "lib-btn",
        type: "button",
        text: "清空回收站",
        onclick: () =>
          this.confirm("trash", "清空回收站", () => this.actions.onEmptyTrash(), {
            verb: "清空",
            note: "回收站里的白板会永久删除",
          }),
      });
      empty.disabled = !this.trashedBoards().length;
      action.append(empty);
      return;
    }
    action.append(
      el("button", {
        class: "lib-btn primary lib-new",
        type: "button",
        "aria-haspopup": "dialog",
        html: icon("add", 15),
        onclick: (event) => {
          event.stopPropagation();
          this.openNewBoard(event.currentTarget, this.viewFolder());
        },
      }, [el("span", { text: "新建白板" })])
    );
  },

  updateSelectionNote() {
    if (!this.libNote) return;
    const count = this.selected ? this.selected.size : 0;
    this.libNote.textContent = count > 1 ? `已选择 ${count} 项` : "";
  },

  setLibView(view) {
    this.libView = view;
    remember(VIEW_KEY, view);
    for (const button of this.libViewSeg ? this.libViewSeg.children : []) {
      button.setAttribute("aria-pressed", String(button.title === (view === "grid" ? "图标" : "列表")));
    }
    this.fillBoardGrid();
  },

  sortMenu(anchor) {
    showMenu(
      SORTS.map((sort) => ({
        label: sort.label,
        checked: this.libSort === sort.key,
        onSelect: () => {
          this.libSort = sort.key;
          remember(SORT_KEY, sort.key);
          this.renderBoards({ opening: false });
        },
      })),
      anchor,
      { root: this.root, label: "排序方式" }
    );
  },

  fillContent(content) {
    const scroll = this.libScroll;
    scroll.textContent = "";
    const boards = content.sections.flatMap((section) => section.boards);
    if (content.trash) {
      scroll.append(el("p", { class: "lib-hint", text: "白板会在 30 天后永久删除" }));
    }
    if (!boards.length) {
      const empty = el("div", { class: "gallery-empty" }, [el("p", { text: content.empty })]);
      if (content.create && !(this.boardQuery || "").trim()) {
        empty.append(
          el("button", {
            class: "lib-btn primary",
            type: "button",
            text: "新建白板",
            onclick: (event) => {
              event.stopPropagation();
              this.openNewBoard(event.currentTarget, this.viewFolder());
            },
          })
        );
      }
      scroll.append(empty);
      return;
    }
    if (this.libView === "list") {
      scroll.append(this.boardTable(content));
      return;
    }
    for (const section of content.sections) {
      if (!section.boards.length) continue;
      const block = el("section", { class: "lib-section" });
      if (section.label) block.append(el("h2", { class: "lib-section-title", text: section.label }));
      const grid = el("div", { class: "gallery-grid" });
      for (const board of section.boards) grid.append(this.boardItem(board, grid, content));
      block.append(grid);
      scroll.append(block);
    }
  },

  // ------------------------------------------------------------ 一块白板

  boardItem(board, grid, content) {
    const selected = this.selected && this.selected.has(board.id);
    const card = el("div", {
      class: `board-card${selected ? " selected" : ""}${board.id === this.currentBoardId ? " active" : ""}`,
      style: { backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated || 0)})` },
    });
    const item = el("div", { class: "board-item", "data-board": board.id }, [
      card,
      el("div", { class: "board-meta" }, [this.boardNameNode(board), this.boardSub(board, content)]),
    ]);
    this.bindBoardPointer(item, card, board, grid, content);
    return item;
  },

  boardSub(board, content) {
    const kind = kindIcon(board);
    const when = content && content.trash ? board.trashed : board.updated;
    return el("div", { class: "board-sub" }, [
      el("span", {
        class: "board-kind",
        title: KIND_TITLES[kind],
        "aria-label": KIND_TITLES[kind],
        html: icon(kind, 14),
      }),
      el("span", {
        class: "board-date",
        text: boardDate(when || 0),
        title: new Date((when || 0) * 1000).toLocaleString("zh-CN"),
      }),
    ]);
  },

  /** 名字：平时是文字，改名时换成输入框。 */
  boardNameNode(board) {
    if (this.renaming !== board.id) {
      return el("div", { class: "board-name", text: boardLabel(board), title: boardLabel(board) });
    }
    const input = el("input", {
      class: "board-name editing",
      type: "text",
      value: board.name || "",
      placeholder: boardLabel(board),
      "aria-label": "白板名称",
      spellcheck: "false",
      maxlength: "64",
      "data-focus-key": `name:${board.id}`,
      onclick: (event) => event.stopPropagation(),
      ondblclick: (event) => event.stopPropagation(),
      onkeydown: (event) => {
        if (isComposing(event)) return; // 输入法选词的回车不是「改完了」
        if (event.key === "Enter") {
          event.preventDefault();
          input.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          input.value = board.name || "";
          input.blur();
        }
      },
      onblur: () => {
        if (this.rerendering || this.renaming !== board.id) return;
        this.renaming = null;
        const next = input.value.trim();
        if (next !== (board.name || "")) this.actions.onRenameBoard(board.id, next);
        this.fillBoardGrid();
      },
    });
    setTimeout(() => {
      if (!input.isConnected || document.activeElement === input) return;
      input.focus();
      input.select();
    }, 0);
    return input;
  },

  startRename(board) {
    closeMenu();
    this.renaming = board.id;
    this.fillBoardGrid();
    const input = this.gallery && this.gallery.querySelector(`[data-focus-key="name:${CSS.escape(board.id)}"]`);
    if (input) {
      input.focus();
      input.select();
    }
  },

  /**
   * 卡片（或列表里的一行）上的手势：点、双击、右键、拖。触摸设备上点一下就打开，
   * 长按由 dragsort 判断：拎起来不挪就是菜单。
   */
  bindBoardPointer(item, handle, board, grid, content) {
    const trash = !!(content && content.trash);
    // 只有手动排列、没在搜索时，拖卡片才是调顺序；别的时候只能拖进文件夹或回收站
    const reorder =
      this.libSort === "manual" && !trash && (this.libNav || "recent") !== "recent" && !(this.boardQuery || "").trim();
    item.addEventListener("click", (event) => {
      if (event.target.closest("input")) return;
      if (this.touchDevice && !trash) {
        this.openBoard(board.id);
        return;
      }
      this.pick(board.id, event.metaKey || event.ctrlKey);
    });
    item.addEventListener("dblclick", (event) => {
      if (event.target.closest("input") || trash) return;
      this.openBoard(board.id);
    });
    item.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      if (!this.selected.has(board.id)) this.pick(board.id, false);
      this.boardMenu(board, { x: event.clientX, y: event.clientY }, trash);
    });
    handle.addEventListener("pointerdown", (event) => {
      if (event.target.closest("input")) return;
      startCardDrag(event, {
        card: handle,
        item,
        id: board.id,
        grid,
        gallery: this.libScroll,
        root: this.root,
        reorder,
        folderOf: (id) => {
          const one = this.boards.find((entry) => entry.id === id);
          if (one && one.trashed) return TRASH_TARGET;
          return (one && one.folder) || "";
        },
        onFile: (id, folder) => this.dropBoards(id, folder),
        onReorder: (ids) => this.actions.onReorderBoards(ids),
        onRefresh: () => this.fillBoardGrid(),
        onStill: () => {
          const box = handle.getBoundingClientRect();
          this.boardMenu(board, { x: box.left + 12, y: box.bottom + 8 }, trash);
        },
      });
    });
  },

  /** 选中一块。``add`` 为真时是 ⌘ 单击：加进来或者去掉。 */
  pick(id, add) {
    if (!this.selected) this.selected = new Set();
    if (add) {
      if (this.selected.has(id)) this.selected.delete(id);
      else this.selected.add(id);
    } else {
      this.selected = new Set([id]);
    }
    this.syncSelection();
  },

  clearSelection() {
    if (!this.selected || !this.selected.size) return;
    this.selected = new Set();
    this.syncSelection();
  },

  /** 只改选中的样子，不重建（双击的第二下要落在同一个元素上）。 */
  syncSelection() {
    if (!this.gallery) return;
    for (const node of this.gallery.querySelectorAll("[data-board]")) {
      const on = this.selected.has(node.dataset.board);
      node.classList.toggle("selected", on);
      const card = node.querySelector(".board-card");
      if (card) card.classList.toggle("selected", on);
    }
    this.updateSelectionNote();
  },

  /** 菜单和拖动作用在哪几块：点的这块在选中里就是整个选中，否则只有它。 */
  targetsOf(board) {
    if (this.selected && this.selected.has(board.id) && this.selected.size > 1) return [...this.selected];
    return [board.id];
  },

  openBoard(id) {
    closeMenu();
    this.closeGallery();
    this.actions.onSelectBoard(id);
  },

  boardMenu(board, at, trash) {
    const ids = this.targetsOf(board);
    if (trash) {
      showMenu(
        [
          { label: "恢复", onSelect: () => ids.forEach((id) => this.actions.onRestoreBoard(id)) },
          "-",
          {
            label: "永久删除",
            danger: true,
            onSelect: () =>
              this.confirm("trash", ids.length > 1 ? `永久删除 ${ids.length} 块白板` : "永久删除这块白板", () =>
                ids.forEach((id) => this.actions.onPurgeBoard(id)), { verb: "删除" }),
          },
        ],
        at,
        { root: this.root, label: boardLabel(board) }
      );
      return;
    }
    const single = ids.length === 1;
    const folders = this.folderNames();
    const current = board.folder || "";
    const moveTo = (folder) => ids.forEach((id) => this.actions.onMoveBoard(id, folder));
    const submenu = [];
    if (current || !single) submenu.push({ label: "不放在文件夹中", onSelect: () => moveTo("") });
    for (const name of folders) {
      submenu.push({ label: name, checked: single ? name === current : undefined, disabled: single && name === current, onSelect: () => moveTo(name) });
    }
    if (submenu.length) submenu.push("-");
    submenu.push({
      label: "新建文件夹",
      onSelect: () => {
        const name = this.freeFolderName();
        this.pendingFolderEdit = name;
        moveTo(name);
      },
    });
    const items = [];
    if (single) items.push({ label: "打开", onSelect: () => this.openBoard(board.id) }, "-");
    if (single) items.push({ label: "重命名", onSelect: () => this.startRename(board) });
    items.push({ label: "移到文件夹", submenu });
    if (single) {
      items.push({ label: "复制", onSelect: () => this.actions.onDuplicateBoard(board.id) });
      if (this.may("export")) {
        items.push({
          label: "导出…",
          onSelect: () => {
            this.closeGallery();
            this.actions.onExportBoard(board.id);
          },
        });
      }
    }
    items.push("-", { label: "移到回收站", onSelect: () => this.trashBoards(ids) });
    showMenu(items, at, { root: this.root, label: boardLabel(board) });
  },

  trashBoards(ids) {
    closeMenu();
    for (const id of ids) this.actions.onDeleteBoard(id);
    if (this.selected) for (const id of ids) this.selected.delete(id);
  },

  /** 卡片拖到了边栏上：文件夹、全部白板（拿出文件夹）或者回收站。 */
  dropBoards(id, folder) {
    const one = this.boards.find((entry) => entry.id === id);
    const ids = one ? this.targetsOf(one) : [id];
    if (folder === TRASH_TARGET) {
      this.trashBoards(ids);
      return;
    }
    for (const each of ids) {
      const entry = this.boards.find((board) => board.id === each);
      if (entry && entry.trashed) this.actions.onRestoreBoard(each);
      this.actions.onMoveBoard(each, folder);
    }
  },

  backgroundMenu(at) {
    const items = [];
    if (this.libNav !== "trash") {
      items.push(
        { label: "新建白板", shortcut: "⌘N", onSelect: () => this.openNewBoard(null, this.viewFolder()) },
        { label: "新建文件夹", shortcut: "⇧⌘N", onSelect: () => this.startNewFolder() },
        "-",
        { label: "导入 PDF 或图片…", shortcut: "⌘I", onSelect: () => this.pickDoc(this.viewFolder()) },
        "-"
      );
    }
    items.push(
      { label: "以图标显示", checked: this.libView === "grid", onSelect: () => this.setLibView("grid") },
      { label: "以列表显示", checked: this.libView === "list", onSelect: () => this.setLibView("list") }
    );
    showMenu(items, at, { root: this.root, label: "白板库" });
  },

  /** 列表显示：名称、文件夹、修改时间三列，选中、右键、拖动和卡片一样。 */
  boardTable(content) {
    const table = el("div", { class: "lib-table", role: "table", "aria-label": content.title });
    table.append(
      el("div", { class: "lib-row head", role: "row" }, [
        el("span", { role: "columnheader", text: "名称" }),
        el("span", { role: "columnheader", text: "文件夹" }),
        el("span", { role: "columnheader", text: content.trash ? "删除时间" : "修改时间" }),
      ])
    );
    for (const section of content.sections) {
      if (!section.boards.length) continue;
      const group = el("div", { class: "lib-rows", role: "rowgroup" });
      if (section.label) group.append(el("div", { class: "lib-row-group", role: "row", text: section.label }));
      for (const board of section.boards) {
        const selected = this.selected && this.selected.has(board.id);
        const thumb = el("span", {
          class: "board-card mini",
          style: { backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated || 0)})` },
        });
        const when = content.trash ? board.trashed : board.updated;
        const row = el("div", { class: `lib-row board-item${selected ? " selected" : ""}`, role: "row", "data-board": board.id }, [
          el("span", { class: "lib-cell-name", role: "cell" }, [thumb, this.boardNameNode(board)]),
          el("span", { class: "lib-cell", role: "cell", text: board.folder || "" }),
          el("span", { class: "lib-cell", role: "cell", text: boardDate(when || 0) }),
        ]);
        this.bindBoardPointer(row, thumb, board, group, content);
        group.append(row);
      }
      table.append(group);
    }
    return table;
  },

  // ------------------------------------------------------------ 新建

  /** 新建白板：选向下延伸（笔记）或四向延伸（大白板）。 */
  openNewBoard(anchor, folder) {
    closeMenu();
    this.closeNewBoard();
    const pick = (kind) => {
      this.closeNewBoard();
      this.closeGallery();
      this.actions.onNewBoard(kind, folder || "");
    };
    const tile = (kind, label, art) =>
      el("button", { class: "kind-tile", type: "button", onclick: () => pick(kind) }, [
        el("span", { class: "kind-art", html: art }),
        el("span", { class: "kind-label", text: label }),
      ]);
    const panel = el("div", { class: "lib-new-panel", role: "dialog", "aria-label": "新建白板" }, [
      el("h2", { text: "新建白板" }),
      el("div", { class: "kind-tiles" }, [
        tile("note", "向下延伸", NOTE_ART),
        tile("board", "四向延伸", BOARD_ART),
      ]),
      // iPad 上没有右键菜单，导入就放在这里（Mac 上空白处右键或 ⌘I 也行）
      el("button", {
        class: "link lib-import",
        type: "button",
        text: "导入 PDF 或图片…",
        onclick: () => {
          this.closeNewBoard();
          this.pickDoc(folder || "");
        },
      }),
    ]);
    panel.addEventListener("click", (event) => event.stopPropagation());
    this.root.append(panel);
    const box = anchor && anchor.isConnected ? anchor.getBoundingClientRect() : null;
    if (box) {
      panel.style.top = `${box.bottom + 8}px`;
      panel.style.left = `${Math.max(12, Math.min(box.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 12))}px`;
    } else {
      panel.style.top = `${Math.max(12, (innerHeight - panel.offsetHeight) / 3)}px`;
      panel.style.left = `${(innerWidth - panel.offsetWidth) / 2}px`;
    }
    const outside = (event) => {
      if (!panel.contains(event.target)) this.closeNewBoard();
    };
    const keys = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      this.closeNewBoard();
    };
    // 在按钮的 click 里打开，按下已经过去了：马上开始听（见 ui-menu.js）
    addEventListener("pointerdown", outside, true);
    addEventListener("keydown", keys, true);
    this.newBoardPanel = { panel, outside, keys };
  },

  closeNewBoard() {
    if (!this.newBoardPanel) return;
    removeEventListener("pointerdown", this.newBoardPanel.outside, true);
    removeEventListener("keydown", this.newBoardPanel.keys, true);
    leave(this.newBoardPanel.panel);
    this.newBoardPanel = null;
  },

  // ------------------------------------------------------------ 键盘

  bindLibraryKeys() {
    if (this._libraryKeys) return;
    this._libraryKeys = (event) => {
      if (!this.gallery || isComposing(event) || menuOpen()) return;
      const field = event.target && event.target.closest && event.target.closest("input, textarea, select");
      const meta = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (meta && key === "n" && !event.shiftKey) {
        event.preventDefault();
        this.openNewBoard(this.gallery.querySelector(".lib-new"), this.viewFolder());
      } else if (meta && key === "n" && event.shiftKey) {
        event.preventDefault();
        this.startNewFolder();
      } else if (meta && key === "i") {
        event.preventDefault();
        this.pickDoc(this.viewFolder());
      } else if (meta && key === "f") {
        event.preventDefault();
        const search = this.gallery.querySelector(".board-search");
        if (search) search.focus();
      } else if (field) {
        return;
      } else if (event.key === "Escape") {
        if (this.newBoardPanel) this.closeNewBoard();
        else if (this.selected && this.selected.size) this.clearSelection();
        else if (this.currentBoardId) this.closeGallery();
        event.preventDefault();
      } else if ((event.key === "Enter" || (meta && (key === "o" || event.key === "ArrowDown"))) && this.selected) {
        if (this.selected.size !== 1 || this.libNav === "trash") return;
        event.preventDefault();
        this.openBoard([...this.selected][0]);
      } else if (meta && event.key === "Backspace" && this.selected && this.selected.size) {
        event.preventDefault();
        if (this.libNav === "trash") return;
        this.trashBoards([...this.selected]);
      }
    };
    addEventListener("keydown", this._libraryKeys, true);
  },

  // ------------------------------------------------------------ 导入

  /** 弹一个文件选择框，选中的 PDF / 图片交给上层去建板。 */
  pickDoc(folder = this.viewFolder()) {
    closeMenu();
    const input = el("input", {
      type: "file",
      multiple: "",
      accept: ".pdf,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff,application/pdf,image/*",
      style: { display: "none" },
    });
    input.addEventListener("change", () => {
      const files = [...(input.files || [])];
      input.remove();
      if (!files.length) return;
      // 和新建白板一样：选好文件就回到白板上，导入完成后切到新的那块
      this.closeGallery();
      this.actions.onNewDoc(files, folder);
    });
    this.root.append(input);
    input.click();
  },

  /**
   * 拖进窗口的文件落在哪个文件夹。白板库开着：指在边栏的文件夹上就是那个，否则是
   * 正在看的那一栏；关着就是当前白板所在的文件夹。
   */
  dropFolder(currentId, event = null) {
    if (this.gallery) {
      const row = event && event.target && event.target.closest && event.target.closest(".lib-side [data-folder]");
      if (row && row.dataset.folder !== TRASH_TARGET) return row.dataset.folder;
      return this.viewFolder();
    }
    const current = this.boards.find((board) => board.id === currentId);
    return (current && current.folder) || "";
  },

  /**
   * 拖着文件经过窗口：白板库里在主区域上盖一层「导入为新白板」，指到边栏的文件夹上
   * 就只亮那一行；白板里是一圈蓝框加一句提示。``event`` 为空表示拖走了或放下了。
   */
  showDrop(event) {
    const library = this.gallery;
    const old = this.root.querySelector(".drop-zone");
    if (this._dropRow) {
      this._dropRow.classList.remove("drop-target");
      this._dropRow = null;
    }
    if (!event) {
      if (old) old.remove();
      return;
    }
    const row = library && event.target && event.target.closest && event.target.closest(".lib-side [data-folder]");
    if (row && row.dataset.folder && row.dataset.folder !== TRASH_TARGET) {
      row.classList.add("drop-target");
      this._dropRow = row;
      if (old) old.remove();
      return;
    }
    const folder = library ? this.viewFolder() : "";
    const title = folder ? `导入到 ${folder}` : "导入为新白板";
    if (old && old.dataset.title === title) return;
    if (old) old.remove();
    const zone = el("div", { class: `drop-zone ${library ? "in-library" : "in-board"}`, "data-title": title, "aria-hidden": "true" }, [
      el("div", { class: "drop-zone-card" }, [
        el("span", { class: "drop-zone-icon", html: icon("doc", 40) }),
        el("span", { class: "drop-zone-title", text: title }),
        el("span", { class: "drop-zone-note", text: "支持 PDF 和图片" }),
      ]),
    ]);
    (library ? this.libMain : this.root).append(zone);
  },

  // ------------------------------------------------------------ 应用

  /** 边栏选中设置时的右边：顶栏只有标题，下面是 ui-settings.js 的 librarySettings。 */
  renderSettingsMain() {
    const main = this.libMain;
    const scroll = this.libScroll && this.libScroll.isConnected ? this.libScroll.scrollTop : 0;
    main.textContent = "";
    this.libHead = null;
    main.append(
      el("header", { class: "lib-head pywebview-drag-region" }, [
        this.touchDevice ? iconButton("sidebar", "显示边栏", () => this.toggleSidebar(), "lib-show-side") : null,
        el("div", { class: "lib-heading" }, [el("h1", { class: "lib-title", text: "设置" })]),
      ])
    );
    this.libScroll = el("div", { class: "lib-scroll" }, [this.librarySettings()]);
    main.append(this.libScroll);
    this.libScroll.scrollTop = scroll;
  },

  renderAppMain(name) {
    const main = this.libMain;
    main.textContent = "";
    this.libHead = null;
    const app = (this.openApp && this.openApp.name === name && this.openApp) || { name, boards: [], total: 0 };
    this.openApp = app;
    main.append(
      el("header", { class: "lib-head pywebview-drag-region" }, [
        this.touchDevice ? iconButton("sidebar", "显示边栏", () => this.toggleSidebar(), "lib-show-side") : null,
        el("div", { class: "lib-heading" }, [el("h1", { class: "lib-title", text: name })]),
      ])
    );
    this.libScroll = el("div", { class: "lib-scroll" });
    this.boardGrid = el("div", { class: "gallery-grid" });
    this.libScroll.append(this.boardGrid);
    main.append(this.libScroll);
    this.fillAppGrid(app);
    if (!app.loaded && !app.loading) this.loadAppBoards(app);
  },

  async loadAppBoards(app) {
    app.loading = true;
    try {
      const response = await fetch(
        `/api/spaces/${encodeURIComponent(app.name)}/boards?offset=${app.boards.length}&limit=${APP_PAGE}`
      );
      if (!response.ok) throw new Error(String(response.status));
      const page = await response.json();
      app.boards.push(...(page.boards || []));
      app.total = page.total || app.boards.length;
      app.loaded = true;
    } catch (err) {
      reportError("ui.loadAppBoards 取不到应用的白板", { app: app.name, message: String(err) });
      app.failed = true;
    }
    app.loading = false;
    if (this.openApp === app && this.gallery && this.libNav === `app:${app.name}`) this.fillAppGrid(app);
  },

  fillAppGrid(app) {
    const grid = this.boardGrid;
    if (!grid) return;
    grid.textContent = "";
    for (const board of app.boards) {
      const card = el("div", {
        class: "board-card app-board",
        title: "只读查看",
        onclick: () => this.actions.onViewAppBoard(app.name, board),
      });
      grid.append(
        el("div", { class: "board-item" }, [
          card,
          el("div", { class: "board-meta" }, [
            el("div", { class: "board-name", text: board.name || board.id }),
            el("div", { class: "board-sub" }, [el("span", { class: "board-date", text: boardDate(board.updated) })]),
          ]),
        ])
      );
    }
    if (app.failed) grid.append(el("p", { class: "gallery-empty", text: "读不出这个应用的白板" }));
    else if (app.loaded && !app.boards.length) grid.append(el("p", { class: "gallery-empty", text: "这个应用还没有白板" }));
    if (app.boards.length < app.total) {
      grid.append(
        el("div", { class: "board-item" }, [
          el("button", {
            class: "board-card add more",
            text: `再显示 ${Math.min(APP_PAGE, app.total - app.boards.length)} 块`,
            onclick: () => this.loadAppBoards(app),
          }),
        ])
      );
    }
  },

  // ------------------------------------------------------------ 触摸

  /** 触摸设备上长按一个元素（边栏里的文件夹）：按住 500ms 不动就弹菜单。 */
  longPress(node, onHold) {
    if (!this.touchDevice) return;
    node.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse") return;
      const start = { x: event.clientX, y: event.clientY };
      let fired = false;
      const timer = setTimeout(() => {
        fired = true;
        const box = node.getBoundingClientRect();
        onHold({ x: box.left + 12, y: box.bottom + 4 });
      }, 500);
      const stop = (up) => {
        clearTimeout(timer);
        removeEventListener("pointermove", move);
        removeEventListener("pointerup", stop);
        removeEventListener("pointercancel", stop);
        // 长按出了菜单，松手时补的那一下 click 别再把文件夹打开
        if (fired && up && up.type === "pointerup") {
          const eat = (click) => {
            click.stopPropagation();
            click.preventDefault();
          };
          addEventListener("click", eat, { capture: true, once: true });
          setTimeout(() => removeEventListener("click", eat, true), 400);
        }
      };
      const move = (moved) => {
        if (Math.hypot(moved.clientX - start.x, moved.clientY - start.y) > 10) stop();
      };
      addEventListener("pointermove", move);
      addEventListener("pointerup", stop);
      addEventListener("pointercancel", stop);
    });
  },
};

// 新建白板里两块大图：向下延伸是一页往下长，四向延伸是往四边长
const NOTE_ART = `<svg width="64" height="72" viewBox="0 0 64 72" fill="none" stroke="currentColor" stroke-width="1.6"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 46V8a4 4 0 0 1 4-4h32a4 4 0 0 1 4 4v38"/>
  <path class="faint" d="M20 16h24M20 24h24M20 32h16"/><path class="faint" d="M12 52v4M12 62v2M52 52v4M52 62v2"/>
  <path d="M32 42v24M26 60l6 6 6-6"/></svg>`;
const BOARD_ART = `<svg width="72" height="72" viewBox="0 0 72 72" fill="none" stroke="currentColor" stroke-width="1.6"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="24" y="24" width="24" height="24" rx="3"/>
  <path class="faint" d="M30 32h12M30 38h8"/><path d="M36 18V4M31 9l5-5 5 5"/><path d="M36 54v14M31 63l5 5 5-5"/>
  <path d="M18 36H4M9 31l-5 5 5 5"/><path d="M54 36h14M63 31l5 5-5 5"/></svg>`;

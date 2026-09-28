// 白板选择界面：满屏的卡片、文件夹、搜索、拖动、新建。
//
// 这些方法装到 UI.prototype 上（见 ui.js 末尾），里面的 this 就是那个 UI。
// 从 ui.js 原样搬过来，只是换了个文件放。

import { icon } from "./icons.js";
import { startCardDrag } from "./dragsort.js";
import { boardLabel, iconButton } from "./ui-common.js";
import { el } from "./util.js";

/** 卡片下方那行时间：今天只给时刻，今年不给年份，其余给全。 */
function boardDate(seconds) {
  const when = new Date(seconds * 1000);
  const now = new Date();
  if (when.toDateString() === now.toDateString()) {
    return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = when.getFullYear() === now.getFullYear();
  return when.toLocaleDateString(
    [],
    sameYear ? { month: "numeric", day: "numeric" } : { year: "numeric", month: "numeric", day: "numeric" }
  );
}

/** 搜索匹配的范围：显示出来的名字、文件夹名，加上文档板的原件文件名。 */
function boardHaystack(board) {
  const doc = board.kind === "doc" && board.doc ? board.doc.name || "" : "";
  return `${boardLabel(board)} ${board.name || ""} ${board.folder || ""} ${doc}`.toLowerCase();
}

export function installBoards(proto) {
  Object.assign(proto, methods);
}

const methods = {
  setBoards(boards, currentId, folders) {
    // 这里是列表变了（别处改了名、新建、归类），不是重新打开界面
    this.boards = boards;
    if (folders) this.folders = folders;
    // 正在看的文件夹被别处删掉了，就退回最外面那一层，否则会停在一个空壳里
    if (this.openFolder && !this.folders.includes(this.openFolder)) this.openFolder = "";
    this.currentBoardId = currentId;
    if (this.gallery) this.renderBoards({ opening: false });
  },

  closeGallery() {
    if (this.gallery) {
      this.gallery.remove();
      this.gallery = null;
    }
  },

  /** 打开选择界面前先把当前白板的缩略图刷新一遍，免得看到的是旧图。 */
  async openBoards() {
    this.boardQuery = "";
    // 打开时停在当前白板所在的那一层，否则归过类的白板一打开界面就看不见了
    const current = this.boards.find((board) => board.id === this.currentBoardId);
    this.openFolder = (current && current.folder) || "";
    if (this.actions.onBoardsOpen) await this.actions.onBoardsOpen();
    this.renderBoards();
  },

  /** Mac 端专门的白板选择界面：满屏缩略图，左上角标出延伸类型，下面是名字和日期。 */
  renderBoards({ opening = true } = {}) {
    // 列表随时可能被广播刷新（别处改了名、新建、删除），重建之前记住焦点落在哪，
    // 建完再放回去，否则正在输入的搜索框或改名框会被抽走。
    const active = document.activeElement;
    const inside = active && this.gallery && this.gallery.contains(active);
    const focusKey = inside ? active.dataset.focusKey : null;
    const caret = focusKey && active.setSelectionRange ? [active.selectionStart, active.selectionEnd] : null;

    this.closeGallery();
    this.closeSheet();

    const search = el("input", {
      class: "board-search",
      type: "text",
      placeholder: "搜索白板",
      value: this.boardQuery,
      spellcheck: "false",
      "data-focus-key": "search",
      oninput: () => {
        this.boardQuery = search.value;
        this.fillBoardGrid();
      },
      onkeydown: (event) => {
        if (event.key !== "Escape" || !search.value) return;
        event.stopPropagation(); // 别让 Esc 顺手把整个界面关掉
        search.value = this.boardQuery = "";
        this.fillBoardGrid();
      },
    });

    const head = el("div", { class: "gallery-head" });
    // 进了文件夹，搜索框前面多一个返回；搜索本身始终在全部白板里找
    if (this.openFolder) {
      const back = iconButton("back", "返回（把白板拖到这里就移出文件夹）", () => this.leaveFolder());
      back.dataset.dropOut = "";
      head.append(back);
    }
    head.append(
      el("label", { class: "search-box" }, [
        el("span", { class: "search-icon", html: icon("search", 18) }),
        search,
      ]),
      iconButton("close", "关闭", () => this.closeGallery())
    );

    this.boardGrid = el("div", { class: "gallery-grid" });
    const gallery = el("div", { class: `gallery${opening ? " opening" : ""}` }, [head]);
    this.folderBarNode = this.openFolder ? this.folderBar(this.openFolder) : null;
    if (this.folderBarNode) gallery.append(this.folderBarNode);
    gallery.append(this.boardGrid);
    this.root.append(gallery);
    this.gallery = gallery;
    this.fillBoardGrid();

    if (!focusKey) return;
    const back = gallery.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
    if (!back) return;
    back.focus();
    if (caret && back.setSelectionRange) back.setSelectionRange(caret[0], caret[1]);
  },

  /** 现有的文件夹名，按名字排序。空文件夹也在里面，所以名单是服务端给的。 */
  folderNames() {
    return [...this.folders].sort((a, b) => a.localeCompare(b, "zh"));
  },

  enterFolder(name) {
    this.openFolder = name;
    this.boardQuery = "";
    this.renderBoards();
  },

  leaveFolder() {
    this.openFolder = "";
    this.boardQuery = "";
    this.renderBoards();
  },

  /**
   * 只重铺格子。搜索时不碰上面那条，输入框和输入法状态才不会被打断。
   *
   * 最外面那一层：文件夹排在前面，后面是没归类的白板。进了文件夹就只剩里面那些。
   * 搜索是在全部白板里找，结果平铺，不分文件夹，也不放「新建」，免得点错。
   */
  fillBoardGrid() {
    const grid = this.boardGrid;
    if (!grid) return;
    grid.textContent = "";
    const query = this.boardQuery.trim().toLowerCase();
    // 搜索是在全部白板里找的，这时候顶上那条文件夹说明反而对不上，先藏起来
    if (this.folderBarNode) this.folderBarNode.style.display = query ? "none" : "";
    if (query) {
      const matched = this.boards.filter((board) => boardHaystack(board).includes(query));
      for (const board of matched) grid.append(this.boardItem(board));
      if (!matched.length) grid.append(el("p", { class: "gallery-empty", text: "没有匹配的白板" }));
      return;
    }
    if (!this.openFolder) {
      for (const name of this.folderNames()) grid.append(this.folderItem(name));
    }
    for (const board of this.boards) {
      if ((board.folder || "") === this.openFolder) grid.append(this.boardItem(board));
    }
    grid.append(
      el("div", { class: "board-item" }, [
        el("button", {
          class: "board-card add",
          html: icon("add", 32),
          title: "新建",
          onclick: () => this.chooseKind(),
        }),
      ])
    );
  },

  /**
   * 让一张卡片可以拖：归进文件夹、移出来、调顺序。
   *
   * 鼠标和手指走同一套（见 dragsort.js），不用浏览器自带的拖放——iOS Safari 不发
   * 那些事件，而且拖影的样子和动画都由浏览器定，两端不可能一致。
   */
  makeDraggable(card, boardId) {
    card.addEventListener("pointerdown", (event) =>
      startCardDrag(event, {
        card,
        item: card.closest(".board-item"),
        id: boardId,
        grid: this.boardGrid,
        gallery: this.gallery,
        root: this.root,
        folderOf: (id) => {
          const board = this.boards.find((one) => one.id === id);
          return (board && board.folder) || "";
        },
        onFile: (id, folder) => this.actions.onMoveBoard(id, folder),
        onReorder: (ids) => this.actions.onReorderBoards(ids),
        onRefresh: () => this.fillBoardGrid(),
      })
    );
  },

  /** 文件夹在格子里就是一块卡片：点开进去，名字可以直接改，也可以把白板拖进来。 */
  folderItem(name) {
    const count = this.boards.filter((board) => board.folder === name).length;
    const card = el(
      "div",
      {
        class: "board-card folder",
        "data-folder": name,
        title: `打开文件夹：${name}（也可以把白板拖进来）`,
        onclick: () => this.enterFolder(name),
      },
      [
        el("span", { class: "folder-card-icon", html: icon("folder", 56) }),
        el("span", { class: "folder-card-count", text: String(count) }),
      ]
    );
    return el("div", { class: "board-item" }, [card, el("div", { class: "board-meta" }, [this.folderName(name)])]);
  },

  /** 文件夹的名字就是它的身份，改名等于把里面每块白板上记的名字一起改掉。 */
  folderName(name) {
    const input = el("input", {
      class: "board-name folder",
      type: "text",
      value: name,
      title: "重命名文件夹",
      spellcheck: "false",
      maxlength: "64",
      "data-focus-key": `folder:${name}`,
      onclick: (event) => event.stopPropagation(),
      onkeydown: (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          input.blur();
        } else if (event.key === "Escape") {
          event.stopPropagation();
          input.value = name;
          input.blur();
        }
      },
      onchange: () => {
        const next = input.value.trim();
        input.value = next || name;
        if (!next || next === name) return;
        if (this.folders.includes(next)) {
          input.value = name;
          this.message("已经有同名的文件夹了", "close", 4000);
          return;
        }
        if (this.openFolder === name) this.openFolder = next;
        this.actions.onRenameFolder(name, next);
      },
    });
    return input;
  },

  /** 进了文件夹之后，搜索框下面那一行：名字、装了几块、删除。 */
  folderBar(name) {
    const bar = el("div", { class: "folder-bar", "data-drop-out": "" }, [
      el("span", { class: "folder-bar-icon", html: icon("folder", 20) }),
      this.folderName(name),
      el("span", {
        class: "folder-bar-count",
        text: `${this.boards.filter((board) => board.folder === name).length} 块`,
      }),
      iconButton("trash", "删除文件夹", () => {
        this.confirm("trash", "删除文件夹？里面的白板会移到外面", () => {
          this.openFolder = "";
          this.actions.onDeleteFolder(name);
        });
      }),
    ]);
    return bar;
  },

  /** 一块白板：缩略图 + 可以直接改的名字 + 最后一次写的时间。 */
  boardItem(board) {
    const card = el(
      "div",
      {
        class: `board-card${board.id === this.currentBoardId ? " active" : ""}`,
        style: {
          backgroundImage: `url(/api/thumb/${board.id}?v=${Math.floor(board.updated)})`,
        },
        onclick: () => {
          this.closeGallery();
          this.actions.onSelectBoard(board.id);
        },
      },
      [
        el("span", {
          class: "kind",
          html: icon(["note", "doc"].includes(board.kind) ? board.kind : "board", 20),
        }),
      ]
    );
    this.makeDraggable(card, board.id);
    if (this.boards.length > 1) {
      card.append(
        el("button", {
          class: "del",
          html: icon("close", 18),
          title: "删除白板",
          onclick: (event) => {
            event.stopPropagation();
            this.confirm("trash", "删除白板？", () => this.actions.onDeleteBoard(board.id));
          },
        })
      );
    }

    // 名字就是一个长得像文字的输入框：点一下直接改，清空就回到默认名。
    const name = el("input", {
      class: "board-name",
      type: "text",
      value: board.name || "",
      placeholder: boardLabel(board),
      title: "重命名白板",
      spellcheck: "false",
      maxlength: "64",
      "data-focus-key": `name:${board.id}`,
      onkeydown: (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          name.blur();
        } else if (event.key === "Escape") {
          event.stopPropagation();
          name.value = board.name || "";
          name.blur();
        }
      },
      // change 只在值真的变了又失去焦点（或按了回车）时才发，正好是我们要的时机
      onchange: () => {
        const next = name.value.trim();
        name.value = next;
        if (next === (board.name || "")) return;
        this.actions.onRenameBoard(board.id, next);
      },
    });

    // 文件夹按钮放在名字这一行，不放卡片上：卡片上那个删除按钮是 hover 才出现的，
    // 触摸屏上按不到。
    const folder = el("button", {
      class: `board-folder${board.folder ? " on" : ""}`,
      html: icon("folder", 17),
      title: board.folder ? `文件夹：${board.folder}` : "归入文件夹",
      onclick: () => this.chooseFolder(board),
    });

    return el("div", { class: "board-item", "data-board": board.id }, [
      card,
      el("div", { class: "board-meta" }, [
        name,
        folder,
        el("span", {
          class: "board-date",
          text: boardDate(board.updated),
          title: new Date(board.updated * 1000).toLocaleString(),
        }),
      ]),
    ]);
  },

  /**
   * 归入文件夹。文件夹只有一层，名字本身就是身份：点现成的名字是移进去，输入一个
   * 没人用过的名字就等于新建；最后一块白板移走之后，这个文件夹自己就不在了。
   */
  chooseFolder(board) {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const move = (folder) => {
      scrim.remove();
      if (folder === (board.folder || "")) return;
      this.actions.onMoveBoard(board.id, folder);
    };
    const row = (folder, label, iconName) =>
      el(
        "button",
        {
          class: `folder-row${folder === (board.folder || "") ? " active" : ""}`,
          onclick: () => move(folder),
        },
        [
          el("span", { class: "folder-row-icon", html: icon(iconName, 18) }),
          el("span", { class: "folder-row-name", text: label }),
        ]
      );
    const rows = [row("", "不归类", "boards")];
    for (const name of this.folderNames()) rows.push(row(name, name, "folder"));

    // 不自动聚焦：iPad 上一开就把键盘顶上来，现成的文件夹反而看不见了
    const input = el("input", {
      class: "folder-new",
      type: "text",
      placeholder: "新文件夹",
      spellcheck: "false",
      maxlength: "64",
      onkeydown: (event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          scrim.remove();
          return;
        }
        if (event.key !== "Enter") return;
        event.preventDefault();
        const next = input.value.trim();
        if (next) move(next);
      },
    });
    const dialog = el("div", { class: "dialog folders" }, [
      el("div", { class: "folder-list" }, rows),
      el("label", { class: "folder-add" }, [
        el("span", { class: "search-icon", html: icon("add", 18) }),
        input,
      ]),
    ]);
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
  },

  /** 现在这一层里还没用过的文件夹名，用来给新建的文件夹起个默认名。 */
  freeFolderName() {
    const base = "未命名文件夹";
    if (!this.folders.includes(base)) return base;
    for (let n = 2; ; n += 1) {
      const name = `${base} ${n}`;
      if (!this.folders.includes(name)) return name;
    }
  },

  /** 弹一个文件选择框，选中的 PDF / 图片交给上层去建板。 */
  pickDoc() {
    const input = el("input", {
      type: "file",
      accept: ".pdf,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff,application/pdf,image/*",
      style: { display: "none" },
    });
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      input.remove();
      if (file) this.actions.onNewDoc(file, this.openFolder);
    });
    this.root.append(input);
    input.click();
  },

  /** 新建白板时选延伸方式：大白板、笔记，或者直接拿一份 PDF / 图片当底。 */
  chooseKind() {
    const scrim = el("div", { class: "scrim", onclick: () => scrim.remove() });
    const pick = (kind) => {
      scrim.remove();
      this.closeGallery();
      // 在文件夹里按的「新建」，新白板就落在这个文件夹里
      this.actions.onNewBoard(kind, this.openFolder);
    };
    const dialog = el("div", { class: "dialog kinds" }, [
      el("button", {
        class: "kind-tile",
        html: icon("board", 48),
        title: "大白板：四个方向都无限延伸",
        onclick: () => pick("board"),
      }),
      el("button", {
        class: "kind-tile",
        html: icon("note", 48),
        title: "笔记：宽度固定，只向下延伸",
        onclick: () => pick("note"),
      }),
      el("button", {
        class: "kind-tile",
        html: icon("doc", 48),
        title: "打开 PDF / 图片，直接在上面写（也可以把文件拖进窗口）",
        onclick: () => {
          scrim.remove();
          const folder = this.openFolder;
          this.closeGallery();
          this.openFolder = folder; // 关了界面也要记住在哪个文件夹里按的
          this.pickDoc();
        },
      }),
    ]);
    // 文件夹只有一层，所以只有在最外面那一层才给这一项
    if (!this.openFolder) {
      dialog.append(
        el("button", {
          class: "kind-tile",
          html: icon("folder", 48),
          title: "新建文件夹",
          onclick: () => {
            scrim.remove();
            this.actions.onNewFolder(this.freeFolderName());
          },
        })
      );
    }
    dialog.addEventListener("click", (event) => event.stopPropagation());
    scrim.append(dialog);
    this.root.append(scrim);
  },
};

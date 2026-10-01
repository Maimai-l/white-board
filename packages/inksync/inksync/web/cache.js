// 本地缓存（IndexedDB）。
//
// 作用有两个：断线时仍然看得到内容、服务端重启后页面不会变成一片空白；
// 以及把没发出去的操作存下来，刷新页面也不会丢笔。
//
// 每个存储前缀（createInkPad 的 ``storage`` 选项，默认 ``inksync:<空间>``）一个库，
// 同一来源下的不同项目互不影响。库里的键：
//
//   board:<id>   {meta, strokes, seq, epoch}：一块白板的内容
//   used         {<id>: 最后使用时间}：清理时按它挑最久没用的
//   pending      [{cid, board, op}]：待发队列（带着所属的白板）
//   last         跟随模式下上次显示的白板
//
// 每个库最多保留 MAX_BOARDS 块白板的内容，超出时删掉最久没用的；待发队列里还有
// 它的操作的白板不删。

const STORE = "kv";
export const MAX_BOARDS = 200;
// 1.0.x 的库名（白板应用和嵌入的手写板共用一个）
const LEGACY_DB = "whiteboard";

function idbRequest(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function openDb(name) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export class Cache {
  /** ``name`` 是存储前缀，同时是 IndexedDB 的库名。 */
  constructor(name = "inksync:") {
    this.name = name;
    this.db = null;
    this._opening = null;
  }

  async open() {
    if (this.db) return this.db;
    if (!this._opening) {
      this._opening = (async () => {
        try {
          this.db = await openDb(this.name);
          if (this.name === "inksync:") await this._migrate();
        } catch (err) {
          this.db = null; // 隐私模式等环境下没有 IndexedDB，降级为不缓存
        }
        return this.db;
      })();
    }
    return this._opening;
  }

  async _tx(mode) {
    const db = await this.open();
    if (!db) return null;
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  async get(key) {
    try {
      const store = await this._tx("readonly");
      if (!store) return null;
      return (await idbRequest(store.get(key))) ?? null;
    } catch (err) {
      return null;
    }
  }

  async set(key, value) {
    try {
      const store = await this._tx("readwrite");
      if (!store) return false;
      await idbRequest(store.put(value, key));
      return true;
    } catch (err) {
      return false;
    }
  }

  async remove(key) {
    try {
      const store = await this._tx("readwrite");
      if (!store) return false;
      await idbRequest(store.delete(key));
      return true;
    } catch (err) {
      return false;
    }
  }

  async saveBoard(boardId, payload) {
    const ok = await this.set(`board:${boardId}`, payload);
    await this._touch(boardId);
    return ok;
  }

  loadBoard(boardId) {
    return this.get(`board:${boardId}`);
  }

  async _touch(boardId) {
    const used = (await this.get("used")) || {};
    used[boardId] = Date.now();
    const ids = Object.keys(used);
    if (ids.length > MAX_BOARDS) {
      const pending = (await this.get("pending")) || [];
      const busy = new Set(pending.map((item) => item && item.board));
      busy.add(boardId);
      ids.sort((a, b) => used[a] - used[b]);
      for (const id of ids) {
        if (Object.keys(used).length <= MAX_BOARDS) break;
        if (busy.has(id)) continue;
        delete used[id];
        await this.remove(`board:${id}`);
      }
    }
    await this.set("used", used);
  }

  setLast(boardId) {
    return this.set("last", boardId);
  }

  getLast() {
    return this.get("last");
  }

  savePending(items) {
    return this.set("pending", items);
  }

  loadPending() {
    return this.get("pending");
  }

  /**
   * 1.0.x 的缓存迁进来（只对默认空间的库做一次）：白板内容照搬，待发操作标上
   * 1.0.x 缓存的 ``last`` 白板——1.0.x 的待发操作总是发往连接当时所在的白板。
   * 迁完删掉 1.0.x 的库；出错时保留 1.0.x 数据，下次再试。
   */
  async _migrate() {
    if (!indexedDB.databases) return;
    let names = [];
    try {
      names = (await indexedDB.databases()).map((db) => db.name);
    } catch (err) {
      return;
    }
    if (!names.includes(LEGACY_DB)) return;
    let legacy;
    try {
      legacy = await openDb(LEGACY_DB);
    } catch (err) {
      return;
    }
    try {
      const store = legacy.transaction(STORE, "readonly").objectStore(STORE);
      const keysRequest = store.getAllKeys();
      const valuesRequest = store.getAll();
      const keys = await idbRequest(keysRequest);
      const values = await idbRequest(valuesRequest);
      const old = new Map(keys.map((key, i) => [key, values[i]]));
      const last = old.get("last");
      // 所有写入在同一个事务里同步发出，最后等事务完成：旧版 Safari 上事务不会跨 await 保持
      const tx = this.db.transaction(STORE, "readwrite");
      const target = tx.objectStore(STORE);
      const used = {};
      for (const [key, value] of old) {
        if (typeof key !== "string" || !key.startsWith("board:") || !value) continue;
        target.put(value, key);
        used[key.slice(6)] = Date.now();
      }
      if (last) target.put(last, "last");
      const pending = (old.get("pending") || []).filter((item) => item && item.cid && item.op);
      if (pending.length && last) {
        target.put(pending.map((item) => ({ ...item, board: item.board || last })), "pending");
      }
      target.put(used, "used");
      await new Promise((resolve, reject) => {
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
      legacy.close();
      indexedDB.deleteDatabase(LEGACY_DB);
      try {
        for (const key of Object.keys(localStorage)) {
          if (key.startsWith("whiteboard.view.")) localStorage.removeItem(key);
        }
      } catch (err) {
        /* 读写不了 localStorage 就算了 */
      }
    } catch (err) {
      try {
        legacy.close();
      } catch (closeErr) {
        /* 已经关了 */
      }
    }
  }
}

/** 视图位置（缩放和平移），每个存储前缀一份，最多记 MAX_BOARDS 块白板。 */
export class Views {
  constructor(name) {
    this.key = `${name}views`;
    this.data = null;
  }

  _load() {
    if (this.data) return this.data;
    try {
      this.data = JSON.parse(localStorage.getItem(this.key) || "{}") || {};
    } catch (err) {
      this.data = {};
    }
    return this.data;
  }

  get(boardId) {
    const view = this._load()[boardId];
    return view && view.scale > 0 ? view : null;
  }

  set(boardId, view) {
    const data = this._load();
    data[boardId] = { scale: view.scale, x: view.x, y: view.y, t: Date.now() };
    const ids = Object.keys(data);
    if (ids.length > MAX_BOARDS) {
      ids.sort((a, b) => (data[a].t || 0) - (data[b].t || 0));
      for (const id of ids.slice(0, ids.length - MAX_BOARDS)) delete data[id];
    }
    try {
      localStorage.setItem(this.key, JSON.stringify(data));
    } catch (err) {
      /* 存不下就算了，不影响使用 */
    }
  }
}

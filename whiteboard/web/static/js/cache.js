// 本地缓存（IndexedDB）。
//
// 作用有两个：断线时仍然看得到内容、服务端重启后页面不会变成一片空白；
// 以及把没发出去的操作存下来，刷新页面也不会丢笔。

const DB_NAME = "whiteboard";
const STORE = "kv";

function idbRequest(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export class Cache {
  constructor() {
    this.db = null;
  }

  async open() {
    if (this.db) return this.db;
    try {
      this.db = await new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(STORE)) {
            request.result.createObjectStore(STORE);
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    } catch (err) {
      this.db = null; // 隐私模式等环境下没有 IndexedDB，降级为不缓存
    }
    return this.db;
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

  saveBoard(boardId, payload) {
    return this.set(`board:${boardId}`, payload);
  }

  loadBoard(boardId) {
    return this.get(`board:${boardId}`);
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
}

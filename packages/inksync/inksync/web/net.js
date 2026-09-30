// 同步通道（手写板的「传输」），协议 v2（docs/design/inksync-2-interface.zh-CN.md）。
//
// 手写板只通过下面这组成员使用传输，自定义传输实现同样的成员即可：
//
//   connect()              开始同步；收到的消息交给 onMessage，状态变化交给 onStatus
//   open(board, options)   在同一连接上换一块白板（options：create、readonly）
//   send(msg)              发一条控制消息（例如 unlock），发不出去返回 false
//   sendLive(msg)          实时笔迹，丢了无妨
//   sendOp(op)             正式操作，返回 cid；收到回执前留在 outbox 里，并记下所属的白板
//   restoreOutbox(items)   把上次没发出去的操作放回 outbox
//   close()                停止同步
//   outbox / boardId / lastSeq / epoch / status   手写板读写的状态
//
// Net 是 WebSocket 实现：断线重连、按序号补齐、离线操作排队。LocalTransport 不连任何
// 服务器，内容由宿主通过手写板的事件自行保存。

export const PROTOCOL = 2;
const PING_INTERVAL = 10000;
const PONG_TIMEOUT = 12000;
const BACKOFF = [400, 800, 1500, 3000, 5000, 8000];

export class Net {
  /**
   * @param {object} options
   * @param {string} options.clientId
   * @param {string} [options.device]   只用于服务端日志，例如 "ipad"
   * @param {object} [options.target]   {space, board, create, readonly, follow}
   * @param {string} [options.url]      WebSocket 地址；省略时连本页所在服务的 /ws
   */
  constructor({ clientId, device, target, url, onMessage, onStatus }) {
    this.clientId = clientId;
    this.device = device || "";
    this.url = url || null;
    this.target = { space: "", follow: false, ...(target || {}) };
    this.onMessage = onMessage;
    this.onStatus = onStatus || (() => {});
    this.onOutboxChange = () => {};
    this.ws = null;
    // 当前显示的白板；跟随模式下由服务端决定
    this.boardId = this.target.follow ? null : this.target.board || null;
    this.lastSeq = 0;
    this.epoch = null;
    this.outbox = []; // [{cid, board, op}]：尚未收到回执的操作，重连后按序重发
    this.opCounter = 0;
    this.status = "offline";
    this.attempt = 0;
    this.refused = null; // 服务端拒绝了握手的原因；之后不再重连
    this._joined = false;
    this._timer = 0;
    this._pingTimer = 0;
    this._lastPong = 0;
    this._closed = false;

    this._onOnline = () => this.connect(true);
    this._onFocus = () => this.connect(false);
    this._onVisible = () => {
      if (!document.hidden) this.connect(false);
    };
    addEventListener("online", this._onOnline);
    addEventListener("focus", this._onFocus);
    document.addEventListener("visibilitychange", this._onVisible);
  }

  /** 停止同步：断开连接，不再重连。手写板被移除时调用。 */
  close() {
    this._closed = true;
    clearTimeout(this._timer);
    this._stopPing();
    removeEventListener("online", this._onOnline);
    removeEventListener("focus", this._onFocus);
    document.removeEventListener("visibilitychange", this._onVisible);
    if (this.ws) {
      try {
        this.ws.close();
      } catch (err) {
        /* 本来就要丢掉这条连接 */
      }
      this.ws = null;
    }
  }

  get online() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  _setStatus(status) {
    if (this.status === status) return;
    this.status = status;
    this.onStatus(status);
  }

  _hello() {
    const target = this.target;
    const msg = {
      t: "hello",
      v: PROTOCOL,
      client: this.clientId,
      space: target.space || "",
      since: this.lastSeq,
      epoch: this.epoch,
      device: this.device,
    };
    if (target.follow) {
      msg.follow = true;
      msg.board = this.boardId;
    } else {
      msg.board = target.board;
      if (target.create) msg.create = target.create;
      if (this.boardId !== target.board) {
        msg.since = 0;
        msg.epoch = null;
      }
    }
    if (target.readonly) msg.readonly = true;
    return msg;
  }

  connect(force = false) {
    if (this._closed || this.refused) return;
    if (this.online && !force) return;
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return;
    clearTimeout(this._timer);
    if (this.ws) {
      try {
        this.ws.close();
      } catch (err) {
        /* 忽略：本来就是要丢掉这条连接 */
      }
      this.ws = null;
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(this.url || `${protocol}//${location.host}/ws`);
    this.ws = ws;
    this._joined = false;
    this._setStatus("syncing");

    ws.onopen = () => {
      this.attempt = 0;
      this._lastPong = Date.now();
      ws.send(JSON.stringify(this._hello()));
      this._startPing();
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      this._receive(msg);
    };

    ws.onclose = () => {
      this._stopPing();
      if (this.ws === ws) this.ws = null;
      this._setStatus("offline");
      this._scheduleReconnect();
    };

    ws.onerror = () => {
      try {
        ws.close();
      } catch (err) {
        /* onclose 会接着处理 */
      }
    };
  }

  _receive(msg) {
    const kind = msg.t;
    if (kind === "pong") {
      this._lastPong = Date.now();
      return;
    }
    if (kind === "ack") {
      this._ack(msg);
      return;
    }
    if (kind === "error" && !this._joined) {
      // 握手被拒：服务端会关掉连接，不再重连（重连只会得到同样的回答）
      this.refused = msg.reason || "error";
    }
    const snapshot = kind === "init" || kind === "sync" || kind === "switch";
    if (snapshot) {
      this._joined = true;
      if (msg.board && msg.board.id) this.boardId = msg.board.id;
      if (msg.epoch) this.epoch = msg.epoch;
      if (typeof msg.seq === "number") this.lastSeq = msg.seq;
      this._setStatus("online");
    } else if (kind === "op" && msg.op && typeof msg.op.seq === "number") {
      if (!msg.board || msg.board === this.boardId) this.lastSeq = msg.op.seq;
    }
    this.onMessage(msg);
    if (snapshot) this._flush();
  }

  _scheduleReconnect() {
    if (this._closed || this.refused) return;
    const delay = BACKOFF[Math.min(this.attempt, BACKOFF.length - 1)];
    this.attempt += 1;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.connect(), delay);
  }

  /** 服务端拒绝握手之后，宿主改好条件（例如换了白板）再试。 */
  retry() {
    this.refused = null;
    this.connect(true);
  }

  _startPing() {
    this._stopPing();
    this._pingTimer = setInterval(() => {
      if (!this.online) return;
      if (Date.now() - this._lastPong > PONG_TIMEOUT) {
        // 连接已经死了但没触发 close（移动端切后台常见），主动重连。
        try {
          this.ws.close();
        } catch (err) {
          /* 同上 */
        }
        return;
      }
      this.send({ t: "ping", ts: Date.now() });
    }, PING_INTERVAL);
  }

  _stopPing() {
    clearInterval(this._pingTimer);
    this._pingTimer = 0;
  }

  send(msg) {
    if (!this.online) return false;
    try {
      this.ws.send(JSON.stringify(msg));
      return true;
    } catch (err) {
      return false;
    }
  }

  /** 在同一连接上换一块白板。``since``/``epoch`` 用调用方设好的 lastSeq/epoch（来自本地缓存）。 */
  open(board, { create, readonly } = {}) {
    this.target = { ...this.target, board, create, readonly: !!readonly, follow: false };
    this.boardId = board;
    const msg = { t: "open", board, since: this.lastSeq, epoch: this.epoch };
    if (create) msg.create = create;
    if (readonly) msg.readonly = true;
    if (this.refused) {
      this.retry();
      return;
    }
    if (!this.send(msg)) this.connect(true);
  }

  /** 实时笔迹：掉线时直接丢弃，笔画结束时的正式操作会补上。 */
  sendLive(msg) {
    this.send(msg);
  }

  /** 正式操作：进待发队列，收到回执才移除，断线重连后重发。 */
  sendOp(op) {
    // 用自增计数器而不是队列长度，避免回执之后再发时撞上同一个 cid。
    const cid = `${this.clientId}-${Date.now().toString(36)}-${(this.opCounter++).toString(36)}`;
    const item = { cid, board: this.boardId, op };
    this.outbox.push(item);
    this.onOutboxChange(this.outbox);
    this.send({ t: "op", cid, board: item.board, op });
    return cid;
  }

  restoreOutbox(items) {
    if (!Array.isArray(items) || !items.length) return;
    const known = new Set(this.outbox.map((item) => item.cid));
    for (const item of items) {
      if (item && item.cid && item.op && !known.has(item.cid)) this.outbox.push(item);
    }
    this._flush();
  }

  _ack(msg) {
    const index = this.outbox.findIndex((item) => item.cid === msg.cid);
    const item = index >= 0 ? this.outbox[index] : null;
    if (index >= 0) {
      this.outbox.splice(index, 1);
      this.onOutboxChange(this.outbox);
    }
    const board = msg.board || (item && item.board) || this.boardId;
    if (typeof msg.seq === "number" && board === this.boardId) this.lastSeq = Math.max(this.lastSeq, msg.seq);
    if (msg.rejected && item) {
      this.onMessage({ t: "rejected", board, op: item.op, reason: msg.rejected });
    } else if (msg.op) {
      this.onMessage({ t: "op", op: msg.op, src: this.clientId, mine: true, board });
    }
    this._setStatus(this.outbox.length ? "syncing" : "online");
  }

  _flush() {
    for (const item of this.outbox) {
      this.send({ t: "op", cid: item.cid, board: item.board || this.boardId, op: item.op });
    }
    this._setStatus(this.outbox.length ? "syncing" : "online");
  }
}

/**
 * 不同步的传输：操作只在本机生效，不需要服务器。
 *
 * 状态固定为 "local"，outbox 始终为空。宿主通过手写板的 ``op`` 事件或
 * ``snapshot()`` 拿到内容自行保存，下次用 ``initial`` 选项或 ``load()`` 放回来。
 */
export class LocalTransport {
  constructor({ onStatus, target } = {}) {
    this.local = true;
    this.onStatus = onStatus || (() => {});
    this.onOutboxChange = () => {};
    this.outbox = [];
    this.boardId = (target && target.board) || null;
    this.lastSeq = 0;
    this.epoch = null;
    this.status = "local";
    this.counter = 0;
  }

  connect() {
    this.onStatus("local");
  }

  open(board) {
    this.boardId = board;
  }

  send() {
    return false;
  }

  sendLive() {}

  sendOp() {
    this.counter += 1;
    return `local-${this.counter}`;
  }

  restoreOutbox() {}

  close() {}
}

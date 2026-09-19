// WebSocket 同步通道：断线重连、按序号补齐、离线操作排队。

const PING_INTERVAL = 10000;
const PONG_TIMEOUT = 12000;
const BACKOFF = [400, 800, 1500, 3000, 5000, 8000];

export class Net {
  constructor({ clientId, role, onMessage, onStatus }) {
    this.clientId = clientId;
    this.role = role;
    this.onMessage = onMessage;
    this.onStatus = onStatus || (() => {});
    this.onOutboxChange = () => {};
    this.ws = null;
    this.boardId = null;
    this.lastSeq = 0;
    this.epoch = null;
    this.outbox = []; // 尚未收到回执的操作，重连后按序重发
    this.opCounter = 0;
    this.status = "offline";
    this.attempt = 0;
    this._timer = 0;
    this._pingTimer = 0;
    this._lastPong = 0;
    this._closed = false;

    addEventListener("online", () => this.connect(true));
    addEventListener("focus", () => this.connect(false));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.connect(false);
    });
  }

  get online() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  _setStatus(status) {
    if (this.status === status) return;
    this.status = status;
    this.onStatus(status);
  }

  connect(force = false) {
    if (this._closed) return;
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
    const ws = new WebSocket(`${protocol}//${location.host}/ws`);
    this.ws = ws;
    this._setStatus("syncing");

    ws.onopen = () => {
      this.attempt = 0;
      this._lastPong = Date.now();
      ws.send(
        JSON.stringify({
          t: "hello",
          client: this.clientId,
          role: this.role,
          board: this.boardId,
          since: this.lastSeq,
          epoch: this.epoch,
        })
      );
      this._startPing();
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (msg.t === "pong") {
        this._lastPong = Date.now();
        return;
      }
      if (msg.t === "ack") {
        this._ack(msg);
        return;
      }
      if (msg.epoch) this.epoch = msg.epoch;
      if (typeof msg.seq === "number") this.lastSeq = msg.seq;
      if (msg.op && typeof msg.op.seq === "number") this.lastSeq = msg.op.seq;
      if (msg.t === "init" || msg.t === "sync" || msg.t === "switch") {
        this._setStatus("online");
      }
      this.onMessage(msg);
      if (msg.t === "init" || msg.t === "sync" || msg.t === "switch") this._flush();
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

  _scheduleReconnect() {
    if (this._closed) return;
    const delay = BACKOFF[Math.min(this.attempt, BACKOFF.length - 1)];
    this.attempt += 1;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.connect(), delay);
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

  /** 实时笔迹：掉线时直接丢弃，笔画结束时的正式操作会补上。 */
  sendLive(msg) {
    this.send(msg);
  }

  /** 正式操作：进待发队列，收到回执才移除，断线重连后重发。 */
  sendOp(op) {
    // 用自增计数器而不是队列长度，避免回执之后再发时撞上同一个 cid。
    const cid = `${this.clientId}-${Date.now().toString(36)}-${(this.opCounter++).toString(36)}`;
    this.outbox.push({ cid, op });
    this.onOutboxChange(this.outbox);
    this.send({ t: "op", cid, op });
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
    if (index >= 0) {
      this.outbox.splice(index, 1);
      this.onOutboxChange(this.outbox);
    }
    if (typeof msg.seq === "number") this.lastSeq = Math.max(this.lastSeq, msg.seq);
    if (msg.op) this.onMessage({ t: "op", op: msg.op, src: this.clientId, mine: true });
    this._setStatus(this.outbox.length ? "syncing" : "online");
  }

  _flush() {
    for (const item of this.outbox) {
      this.send({ t: "op", cid: item.cid, op: item.op });
    }
    this._setStatus(this.outbox.length ? "syncing" : "online");
  }

  close() {
    this._closed = true;
    this._stopPing();
    clearTimeout(this._timer);
    if (this.ws) this.ws.close();
  }
}
